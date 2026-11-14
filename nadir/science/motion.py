"""Baseline relative-motion analysis between epochs.

For a pair (A earlier, B later):

1. detect + centroid sources on each native image, map to a common pixel grid;
2. cross-match and robustly fit the B->A registration from stationary sources;
3. for every matched source, displacement = registered B position - A position;
4. uncertainty = max(formal centroid error, empirical per-source scatter of the
   reference stars) combined with the registration-fit uncertainty;
5. classify, taking flags / saturation / blending into account.

Nothing here invents a measurement: a source that cannot be measured reliably
is labelled so, with reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .observation import Observation
from .registration import Transform, fit_transform, match_sources
from .reprojection import Grid
from .sources import Source, detect_sources

DETECTION_SIGMA = 5.0  # combined 2-D significance required for a motion candidate
MATCH_RADIUS_NATIVE_PX = 3.0
# Flags that suggest the *detection itself* may be an artifact rather than a star.
ARTIFACT_FLAGS = ("SNOWBALL", "STREAK", "GHOST_FPA", "GHOST_EXT", "PERSIST", "CROSSTALK")
# Systematic floor (native pixels, per axis, per pair) for sources centroided on
# PSF wings because their core is saturated/masked.  Empirical: Barnard's Star in
# band D2 shows a ~1.2" detector-frame bias that flips with spacecraft orientation
# (docs/SCIENCE.md).  It is a floor, not a correction.
CORE_MASKED_FLOOR_PX = 0.2


@dataclass
class SourceMotion:
    id: int
    ra: float
    dec: float
    x: float  # common-grid pixel at epoch A
    y: float
    d_east_arcsec: float
    d_north_arcsec: float
    sigma_east_arcsec: float
    sigma_north_arcsec: float
    total_arcsec: float
    significance: float
    status: str
    reasons: list
    is_reference: bool
    a: dict
    b: dict
    catalog: dict | None = None


@dataclass
class PairResult:
    ok: bool
    message: str
    dt_days: float | None = None
    transform: Transform | None = None
    registration: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)  # SourceMotion
    unmatched_a: list = field(default_factory=list)
    unmatched_b: list = field(default_factory=list)
    n_detected: tuple = (0, 0)


def grid_positions(sources: list[Source], grid: Grid) -> np.ndarray:
    if not sources:
        return np.empty((0, 2))
    return grid.world_to_pixel([s.ra for s in sources], [s.dec for s in sources])


def _native_to_grid_scale(obs: Observation, grid: Grid) -> float:
    from astropy.wcs.utils import proj_plane_pixel_scales
    return float(np.mean(proj_plane_pixel_scales(obs.wcs.celestial)) * 3600.0) / grid.pixel_scale_arcsec


def _usable(s: Source) -> bool:
    return s.fit_ok and np.isfinite([s.x_err, s.y_err]).all()


def analyze_pair(obs_a: Observation, obs_b: Observation, grid: Grid,
                 sources_a: list[Source] | None = None, sources_b: list[Source] | None = None,
                 model: str = "auto") -> PairResult:
    if obs_a.mjd is None or obs_b.mjd is None:
        return PairResult(False, "An observation lacks a timestamp; a time baseline cannot be defined.")
    sa = [s for s in (sources_a if sources_a is not None else detect_sources(obs_a)) if _usable(s)]
    sb = [s for s in (sources_b if sources_b is not None else detect_sources(obs_b)) if _usable(s)]
    dt = obs_b.mjd - obs_a.mjd
    base = dict(dt_days=dt, n_detected=(len(sa), len(sb)))
    if len(sa) < 3 or len(sb) < 3:
        return PairResult(False, f"Too few measurable sources (A: {len(sa)}, B: {len(sb)}) to register the "
                                 "images; no displacement can be measured.", **base)
    ga, gb = grid_positions(sa, grid), grid_positions(sb, grid)
    ka, kb = _native_to_grid_scale(obs_a, grid), _native_to_grid_scale(obs_b, grid)
    pairs = match_sources(ga, gb, MATCH_RADIUS_NATIVE_PX * ka)
    # Registration uses only clean stars: unsaturated, unblended, whole fit window usable.
    clean = np.array([not (sa[i].core_masked or sa[i].blended or sb[j].core_masked or sb[j].blended)
                      for i, j in pairs], bool)
    if clean.sum() < 3:
        return PairResult(False, f"Only {int(clean.sum())} clean reference stars matched between epochs; "
                                 "registration is not possible.", **base)
    ref_pairs = pairs[clean]
    try:
        tf = fit_transform(gb[ref_pairs[:, 1]], ga[ref_pairs[:, 0]], model=model)
    except ValueError as exc:
        return PairResult(False, str(exc), **base)

    scale = grid.pixel_scale_arcsec
    # Empirical single-source scatter (includes both epochs' centroid noise and
    # pixel-phase systematics for typical reference stars).
    floor_x, floor_y = tf.rms_x, tf.rms_y
    ref_set = {(int(i), int(j)) for (i, j), keep in zip(ref_pairs, tf.inliers) if keep}
    moved = tf.apply(gb)
    reg_sig = tf.position_sigma(gb)

    # Data-driven quality gates calibrated on the reference stars of this pair.
    def robust(v):
        v = np.asarray(v, float)
        med = np.median(v)
        return med, 1.4826 * np.median(np.abs(v - med))

    ref_list = sorted(ref_set)
    rf_med, rf_mad = robust([max(sa[i].resid_frac, sb[j].resid_frac) for i, j in ref_list])
    resid_limit = max(rf_med + 5 * rf_mad, 1.5 * rf_med)
    lr_med, lr_mad = robust([np.log(sa[i].flux / sb[j].flux) for i, j in ref_list])
    flux_limit = max(5 * lr_mad, 0.25)
    dpa = abs((obs_a.detector_pa_deg - obs_b.detector_pa_deg + 180) % 360 - 180)

    out, used_a, used_b = [], set(), set()
    for n, (i, j) in enumerate(pairs):
        i, j = int(i), int(j)
        used_a.add(i), used_b.add(j)
        a, b = sa[i], sb[j]
        dx, dy = moved[j] - ga[i]
        meas_x = np.hypot(a.x_err * ka, b.x_err * kb)
        meas_y = np.hypot(a.y_err * ka, b.y_err * kb)
        fx, fy = floor_x, floor_y
        if a.core_masked or b.core_masked:
            fx, fy = max(fx, CORE_MASKED_FLOOR_PX * ka), max(fy, CORE_MASKED_FLOOR_PX * ka)
        sx = float(np.hypot(max(meas_x, fx), reg_sig[j, 0]))
        sy = float(np.hypot(max(meas_y, fy), reg_sig[j, 1]))
        signif = float(np.hypot(dx / sx, dy / sy))
        reasons = []
        if a.core_masked or b.core_masked:
            why = "saturated core (OVERFLOW flag)" if (a.saturated or b.saturated) else "unusable pixels in the core"
            reasons.append(f"{why}: centroid rests on PSF wings; a {CORE_MASKED_FLOOR_PX} px systematic floor is applied")
            if dpa > 20:
                reasons.append(f"detector orientation differs by {dpa:.0f} deg between epochs, so PSF-wing "
                               "asymmetry bias does not cancel")
        elif max(a.resid_frac, b.resid_frac) > resid_limit:
            reasons.append("poor PSF fit compared with the reference stars (likely blend or artifact)")
        if a.flux > 0 and b.flux > 0 and not (a.core_masked or b.core_masked) \
                and abs(np.log(a.flux / b.flux) - lr_med) > flux_limit:
            reasons.append("flux differs between epochs beyond the reference-star scatter "
                           "(variable source, blend, or artifact)")
        if a.blended or b.blended:
            reasons.append("blended with a neighbour within 2.5 px")
        art = sorted(set(a.warn_flags + b.warn_flags) & set(ARTIFACT_FLAGS))
        if art:
            reasons.append("artifact flags near core: " + ", ".join(art))
        is_ref = (i, j) in ref_set
        if is_ref:
            status = "reference"
        elif signif >= DETECTION_SIGMA:
            status = "candidate_motion" if not reasons else "candidate_motion_low_confidence"
        else:
            status = "no_significant_motion"
        out.append(SourceMotion(
            id=n, ra=a.ra, dec=a.dec, x=float(ga[i, 0]), y=float(ga[i, 1]),
            d_east_arcsec=float(-dx * scale), d_north_arcsec=float(dy * scale),
            sigma_east_arcsec=sx * scale, sigma_north_arcsec=sy * scale,
            total_arcsec=float(np.hypot(dx, dy) * scale), significance=signif,
            status=status, reasons=reasons, is_reference=is_ref,
            a=_src_dict(a, ga[i]), b=_src_dict(b, gb[j]),
        ))

    def lone(srcs, pos, used):
        return [dict(_src_dict(s, pos[k]), ra=s.ra, dec=s.dec) for k, s in enumerate(srcs) if k not in used]

    reg = {
        "model": tf.model, "n_matched_clean": int(len(ref_pairs)), "n_used": tf.n_used,
        "converged": tf.converged,
        "shift_east_arcsec": float(-tf.offset[0] * scale), "shift_north_arcsec": float(tf.offset[1] * scale),
        "rotation_deg": tf.rotation_deg, "scale": tf.scale,
        "rms_east_arcsec": tf.rms_x * scale, "rms_north_arcsec": tf.rms_y * scale,
        "rms_native_px": float(np.hypot(tf.rms_x, tf.rms_y) / np.sqrt(2) / ka),
    }
    msg = (f"Registered with {tf.n_used} reference stars ({tf.model}); per-source scatter "
           f"{reg['rms_east_arcsec']:.2f}\" E, {reg['rms_north_arcsec']:.2f}\" N.")
    return PairResult(True, msg, dt_days=dt, transform=tf, registration=reg, sources=out,
                      unmatched_a=lone(sa, ga, used_a), unmatched_b=lone(sb, gb, used_b),
                      n_detected=(len(sa), len(sb)))


def _src_dict(s: Source, gpos) -> dict:
    return {"grid_x": float(gpos[0]), "grid_y": float(gpos[1]), "native_x": s.x, "native_y": s.y,
            "x_err_px": s.x_err, "y_err_px": s.y_err, "flux": s.flux, "snr": s.snr, "sigma_px": s.sigma,
            "chi2_red": s.chi2_red, "resid_frac": s.resid_frac, "method": s.method, "saturated": s.saturated, "core_masked": s.core_masked,
            "blended": s.blended, "n_bad": s.n_bad, "warn_flags": list(s.warn_flags), "ra": s.ra, "dec": s.dec}


# ---------------------------------------------------------------------------
# Catalog-predicted displacement (for cross-referencing, never used in the fit)
# ---------------------------------------------------------------------------

def parallax_factors(ra_deg: float, dec_deg: float, mjd: float) -> tuple[float, float]:
    """Parallax factors (east, north): offset in units of the parallax for an
    observer at the geocentre (SPHEREx is in low Earth orbit)."""
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    pos = get_body_barycentric("earth", Time(mjd, format="mjd", scale="utc")).xyz.to_value("au")
    a, d = np.radians(ra_deg), np.radians(dec_deg)
    pe = pos[0] * np.sin(a) - pos[1] * np.cos(a)
    pn = pos[0] * np.cos(a) * np.sin(d) + pos[1] * np.sin(a) * np.sin(d) - pos[2] * np.cos(d)
    return float(pe), float(pn)


def predicted_displacement(star: dict, mjd_a: float, mjd_b: float) -> dict | None:
    """Expected A->B displacement (arcsec) of a Gaia star from proper motion and parallax."""
    if star.get("pmra") is None or star.get("pmdec") is None:
        return None
    dt_yr = (mjd_b - mjd_a) / 365.25
    e, n = star["pmra"] * dt_yr / 1e3, star["pmdec"] * dt_yr / 1e3
    plx = star.get("parallax") or 0.0
    pe = pn = 0.0
    if plx > 0:
        ea, na = parallax_factors(star["ra"], star["dec"], mjd_a)
        eb, nb = parallax_factors(star["ra"], star["dec"], mjd_b)
        pe, pn = plx * (eb - ea) / 1e3, plx * (nb - na) / 1e3
    return {"east_arcsec": e + pe, "north_arcsec": n + pn, "pm_east_arcsec": e, "pm_north_arcsec": n,
            "parallax_east_arcsec": pe, "parallax_north_arcsec": pn,
            "total_arcsec": float(np.hypot(e + pe, n + pn))}


def gaia_position_at(star: dict, mjd: float, ref_epoch: float = 2016.0) -> tuple[float, float]:
    """Linear proper-motion propagation of a Gaia position to ``mjd`` (no parallax)."""
    yr = 2000.0 + (mjd - 51544.5) / 365.25
    dt = yr - ref_epoch
    pmra, pmdec = star.get("pmra") or 0.0, star.get("pmdec") or 0.0
    dec = star["dec"] + pmdec * dt / 3.6e6
    ra = star["ra"] + pmra * dt / 3.6e6 / np.cos(np.radians(star["dec"]))
    return float(ra), float(dec)


def cross_reference(result: PairResult, gaia: list[dict], grid: Grid, mjd_a: float, mjd_b: float,
                    radius_arcsec: float = 6.0) -> int:
    """Attach the brightest Gaia star within ``radius_arcsec`` (at epoch A) to each source."""
    if not gaia or not result.sources:
        return 0
    pos = np.array([gaia_position_at(g, mjd_a) for g in gaia])
    gxy = grid.world_to_pixel(pos[:, 0], pos[:, 1])
    r_px = radius_arcsec / grid.pixel_scale_arcsec
    n = 0
    for s in result.sources:
        d = np.hypot(gxy[:, 0] - s.x, gxy[:, 1] - s.y)
        near = np.flatnonzero(d < r_px)
        if near.size == 0:
            continue
        k = min(near, key=lambda q: gaia[q]["gmag"] if gaia[q]["gmag"] is not None else 99)
        g = gaia[k]
        s.catalog = {"catalog": "Gaia DR3", "source_id": g["source_id"], "gmag": g["gmag"],
                     "pmra_masyr": g["pmra"], "pmdec_masyr": g["pmdec"], "parallax_mas": g["parallax"],
                     "separation_arcsec": float(d[k] * grid.pixel_scale_arcsec),
                     "n_gaia_within_radius": int(near.size),
                     "predicted": predicted_displacement(g, mjd_a, mjd_b)}
        n += 1
    return n


# ---------------------------------------------------------------------------
# Multi-epoch track of one source
# ---------------------------------------------------------------------------

def track_source(observations: list[Observation], ref_index: int, grid: Grid, ra: float, dec: float,
                 detections: list[list[Source]], pick_radius_px: float = 3.0) -> dict:
    """Position of the source nearest (ra, dec) in every epoch, relative to the
    reference epoch, each epoch registered to the reference with field stars.

    Returns per-epoch offsets and, when at least 3 epochs spanning > 30 days are
    measurable, a weighted linear proper-motion fit whose uncertainties are
    scaled by the fit's reduced chi^2 (i.e. derived from the observed scatter).
    """
    ref = observations[ref_index]
    scale = grid.pixel_scale_arcsec
    target = grid.world_to_pixel(ra, dec)[0]
    points, skipped = [], []
    for k, obs in enumerate(observations):
        srcs = [s for s in detections[k] if _usable(s)]
        if not srcs or obs.mjd is None:
            skipped.append({"obs_id": obs.obs_id, "reason": "no measurable sources or no timestamp"})
            continue
        g = grid_positions(srcs, grid)
        if k == ref_index:
            pos, sig_reg, floor = g, np.zeros_like(g), (0.0, 0.0)
        else:
            res = analyze_pair(ref, obs, grid, detections[ref_index], detections[k])
            if not res.ok:
                skipped.append({"obs_id": obs.obs_id, "reason": res.message})
                continue
            pos, sig_reg = res.transform.apply(g), res.transform.position_sigma(g)
            floor = (res.transform.rms_x, res.transform.rms_y)
        d = np.hypot(pos[:, 0] - target[0], pos[:, 1] - target[1])
        j = int(np.argmin(d))
        kn = _native_to_grid_scale(obs, grid)
        if d[j] > pick_radius_px * kn:
            skipped.append({"obs_id": obs.obs_id, "reason": "source not detected near the selected position"})
            continue
        s = srcs[j]
        f0, f1 = floor[0] / np.sqrt(2), floor[1] / np.sqrt(2)
        if s.core_masked:
            f0 = f1 = max(f0, f1, CORE_MASKED_FLOOR_PX * kn / np.sqrt(2))
        sx = float(np.hypot(max(s.x_err * kn, f0), sig_reg[j, 0]))
        sy = float(np.hypot(max(s.y_err * kn, f1), sig_reg[j, 1]))
        points.append({"obs_id": obs.obs_id, "mjd": obs.mjd, "date": obs.date_iso,
                       "wavelength_um": obs.wavelength_um,
                       "east_arcsec": float(-(pos[j, 0] - target[0]) * scale),
                       "north_arcsec": float((pos[j, 1] - target[1]) * scale),
                       "sigma_east_arcsec": sx * scale, "sigma_north_arcsec": sy * scale,
                       "saturated": s.saturated, "core_masked": s.core_masked, "blended": s.blended,
                       "detector_pa_deg": obs.detector_pa_deg, "is_reference_epoch": k == ref_index})
    out = {"points": points, "skipped": skipped, "fit": None,
           "reference_obs": ref.obs_id, "selected": {"ra": ra, "dec": dec}}
    if len(points) >= 3:
        t = np.array([p["mjd"] for p in points])
        if t.max() - t.min() > 30:
            out["fit"] = _linear_pm_fit(points)
        else:
            out["fit_note"] = "Time baseline under 30 days; no proper-motion fit attempted."
    else:
        out["fit_note"] = f"Only {len(points)} usable epochs; no proper-motion fit attempted."
    return out


def _linear_pm_fit(points: list[dict]) -> dict:
    t = np.array([p["mjd"] for p in points])
    t0 = float(t.mean())
    ty = (t - t0) / 365.25
    fit = {"t0_mjd": t0, "n_epochs": len(points), "baseline_days": float(t.max() - t.min())}
    for axis in ("east", "north"):
        v = np.array([p[f"{axis}_arcsec"] for p in points])
        s = np.array([max(p[f"sigma_{axis}_arcsec"], 1e-3) for p in points])
        keep = np.ones(len(v), bool)
        for _ in range(5):  # sigma-clip gross outliers (cosmic rays, mis-identifications)
            A = np.c_[np.ones(keep.sum()), ty[keep]] / s[keep, None]
            coef, *_ = np.linalg.lstsq(A, v[keep] / s[keep], rcond=None)
            resid = (v - coef[0] - coef[1] * ty) / s
            sd = 1.4826 * np.median(np.abs(resid[keep] - np.median(resid[keep])))
            new = np.abs(resid) < 4 * max(sd, 1e-6)
            if new.sum() < 3 or np.array_equal(new, keep):
                break
            keep = new
        A = np.c_[np.ones(keep.sum()), ty[keep]] / s[keep, None]
        coef, *_ = np.linalg.lstsq(A, v[keep] / s[keep], rcond=None)
        chi2 = float(np.sum(((v[keep] - coef[0] - coef[1] * ty[keep]) / s[keep]) ** 2) / max(keep.sum() - 2, 1))
        cov = np.linalg.inv(A.T @ A) * max(chi2, 1.0)
        fit[axis] = {"pm_arcsec_per_yr": float(coef[1]), "pm_err_arcsec_per_yr": float(np.sqrt(cov[1, 1])),
                     "offset_arcsec": float(coef[0]), "chi2_red": chi2, "n_used": int(keep.sum())}
    return fit
