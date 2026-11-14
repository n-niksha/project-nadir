"""Data access: SPHEREx Level-2 spectral image discovery and cutout retrieval.

Discovery uses the IRSA Simple Image Access (SIA v2) service: one ~2 s query
returns every DR1 + QR3 spectral image of all six detectors covering a position.
The TAP service (``spherex.plane`` + ``spherex.artifact``) returns the same
images but took 9-46 s per position in our measurements (2026-11-13), so it is
kept only as a fallback.  Pixels are fetched with the IRSA cutout service
(``?center=..&size=..`` appended to the archive URL).

Query results are cached on disk.  If the archive is unreachable, an expired
cache entry is served instead and marked ``stale`` so the UI can disclose it.

Every cached cutout is accompanied by a JSON provenance record.  The cutout
service always ships the ~3.9 MB EPSF table (441 detector tiles); we keep only
the tiles overlapping the cutout (recorded in the provenance), so a cached
cutout is ~0.4 MB.
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

import re

import requests
from astropy.io import fits

from .. import __version__, config

log = logging.getLogger(__name__)

KEEP_HDUS = ("IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE")
CUTOUT_CACHE_VERSION = 2  # v2: trimmed EPSF tiles retained


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
    archive_checksum: str  # checksum of the *full* archive file, as published ("" via SIA)
    obs_id: str = ""
    wl_min_um: float | None = None  # detector band limits (not the wavelength at the target)
    wl_max_um: float | None = None

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


def cached_query(key: str, fetch, service: str, ttl: float, refresh: bool = False) -> tuple[list[dict], dict]:
    """Run ``fetch()`` (-> list of row dicts) with an on-disk JSON cache.

    Returns (rows, provenance).  provenance["cached"] says whether the rows came
    from disk; provenance["stale"] is True when the archive failed and an
    expired cache entry was served instead (never silently)."""
    path = _query_cache_path(key)
    payload = None
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            payload["retrieved_unix"]
        except (json.JSONDecodeError, KeyError):
            log.warning("Ignoring corrupt query cache %s", path)
            payload = None

    def prov(p, cached, stale=False, error=None):
        out = {"adql": p["adql"], "service": p["service"], "retrieved_utc": p["retrieved_utc"],
               "cached": cached, "stale": stale}
        if error:
            out["archive_error"] = error
        return out

    if payload and not refresh and time.time() - payload["retrieved_unix"] < ttl:
        return payload["rows"], prov(payload, True)
    try:
        rows = fetch()
    except DataAccessError as exc:
        if payload:
            log.warning("Archive failed (%s); serving stale cache from %s", exc, payload["retrieved_utc"])
            return payload["rows"], prov(payload, True, stale=True, error=str(exc))
        raise
    payload = {
        "adql": key,
        "service": service,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retrieved_unix": time.time(),
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)
    return rows, prov(payload, False)


def cached_tap(adql: str, ttl: float = config.TAP_CACHE_TTL_S, refresh: bool = False) -> tuple[list[dict], dict]:
    """TAP query with an on-disk JSON cache.  Returns (rows, query provenance)."""
    return cached_query(adql, lambda: _tap_sync(adql), config.IRSA_TAP, ttl, refresh)


def _sia_sync(ra: float, dec: float) -> list[dict]:
    """SIA v2 query (DR1 + QR3) for images containing (ra, dec); returns row dicts."""
    from astropy.io.votable import parse_single_table

    params = {"COLLECTION": list(config.SIA_COLLECTIONS), "POS": f"CIRCLE {ra:.6f} {dec:.6f} 0.0003",
              "RESPONSEFORMAT": "VOTABLE", "MAXREC": "5000"}
    try:
        resp = requests.get(config.IRSA_SIA, params=params, timeout=config.HTTP_TIMEOUT_S)
        resp.raise_for_status()
        table = parse_single_table(io.BytesIO(resp.content)).to_table(use_names_over_ids=True)
    except Exception as exc:  # network, HTTP or VOTable errors
        raise DataAccessError(f"IRSA SIA query failed: {exc}") from exc
    keep = ("access_url", "t_min", "t_max", "energy_bandpassname", "obs_publisher_did", "obs_id",
            "obs_collection", "em_min", "em_max", "dataproduct_subtype")
    missing = [k for k in keep if k not in table.colnames]
    if missing:
        raise DataAccessError(f"IRSA SIA response lacks columns {missing}")
    rows = []
    for row in table:
        rows.append({k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in keep})
    return [r for r in rows if str(r["dataproduct_subtype"]) == "science"]


def _record_from_sia(r: dict) -> ArchiveRecord:
    url = str(r["access_url"])
    if not url.startswith(config.IRSA_BASE):
        raise ValueError(f"unexpected access_url {url}")
    uri = url[len(config.IRSA_BASE):]
    m = re.search(r"_(l2b-v\d+)", uri)
    return ArchiveRecord(
        uri=uri, band=str(r["energy_bandpassname"]).replace("SPHEREx-", ""),
        mjd_start=float(r["t_min"]), mjd_end=float(r["t_max"]),
        publisher_did=str(r["obs_publisher_did"]), pipeline_version=m.group(1) if m else "",
        archive_checksum="", obs_id=str(r["obs_id"]),
        wl_min_um=float(r["em_min"]) * 1e6, wl_max_um=float(r["em_max"]) * 1e6,
    )


def find_all_bands(ra: float, dec: float, refresh: bool = False) -> tuple[dict[str, list[ArchiveRecord]], dict]:
    """Every SPHEREx spectral image covering (ra, dec), grouped by detector band
    and sorted by time.  SIA first; TAP if SIA is unavailable."""
    if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
        raise ValueError("RA must be in [0, 360) and Dec in [-90, 90] degrees")
    key = f"SIA spherex dr1+qr3 {ra:.6f} {dec:.6f}"
    try:
        rows, prov = cached_query(key, lambda: _sia_sync(ra, dec), config.IRSA_SIA, config.TAP_CACHE_TTL_S, refresh)
        records = []
        for r in rows:
            try:
                records.append(_record_from_sia(r))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping SIA row with incomplete metadata: %s", exc)
    except DataAccessError as sia_exc:
        log.warning("SIA failed (%s); falling back to TAP", sia_exc)
        records = []
        for band in config.BANDS:
            recs, prov = find_observations_tap(ra, dec, band, refresh)
            records += recs
        prov = dict(prov, fallback_from="SIA", sia_error=str(sia_exc))
    grouped: dict[str, list[ArchiveRecord]] = {b: [] for b in config.BANDS}
    for rec in records:
        if rec.band in grouped:
            grouped[rec.band].append(rec)
    for recs in grouped.values():
        recs.sort(key=lambda r: r.mjd_start)
    return grouped, prov


def find_observations(ra: float, dec: float, band: str = config.DEFAULT_BAND, refresh: bool = False) -> tuple[list[ArchiveRecord], dict]:
    """List every SPHEREx spectral image (DR1 + QR3) of one band whose footprint contains (ra, dec)."""
    if band not in config.BANDS:
        raise ValueError(f"Unknown band {band!r}; expected one of {sorted(config.BANDS)}")
    grouped, prov = find_all_bands(ra, dec, refresh)
    return grouped[band], prov


def find_observations_tap(ra: float, dec: float, band: str = config.DEFAULT_BAND, refresh: bool = False) -> tuple[list[ArchiveRecord], dict]:
    """TAP equivalent of the SIA discovery for one band (slow; fallback only)."""
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
    stem = hashlib.sha1(f"v{CUTOUT_CACHE_VERSION}|{url}".encode()).hexdigest()[:24]
    d = config.CACHE_DIR / "cutouts"
    return d / f"{stem}.fits", d / f"{stem}.json"


def _http_get(url: str) -> bytes:
    """GET a FITS cutout.  Retries with exponential backoff; HTTP 429/503
    ("Server busy, retry later") honour Retry-After and get more attempts."""
    last: Exception | None = None
    attempts = config.HTTP_RETRIES
    attempt = 0
    while attempt < attempts:
        wait = min(2.0 * 2 ** attempt, 30.0)
        try:
            resp = requests.get(url, timeout=config.HTTP_TIMEOUT_S)
            if resp.status_code == 200 and resp.content[:6] == b"SIMPLE":
                return resp.content
            # The cutout service answers 200 with a text body for some failures.
            snippet = resp.content[:200].decode("utf-8", "replace")
            last = DataAccessError(f"HTTP {resp.status_code}, non-FITS response: {snippet!r}")
            if resp.status_code in (400, 404):
                break  # not retryable (e.g. position outside this image)
            if resp.status_code in (429, 503):
                attempts = max(attempts, config.HTTP_RETRIES_BUSY)
                try:
                    wait = max(wait, float(resp.headers.get("Retry-After", 0)))
                except ValueError:
                    pass
        except requests.RequestException as exc:
            last = exc
        attempt += 1
        if attempt < attempts:
            time.sleep(wait)
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
            if "EPSF" in names:
                from ..science.psf import trim_epsf_hdu
                trimmed = trim_epsf_hdu(hdul["EPSF"], hdul["IMAGE"].header)
                if trimmed is not None:
                    kept.append(trimmed)
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
        "note": "EPSF table trimmed to the tiles overlapping this cutout before caching; pixel data unmodified.",
        "nadir_version": __version__,
        "archive_record": asdict(record),
    }
    ppath.write_text(json.dumps(prov, indent=1))
    return fpath, prov


def is_cached(record: ArchiveRecord, ra: float, dec: float, size_deg: float) -> bool:
    fpath, ppath = _cutout_paths(cutout_url(record, ra, dec, size_deg))
    return fpath.exists() and ppath.exists()


class Cancelled(Exception):
    """Raised inside a job when the client cancelled or superseded it."""


def fetch_cutouts(records: list[ArchiveRecord], ra: float, dec: float, size_deg: float,
                  workers: int = config.DOWNLOAD_WORKERS, progress=None, cancelled=None) -> list[dict]:
    """Fetch many cutouts in parallel.

    Returns one dict per record: {"record", "path", "provenance", "error"}; failures
    are reported per item rather than aborting the batch.  ``progress(done, total,
    from_cache)`` is called after each item; if ``cancelled()`` becomes true, items
    not yet started are skipped and ``Cancelled`` is raised.
    """
    import threading
    lock = threading.Lock()
    state = {"done": 0, "cached": 0}

    def one(rec: ArchiveRecord) -> dict:
        if cancelled is not None and cancelled():
            return {"record": rec, "path": None, "provenance": None, "error": "cancelled"}
        hit = is_cached(rec, ra, dec, size_deg)
        out = _one(rec)
        with lock:
            state["done"] += 1
            state["cached"] += int(hit)
            if progress:
                progress(state["done"], len(records), state["cached"])
        return out

    def _one(rec: ArchiveRecord) -> dict:
        try:
            path, prov = fetch_cutout(rec, ra, dec, size_deg)
            return {"record": rec, "path": path, "provenance": prov, "error": None}
        except DataAccessError as exc:
            log.warning("%s", exc)
            return {"record": rec, "path": None, "provenance": None, "error": str(exc)}

    if not records:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        out = list(pool.map(one, records))
    if cancelled is not None and cancelled():
        raise Cancelled()
    return out
