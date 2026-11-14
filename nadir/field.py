"""A *field*: every SPHEREx epoch of one sky position in one detector band,
registered onto one celestial grid, with field-wide multi-epoch tracks.

This is the unit the web application works with.  ``build_field`` runs as a
background job with staged progress; results stay in memory (LRU) and every
input is cached on disk, so a rebuild of a known field needs no network.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import io
import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import asdict

import numpy as np

from . import config
from .data import catalogs, irsa
from .jobs import Job
from .processing import detect_many
from .science import fieldtrack, motion, pairing, spectral
from .science.observation import WARN_FLAGS, Observation, ObservationError, load_observation
from .science.reprojection import Reprojected, make_grid, reproject_observation
from .science.sources import Source, robust_background

log = logging.getLogger(__name__)

STAGES = ["discover", "download", "read", "detect", "register", "render", "track"]
ARTIFACT = set(motion.ARTIFACT_FLAGS)
MAX_FIELDS = 6


def field_id(ra, dec, size, band) -> str:
    return hashlib.sha1(f"{ra:.5f}|{dec:.5f}|{size:.4f}|{band}".encode()).hexdigest()[:12]


class Field:
    def __init__(self, fid, ra, dec, size_deg, band):
        self.fid, self.ra, self.dec, self.size_deg, self.band = fid, ra, dec, size_deg, band
        self.grid = make_grid(ra, dec, size_deg)
        self.observations: list[Observation] = []
        self.usable: list[bool] = []
        self.unusable_reason: list[str] = []
        self.detections: list[list[Source]] = []
        self.ref: int | None = None
        self.best_pair: dict | None = None
        self.pairs: list[dict] = []
        self.transforms: dict = {}
        self.reg_stats: dict = {}
        self.frames: dict[int, Reprojected] = {}
        self.display: dict[int, np.ndarray] = {}
        self.flux_scale: dict[int, float] = {}
        self.tracks: list[fieldtrack.SourceTrack] = []
        self.pair_result = None
        self.stretch = (0.0, 1.0)
        self.provenance: dict = {}
        self.records_all: dict = {}
        self.failures: list = []
        self.timings: dict = {}
        self.png_cache: dict = {}
        self.catalogs: dict | None = None
        self.spectral_obs: list | None = None
        self.spectra: dict = {}
        self.lock = threading.Lock()
        self.built_at = None

    # ------------------------------------------------------------------ summary
    def epoch_info(self, i: int) -> dict:
        o = self.observations[i]
        d = o.summary()
        d.update(index=i, usable=self.usable[i], unusable_reason=self.unusable_reason[i],
                 registration=self.reg_stats.get(i), has_frame=i in self.frames,
                 flux_scale=self.flux_scale.get(i), is_reference=i == self.ref,
                 n_sources=len(self.detections[i]) if i < len(self.detections) else None)
        return d

    def summary(self) -> dict:
        g = self.grid
        tiers = {}
        for t in self.tracks:
            tiers[t.tier] = tiers.get(t.tier, 0) + 1
        mjds = [o.mjd for o, u in zip(self.observations, self.usable) if u and o.mjd]
        visits = []
        if mjds:
            lab = fieldtrack.visits_of(mjds)
            for v in sorted(set(lab)):
                m = [x for x, l_ in zip(mjds, lab) if l_ == v]
                visits.append({"visit": int(v), "mjd_min": min(m), "mjd_max": max(m), "n": len(m)})
        return clean({
            "fid": self.fid, "ra": self.ra, "dec": self.dec, "size_deg": self.size_deg, "band": self.band,
            "built_at": self.built_at,
            "grid": {"nx": g.shape[1], "ny": g.shape[0], "crval": list(g.wcs.wcs.crval),
                     "crpix": list(g.wcs.wcs.crpix), "cdelt": list(g.wcs.wcs.cdelt),
                     "pixel_scale_arcsec": g.pixel_scale_arcsec, "projection": "TAN (gnomonic), north up, east left"},
            "reference_index": self.ref, "best_pair": self.best_pair, "suggested_pairs": self.pairs,
            "epochs": [self.epoch_info(i) for i in range(len(self.observations))],
            "visits": visits,
            "sources": [self.source_summary(t) for t in sorted(self.tracks, key=lambda t: -t.priority)],
            "tier_counts": tiers,
            "stretch": {"lo": self.stretch[0], "hi": self.stretch[1], "unit": "MJy/sr (background-subtracted, "
                        "scaled to the reference epoch's stellar flux level)", "function": "asinh"},
            "provenance": self.provenance, "failures": self.failures, "timings": self.timings,
            "bands_available": {b: len(v) for b, v in self.records_all.items()},
            "catalogs_ready": self.catalogs is not None,
            "spectral_ready": self.spectral_obs is not None,
        })

    def source_summary(self, t: fieldtrack.SourceTrack) -> dict:
        d = {k: getattr(t, k) for k in ("id", "ra", "dec", "x", "y", "tier", "reasons", "fit", "n_epochs", "n_visits",
                                         "baseline_days", "clean_fraction", "core_masked_fraction",
                                         "blended_fraction", "priority")}
        v = t.variability
        d["variability"] = None if v is None else {k: v.get(k) for k in ("var_z", "n_pairs", "flag", "note",
                                                                        "max_abs_mag_change")}
        d["pair"] = getattr(t, "pair", None)
        d["catalog"] = getattr(t, "catalog", None)
        ref_pt = next((p for p in t.points if p["is_reference_epoch"]), None)
        d["flux_ref"] = ref_pt["flux"] if ref_pt else None
        d["saturated"] = t.core_masked_fraction > 0.5
        return d

    def source_detail(self, sid: int) -> dict:
        t = next((t for t in self.tracks if t.id == sid), None)
        if t is None:
            raise KeyError(sid)
        d = self.source_summary(t)
        d["points"] = t.points
        d["variability_detail"] = t.variability
        cat = getattr(t, "catalog", None)
        if cat and cat.get("gaia") and cat["gaia"].get("pmra") is not None:
            g = cat["gaia"]
            ref_mjd = self.observations[self.ref].mjd
            d["gaia_prediction"] = [
                {"mjd": p["mjd"], **{k: v for k, v in motion.predicted_displacement(g, ref_mjd, p["mjd"]).items()
                                     if k in ("east_arcsec", "north_arcsec")}} for p in t.points]
            span = np.linspace(min(p["mjd"] for p in t.points) - 10, max(p["mjd"] for p in t.points) + 10, 80)
            d["gaia_curve"] = [{"mjd": float(m), **{k: v for k, v in motion.predicted_displacement(g, ref_mjd, m).items()
                                                   if k in ("east_arcsec", "north_arcsec")}} for m in span]
        d["history"] = getattr(t, "history", None)
        return clean(d)


_fields: "OrderedDict[str, Field]" = OrderedDict()
_fields_lock = threading.Lock()


def get_field(fid: str) -> Field | None:
    with _fields_lock:
        f = _fields.get(fid)
        if f:
            _fields.move_to_end(fid)
        return f


def _store(f: Field):
    with _fields_lock:
        _fields[f.fid] = f
        _fields.move_to_end(f.fid)
        while len(_fields) > MAX_FIELDS:
            _fields.popitem(last=False)


# ---------------------------------------------------------------------- build
def build_field(job: Job, ra: float, dec: float, size_deg: float, band: str, refresh: bool = False) -> dict:
    fid = field_id(ra, dec, size_deg, band)
    existing = get_field(fid)
    if existing is not None and existing.built_at and not refresh:
        return {"fid": fid, "reused": True}
    f = Field(fid, ra, dec, size_deg, band)
    T = {}

    # 1. discover ---------------------------------------------------------
    t0 = time.perf_counter()
    job.set_stage("discover", "Asking the NASA/IPAC archive (IRSA) which SPHEREx images cover this position")
    grouped, qprov = irsa.find_all_bands(ra, dec, refresh=refresh)
    f.records_all = grouped
    records = grouped[band]
    T["discover_s"] = time.perf_counter() - t0
    if not records:
        raise irsa.DataAccessError(f"SPHEREx has no {band} images covering RA {ra:.4f}, Dec {dec:.4f} "
                                   f"in DR1/QR3 (other bands: {', '.join(f'{b}={len(v)}' for b, v in grouped.items())})")

    # 2. download ---------------------------------------------------------
    t0 = time.perf_counter()
    n_cached = sum(irsa.is_cached(r, ra, dec, size_deg) for r in records)
    job.set_stage("download", f"{len(records)} images; {n_cached} already in the local cache", total=len(records))
    fetched = irsa.fetch_cutouts(records, ra, dec, size_deg,
                                 progress=lambda d, n, c: job.progress(d, n, cached=c, fresh=d - c),
                                 cancelled=lambda: job.cancelled)
    T["download_s"] = time.perf_counter() - t0
    f.provenance = {"query": qprov, "n_from_cache": n_cached, "n_downloaded": len(records) - n_cached,
                    "archive": "NASA/IPAC Infrared Science Archive (IRSA)", "releases": sorted(set(r.release for r in records))}

    # 3. read ---------------------------------------------------------------
    t0 = time.perf_counter()
    job.set_stage("read", "Reading FITS images, flags, variance, WCS and wavelengths", total=len(fetched))
    obs = []
    for k, item in enumerate(fetched):
        job.check()
        if item["error"]:
            f.failures.append({"uri": item["record"].uri, "error": item["error"]})
            continue
        try:
            obs.append(load_observation(item["path"], (ra, dec), item["provenance"]))
        except ObservationError as exc:
            f.failures.append({"uri": item["record"].uri, "error": str(exc)})
        job.progress(k + 1)
    obs.sort(key=lambda o: (o.mjd is None, o.mjd or 0.0))
    f.observations = obs
    expected = (size_deg * 3600.0 / config.PIXEL_SCALE_ARCSEC) ** 2
    checks = [pairing.is_usable(o, int(expected)) for o in obs]
    f.usable = [c[0] for c in checks]
    f.unusable_reason = [c[1] for c in checks]
    T["read_s"] = time.perf_counter() - t0
    if sum(f.usable) < 2:
        raise irsa.DataAccessError(f"Only {sum(f.usable)} usable {band} images at this position; "
                                   "a time comparison needs at least two.")

    # 4. detect -------------------------------------------------------------
    t0 = time.perf_counter()
    job.set_stage("detect", "Finding and centroiding sources on native pixels", total=len(obs))
    f.detections = detect_many([(o.path, ra, dec, o.provenance) for o in obs],
                               progress=lambda d, n, **k: job.progress(d, n, **k), check=job.check)
    T["detect_s"] = time.perf_counter() - t0

    # 5. register -----------------------------------------------------------
    t0 = time.perf_counter()
    f.pairs = pairing.rank_pairs(obs, f.usable)
    if not f.pairs:
        f.pairs = sorted(pairing.rank_pairs(obs, f.usable, max_rel_dlambda=1.0), key=lambda p: p["rel_dlambda"])[:12]
    if f.pairs:
        f.best_pair = dict(f.pairs[0])
        f.ref = f.best_pair["a"]
    else:
        f.ref = next(i for i, u in enumerate(f.usable) if u)
    usable_idx = [i for i, u in enumerate(f.usable) if u]
    job.set_stage("register", "Aligning every epoch to the reference epoch with field stars", total=len(usable_idx))
    for n, i in enumerate(usable_idx):
        job.check()
        if i == f.ref:
            f.reg_stats[i] = {"ok": True, "reference": True}
        else:
            res = motion.analyze_pair(obs[f.ref], obs[i], f.grid, f.detections[f.ref], f.detections[i])
            if res.ok:
                f.transforms[i] = res.transform
                f.reg_stats[i] = dict(res.registration, ok=True)
                if f.best_pair and i == f.best_pair["b"]:
                    f.pair_result = res
            else:
                f.reg_stats[i] = {"ok": False, "message": res.message}
                f.usable[i] = False
                f.unusable_reason[i] = "registration failed: " + res.message
        job.progress(n + 1)
    T["register_s"] = time.perf_counter() - t0

    # 6. track (before rendering: display normalisation uses stationary stars) --
    t0 = time.perf_counter()
    job.set_stage("track", "Following every source through all epochs", total=1)
    _build_tracks(f)
    T["track_s"] = time.perf_counter() - t0

    # 7. render -------------------------------------------------------------
    t0 = time.perf_counter()
    good_idx = [i for i, u in enumerate(f.usable) if u]
    job.set_stage("render", "Resampling all epochs onto one celestial grid", total=len(good_idx))
    _render_frames(f, good_idx, job)
    T["render_s"] = time.perf_counter() - t0
    f.timings = {k: round(v, 3) for k, v in T.items()}
    f.built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _store(f)
    return {"fid": fid, "reused": False}


def _build_tracks(f: Field):
    g = f.grid
    ref_obs = f.observations[f.ref]
    frames, idx_list = [], []
    for i, ok in enumerate(f.usable):
        if not ok:
            continue
        srcs = [s for s in f.detections[i] if motion._usable(s)]
        if not srcs:
            continue
        xy = motion.grid_positions(srcs, g)
        kn = motion._native_to_grid_scale(f.observations[i], g)
        if i == f.ref:
            sig_reg, floor = np.zeros_like(xy), (0.0, 0.0)
        else:
            tf = f.transforms[i]
            xy, sig_reg = tf.apply(xy), tf.position_sigma(xy)
            floor = (tf.rms_x / math.sqrt(2), tf.rms_y / math.sqrt(2))
        sig = np.empty_like(xy)
        for j, s in enumerate(srcs):
            f0, f1 = floor
            if s.core_masked:
                f0 = f1 = max(f0, f1, motion.CORE_MASKED_FLOOR_PX * kn)
            sig[j] = (math.hypot(max(s.x_err * kn, f0), sig_reg[j, 0]), math.hypot(max(s.y_err * kn, f1), sig_reg[j, 1]))
        frames.append(fieldtrack.EpochFrame(
            index=i, mjd=f.observations[i].mjd, wavelength_um=f.observations[i].wavelength_um, xy=xy, sigma_xy=sig,
            flux=np.array([s.flux for s in srcs]), core_masked=np.array([s.core_masked for s in srcs]),
            blended=np.array([s.blended for s in srcs]),
            artifact=np.array([bool(set(s.warn_flags) & ARTIFACT) for s in srcs]), is_reference=i == f.ref))
        idx_list.append(i)
    ref_pos = idx_list.index(f.ref)
    ref_srcs = [s for s in f.detections[f.ref] if motion._usable(s)]
    world = np.array([[s.ra, s.dec] for s in ref_srcs])
    kref = motion._native_to_grid_scale(ref_obs, g)
    f.tracks = fieldtrack.build_tracks(frames, ref_pos, world, g.pixel_scale_arcsec,
                                       radius_px=motion.MATCH_RADIUS_NATIVE_PX * kref)
    # 2-epoch (milestone-1) significance for the best pair, used to show rejections.
    pair_sig = {}
    if f.pair_result is not None:
        ref_xy = frames[ref_pos].xy
        for s in f.pair_result.sources:
            d = np.hypot(ref_xy[:, 0] - s.x, ref_xy[:, 1] - s.y)
            k = int(np.argmin(d))
            if d[k] < 0.5:
                pair_sig[k] = s.significance
                f.tracks[k].pair = {"significance": s.significance, "d_east_arcsec": s.d_east_arcsec,
                                    "d_north_arcsec": s.d_north_arcsec, "sigma_east_arcsec": s.sigma_east_arcsec,
                                    "sigma_north_arcsec": s.sigma_north_arcsec, "status": s.status,
                                    "reasons": s.reasons}
    fieldtrack.apply_pair_check(f.tracks, pair_sig)
    fieldtrack.variability(f.tracks)
    for t in f.tracks:
        t.priority = fieldtrack.priority_score(t)
    # display flux normalisation per epoch, from clean stationary stars
    for k_fr, fr in enumerate(frames):
        ratios = []
        for t in f.tracks:
            if t.tier != "stationary" or t.core_masked_fraction > 0:
                continue
            pa = next((p for p in t.points if p["is_reference_epoch"]), None)
            pb = next((p for p in t.points if p["frame"] == k_fr), None)
            if pa and pb and pa["flux"] > 0 and pb["flux"] > 0 and not (pa["blended"] or pb["blended"]):
                ratios.append(pa["flux"] / pb["flux"])
        f.flux_scale[fr.index] = float(np.median(ratios)) if len(ratios) >= 5 else 1.0


def _render_frames(f: Field, idx: list[int], job: Job):
    g = f.grid
    centre = np.array([[(g.shape[1] - 1) / 2, (g.shape[0] - 1) / 2]])

    def one(i):
        shift = (0.0, 0.0)
        if i in f.transforms:
            shift = tuple(float(v) for v in (f.transforms[i].apply(centre) - centre)[0])
        return i, reproject_observation(f.observations[i], g, shift)

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i, rp in pool.map(one, idx):
            job.check()
            f.frames[i] = rp
            valid = np.isfinite(rp.image)
            bkg, _ = robust_background(rp.image, ~valid)
            f.display[i] = ((rp.image - bkg) * f.flux_scale.get(i, 1.0)).astype(np.float32)
            done += 1
            job.progress(done)
    ref = f.display[f.ref]
    fin = ref[np.isfinite(ref)]
    _, sig = robust_background(ref, ~np.isfinite(ref))
    f.stretch = (float(-2.0 * sig), float(np.percentile(fin, 99.8)) if fin.size else 1.0)


# ------------------------------------------------------------------- images
def _palette_png(index_img: np.ndarray, palette: list[int]) -> bytes:
    from PIL import Image
    im = Image.fromarray(index_img[::-1].astype(np.uint8), "P")  # row 0 at the bottom = north up
    im.putpalette(palette)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


GRAY_PALETTE = sum(([int(v), int(v), int(min(255, v * 1.02 + 2))] for v in np.linspace(0, 255, 254)), []) \
    + [16, 20, 44] + [255, 246, 228]  # 254 = no data (navy), 255 = saturated core (near-white)


def frame_png(f: Field, i: int) -> bytes:
    key = ("frame", i)
    if key in f.png_cache:
        return f.png_cache[key]
    img, rp = f.display[i], f.frames[i]
    lo, hi = f.stretch
    soft = max((hi - lo) * 0.02, 1e-6)
    x = np.arcsinh(np.clip(img - lo, 0, None) / soft) / np.arcsinh((hi - lo) / soft)
    idx = np.clip(np.nan_to_num(x) * 253, 0, 253).astype(np.uint8)
    bad = ~np.isfinite(img)
    idx[bad] = 254
    idx[bad & rp.saturated] = 255
    png = _palette_png(idx, GRAY_PALETTE)
    f.png_cache[key] = png
    return png


DIFF_PALETTE = []
for v in np.linspace(-1, 1, 255):
    a = abs(v)
    if v < 0:
        DIFF_PALETTE += [int(30 * a), int(120 * a), int(255 * a)]
    else:
        DIFF_PALETTE += [int(255 * a), int(170 * a), int(40 * a)]
DIFF_PALETTE += [28, 30, 38]  # 255 = no data


def diff_png(f: Field, a: int, b: int) -> tuple[bytes, dict]:
    key = ("diff", a, b)
    if key in f.png_cache:
        return f.png_cache[key]
    da, db = f.display[a], f.display[b]
    diff = db - da
    valid = np.isfinite(diff)
    _, sig = robust_background(diff, ~valid)
    soft = 4.0 * sig
    vmax = max(float(np.percentile(np.abs(diff[valid]), 99.9)) if valid.any() else 1.0, 3 * soft)
    x = np.clip(np.arcsinh(np.nan_to_num(diff) / soft) / np.arcsinh(vmax / soft), -1, 1)
    idx = np.round((x + 1) * 127).astype(np.uint8)
    idx[~valid] = 255
    meta = {"robust_sigma": sig, "soft": soft, "vmax": vmax, "valid_fraction": float(valid.mean())}
    out = (_palette_png(idx, DIFF_PALETTE), meta)
    f.png_cache[key] = out
    return out


# --------------------------------------------------------------------- pairs
def pair_view(f: Field, a: int, b: int) -> dict:
    """Per-source displacement between two epochs, from the registered positions."""
    if a not in f.display or b not in f.display:
        raise ValueError("Both epochs must be usable (registered) observations of this field")
    if f.observations[a].mjd > f.observations[b].mjd:
        a, b = b, a
    oa, ob = f.observations[a], f.observations[b]
    rel = abs(oa.wavelength_um - ob.wavelength_um) / (0.5 * (oa.wavelength_um + ob.wavelength_um))
    dt = ob.mjd - oa.mjd
    rows = []
    for t in f.tracks:
        pa = next((p for p in t.points if p["obs_index"] == a), None)
        pb = next((p for p in t.points if p["obs_index"] == b), None)
        if not (pa and pb):
            continue
        de, dn = pb["east_arcsec"] - pa["east_arcsec"], pb["north_arcsec"] - pa["north_arcsec"]
        se, sn = math.hypot(pa["sigma_east_arcsec"], pb["sigma_east_arcsec"]), math.hypot(pa["sigma_north_arcsec"], pb["sigma_north_arcsec"])
        rows.append({"id": t.id, "d_east_arcsec": de, "d_north_arcsec": dn, "sigma_east_arcsec": se,
                     "sigma_north_arcsec": sn, "significance": math.hypot(de / se, dn / sn),
                     "flux_ratio": pb["flux"] / pa["flux"] if pa["flux"] > 0 else None})
    _, meta = diff_png(f, a, b)
    notes = []
    if rel > 0.02:
        notes.append(f"Wavelengths differ by {100 * rel:.1f}% (more than ~one SPHEREx resolution element): "
                     "brightness differences in the difference image include real spectral differences between "
                     "sources, not only variability.")
    if dt < 30:
        notes.append("Epochs are less than 30 days apart: only very fast motion (asteroids) is detectable.")
    if abs(((oa.detector_pa_deg - ob.detector_pa_deg) + 180) % 360 - 180) > 20:
        notes.append("Detector orientation differs between epochs: PSF-wing residuals around bright stars "
                     "do not cancel in the difference image.")
    return clean({"a": a, "b": b, "dt_days": dt, "rel_dlambda": rel, "notes": notes, "difference": meta,
                  "sources": rows})


# ----------------------------------------------------------------- catalogs
def load_catalogs(job: Job, f: Field) -> dict:
    """Gaia DR3, SIMBAD, 2MASS, AllWISE in the field; cross-match to tracks.
    Each catalog is optional: failures are reported, not fatal."""
    out, errors = {}, {}
    r = f.size_deg * 0.75
    steps = [("gaia", lambda: catalogs.gaia_cone(f.ra, f.dec, r, g_limit=18.0)),
             ("simbad", lambda: catalogs.simbad_region(f.ra, f.dec, r)),
             ("twomass", lambda: catalogs.twomass_cone(f.ra, f.dec, r)),
             ("allwise", lambda: catalogs.allwise_cone(f.ra, f.dec, r))]
    job.set_stage("catalogs", "Querying Gaia DR3, SIMBAD, 2MASS and AllWISE", total=len(steps))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fn): name for name, fn in steps}
        for n, fut in enumerate(concurrent.futures.as_completed(futs)):
            name = futs[fut]
            try:
                rows, prov = fut.result()
                out[name] = {"rows": rows, "provenance": prov}
            except Exception as exc:
                errors[name] = str(exc)
            job.progress(n + 1, message=f"{name} done")
    _crossmatch(f, out)
    f.catalogs = {"data": out, "errors": errors}
    return {"fid": f.fid, "errors": errors}


def _sep_arcsec(ra1, dec1, ra2, dec2):
    return np.hypot((np.asarray(ra1) - ra2) * math.cos(math.radians(dec2)), np.asarray(dec1) - dec2) * 3600.0


def _crossmatch(f: Field, cats: dict):
    ref_mjd = f.observations[f.ref].mjd
    gaia = cats.get("gaia", {}).get("rows", [])
    simbad = cats.get("simbad", {}).get("rows", [])
    tm = cats.get("twomass", {}).get("rows", [])
    aw = cats.get("allwise", {}).get("rows", [])
    gpos = np.array([motion.gaia_position_at(g, ref_mjd) for g in gaia]) if gaia else np.empty((0, 2))
    for t in f.tracks:
        cat = {}
        if len(gpos):
            d = _sep_arcsec(gpos[:, 0], gpos[:, 1], t.ra, t.dec)
            near = np.flatnonzero(d < 6.0)
            if near.size:
                k = min(near, key=lambda q: gaia[q]["gmag"] if gaia[q]["gmag"] is not None else 99)
                g = dict(gaia[k], separation_arcsec=float(d[k]), n_within_6arcsec=int(near.size))
                cat["gaia"] = g
                if t.fit and g.get("pmra") is not None:
                    ge, gn = g["pmra"] / 1e3, g["pmdec"] / 1e3
                    ze = (t.fit["pm_east"] - ge) / t.fit["pm_east_err"]
                    zn = (t.fit["pm_north"] - gn) / t.fit["pm_north_err"]
                    cat["pm_comparison"] = {"gaia_pm_east": ge, "gaia_pm_north": gn, "z_east": ze, "z_north": zn,
                                            "z_total": math.hypot(ze, zn), "consistent": math.hypot(ze, zn) < 3.0}
        if simbad:
            # SIMBAD positions are J2000; propagate with SIMBAD proper motions when known.
            best = None
            for s in simbad:
                if s["otype"] in ("Pl", "Pl?"):  # exoplanets share their host's position
                    continue
                ra_s, dec_s = s["ra"], s["dec"]
                if s["pmra"] is not None and s["pmdec"] is not None:
                    yr = 2000.0 + (ref_mjd - 51544.5) / 365.25 - 2000.0
                    dec_s = s["dec"] + s["pmdec"] * yr / 3.6e6
                    ra_s = s["ra"] + s["pmra"] * yr / 3.6e6 / math.cos(math.radians(s["dec"]))
                d = float(_sep_arcsec(ra_s, dec_s, t.ra, t.dec))
                if d < 6.0 and (best is None or d < best[0]):
                    best = (d, s)
            if best:
                cat["simbad"] = dict(best[1], separation_arcsec=best[0], groups=catalogs.otype_groups(best[1]["otype"]))
        if tm:
            cat["twomass"] = _nearest_epoch_matched(tm, t, cat.get("gaia"), ref_mjd)
        if aw:
            cat["allwise"] = _nearest_epoch_matched(aw, t, cat.get("gaia"), ref_mjd)
        t.catalog = {k: v for k, v in cat.items() if v} or None
        if t.catalog:
            t.history = _history(t, ref_mjd)


def _nearest_epoch_matched(rows, t, gaia, ref_mjd, radius=4.0):
    """Nearest catalog source after moving the SPHEREx position back to the
    catalog's epoch with the Gaia (preferred) or measured proper motion."""
    pm = None
    if gaia and gaia.get("pmra") is not None:
        pm = (gaia["pmra"] / 1e3, gaia["pmdec"] / 1e3)
    elif t.fit and t.tier in ("significant", "systematics_limited"):
        pm = (t.fit["pm_east"], t.fit["pm_north"])
    best = None
    for r in rows:
        ra_t, dec_t = t.ra, t.dec
        if pm and r.get("mjd"):
            dt = (r["mjd"] - ref_mjd) / 365.25
            dec_t = t.dec + pm[1] * dt / 3600.0
            ra_t = t.ra + pm[0] * dt / 3600.0 / math.cos(math.radians(t.dec))
        d = float(_sep_arcsec(r["ra"], r["dec"], ra_t, dec_t))
        if d < radius and (best is None or d < best["separation_arcsec"]):
            best = dict(r, separation_arcsec=d, pm_used_for_match=pm is not None)
    return best


