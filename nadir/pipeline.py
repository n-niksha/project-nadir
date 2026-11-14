"""Orchestration: ties data access to the science modules.

The API server and the command-line scripts both call into this module, so
there is exactly one implementation of "search", "analyze a pair" and "track".
All returned structures are plain JSON-serialisable Python (plus numpy arrays
under the ``arrays`` key, which presentation code turns into images).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from . import config
from .data import catalogs, irsa
from .science import motion, pairing
from .science.difference import difference_image
from .science.observation import Observation, ObservationError, load_observation
from .science.registration import cross_correlation_shift
from .science.reprojection import make_grid, reproject_observation
from .science.sources import Source, detect_sources

log = logging.getLogger(__name__)


@dataclass
class Session:
    ra: float
    dec: float
    size_deg: float
    band: str
    observations: list[Observation]
    usable: list[bool]
    unusable_reasons: list[str]
    failures: list[dict]
    query: dict
    n_archive: int
    timings: dict = field(default_factory=dict)
    _detections: dict = field(default_factory=dict)
    _gaia: tuple | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def detections(self, i: int) -> list[Source]:
        with self._lock:
            if i not in self._detections:
                self._detections[i] = detect_sources(self.observations[i])
            return self._detections[i]

    def gaia(self) -> tuple[list[dict], dict | None, str | None]:
        """(stars, provenance, error).  Never raises: the cross-reference is optional."""
        if self._gaia is None:
            try:
                stars, prov = catalogs.gaia_cone(self.ra, self.dec, self.size_deg * 0.75)
                self._gaia = (stars, prov, None)
            except irsa.DataAccessError as exc:
                log.warning("Gaia cross-reference unavailable: %s", exc)
                return [], None, str(exc)  # not cached: retry next time
        return self._gaia


_sessions: dict[tuple, Session] = {}


def search(ra: float, dec: float, size_deg: float = config.DEFAULT_CUTOUT_SIZE_DEG,
           band: str = config.DEFAULT_BAND, max_observations: int | None = None,
           refresh: bool = False) -> Session:
    """Find and fetch every SPHEREx observation of a position in one band."""
    if not (0.02 <= size_deg <= 0.5):
        raise ValueError("size_deg must be between 0.02 and 0.5 degrees")
    key = (round(ra, 5), round(dec, 5), round(size_deg, 4), band, max_observations)
    if key in _sessions and not refresh:
        return _sessions[key]
    t0 = time.perf_counter()
    records, qprov = irsa.find_observations(ra, dec, band, refresh=refresh)
    t1 = time.perf_counter()
    n_archive = len(records)
    if max_observations and len(records) > max_observations:
        # keep an even spread in time so long baselines survive the cut
        pick = np.unique(np.round(np.linspace(0, len(records) - 1, max_observations)).astype(int))
        records = [records[i] for i in pick]
    fetched = irsa.fetch_cutouts(records, ra, dec, size_deg)
    t2 = time.perf_counter()
    observations, failures = [], []
    for item in fetched:
        if item["error"]:
            failures.append({"uri": item["record"].uri, "error": item["error"]})
            continue
        try:
            observations.append(load_observation(item["path"], (ra, dec), item["provenance"]))
        except ObservationError as exc:
            failures.append({"uri": item["record"].uri, "error": str(exc)})
    observations.sort(key=lambda o: (o.mjd is None, o.mjd or 0.0))
    expected = (size_deg * 3600.0 / config.PIXEL_SCALE_ARCSEC) ** 2
    checks = [pairing.is_usable(o, int(expected)) for o in observations]
    session = Session(ra, dec, size_deg, band, observations, [c[0] for c in checks], [c[1] for c in checks],
                      failures, qprov, n_archive,
                      timings={"tap_query_s": t1 - t0, "fetch_s": t2 - t1, "parse_s": time.perf_counter() - t2})
    _sessions[key] = session
    return session


def session_summary(session: Session, max_rel_dlambda: float = 0.02) -> dict:
    obs = []
    for i, o in enumerate(session.observations):
        d = o.summary()
        d.update(index=i, usable=session.usable[i], unusable_reason=session.unusable_reasons[i])
        obs.append(d)
    pairs = pairing.rank_pairs(session.observations, session.usable, max_rel_dlambda)
    note = None
    if not pairs and sum(session.usable) >= 2:
        pairs = pairing.rank_pairs(session.observations, session.usable, max_rel_dlambda=1.0)
        pairs.sort(key=lambda p: p["rel_dlambda"])
        note = (f"No pair agrees in wavelength to within {max_rel_dlambda:.0%} with a >30 d baseline; "
                "showing the closest wavelength matches. Difference images will contain spectral residuals.")
    elif not pairs:
        note = "Fewer than two usable observations with a >30 day baseline; no time-domain comparison is possible."
    return {"ra": session.ra, "dec": session.dec, "size_deg": session.size_deg, "band": session.band,
            "n_in_archive": session.n_archive, "n_loaded": len(session.observations),
            "n_usable": int(sum(session.usable)), "observations": obs, "suggested_pairs": pairs,
            "pair_note": note, "failures": session.failures, "query": session.query,
            "timings": session.timings}


def analyze(session: Session, ia: int, ib: int, oversample: int = 2) -> dict:
    """Full pair analysis.  Returns JSON-safe results plus raw arrays under 'arrays'."""
    n = len(session.observations)
    if not (0 <= ia < n and 0 <= ib < n) or ia == ib:
        raise ValueError("Pick two different valid observation indices")
    oa, ob = session.observations[ia], session.observations[ib]
    if oa.mjd is not None and ob.mjd is not None and oa.mjd > ob.mjd:
        ia, ib, oa, ob = ib, ia, ob, oa
    timings = {}
    t = time.perf_counter()
    grid = make_grid(session.ra, session.dec, session.size_deg, oversample)
    sa, sb = session.detections(ia), session.detections(ib)
    timings["detect_s"] = time.perf_counter() - t
    t = time.perf_counter()
    res = motion.analyze_pair(oa, ob, grid, sa, sb)
    timings["motion_s"] = time.perf_counter() - t

    gaia_info = {"available": False}
    if res.ok:
        t = time.perf_counter()
        stars, gprov, gerr = session.gaia()
        if gerr:
            gaia_info["error"] = gerr
        else:
            nx = motion.cross_reference(res, stars, grid, oa.mjd, ob.mjd)
            gaia_info = {"available": True, "n_catalog": len(stars), "n_matched": nx, "provenance": gprov}
        timings["catalog_s"] = time.perf_counter() - t

    t = time.perf_counter()
    shift = (0.0, 0.0)
    if res.ok:
        c = np.array([[(grid.shape[1] - 1) / 2, (grid.shape[0] - 1) / 2]])
        shift = tuple(float(v) for v in (res.transform.apply(c) - c)[0])
    ra_img = reproject_observation(oa, grid)
    rb_wcs = reproject_observation(ob, grid)
    rb_reg = reproject_observation(ob, grid, shift) if res.ok else rb_wcs
    timings["reproject_s"] = time.perf_counter() - t

    t = time.perf_counter()
    flux_scale = 1.0
    refs = [s for s in res.sources if s.is_reference and s.a["flux"] > 0 and s.b["flux"] > 0]
    if len(refs) >= 5:
        flux_scale = float(np.median([s.a["flux"] / s.b["flux"] for s in refs]))
    diff = difference_image(ra_img, rb_reg, flux_scale)
    xc = cross_correlation_shift(ra_img.image, rb_wcs.image, valid=~(ra_img.saturated | rb_wcs.saturated))
    timings["difference_s"] = time.perf_counter() - t

    xcorr = None
    if xc is not None:
        xcorr = {"shift_east_arcsec": -xc[0] * grid.pixel_scale_arcsec,
                 "shift_north_arcsec": xc[1] * grid.pixel_scale_arcsec,
                 "note": "Independent FFT cross-correlation of the WCS-aligned images (whole-field check)."}

    rel = None
    if oa.wavelength_um and ob.wavelength_um:
        rel = abs(oa.wavelength_um - ob.wavelength_um) / (0.5 * (oa.wavelength_um + ob.wavelength_um))
    sources = [asdict(s) for s in res.sources]
    target = _pick_target(sources, grid)
    out = {
        "ok": res.ok, "message": res.message,
        "a": dict(oa.summary(), index=ia), "b": dict(ob.summary(), index=ib),
        "dt_days": res.dt_days, "rel_dlambda": rel,
        "grid": {"nx": grid.shape[1], "ny": grid.shape[0], "pixel_scale_arcsec": grid.pixel_scale_arcsec,
                 "center_ra": session.ra, "center_dec": session.dec, "projection": "TAN, north up, east left"},
        "registration": res.registration, "xcorr_check": xcorr,
        "n_detected": list(res.n_detected), "sources": sources,
        "unmatched_a": res.unmatched_a, "unmatched_b": res.unmatched_b,
        "target_id": target, "gaia": gaia_info,
        "difference": {k: diff[k] for k in ("robust_sigma", "median", "flux_scale", "valid_fraction",
                                              "background_a", "background_b")},
        "timings": timings,
        "arrays": {"a": ra_img, "b": rb_reg, "b_wcs": rb_wcs, "diff": diff["diff"]},
    }
    out["verdict"] = build_verdict(out)
    return _clean(out)


def _pick_target(sources: list[dict], grid) -> int | None:
    """Matched source closest to the requested position (within 2 native pixels)."""
    if not sources:
        return None
    cx, cy = (grid.shape[1] - 1) / 2, (grid.shape[0] - 1) / 2
    d = [math.hypot(s["x"] - cx, s["y"] - cy) for s in sources]
    k = int(np.argmin(d))
    return sources[k]["id"] if d[k] * grid.pixel_scale_arcsec <= 2 * config.PIXEL_SCALE_ARCSEC else None


def describe_source(s: dict, dt_days: float) -> str:
    """One honest sentence-level summary of what was measured for a source."""
    yrs = dt_days / 365.25
    sig_tot = math.hypot(s["sigma_east_arcsec"], s["sigma_north_arcsec"]) / math.sqrt(2)
    txt = (f"Measured displacement {s['total_arcsec']:.2f}\" "
           f"(E {s['d_east_arcsec']:+.2f} ± {s['sigma_east_arcsec']:.2f}\", "
           f"N {s['d_north_arcsec']:+.2f} ± {s['sigma_north_arcsec']:.2f}\") over {dt_days:.1f} d")
    if abs(yrs) > 0.05:
        txt += f" = {s['total_arcsec'] / abs(yrs):.2f} ± {sig_tot / abs(yrs):.2f} \"/yr"
    txt += f"; significance {s['significance']:.1f}σ. "
    status = s["status"]
    if status == "reference":
        txt += "Consistent with zero: this source was used as a stationary reference star. "
    elif status == "no_significant_motion":
        txt += f"Below the {motion.DETECTION_SIGMA:.0f}σ threshold: no reliable motion detected. "
    elif status == "candidate_motion":
        txt += "Significant displacement with clean measurements in both epochs: motion candidate. "
    else:
        txt += ("Displacement is formally significant, but this is a LOW-CONFIDENCE measurement: "
                + "; ".join(s["reasons"]) + ". ")
    cat = s.get("catalog")
    if cat and cat.get("predicted"):
        p = cat["predicted"]
        de, dn = s["d_east_arcsec"] - p["east_arcsec"], s["d_north_arcsec"] - p["north_arcsec"]
        nsig = math.hypot(de / s["sigma_east_arcsec"], dn / s["sigma_north_arcsec"])
        txt += (f"Gaia DR3 {cat['source_id']} (G={cat['gmag']:.1f}) predicts E {p['east_arcsec']:+.2f}\", "
                f"N {p['north_arcsec']:+.2f}\" from proper motion + parallax; measured − predicted = "
                f"({de:+.2f}\", {dn:+.2f}\"), {nsig:.1f}σ"
                + (" — consistent." if nsig < 3 else " — NOT consistent; treat the measurement with caution."))
        if cat.get("n_gaia_within_radius", 1) > 1:
            txt += f" ({cat['n_gaia_within_radius']} Gaia stars lie within one SPHEREx pixel: crowded.)"
    elif cat:
        txt += f"Nearest Gaia DR3 star {cat['source_id']} has no proper motion listed."
    else:
        txt += "No Gaia DR3 counterpart (G<17) within 6\"."
    return txt


def build_verdict(out: dict) -> dict:
    if not out["ok"]:
        return {"headline": "No reliable measurement", "target": out["message"], "candidates": []}
    by_id = {s["id"]: s for s in out["sources"]}
    dt = out["dt_days"]
    if out["target_id"] is not None:
        target_txt = describe_source(by_id[out["target_id"]], dt)
    else:
        near = [u for u in out["unmatched_a"] + out["unmatched_b"]]
        target_txt = ("No source at the requested position was measured in both epochs, so no displacement "
                      "can be reported for it. " + (f"{len(near)} detections exist in only one epoch "
                                                    "(noise peaks, artifacts, blends, or motion larger than "
                                                    "the 3-pixel match radius)." if near else ""))
    cands = [s for s in out["sources"] if s["status"].startswith("candidate")]
    cands.sort(key=lambda s: -s["significance"])
    reg = out["registration"]
    head = (f"{len(cands)} motion candidate(s) among {len(out['sources'])} matched sources; "
            f"registration scatter {reg['rms_east_arcsec']:.2f}\"/{reg['rms_north_arcsec']:.2f}\" (E/N) "
            f"from {reg['n_used']} reference stars.")
    return {"headline": head, "target": target_txt, "candidates": [s["id"] for s in cands]}


def track(session: Session, ref_index: int, ra: float, dec: float, oversample: int = 2) -> dict:
    """Multi-epoch positions of the source nearest (ra, dec), all usable epochs of the session."""
    idx = [i for i, u in enumerate(session.usable) if u]
    if ref_index not in idx:
        raise ValueError("Reference observation is not usable")
    t = time.perf_counter()
    grid = make_grid(session.ra, session.dec, session.size_deg, oversample)
    obs = [session.observations[i] for i in idx]
    det = [session.detections(i) for i in idx]
    out = motion.track_source(obs, idx.index(ref_index), grid, ra, dec, det)
    out["elapsed_s"] = time.perf_counter() - t
    stars, _, gerr = session.gaia()
    out["catalog"] = None
    out["skipped_unusable"] = [{"obs_id": o.obs_id, "reason": r} for o, u, r in
                               zip(session.observations, session.usable, session.unusable_reasons) if not u]
    if stars and out["points"]:
        mjd0 = session.observations[ref_index].mjd
        pos = np.array([motion.gaia_position_at(g, mjd0) for g in stars])
        sep = np.hypot((pos[:, 0] - ra) * math.cos(math.radians(dec)), pos[:, 1] - dec) * 3600.0
        near = np.flatnonzero(sep < 6.0)
        if near.size:
            g = stars[min(near, key=lambda q: stars[q]["gmag"] or 99)]
            out["catalog"] = {"catalog": "Gaia DR3", "source_id": g["source_id"], "gmag": g["gmag"],
                              "pm_east_arcsec_per_yr": None if g["pmra"] is None else g["pmra"] / 1e3,
                              "pm_north_arcsec_per_yr": None if g["pmdec"] is None else g["pmdec"] / 1e3,
                              "parallax_mas": g["parallax"]}
            # Catalog-predicted apparent offsets (proper motion + parallax) relative to the
            # reference epoch, for comparison only -- never used in the measurement.
            for p in out["points"]:
                pred = motion.predicted_displacement(g, mjd0, p["mjd"])
                p["predicted_east_arcsec"] = pred and pred["east_arcsec"]
                p["predicted_north_arcsec"] = pred and pred["north_arcsec"]
    elif gerr:
        out["catalog_error"] = gerr
    return _clean(out)


def _clean(obj):
    """Make nested results JSON-safe (NaN/inf -> None, numpy scalars -> Python)."""
    if isinstance(obj, dict):
        return {k: (v if k == "arrays" else _clean(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj
