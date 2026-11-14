"""Source detection and centroiding on native (un-resampled) SPHEREx pixels.

SPHEREx is strongly undersampled (PSF FWHM ~7.5" on 6.15" pixels), so a
point-sampled Gaussian is a poor model.  We fit a *pixel-integrated* circular
Gaussian plus constant background, weighted by the pipeline variance and
ignoring unusable pixels.  Formal errors come from the fit covariance (scaled
by reduced chi^2 when > 1); they do not capture pixel-phase systematics, which
is why the motion analysis uses the empirical scatter of reference stars as
its error floor.

Sources whose core is unusable (saturation) are fitted on their wings only
with a circular Moffat (an archive-ePSF alternative exists in ``psf.py`` but
did not perform better; see USE_EPSF_FOR_CORE_MASKED).  Both carry ``core_masked=True``
and a systematic error floor downstream.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage, optimize, special

from .observation import WARN_FLAGS, Observation

SQRT2 = np.sqrt(2.0)
# Archive-ePSF wing fits were tested on Barnard's Star (61 D2 epochs, 3 visits):
# they did not reduce the per-visit systematic offsets (1-2") and had 2-3x the
# within-visit scatter of the Moffat fit, so the Moffat fit stays the default.
USE_EPSF_FOR_CORE_MASKED = False


@dataclass
class Source:
    x: float  # 0-based native pixel
    y: float
    x_err: float  # formal 1-sigma, pixels
    y_err: float
    flux: float  # integrated, image units * pixel
    sigma: float  # fitted Gaussian sigma, pixels
    background: float
    peak: float
    snr: float
    chi2_red: float
    saturated: bool  # core touches an OVERFLOW-flagged pixel
    core_masked: bool  # core (3x3) contains any unusable pixel; centroid relies on wings
    n_bad: int  # unusable pixels in the fit window
    n_pix: int  # usable pixels in the fit window
    blended: bool
    warn_flags: list
    fit_ok: bool
    resid_frac: float = float("nan")  # sum|data - model| / sum|model - bkg|: PSF-model mismatch
    method: str = "gaussian"  # or "moffat_wings" for core-masked sources
    ra: float = float("nan")
    dec: float = float("nan")

    def to_dict(self) -> dict:
        return asdict(self)


def _pixel_gaussian(params, xx, yy):
    x0, y0, flux, sigma, bkg = params
    s = sigma * SQRT2
    gx = 0.5 * (special.erf((xx + 0.5 - x0) / s) - special.erf((xx - 0.5 - x0) / s))
    gy = 0.5 * (special.erf((yy + 0.5 - y0) / s) - special.erf((yy - 0.5 - y0) / s))
    return flux * gx * gy + bkg


def robust_background(image: np.ndarray, mask: np.ndarray | None = None) -> tuple[float, float]:
    """Sigma-clipped median and robust sigma (1.4826 * MAD) of the usable pixels."""
    vals = image[~mask] if mask is not None else image.ravel()
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return 0.0, 1.0
    med = np.median(vals)
    sig = 1.4826 * np.median(np.abs(vals - med))
    for _ in range(5):
        keep = np.abs(vals - med) < 3 * max(sig, 1e-30)
        if keep.sum() < 10:
            break
        med = np.median(vals[keep])
        sig = 1.4826 * np.median(np.abs(vals[keep] - med))
    return float(med), float(sig if sig > 0 else np.std(vals) or 1.0)


def find_peaks(image, bad, saturated, nsigma=5.0, max_sources=150, min_sep=3.0, edge=3):
    """Local maxima above ``nsigma`` robust sigma.  Saturated (NaN/overflow)
    cores are filled with a value above the image maximum so they are detected;
    a saturated plateau yields one peak at its centroid."""
    bkg, sig = robust_background(image, bad)
    work = np.where(np.isfinite(image), image, bkg) - bkg
    work[bad & ~saturated] = 0.0
    sat_value = None
    if saturated.any():
        sat_value = max(float(np.nanmax(work)), nsigma * sig) * 1.05
        work[saturated] = sat_value
    peaks_mask = (work == ndimage.maximum_filter(work, size=3)) & (work > nsigma * sig)
    labels, n = ndimage.label(peaks_mask)
    if n == 0:
        return [], bkg, sig
    idx = np.arange(1, n + 1)
    centers = ndimage.center_of_mass(peaks_mask, labels, idx)
    heights = ndimage.maximum(work, labels, idx)
    order = np.argsort(heights)[::-1]
    ny, nx = image.shape
    out = []
    for i in order:
        cy, cx = centers[i]
        if not (edge <= cx < nx - edge and edge <= cy < ny - edge):
            continue
        if any((cx - px) ** 2 + (cy - py) ** 2 < min_sep**2 for px, py, _ in out):
            continue
        out.append((float(cx), float(cy), float(heights[i])))
        if len(out) >= max_sources:
            break
    return out, bkg, sig


def fit_source(obs: Observation, bad: np.ndarray, px: float, py: float, noise: float,
               sigma0: float, half: int | None = None) -> Source | None:
    ny, nx = obs.image.shape
    ix, iy = int(round(px)), int(round(py))
    core = bad[max(iy - 1, 0):iy + 2, max(ix - 1, 0):ix + 2]
    core_masked = bool(core.any())
    saturated = bool(obs.saturated_mask[max(iy - 1, 0):iy + 2, max(ix - 1, 0):ix + 2].any())
    if half is None:
        half = 6 if core_masked else 2
    y0, y1 = max(iy - half, 0), min(iy + half + 1, ny)
    x0, x1 = max(ix - half, 0), min(ix + half + 1, nx)
    img = obs.image[y0:y1, x0:x1]
    var = obs.variance[y0:y1, x0:x1]
    b = bad[y0:y1, x0:x1]
    good = ~b
    n_pix, n_bad = int(good.sum()), int(b.sum())
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(float)
    flag_or = int(np.bitwise_or.reduce(obs.flags[max(iy - 1, 0):iy + 2, max(ix - 1, 0):ix + 2].ravel()))
    warn = [n for n in obs.flag_names(flag_or) if n in WARN_FLAGS]

    def failed():
        return Source(px, py, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, 0.0, np.nan,
                      saturated, core_masked, n_bad, n_pix, False, warn, False)

    if n_pix < 9 or n_pix < 0.5 * b.size:
        return failed()
    if core_masked:
        if USE_EPSF_FOR_CORE_MASKED and obs.epsf is not None and obs.wcs_det is not None:
            s = _fit_epsf_wings(obs, bad, px, py, saturated, n_bad, n_pix, warn)
            if s is not None:
                return s
        return _fit_wings(px, py, img[good], var[good], xx[good], yy[good], saturated, n_bad, n_pix, warn) or failed()
    d, w = img[good], 1.0 / np.sqrt(var[good])
    gx, gy = xx[good], yy[good]
    bkg0 = float(np.percentile(d, 20))
    peak0 = float(d.max() - bkg0)
    if peak0 <= 0:
        return failed()
    p0 = [px, py, max(peak0 * 2 * np.pi * sigma0**2, 1e-6), sigma0, bkg0]
    lo = [px - 1.5, py - 1.5, 0.0, 0.25, -np.inf]
    hi = [px + 1.5, py + 1.5, np.inf, 3.0, np.inf]
    try:
        res = optimize.least_squares(lambda p: (_pixel_gaussian(p, gx, gy) - d) * w, p0,
                                     bounds=(lo, hi), x_scale=[1, 1, p0[2], 0.5, max(abs(bkg0), noise)],
                                     max_nfev=200)
    except (ValueError, FloatingPointError):
        return failed()
    dof = max(n_pix - 5, 1)
    chi2 = float(np.sum(res.fun**2) / dof)
    try:
        cov = np.linalg.inv(res.jac.T @ res.jac) * max(chi2, 1.0)
        xe, ye, fe = (float(np.sqrt(cov[i, i])) for i in range(3))
    except np.linalg.LinAlgError:
        return failed()
    x, y, flux, sigma, bkg = (float(v) for v in res.x)
    at_bound = abs(x - px) > 1.49 or abs(y - py) > 1.49 or sigma <= 0.2501 or sigma >= 2.999
    ok = bool(res.success and np.isfinite([xe, ye, fe]).all() and not at_bound and flux > 0)
    model = _pixel_gaussian(res.x, gx, gy)
    resid_frac = float(np.sum(np.abs(model - d)) / max(np.sum(np.abs(model - bkg)), 1e-30))
    return Source(x, y, xe, ye, flux, sigma, bkg, peak0, float(flux / fe) if fe > 0 else 0.0, chi2,
                  saturated, core_masked, n_bad, n_pix, False, warn, ok, resid_frac)


def _fit_epsf_wings(obs, bad, px, py, saturated, n_bad, n_pix, warn) -> Source | None:
    """Fit the archive ePSF (detector-frame, pixel-integrated) to the usable
    pixels of a core-masked source.  On Barnard's Star this removed the ~2"
    orientation-dependent bias of the Moffat fit at the cost of larger
    per-epoch scatter (docs/SCIENCE.md)."""
    from . import psf as P
    xd, yd = P.detector_xy(obs.wcs_det, px, py)
    model = obs.epsf.at(xd, yd)
    f = P.fit_epsf(obs.image, obs.variance, ~bad, model, px, py, half=5, model_error=0.15)
    if f is None or not f["success"]:
        return None
    snr = float(f["flux"] / f["flux_err"]) if f["flux_err"] > 0 else 0.0
    return Source(f["x"], f["y"], f["x_err"], f["y_err"], f["flux"], float("nan"), f["background"],
                  float("nan"), snr, f["chi2_red"], saturated, True, n_bad, f["n_pix"], False, warn, True,
                  f["resid_frac"], "epsf_wings")


def _fit_wings(px, py, d, var, gx, gy, saturated, n_bad, n_pix, warn) -> Source | None:
    """Circular Moffat + background fitted to the wings of a core-masked source.
    A 15% fractional model-error term keeps the brightest wing pixels from
    dominating (the true PSF is not a Moffat)."""
    if d.size < 20:
        return None
    bkg0 = float(np.percentile(d, 10))
    amp0 = float(d.max() - bkg0)
    if amp0 <= 0:
        return None
    w = 1.0 / np.sqrt(var + (0.15 * np.abs(d - bkg0)) ** 2)

    def model(p):
        x0, y0, amp, a, beta, bkg = p
        return amp * (1.0 + ((gx - x0) ** 2 + (gy - y0) ** 2) / a**2) ** (-beta) + bkg

    p0 = [px, py, amp0 * 5, 1.0, 1.5, bkg0]
    lo = [px - 2, py - 2, 0.0, 0.2, 0.5, -np.inf]
    hi = [px + 2, py + 2, np.inf, 5.0, 6.0, np.inf]
    try:
        res = optimize.least_squares(lambda p: (model(p) - d) * w, p0, bounds=(lo, hi),
                                     x_scale=[1, 1, p0[2], 1, 1, max(abs(bkg0), 1e-3)], max_nfev=400)
        chi2 = float(np.sum(res.fun**2) / max(d.size - 6, 1))
        cov = np.linalg.inv(res.jac.T @ res.jac) * max(chi2, 1.0)
    except (ValueError, FloatingPointError, np.linalg.LinAlgError):
        return None
    x, y, amp, a, beta, bkg = (float(v) for v in res.x)
    xe, ye, ae = (float(np.sqrt(cov[i, i])) for i in range(3))
    flux = float(np.pi * amp * a**2 / (beta - 1)) if beta > 1 else float("nan")  # analytic Moffat integral
    ok = bool(res.success and np.isfinite([xe, ye]).all() and abs(x - px) < 1.99 and abs(y - py) < 1.99)
    m = model(res.x)
    resid_frac = float(np.sum(np.abs(m - d)) / max(np.sum(np.abs(m - bkg)), 1e-30))
    snr = float(np.sum(d - bkg) / np.sqrt(np.sum(var)))  # wing flux only; the core is not measured
    return Source(x, y, xe, ye, flux, a, bkg, amp0, snr, chi2,
                  saturated, True, n_bad, n_pix, False, warn, ok, resid_frac, "moffat_wings")


def detect_sources(obs: Observation, nsigma: float = 5.0, max_sources: int = 150) -> list[Source]:
    """Detect and centroid sources; fill in ICRS coordinates from the image WCS."""
    bad = obs.bad_mask
    sat = bad & (obs.saturated_mask | ~np.isfinite(obs.image))
    # A NaN pixel only counts as "saturated" if it touches an OVERFLOW flag or is NaN
    # inside a bright core; isolated dead pixels are simply ignored.
    peaks, _, noise = find_peaks(obs.image, bad, sat & _near_bright(obs.image, sat), nsigma, max_sources)
    scale = _pixel_scale_arcsec(obs)
    sigma0 = float(np.clip((obs.psf_fwhm_arcsec or 7.5) / 2.3548 / scale, 0.4, 1.5))
    sources = []
    for px, py, _ in peaks:
        s = fit_source(obs, bad, px, py, noise, sigma0)
        if s is not None:
            sources.append(s)
    # Blend flag: a neighbour within 2.5 px carrying >= 25% of this source's flux.
    for s in sources:
        for t in sources:
            if t is s or not (s.fit_ok and t.fit_ok):
                continue
            if (s.x - t.x) ** 2 + (s.y - t.y) ** 2 < 2.5**2 and t.flux >= 0.25 * s.flux:
                s.blended = True
                break
    if sources:
        world = obs.wcs.all_pix2world([[s.x, s.y] for s in sources], 0)
        for s, (ra, dec) in zip(sources, world):
            s.ra, s.dec = float(ra), float(dec)
    return sources


def _near_bright(image, sat):
    """Restrict 'saturated' to bad pixels adjacent to a bright (top 2%) pixel."""
    if not sat.any():
        return sat
    finite = np.isfinite(image)
    if not finite.any():
        return np.zeros_like(sat)
    thr = np.percentile(image[finite], 98)
    bright = ndimage.binary_dilation(finite & (image >= thr), iterations=2)
    return sat & bright


def _pixel_scale_arcsec(obs: Observation) -> float:
    from astropy.wcs.utils import proj_plane_pixel_scales
    return float(np.mean(proj_plane_pixel_scales(obs.wcs.celestial)) * 3600.0)
