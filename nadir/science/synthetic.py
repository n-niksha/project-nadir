"""Synthetic SPHEREx-like observations with exactly known truth.

Used ONLY by the test-suite and the benchmark to validate the algorithms with
controlled shifts.  Nothing in the real-data path imports this module.
"""
from __future__ import annotations

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy import special

from .observation import DEFAULT_FLAG_BITS, Observation

PIX = 6.15 / 3600.0  # deg


def make_wcs(ra0, dec0, n, rotation_deg=0.0, offset_arcsec=(0.0, 0.0)) -> WCS:
    """TAN WCS for an n x n image.  ``offset_arcsec`` deliberately mis-points
    the WCS (east, north) to emulate an astrometric-solution error."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
    w.wcs.crval = [ra0 + offset_arcsec[0] / 3600.0 / np.cos(np.radians(dec0)), dec0 + offset_arcsec[1] / 3600.0]
    c, s = np.cos(np.radians(rotation_deg)), np.sin(np.radians(rotation_deg))
    w.wcs.cd = np.array([[-PIX * c, PIX * s], [PIX * s, PIX * c]])
    return w


def random_field(n_stars, ra0, dec0, half_width_deg, rng, flux_range=(300.0, 5000.0)):
    """Star list [(ra, dec, flux)] with log-uniform fluxes."""
    ra = ra0 + rng.uniform(-half_width_deg, half_width_deg, n_stars) / np.cos(np.radians(dec0))
    dec = dec0 + rng.uniform(-half_width_deg, half_width_deg, n_stars)
    flux = np.exp(rng.uniform(np.log(flux_range[0]), np.log(flux_range[1]), n_stars))
    return np.c_[ra, dec, flux]


def render_observation(stars, ra0=150.0, dec0=20.0, n=101, mjd=61000.0, rotation_deg=0.0,
                       wcs_error_arcsec=(0.0, 0.0), psf_sigma_px=0.55, background=0.3, read_noise=0.02,
                       saturation=None, wavelength_um=1.5, seed=0, obs_id="SYN") -> Observation:
    """Render pixel-integrated Gaussian stars with Poisson-like + read noise.

    The *true* sky->pixel mapping uses the un-perturbed WCS; the Observation is
    handed a WCS perturbed by ``wcs_error_arcsec``, exactly like a real image
    with an imperfect astrometric solution.  ``saturation`` (image units) turns
    brighter pixels into NaN with the OVERFLOW flag set."""
    rng = np.random.default_rng(seed)
    true_wcs = make_wcs(ra0, dec0, n, rotation_deg)
    stars = np.atleast_2d(np.asarray(stars, float))
    xy = true_wcs.all_world2pix(stars[:, :2], 0)
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    img = np.full((n, n), background)
    s = psf_sigma_px * np.sqrt(2)
    for (x0, y0), flux in zip(xy, stars[:, 2]):
        if not (-5 < x0 < n + 5 and -5 < y0 < n + 5):
            continue
        x_lo, x_hi = max(int(x0) - 6, 0), min(int(x0) + 8, n)
        y_lo, y_hi = max(int(y0) - 6, 0), min(int(y0) + 8, n)
        gx = 0.5 * (special.erf((xx[y_lo:y_hi, x_lo:x_hi] + 0.5 - x0) / s) - special.erf((xx[y_lo:y_hi, x_lo:x_hi] - 0.5 - x0) / s))
        gy = 0.5 * (special.erf((yy[y_lo:y_hi, x_lo:x_hi] + 0.5 - y0) / s) - special.erf((yy[y_lo:y_hi, x_lo:x_hi] - 0.5 - y0) / s))
        img[y_lo:y_hi, x_lo:x_hi] += flux * gx * gy
    gain = 50.0  # arbitrary "counts per image unit" to give source shot noise
    variance = img / gain + read_noise**2
    img = img + rng.normal(0.0, np.sqrt(variance))
    flags = np.zeros((n, n), np.int64)
    if saturation is not None:
        sat = img > saturation
        flags[sat] |= 1 << DEFAULT_FLAG_BITS["OVERFLOW"]
        img[sat] = np.nan
    return Observation(
        obs_id=obs_id, image=img, flags=flags, variance=variance,
        wcs=make_wcs(ra0, dec0, n, rotation_deg, wcs_error_arcsec), flag_bits=dict(DEFAULT_FLAG_BITS),
        mjd=mjd, date_iso=None, detector=2, exptime_s=113.6, psf_fwhm_arcsec=psf_sigma_px * 2.3548 * 6.15,
        wavelength_um=wavelength_um, bandwidth_um=wavelength_um / 41.0, zodi=None, release="synthetic",
        pipeline_version="synthetic", provenance={"source_url": "synthetic://" + obs_id, "release": "synthetic"},
    )


def to_fits(obs: Observation) -> fits.HDUList:
    """Serialise a synthetic observation in the SPHEREx Level-2 HDU layout
    (without EPSF / WCS-WAVE), for exercising the FITS parser."""
    hdr = obs.wcs.to_header()
    hdr["DETECTOR"] = obs.detector
    hdr["OBSID"] = obs.obs_id
    hdr["MJD-AVG"] = obs.mjd
    hdr["DATE-AVG"] = "2026-01-01T00:00:00.000"
    hdr["XPOSURE"] = obs.exptime_s
    hdr["PSF_FWHM"] = obs.psf_fwhm_arcsec
    fh = obs.wcs.to_header()
    for name, bit in obs.flag_bits.items():
        fh[f"MSKN{bit:04d}"] = name
    primary = fits.PrimaryHDU()
    primary.header["VERSION"] = "synthetic"
    return fits.HDUList([
        primary,
        fits.ImageHDU(obs.image.astype(np.float32), hdr, name="IMAGE"),
        fits.ImageHDU(obs.flags.astype(np.int32), fh, name="FLAGS"),
        fits.ImageHDU(obs.variance.astype(np.float32), obs.wcs.to_header(), name="VARIANCE"),
    ])
