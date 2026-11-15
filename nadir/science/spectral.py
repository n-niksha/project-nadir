"""Forced ePSF photometry and multi-epoch SPHEREx spectra.

SPHEREx observes through linear variable filters: each exposure sees a given
sky position at one wavelength (R ~ 40-130).  A source's spectrum is built by
measuring its flux in *many* exposures, in all six detectors, each at the
wavelength the spectral WCS assigns to the source's pixel.  The spectrum is
therefore a collection of epochs, not a simultaneous measurement; a variable
source will show scatter.

Photometry: forced fit of the archive ePSF at the known position (linear least
squares for flux + constant background), weighted by the pipeline variance.
The ePSF is normalised to unit total flux on the native-pixel lattice, so the
fitted amplitude is the total flux; converting MJy/sr x pixel to Jy uses the
image's median pixel solid angle (``OMEGA_MEDIAN``).  Without an ePSF, a 3-pixel
radius aperture with an annulus background is used and labelled as such
(it is not aperture-corrected).
"""
from __future__ import annotations

import math

import numpy as np

from . import psf as P
from .observation import Observation

SAT_REASON = "core pixels saturated or unusable: flux not measurable"


def forced_photometry(obs: Observation, ra: float, dec: float, half: int = 4) -> dict:
    """Flux density (mJy) of a point source at (ra, dec) in one observation."""
    x, y = (float(v) for v in obs.wcs.all_world2pix([[ra, dec]], 0)[0])
    ny, nx = obs.image.shape
    out = {"obs_id": obs.obs_id, "mjd": obs.mjd, "x": x, "y": y, "ok": False, "method": None,
           "wavelength_calibration": "WCS-WAVE approximate; science CWAVE/CBAND maps not loaded",
           "covariance_assumption": "diagonal pixel variance; correlated noise and PSF errors not modeled",
           "interpretation": "model-dependent photometry; non-simultaneous spectral samples"}
    if obs.wavelength_um is None:
        out["reason"] = "wavelength unknown"
        return out
    out["wavelength_um"], out["bandwidth_um"] = _wavelength_at(obs, x, y)
    if not (half <= x < nx - half and half <= y < ny - half):
        out["reason"] = "too close to the cutout edge"
        return out
    if obs.pixel_sr is None:
        out["reason"] = "pixel solid angle (OMEGA_MEDIAN) missing"
        return out
    ix, iy = int(round(x)), int(round(y))
    sl = (slice(iy - half, iy + half + 1), slice(ix - half, ix + half + 1))
    img, var, bad = obs.image[sl], obs.variance[sl], obs.bad_mask[sl]
    core_bad = bad[half - 1:half + 2, half - 1:half + 2].any()
    flags = int(np.bitwise_or.reduce(obs.flags[iy - 1:iy + 2, ix - 1:ix + 2].ravel()))
    out["flags"] = [n for n in obs.flag_names(flags) if n not in ("SOURCE", "FULLSAMPLE")]
    if core_bad:
        out["reason"] = SAT_REASON
        out["saturated"] = True
        return out
    yy, xx = np.mgrid[sl[0], sl[1]].astype(float)
    good = ~bad
    to_mjy = obs.pixel_sr * 1e9  # (MJy/sr * pixel) -> mJy
    if obs.epsf is not None and obs.wcs_det is not None:
        model = P.epsf_values(obs.epsf.at(*P.detector_xy(obs.wcs_det, x, y)), xx - x, yy - y)
        A = np.c_[model[good], np.ones(good.sum())]
        w = 1.0 / np.sqrt(var[good])
        try:
            coef, *_ = np.linalg.lstsq(A * w[:, None], img[good] * w, rcond=None)
            cov = np.linalg.inv((A * w[:, None]).T @ (A * w[:, None]))
        except np.linalg.LinAlgError:
            out["reason"] = "photometric fit failed"
            return out
        resid = (A @ coef - img[good]) * w
        chi2 = float(np.sum(resid**2) / max(good.sum() - 2, 1))
        flux, err = float(coef[0]), float(math.sqrt(cov[0, 0]) * math.sqrt(max(chi2, 1.0)))
        out.update(method="epsf_forced", chi2_red=chi2,
                   quality="model_mismatch" if chi2 > 3 else "conditional_measurement",
                   quality_reason="Excess residuals; PSF/position/confusion errors may bias flux." if chi2 > 3 else None)
    else:
        r = np.hypot(xx - x, yy - y)
        ap = good & (r <= 3.0)
        ann = good & (r > 3.5)
        if ap.sum() < 20 or ann.sum() < 8:
            out["reason"] = "too few usable pixels for aperture photometry"
            return out
        bkg = float(np.median(img[ann]))
        flux = float(np.sum(img[ap] - bkg))
        err = float(math.sqrt(np.sum(var[ap])))
        out.update(method="aperture_r3px_uncorrected")
    out.update(ok=True, flux_mjy=flux * to_mjy, flux_err_mjy=err * to_mjy, snr=flux / err if err > 0 else 0.0)
    return out