def _history(t, ref_mjd):
    """Catalog positions from other eras (2MASS ~1999, AllWISE ~2010, Gaia 2016)
    relative to the SPHEREx reference position, in arcsec E/N."""
    pts = []
    cos = math.cos(math.radians(t.dec))
    for key, label, mjd_of in (("twomass", "2MASS", lambda r: r.get("mjd")), ("allwise", "AllWISE", lambda r: r.get("mjd")),
                               ("gaia", "Gaia DR3", lambda r: 57388.5)):
        r = (t.catalog or {}).get(key)
        if not r or mjd_of(r) is None:
            continue
        pts.append({"catalog": label, "mjd": mjd_of(r), "year": 2000.0 + (mjd_of(r) - 51544.5) / 365.25,
                    "east_arcsec": (r["ra"] - t.ra) * cos * 3600.0, "north_arcsec": (r["dec"] - t.dec) * 3600.0})
    return sorted(pts, key=lambda p: p["mjd"]) or None


def catalog_overlay(f: Field) -> dict:
    if f.catalogs is None:
        return {"ready": False}
    g = f.grid
    out = {"ready": True, "errors": f.catalogs["errors"], "layers": {}}
    ref_mjd = f.observations[f.ref].mjd
    for name, data in f.catalogs["data"].items():
        rows = data["rows"]
        items = []
        for r in rows:
            ra, dec = (motion.gaia_position_at(r, ref_mjd) if name == "gaia" else (r["ra"], r["dec"]))
            x, y = g.world_to_pixel(ra, dec)[0]
            if not (0 <= x < g.shape[1] and 0 <= y < g.shape[0]):
                continue
            item = {"x": float(x), "y": float(y), "ra": ra, "dec": dec}
            if name == "gaia":
                item.update(id=r["source_id"], mag=r["gmag"], pmra=r["pmra"], pmdec=r["pmdec"],
                            parallax=r["parallax"], variable=r["variable"],
                            cls=_gaia_class(r))
            elif name == "simbad":
                item.update(id=r["id"], otype=r["otype"], sp_type=r["sp_type"], groups=catalogs.otype_groups(r["otype"]),
                            epoch="J2000 (not propagated)")
            else:
                item.update(id=r["id"], mags=r["mags"], epoch_mjd=r.get("mjd"))
            items.append(item)
        prov = data["provenance"]
        out["layers"][name] = {"items": items, "catalog": prov.get("catalog"), "retrieved_utc": prov.get("retrieved_utc"),
                               "cached": prov.get("cached"), "stale": prov.get("stale", False)}
    return clean(out)


