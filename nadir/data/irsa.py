"""Data access: SPHEREx Level-2 spectral image discovery and cutout retrieval.

Discovery goes through the IRSA TAP service (tables ``spherex.plane`` and
``spherex.artifact``), which serves both the DR1 and QR3 releases.  Pixels are
fetched with the IRSA cutout service (``?center=..&size=..`` appended to the
archive URL).

Every cached cutout is accompanied by a JSON provenance record.  The cutout
service always ships the ~3.9 MB EPSF table; we drop it before caching (this is
recorded in the provenance) so a cached cutout is ~0.3 MB.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from astropy.io import fits

from .. import __version__, config

log = logging.getLogger(__name__)

KEEP_HDUS = ("IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE")


class DataAccessError(RuntimeError):
    """Raised when the archive cannot be queried or returns unusable data."""


@dataclass
class ArchiveRecord:
    """One SPHEREx Level-2 spectral image that covers the requested position."""

    uri: str  # archive-relative path of the full image
    band: str  # e.g. "D2"
    mjd_start: float
    mjd_end: float
    publisher_did: str
    pipeline_version: str
    archive_checksum: str  # checksum of the *full* archive file, as published

    @property
    def release(self) -> str:
        parts = self.uri.split("/")
        return parts[3] if len(parts) > 3 else "unknown"

    @property
    def obs_key(self) -> str:
        return Path(self.uri).name.removesuffix(".fits")


def _tap_sync(adql: str, timeout: float = 180.0) -> list[dict]:
    """Run a synchronous TAP query and return rows as dicts."""
    import pyvo

    try:
        service = pyvo.dal.TAPService(config.IRSA_TAP)
        table = service.search(adql, maxrec=20000).to_table()
    except Exception as exc:  # pyvo raises many types; normalise them
        raise DataAccessError(f"IRSA TAP query failed: {exc}") from exc
    rows = []
    for row in table:
        rows.append({c: (row[c].item() if hasattr(row[c], "item") else row[c]) for c in table.colnames})
    return rows


def _query_cache_path(key: str) -> Path:
    return config.CACHE_DIR / "queries" / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")


def cached_tap(adql: str, ttl: float = config.TAP_CACHE_TTL_S, refresh: bool = False) -> tuple[list[dict], dict]:
    """TAP query with an on-disk JSON cache.  Returns (rows, query provenance)."""
    path = _query_cache_path(adql)
    if path.exists() and not refresh:
        try:
            payload = json.loads(path.read_text())
            if time.time() - payload["retrieved_unix"] < ttl:
                return payload["rows"], {k: payload[k] for k in ("adql", "service", "retrieved_utc")} | {"cached": True}
        except (json.JSONDecodeError, KeyError):
            log.warning("Ignoring corrupt query cache %s", path)
    rows = _tap_sync(adql)
    payload = {
        "adql": adql,
        "service": config.IRSA_TAP,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retrieved_unix": time.time(),
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return rows, {k: payload[k] for k in ("adql", "service", "retrieved_utc")} | {"cached": False}


def find_observations(ra: float, dec: float, band: str = config.DEFAULT_BAND, refresh: bool = False) -> tuple[list[ArchiveRecord], dict]:
    """List every SPHEREx spectral image (DR1 + QR3) whose footprint contains (ra, dec)."""
    if band not in config.BANDS:
        raise ValueError(f"Unknown band {band!r}; expected one of {sorted(config.BANDS)}")
    if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
        raise ValueError("RA must be in [0, 360) and Dec in [-90, 90] degrees")
    adql = (
        "SELECT a.uri, p.time_bounds_lower, p.time_bounds_upper, p.energy_bandpassname, "
        "p.obs_publisher_did, p.provenance_version, a.contentchecksum "
        "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
        f"WHERE 1 = CONTAINS(POINT('ICRS', {ra:.6f}, {dec:.6f}), p.poly) "
        f"AND p.energy_bandpassname = '{config.BANDS[band]}' "
        "ORDER BY p.time_bounds_lower"
    )
    rows, prov = cached_tap(adql, refresh=refresh)
    records = []
    for r in rows:
        try:
            records.append(
                ArchiveRecord(
                    uri=str(r["uri"]),
                    band=band,
                    mjd_start=float(r["time_bounds_lower"]),
                    mjd_end=float(r["time_bounds_upper"]),
                    publisher_did=str(r.get("obs_publisher_did") or ""),
                    pipeline_version=str(r.get("provenance_version") or ""),
                    archive_checksum=str(r.get("contentchecksum") or ""),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Skipping archive row with incomplete metadata: %s", exc)
    return records, prov


def cutout_url(record: ArchiveRecord, ra: float, dec: float, size_deg: float) -> str:
    return f"{config.IRSA_BASE}{record.uri}?center={ra:.6f},{dec:.6f}d&size={size_deg:.4f}"


def _cutout_paths(url: str) -> tuple[Path, Path]:
    stem = hashlib.sha1(url.encode()).hexdigest()[:24]
    d = config.CACHE_DIR / "cutouts"
    return d / f"{stem}.fits", d / f"{stem}.json"


def _http_get(url: str) -> bytes:
    last: Exception | None = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            resp = requests.get(url, timeout=config.HTTP_TIMEOUT_S)
            if resp.status_code == 200 and resp.content[:6] == b"SIMPLE":
                return resp.content
            # The cutout service answers 200 with a text body for some failures.
            snippet = resp.content[:200].decode("utf-8", "replace")
            last = DataAccessError(f"HTTP {resp.status_code}, non-FITS response: {snippet!r}")
            if resp.status_code in (400, 404):
                break  # not retryable (e.g. position outside this image)
        except requests.RequestException as exc:
            last = exc
        time.sleep(2.0 * (attempt + 1))
    raise DataAccessError(f"Cutout download failed for {url}: {last}")


def fetch_cutout(record: ArchiveRecord, ra: float, dec: float, size_deg: float) -> tuple[Path, dict]:
    """Download (or load from cache) one cutout.  Returns (fits path, provenance dict)."""
    url = cutout_url(record, ra, dec, size_deg)
    fpath, ppath = _cutout_paths(url)
    if fpath.exists() and ppath.exists():
        try:
            return fpath, json.loads(ppath.read_text())
        except json.JSONDecodeError:
            log.warning("Corrupt provenance %s; re-downloading", ppath)

    raw = _http_get(url)
    try:
        with fits.open(io.BytesIO(raw), memmap=False) as hdul:
            names = [h.name for h in hdul]
            missing = [n for n in ("IMAGE", "FLAGS", "VARIANCE") if n not in names]
            if missing:
                raise DataAccessError(f"Cutout lacks required HDUs {missing}: {url}")
            kept = fits.HDUList([hdul[0].copy()] + [hdul[n].copy() for n in KEEP_HDUS if n in names])
            fpath.parent.mkdir(parents=True, exist_ok=True)
            tmp = fpath.with_suffix(".tmp")
            kept.writeto(tmp, overwrite=True)
            tmp.replace(fpath)
    except OSError as exc:
        raise DataAccessError(f"Unreadable FITS from {url}: {exc}") from exc

    prov = {
        "source_url": url,
        "archive": "NASA/IPAC Infrared Science Archive (IRSA)",
        "mission": "SPHEREx",
        "release": record.release,
        "product": "Level 2 spectral image (cutout)",
        "request": {"ra_deg": ra, "dec_deg": dec, "size_deg": size_deg},
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response_bytes": len(raw),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "hdus_received": names,
        "hdus_cached": [h.name for h in kept],
        "note": "EPSF HDU dropped before caching to save space; pixel data unmodified.",
        "nadir_version": __version__,
        "archive_record": asdict(record),
    }
    ppath.write_text(json.dumps(prov, indent=1))
    return fpath, prov


def fetch_cutouts(records: list[ArchiveRecord], ra: float, dec: float, size_deg: float,
                  workers: int = config.DOWNLOAD_WORKERS) -> list[dict]:
    """Fetch many cutouts in parallel.

    Returns one dict per record: {"record", "path", "provenance", "error"}; failures
    are reported per item rather than aborting the batch.
    """
    def one(rec: ArchiveRecord) -> dict:
        try:
            path, prov = fetch_cutout(rec, ra, dec, size_deg)
            return {"record": rec, "path": path, "provenance": prov, "error": None}
        except DataAccessError as exc:
            log.warning("%s", exc)
            return {"record": rec, "path": None, "provenance": None, "error": str(exc)}

    if not records:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, records))