def _wavelength_at(obs: Observation, x: float, y: float) -> tuple[float | None, float | None]:
    """Wavelength at an arbitrary pixel of the cutout from the spectral WCS."""
    if obs.path is None:
        return obs.wavelength_um, obs.bandwidth_um
    from astropy.io import fits

    from .observation import wavelength_at
    try:
        with fits.open(obs.path, memmap=False) as hdul:
            if "WCS-WAVE" not in hdul:
                return obs.wavelength_um, obs.bandwidth_um
            ny, nx = obs.image.shape
            return wavelength_at(hdul, min(max(x, 0), nx - 1), min(max(y, 0), ny - 1))
    except OSError:
        return obs.wavelength_um, obs.bandwidth_um


def build_spectrum(observations: list[tuple[str, Observation]], ra: float, dec: float, pm_east_arcsec_yr: float = 0.0,
                   pm_north_arcsec_yr: float = 0.0, ref_mjd: float | None = None) -> dict:
    """Spectrum of one source from many (band, Observation) pairs.

    If a proper motion is given (measured or catalog), the forced position is
    propagated to each exposure's epoch so a moving star is not missed."""
    points, rejected = [], []
    for band, obs in observations:
        r, d = ra, dec
        if ref_mjd is not None and obs.mjd is not None and (pm_east_arcsec_yr or pm_north_arcsec_yr):
            dt = (obs.mjd - ref_mjd) / 365.25
            d = dec + pm_north_arcsec_yr * dt / 3600.0
            r = ra + pm_east_arcsec_yr * dt / 3600.0 / math.cos(math.radians(dec))
        m = forced_photometry(obs, r, d)
        m["band"] = band
        m["source_url"] = (obs.provenance or {}).get("source_url")
        m["response_sha256"] = (obs.provenance or {}).get("response_sha256")
        m["release"] = obs.release
        (points if m["ok"] else rejected).append(m)
    points.sort(key=lambda p: p["wavelength_um"])
    sat = sum(1 for r in rejected if r.get("saturated"))
    out = {"points": points, "rejected": rejected, "n_measured": len(points), "n_saturated": sat,
           "bands": sorted(set(p["band"] for p in points))}
    if points:
        out["blackbody"] = blackbody_fit(points)
    return out


def planck_fnu(wl_um, T):
    """Planck B_nu shape (arbitrary scale) at wavelength(s) in microns."""
    h, c, k = 6.62607015e-34, 2.99792458e8, 1.380649e-23
    nu = c / (np.asarray(wl_um, float) * 1e-6)
    x = np.clip(h * nu / (k * T), 1e-6, 700)
    return nu**3 / np.expm1(x)


def blackbody_fit(points: list[dict], min_snr: float = 5.0) -> dict | None:
    """Best single-temperature blackbody (scale free) to good points.

    This is an *inferred colour temperature* under an explicit, crude model:
    real stellar spectra have absorption bands (H2O, CO in cool stars), so it
    is not an effective temperature.  Above ~5000 K the 0.75-5 um range is on
    the Rayleigh-Jeans tail and T is poorly constrained; we report that."""
    good = [p for p in points if p["snr"] >= min_snr and p["flux_mjy"] > 0
            and not p.get("flags") and not p.get("saturated") and p.get("quality") != "model_mismatch"
            and all(math.isfinite(p[key]) for key in ("wavelength_um", "flux_mjy", "flux_err_mjy"))
            and p["wavelength_um"] > 0 and p["flux_err_mjy"] > 0]
    if len(good) < 6:
        return None
    wl = np.array([p["wavelength_um"] for p in good])
    f = np.array([p["flux_mjy"] for p in good])
    e = np.array([max(p["flux_err_mjy"], 0.03 * p["flux_mjy"]) for p in good])  # 3% calibration floor
    temps = np.geomspace(500.0, 30000.0, 400)
    chi = []
    for T in temps:
        m = planck_fnu(wl, T)
        a = np.sum(f * m / e**2) / np.sum(m * m / e**2)
        chi.append(np.sum(((f - a * m) / e) ** 2))
    chi = np.array(chi)
    k = int(np.argmin(chi))
    dof = max(len(good) - 2, 1)
    scaled = (chi - chi[k]) / max(chi[k] / dof, 1.0)  # inflate by reduced chi2
    inside = temps[scaled <= 1.0]
    lo, hi = float(inside.min()), float(inside.max())
    T = float(temps[k])
    m = planck_fnu(wl, T)
    a = float(np.sum(f * m / e**2) / np.sum(m * m / e**2))
    grid_wl = np.geomspace(0.7, 5.1, 80)
    return {"T_color_K": T, "T_lo_K": lo, "T_hi_K": hi, "chi2_red": float(chi[k] / dof), "n_points": len(good),
            "interval_grid_limited": len(inside) == 1,
            "grid_step_K": float(temps[min(k+1,len(temps)-1)]-temps[max(k-1,0)]) / (2 if 0 < k < len(temps)-1 else 1),
            "unconstrained_high": hi >= temps[-2], "unconstrained_low": lo <= temps[1],
            "excluded_points": len(points) - len(good), "curve": {"wavelength_um": grid_wl.tolist(),
                                                             "flux_mjy": (a * planck_fnu(grid_wl, T)).tolist()},
            "label": "Inferred: single-blackbody colour temperature (model), not an effective temperature"}