def _gaia_class(r):
    ps, pg, pq = r.get("p_star"), r.get("p_galaxy"), r.get("p_quasar")
    if ps is None and pg is None and pq is None:
        return "unclassified"
    best = max((("star", ps or 0), ("galaxy", pg or 0), ("quasar", pq or 0)), key=lambda kv: kv[1])
    return best[0] if best[1] >= 0.5 else "uncertain"


# ------------------------------------------------------------------ spectra
SPECTRAL_PER_BAND = 20


def load_spectral_set(job: Job, f: Field) -> dict:
    """Cutouts of the same field in all six detectors for spectra.  For the
    field's own band all epochs are reused; other bands take an even subset
    across time (and so across wavelength)."""
    plan = []
    for b, recs in f.records_all.items():
        if b == f.band:
            continue
        pick = recs if len(recs) <= SPECTRAL_PER_BAND else [recs[i] for i in np.unique(
            np.round(np.linspace(0, len(recs) - 1, SPECTRAL_PER_BAND)).astype(int))]
        plan += [(b, r) for r in pick]
    n_cached = sum(irsa.is_cached(r, f.ra, f.dec, f.size_deg) for _, r in plan)
    job.set_stage("download", f"{len(plan)} images in the other five detectors ({n_cached} cached)", total=len(plan))
    fetched = irsa.fetch_cutouts([r for _, r in plan], f.ra, f.dec, f.size_deg,
                                 progress=lambda d, n, c: job.progress(d, n, cached=c), cancelled=lambda: job.cancelled)
    job.set_stage("read", "Reading spectral images", total=len(fetched))
    out = [(f.band, o) for o, u in zip(f.observations, f.usable) if u]
    failures = 0
    for k, ((band, _), item) in enumerate(zip(plan, fetched)):
        job.check()
        if item["error"]:
            failures += 1
            continue
        try:
            out.append((band, load_observation(item["path"], (f.ra, f.dec), item["provenance"])))
        except ObservationError:
            failures += 1
        job.progress(k + 1)
    f.spectral_obs = out
    return {"fid": f.fid, "n_images": len(out), "failures": failures}


