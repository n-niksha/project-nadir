"""Field-wide multi-epoch astrometry, photometric variability and evidence tiers.

Milestone 1 measured displacements between *two* epochs.  A pair cannot tell
a moving star from a blend whose centroid shifts with wavelength or pixel
phase, or from a saturated star whose wing-fit is biased by the detector
orientation.  Here every source of the reference epoch is followed through
*all* registered epochs, and the evidence is judged by whether a single
straight-line proper motion explains the positions from several independent
visits.

Inputs are plain arrays/dicts from ``motion``/``registration``; no I/O.

Evidence tiers (``tier``), in decreasing strength:

``significant``   linear motion detected at >= 5 sigma from >= 3 visits,
                   consistent with a straight line (chi2_red <= CHI2_MAX on both
                   axes after clipping), and measured on clean (unsaturated,
                   unblended) pixels in most epochs.
``systematics_limited``  as ``significant`` but measured mostly on PSF wings
                   (saturated core) -- motion is real at the quoted level, but
                   the accuracy is set by a systematic floor.
``candidate``     >= 5 sigma but fails one consistency requirement (too few
                   visits, poor straight-line fit, blending, artifacts).  Never
                   presented as a detection.
``rejected``      a 2-epoch measurement was >= 5 sigma, but the multi-epoch
                   data do not reproduce it.
``stationary``    motion below 5 sigma (the normal case).
``insufficient``  measured in fewer than 2 visits; nothing can be said.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .registration import match_sources

SIGNIF = 5.0
CHI2_MAX = 5.0
MIN_VISITS = 3
VISIT_GAP_DAYS = 30.0
CLEAN_FRACTION = 0.5
# Saturated (core-masked) sources: positions within one visit share a
# systematic offset of 1-2" (PSF-wing asymmetry, detector orientation, bloom),
# so the proper motion is fitted to visit means with this per-visit error
# added in quadrature.  Calibrated on Barnard's Star against Gaia DR3; agreement
# for Barnard is therefore not an independent check (docs/SCIENCE.md).
VISIT_SYSTEMATIC_ARCSEC = 1.2
MIN_VAR_PAIRS = 6  # independent cross-visit, matched-wavelength flux comparisons needed to flag variability


@dataclass
class EpochFrame:
    """One epoch already registered to the reference grid."""
    index: int  # index in the session's observation list
    mjd: float
    wavelength_um: float | None
    xy: np.ndarray  # (n, 2) registered grid positions of usable sources
    sigma_xy: np.ndarray  # (n, 2) per-source 1-sigma (grid px): centroid (+) floors (+) transform
    flux: np.ndarray  # (n,) fitted flux (image units * pixel)
    core_masked: np.ndarray  # (n,) bool
    blended: np.ndarray
    artifact: np.ndarray  # (n,) bool: artifact flags near the core
    is_reference: bool = False


@dataclass
class SourceTrack:
    id: int
    ref_index: int  # row in the reference epoch's source list
    ra: float
    dec: float
    x: float  # reference grid position
    y: float
    points: list = field(default_factory=list)
    fit: dict | None = None
    n_epochs: int = 0
    n_visits: int = 0
    baseline_days: float = 0.0
    clean_fraction: float = 0.0
    core_masked_fraction: float = 0.0
    blended_fraction: float = 0.0
    artifact_fraction: float = 0.0
    variability: dict | None = None
    tier: str = "insufficient"
    reasons: list = field(default_factory=list)
    priority: float = 0.0


def visits_of(mjds) -> np.ndarray:
    """Group epochs into visits: a new visit starts after a gap > VISIT_GAP_DAYS."""
    mjds = np.asarray(mjds, float)
    order = np.argsort(mjds)
    labels = np.empty(len(mjds), int)
    v, last = 0, None
    for i in order:
        if last is not None and mjds[i] - last > VISIT_GAP_DAYS:
            v += 1
        labels[i] = v
        last = mjds[i]
    return labels


def associate(frames: list[EpochFrame], ref: EpochFrame, radius_px: float, max_pass: int = 2) -> dict:
    """For every reference source, the matching row in each epoch.

    Pass 1 matches each epoch to the reference positions.  Pass 2 matches to
    positions *predicted* from a provisional straight-line fit, so a source that
    moved further than ``radius_px`` by the last visit is still followed.
    Returns {frame_list_index: array(len(ref.xy)) of row or -1}."""
    n = len(ref.xy)
    links = {}
    for k, fr in enumerate(frames):
        rows = np.full(n, -1, int)
        if fr.is_reference:
            rows[:] = np.arange(n)
        elif len(fr.xy):
            pairs = match_sources(ref.xy, fr.xy, radius_px)
            rows[pairs[:, 0]] = pairs[:, 1]
        links[k] = rows
    for _ in range(max_pass - 1):
        pred_rate = _provisional_rates(frames, ref, links)
        for k, fr in enumerate(frames):
            if fr.is_reference or not len(fr.xy):
                continue
            dt = (fr.mjd - ref.mjd) / 365.25
            predicted = ref.xy + pred_rate * dt
            pairs = match_sources(predicted, fr.xy, radius_px)
            rows = np.full(n, -1, int)
            rows[pairs[:, 0]] = pairs[:, 1]
            links[k] = rows
    return links


def _provisional_rates(frames, ref, links) -> np.ndarray:
    """Unweighted straight-line rate (grid px / yr) per reference source; 0 if undetermined."""
    n = len(ref.xy)
    rates = np.zeros((n, 2))
    t = np.array([(fr.mjd - ref.mjd) / 365.25 for fr in frames])
    for i in range(n):
        ks = [k for k in links if links[k][i] >= 0]
        if len(ks) < 3 or np.ptp(t[ks]) < VISIT_GAP_DAYS / 365.25:
            continue
        pos = np.array([frames[k].xy[links[k][i]] for k in ks])
        tt = t[ks]
        A = np.c_[np.ones(len(tt)), tt]
        for ax in (0, 1):
            coef, *_ = np.linalg.lstsq(A, pos[:, ax], rcond=None)
            rates[i, ax] = coef[1]
    return rates


def linear_fit(t_yr, v, s, clip=4.0):
    """Weighted straight line with iterative MAD clipping; errors scaled by sqrt(chi2_red) if > 1."""
    t_yr, v, s = map(lambda a: np.asarray(a, float), (t_yr, v, s))
    s = np.maximum(s, 1e-6)
    keep = np.ones(len(v), bool)
    for _ in range(5):
        if keep.sum() < 3:
            break
        A = np.c_[np.ones(keep.sum()), t_yr[keep]] / s[keep, None]
        coef, *_ = np.linalg.lstsq(A, v[keep] / s[keep], rcond=None)
        r = (v - coef[0] - coef[1] * t_yr) / s
        mad = 1.4826 * np.median(np.abs(r[keep] - np.median(r[keep])))
        new = np.abs(r) < clip * max(mad, 1.0)
        if new.sum() < 3 or np.array_equal(new, keep):
            break
        keep = new
    A = np.c_[np.ones(keep.sum()), t_yr[keep]] / s[keep, None]
    coef, *_ = np.linalg.lstsq(A, v[keep] / s[keep], rcond=None)
    resid = (v - coef[0] - coef[1] * t_yr) / s
    dof = max(keep.sum() - 2, 1)
    chi2 = float(np.sum(resid[keep] ** 2) / dof)
    cov = np.linalg.inv(A.T @ A) * max(chi2, 1.0)
    return {"rate": float(coef[1]), "rate_err": float(math.sqrt(cov[1, 1])), "offset": float(coef[0]),
            "chi2_red": chi2, "n_used": int(keep.sum()), "used": keep.tolist(),
            "residuals": (v - coef[0] - coef[1] * t_yr).tolist()}


def build_tracks(frames: list[EpochFrame], ref_pos: int, ref_world: np.ndarray, pixel_scale_arcsec: float,
                 radius_px: float) -> list[SourceTrack]:
    """Associate, fit and summarise every reference source.

    ``ref_world`` is (n, 2) RA/Dec of the reference sources.  Positions are
    reported as East/North offsets (arcsec) from the reference-epoch position;
    East = -x on the north-up/east-left grid."""
    ref = frames[ref_pos]
    links = associate(frames, ref, radius_px)
    mjds = np.array([fr.mjd for fr in frames])
    visit = visits_of(mjds)
    tracks = []
    for i in range(len(ref.xy)):
        tr = SourceTrack(id=i, ref_index=i, ra=float(ref_world[i, 0]), dec=float(ref_world[i, 1]),
                         x=float(ref.xy[i, 0]), y=float(ref.xy[i, 1]))
        ks = [k for k in range(len(frames)) if links[k][i] >= 0]
        for k in ks:
            fr, j = frames[k], links[k][i]
            dx, dy = fr.xy[j] - ref.xy[i]
            tr.points.append({
                "frame": k, "obs_index": fr.index, "mjd": fr.mjd, "visit": int(visit[k]),
                "wavelength_um": fr.wavelength_um,
                "east_arcsec": float(-dx * pixel_scale_arcsec), "north_arcsec": float(dy * pixel_scale_arcsec),
                "sigma_east_arcsec": float(fr.sigma_xy[j, 0] * pixel_scale_arcsec),
                "sigma_north_arcsec": float(fr.sigma_xy[j, 1] * pixel_scale_arcsec),
                "flux": float(fr.flux[j]), "core_masked": bool(fr.core_masked[j]), "blended": bool(fr.blended[j]),
                "artifact": bool(fr.artifact[j]), "is_reference_epoch": fr.is_reference,
            })
        _summarise(tr, ref.mjd)
        tracks.append(tr)
    return tracks


def _summarise(tr: SourceTrack, ref_mjd: float) -> None:
    P = tr.points
    tr.n_epochs = len(P)
    if not P:
        tr.tier, tr.reasons = "insufficient", ["not measured in any epoch"]
        return
    t = np.array([p["mjd"] for p in P])
    tr.n_visits = len(set(p["visit"] for p in P))
    tr.baseline_days = float(np.ptp(t))
    cm = np.array([p["core_masked"] for p in P])
    bl = np.array([p["blended"] for p in P])
    af = np.array([p["artifact"] for p in P])
    tr.core_masked_fraction = float(cm.mean())
    tr.blended_fraction = float(bl.mean())
    tr.artifact_fraction = float(af.mean())
    tr.clean_fraction = float((~cm & ~bl & ~af).mean())
    if tr.n_visits < 2 or len(P) < 3:
        tr.tier, tr.reasons = "insufficient", [f"measured in {tr.n_visits} visit(s), {len(P)} epoch(s)"]
        return
    ty = (t - ref_mjd) / 365.25
    visit_mode = tr.core_masked_fraction > 1 - CLEAN_FRACTION
    if visit_mode:
        fe, fn, vpts = _visit_fit(P, ref_mjd)
        model = (f"visit-mean fit, {VISIT_SYSTEMATIC_ARCSEC}\" per-visit systematic (saturated source); "
                 "linear proper motion relative to the field, parallax not fitted")
    else:
        fe = linear_fit(ty, [p["east_arcsec"] for p in P], [p["sigma_east_arcsec"] for p in P])
        fn = linear_fit(ty, [p["north_arcsec"] for p in P], [p["sigma_north_arcsec"] for p in P])
        vpts = None
        model = "linear proper motion relative to the field (parallax not fitted)"
        for p, ue, un in zip(P, fe["used"], fn["used"]):
            p["used_in_fit"] = bool(ue and un)
    for p in P:
        p["resid_east_arcsec"] = p["east_arcsec"] - fe["offset"] - fe["rate"] * (p["mjd"] - ref_mjd) / 365.25
        p["resid_north_arcsec"] = p["north_arcsec"] - fn["offset"] - fn["rate"] * (p["mjd"] - ref_mjd) / 365.25
        p.setdefault("used_in_fit", True)
    mu = math.hypot(fe["rate"], fn["rate"])
    snr = math.hypot(fe["rate"] / fe["rate_err"], fn["rate"] / fn["rate_err"])
    pa = math.degrees(math.atan2(fe["rate"], fn["rate"])) % 360.0
    tr.fit = {"pm_east": fe["rate"], "pm_east_err": fe["rate_err"], "pm_north": fn["rate"],
              "pm_north_err": fn["rate_err"], "pm_total": mu, "pm_pa_deg": pa, "snr": snr,
              "chi2_red_east": fe["chi2_red"], "chi2_red_north": fn["chi2_red"],
              "n_used": min(fe["n_used"], fn["n_used"]), "t0_mjd": ref_mjd,
              "offset_east": fe["offset"], "offset_north": fn["offset"], "model": model,
              "visit_points": vpts}
    _classify(tr)


def _visit_fit(P, ref_mjd):
    """Straight-line fit to per-visit weighted mean positions, each with
    VISIT_SYSTEMATIC_ARCSEC added in quadrature to its statistical error."""
    by_visit = {}
    for p in P:
        by_visit.setdefault(p["visit"], []).append(p)
    vpts = []
    for v, pts in sorted(by_visit.items()):
        row = {"visit": v, "n": len(pts), "mjd": float(np.mean([p["mjd"] for p in pts]))}
        for ax in ("east", "north"):
            x = np.array([p[f"{ax}_arcsec"] for p in pts])
            s = np.array([p[f"sigma_{ax}_arcsec"] for p in pts])
            med = float(np.median(x))
            stat = max(1.2533 * float(np.std(x)) / math.sqrt(len(x)) if len(x) > 1 else float(s[0]), 1e-3)
            row[f"{ax}_arcsec"] = med
            row[f"sigma_{ax}_arcsec"] = math.hypot(stat, VISIT_SYSTEMATIC_ARCSEC)
        vpts.append(row)
    ty = [(r["mjd"] - ref_mjd) / 365.25 for r in vpts]
    if len(vpts) < 2:
        raise ValueError("visit fit needs two visits")
    fits = []
    for ax in ("east", "north"):
        v = np.array([r[f"{ax}_arcsec"] for r in vpts])
        s = np.array([r[f"sigma_{ax}_arcsec"] for r in vpts])
        A = np.c_[np.ones(len(v)), ty] / s[:, None]
        coef, *_ = np.linalg.lstsq(A, v / s, rcond=None)
        dof = len(v) - 2
        chi2 = float(np.sum(((v - coef[0] - coef[1] * np.array(ty)) / s) ** 2) / dof) if dof > 0 else 0.0
        cov = np.linalg.inv(A.T @ A) * max(chi2, 1.0)
        fits.append({"rate": float(coef[1]), "rate_err": float(math.sqrt(cov[1, 1])), "offset": float(coef[0]),
                     "chi2_red": chi2, "n_used": len(v)})
    return fits[0], fits[1], vpts


def _classify(tr: SourceTrack, pair_significance: float | None = None) -> None:
    f = tr.fit
    reasons = []
    if f is None:
        return
    consistent = max(f["chi2_red_east"], f["chi2_red_north"]) <= CHI2_MAX
    if f["snr"] < SIGNIF:
        tr.tier = "stationary"
        reasons.append(f"proper motion {f['snr']:.1f} sigma (< {SIGNIF:.0f})")
    else:
        if tr.n_visits < MIN_VISITS:
            reasons.append(f"only {tr.n_visits} visits (need {MIN_VISITS} to test consistency)")
        if not consistent:
            reasons.append(f"positions not consistent with a straight line (chi2_red "
                           f"{f['chi2_red_east']:.1f}/{f['chi2_red_north']:.1f} > {CHI2_MAX:.0f})")
        if tr.blended_fraction > 0.5:
            reasons.append("blended in most epochs")
        if tr.artifact_fraction > 0.5:
            reasons.append("artifact flags near the core in most epochs")
        if reasons:
            tr.tier = "candidate"
        elif tr.core_masked_fraction > 1 - CLEAN_FRACTION:
            tr.tier = "systematics_limited"
            reasons.append("core saturated/masked in most epochs: centroids from PSF wings; "
                           "accuracy limited by a systematic floor")
        else:
            tr.tier = "significant"
    tr.reasons = reasons


def apply_pair_check(tracks: list[SourceTrack], pair_sig: dict[int, float]) -> None:
    """Downgrade sources whose 2-epoch significance is not reproduced by the
    multi-epoch fit.  ``pair_sig`` maps reference-source row -> pair significance."""
    for tr in tracks:
        s = pair_sig.get(tr.ref_index)
        if s is None or s < SIGNIF:
            continue
        tr.reasons = list(tr.reasons)
        if tr.tier == "stationary":
            tr.tier = "rejected"
            tr.reasons.insert(0, f"2-epoch displacement was {s:.1f} sigma, but the multi-epoch fit "
                                 f"finds {tr.fit['snr']:.1f} sigma: not reproduced")
        elif tr.tier == "insufficient":
            tr.reasons.insert(0, f"2-epoch displacement {s:.1f} sigma cannot be checked against other visits")


def variability(tracks: list[SourceTrack], max_rel_dlambda: float = 0.02, min_dt_days: float = 30.0) -> None:
    """Brightness change relative to the field, from cross-visit epoch pairs at
    matching wavelength.

    For each such epoch pair the ensemble median log flux ratio of clean
    stationary sources removes calibration/zodi/airglow differences; the robust
    scatter of that ensemble sets the noise.  A source's statistic is the RMS of
    its normalised deviations ("var_z", ~1 for constant sources).  It is a
    heuristic test statistic, not a probability."""
    by_frame = {}
    for tr in tracks:
        for p in tr.points:
            by_frame.setdefault(p["frame"], (p["mjd"], p["wavelength_um"]))
    frames = sorted(by_frame)
    pairs = []
    for a_i, a in enumerate(frames):
        for b in frames[a_i + 1:]:
            (ma, la), (mb, lb) = by_frame[a], by_frame[b]
            if la is None or lb is None or abs(mb - ma) < min_dt_days:
                continue
            if abs(la - lb) / (0.5 * (la + lb)) <= max_rel_dlambda:
                pairs.append((a, b))
    if not pairs:
        return
    ensemble = {}
    lookup = [{p["frame"]: p for p in tr.points} for tr in tracks]
    for a, b in pairs:
        vals = []
        for tr, lk in zip(tracks, lookup):
            pa, pb = lk.get(a), lk.get(b)
            if pa and pb and tr.tier in ("stationary",) and not (pa["core_masked"] or pb["core_masked"] or
                                                                  pa["blended"] or pb["blended"]) \
                    and pa["flux"] > 0 and pb["flux"] > 0:
                vals.append(math.log(pb["flux"] / pa["flux"]))
        if len(vals) >= 8:
            v = np.array(vals)
            med = float(np.median(v))
            ensemble[(a, b)] = (med, max(1.4826 * float(np.median(np.abs(v - med))), 0.01))
    for tr, lk in zip(tracks, lookup):
        devs, raw = [], []
        for (a, b), (med, sig) in ensemble.items():
            pa, pb = lk.get(a), lk.get(b)
            if not (pa and pb) or pa["flux"] <= 0 or pb["flux"] <= 0:
                continue
            if pa["core_masked"] or pb["core_masked"]:
                continue
            d = math.log(pb["flux"] / pa["flux"]) - med
            devs.append(d / sig)
            raw.append({"mjd_a": pa["mjd"], "mjd_b": pb["mjd"], "wavelength_um": pa["wavelength_um"],
                        "log_ratio": d, "z": d / sig})
        if len(devs) >= 2:
            # median-based: a single-epoch artefact or mis-association cannot
            # make a constant source look variable (it would with an RMS).
            z = float(np.median(np.abs(devs)) / 0.6745)
            tr.variability = {"var_z": z, "n_pairs": len(devs), "pairs": raw[:40],
                              "max_abs_mag_change": float(np.max(np.abs([r["log_ratio"] for r in raw])) * 1.0857),
                              "flag": z >= 5.0 and len(devs) >= MIN_VAR_PAIRS}
        elif tr.core_masked_fraction > 0.5:
            tr.variability = {"var_z": None, "n_pairs": 0, "note": "saturated: no reliable relative photometry"}


def priority_score(tr: SourceTrack) -> float:
    """Transparent ranking heuristic for the Discover list (NOT a probability).

    score = min(motion S/N, 50) x consistency x quality + min(var_z, 30) x clean fraction
    consistency = 1 if chi2_red <= CHI2_MAX else CHI2_MAX / chi2_red
    quality = 1 (significant), 0.6 (systematics-limited), 0.3 x clean fraction (candidate)
    Variability counts only when flagged (var_z >= 5 from >= MIN_VAR_PAIRS pairs)."""
    if tr.fit is None:
        return 0.0
    chi = max(tr.fit["chi2_red_east"], tr.fit["chi2_red_north"])
    consistency = 1.0 if chi <= CHI2_MAX else CHI2_MAX / chi
    quality = {"significant": 1.0, "systematics_limited": 0.6}.get(tr.tier, 0.3 * tr.clean_fraction)
    motion = min(tr.fit["snr"], 50.0) if tr.fit["snr"] >= SIGNIF else 0.0
    var = 0.0
    if tr.variability and tr.variability.get("flag"):
        var = min(tr.variability["var_z"], 30.0)
    if tr.tier in ("rejected", "insufficient"):
        return 0.0
    return float(motion * consistency * quality + var * tr.clean_fraction)