def catalog_photometry(twomass: dict | None, wise: dict | None) -> list[dict]:
    """Independent photometric reference points (mJy) from 2MASS/AllWISE magnitudes."""
    from ..data.catalogs import TWOMASS_WL_UM, TWOMASS_ZP_JY, WISE_WL_UM, WISE_ZP_JY
    pts = []
    for src, zps, wls in ((twomass, TWOMASS_ZP_JY, TWOMASS_WL_UM), (wise, WISE_ZP_JY, WISE_WL_UM)):
        if not src:
            continue
        for b, m in src["mags"].items():
            if m is None:
                continue
            em = src.get("mag_errs", {}).get(b) or 0.03
            fl = zps[b] * 10 ** (-0.4 * m) * 1e3
            pts.append({"band": b, "wavelength_um": wls[b], "flux_mjy": fl, "flux_err_mjy": fl * em / 1.0857,
                        "catalog": "2MASS" if b in zps and b in "JHK" else "AllWISE", "mag": m, "id": src["id"]})
    return pts


def compare_to_catalog(spectrum_points: list[dict], cat_points: list[dict], rel_window: float = 0.04) -> list[dict]:
    """SPHEREx median flux within +-rel_window of each catalog band centre vs the catalog flux.

    Broad-band filters average over the spectrum while SPHEREx samples it at
    R~40, so agreement within ~10-20% is the expectation, not 1%."""
    out = []
    for c in cat_points:
        near = [p for p in spectrum_points if abs(p["wavelength_um"] / c["wavelength_um"] - 1) <= rel_window]
        if len(near) < 2:
            continue
        f = float(np.median([p["flux_mjy"] for p in near]))
        out.append({"band": c["band"], "catalog": c["catalog"], "wavelength_um": c["wavelength_um"],
                    "spherex_mjy": f, "catalog_mjy": c["flux_mjy"], "ratio": f / c["flux_mjy"], "n": len(near)})
    return out


# Educational reference features.  Shown as "Illustrative": where such features
# *occur* in astrophysical spectra -- never a claim that NADIR detected them.
REFERENCE_FEATURES = [
    {"wavelength_um": 1.282, "label": "Pa-beta", "kind": "H recombination line (emission in nebulae, young stars)"},
    {"wavelength_um": 1.4, "label": "H2O", "kind": "water vapour absorption band (cool stars, brown dwarfs)"},
    {"wavelength_um": 1.875, "label": "Pa-alpha", "kind": "H recombination line"},
    {"wavelength_um": 1.9, "label": "H2O", "kind": "water vapour absorption band"},
    {"wavelength_um": 2.166, "label": "Br-gamma", "kind": "H recombination line"},
    {"wavelength_um": 2.3, "label": "CO", "kind": "carbon monoxide overtone absorption (cool giants and dwarfs)"},
    {"wavelength_um": 2.7, "label": "H2O", "kind": "water vapour absorption band"},
    {"wavelength_um": 3.05, "label": "H2O ice", "kind": "water ice absorption toward cold clouds (SPHEREx ice survey)"},
    {"wavelength_um": 3.3, "label": "CH4 / PAH", "kind": "methane absorption (T dwarfs) or PAH emission (dusty galaxies)"},
    {"wavelength_um": 4.27, "label": "CO2 ice", "kind": "carbon dioxide ice absorption"},
    {"wavelength_um": 4.67, "label": "CO", "kind": "CO fundamental band (gas absorption / ice at 4.67)"},
]