def source_spectrum(f: Field, sid: int) -> dict:
    if f.spectral_obs is None:
        raise ValueError("Spectral images not loaded for this field")
    if sid in f.spectra:
        return f.spectra[sid]
    t = next(t for t in f.tracks if t.id == sid)
    pm_e = pm_n = 0.0
    pm_source = None
    cat = t.catalog or {}
    if cat.get("gaia") and cat["gaia"].get("pmra") is not None:
        pm_e, pm_n, pm_source = cat["gaia"]["pmra"] / 1e3, cat["gaia"]["pmdec"] / 1e3, "Gaia DR3"
    elif t.fit and t.tier in ("significant", "systematics_limited"):
        pm_e, pm_n, pm_source = t.fit["pm_east"], t.fit["pm_north"], "NADIR measured"
    sp = spectral.build_spectrum(f.spectral_obs, t.ra, t.dec, pm_e, pm_n, f.observations[f.ref].mjd)
    ref = spectral.catalog_photometry(cat.get("twomass"), cat.get("allwise"))
    sp.update(source_id=sid, pm_used=pm_source, catalog_points=ref,
              catalog_comparison=spectral.compare_to_catalog(sp["points"], ref),
              reference_features=spectral.REFERENCE_FEATURES,
              gaia_teff=(cat.get("gaia") or {}).get("teff_gspphot"),
              simbad_sp_type=(cat.get("simbad") or {}).get("sp_type"))
    for p in sp["rejected"]:
        p.pop("x", None), p.pop("y", None)
    sp = clean(sp)
    f.spectra[sid] = sp
    return sp


def clean(obj):
    """JSON-safe (NaN/inf -> None, numpy -> Python)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    return obj
