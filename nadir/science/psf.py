"""Archive-provided effective PSF (ePSF) model and ePSF fitting.

Every SPHEREx Level-2 image ships an ``EPSF`` table: a 21 x 21 grid of
detector tiles (11 x 41 on detector 3), each with a 33 x 33 effective PSF oversampled 5x (so it spans
+-3.2 native pixels), already in the image's pixel orientation (``DETCOORD =
'sky'``).  An ePSF is the PSF convolved with the pixel response, so the
expected value of a native pixel whose centre lies (dx, dy) from the star is
``flux * f(dx, dy)`` -- exactly what an undersampled image needs.

We use it to centroid sources whose core is unusable (saturated): the fit uses
only good pixels, but the model knows the true, asymmetric wing shape in the
detector frame, unlike the circular Moffat used in milestone 1.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from scipy import ndimage, optimize

OVERSAMPLE = 5
HALF = 16  # centre index of the 33 x 33 grid


@dataclass
class EPSFGrid:
    xcen: np.ndarray  # tile centres, detector (0-based active) pixels
    ycen: np.ndarray
    psfs: np.ndarray  # (n, 33, 33)
    cwave: np.ndarray

    @classmethod
    def from_hdu(cls, hdu) -> "EPSFGrid":
        t = hdu.data
        return cls(np.asarray(t["XCENTER"], float), np.asarray(t["YCENTER"], float),
                   np.asarray(t["EPSF"], float).reshape(-1, 33, 33), np.asarray(t["CWAVE"], float))

    def at(self, xd: float, yd: float) -> np.ndarray:
        """Inverse-distance-weighted blend of the (up to 4) nearest tiles,
        normalised so the native-pixel lattice through the centre sums to 1."""
        d = np.hypot(self.xcen - xd, self.ycen - yd)
        k = np.argsort(d)[: min(4, len(d))]
        w = 1.0 / np.maximum(d[k], 1.0)
        psf = np.tensordot(w / w.sum(), self.psfs[k], axes=1)
        return psf / psf[HALF % OVERSAMPLE::OVERSAMPLE, HALF % OVERSAMPLE::OVERSAMPLE].sum()


def trim_epsf_hdu(hdu, image_header, margin: float = 60.0) -> fits.BinTableHDU | None:
    """Keep only ePSF tiles near the cutout footprint (saves ~3.8 MB per cutout)."""
    from astropy.wcs import WCS
    try:
        ny, nx = image_header["NAXIS2"], image_header["NAXIS1"]
        wa = WCS(image_header, key="A")
        (x0, y0), (x1, y1) = wa.wcs_pix2world([[0, 0], [nx - 1, ny - 1]], 0)
    except Exception:
        return None
    t = hdu.data
    xc, yc = np.asarray(t["XCENTER"]), np.asarray(t["YCENTER"])
    keep = ((xc > min(x0, x1) - margin - 50) & (xc < max(x0, x1) + margin + 50)
            & (yc > min(y0, y1) - margin - 50) & (yc < max(y0, y1) + margin + 50))
    if not keep.any():
        d = np.hypot(xc - (x0 + x1) / 2, yc - (y0 + y1) / 2)
        keep = d <= np.sort(d)[min(3, len(d) - 1)]
    out = fits.BinTableHDU(t[keep], header=hdu.header.copy(), name="EPSF")
    out.header["NADIRTRM"] = (True, "tiles far from the cutout removed by NADIR")
    return out


def detector_xy(obs_wcs_a, x, y):
    """Cutout 0-based pixel -> detector 0-based active pixel (alternate WCS 'A')."""
    return obs_wcs_a.wcs_pix2world([[x, y]], 0)[0]


def epsf_values(psf: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """f(dx, dy) at arbitrary offsets (native pixels) by cubic interpolation."""
    coords = np.vstack([HALF + OVERSAMPLE * np.ravel(dy), HALF + OVERSAMPLE * np.ravel(dx)])
    return ndimage.map_coordinates(psf, coords, order=3, mode="constant", cval=0.0).reshape(np.shape(dx))


def fit_epsf(image, variance, good, psf, px, py, half=5, model_error=0.05):
    """Fit flux, position and a constant background with the ePSF.

    ``good`` masks usable pixels (saturated cores excluded).  Pixels beyond the
    ePSF footprint are not used.  A fractional model-error term keeps the
    brightest unmasked pixels (where any PSF mismatch is largest) from
    dominating.  Returns dict or None."""
    ny, nx = image.shape
    ix, iy = int(round(px)), int(round(py))
    y0, y1 = max(iy - half, 0), min(iy + half + 1, ny)
    x0, x1 = max(ix - half, 0), min(ix + half + 1, nx)
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(float)
    g = good[y0:y1, x0:x1] & (np.abs(xx - px) <= 3.0) & (np.abs(yy - py) <= 3.0)
    if g.sum() < 12:
        return None
    d, v = image[y0:y1, x0:x1][g], variance[y0:y1, x0:x1][g]
    gx, gy = xx[g], yy[g]
    bkg0 = float(np.percentile(image[y0:y1, x0:x1][good[y0:y1, x0:x1]], 5)) if good[y0:y1, x0:x1].any() else 0.0
    f0 = epsf_values(psf, gx - px, gy - py)
    flux0 = max(float(np.sum((d - bkg0) * f0) / max(np.sum(f0 * f0), 1e-12)), 1e-6)
    w = 1.0 / np.sqrt(v + (model_error * np.abs(d - bkg0)) ** 2)

    def resid(p):
        x, y, fl, b = p
        return (fl * epsf_values(psf, gx - x, gy - y) + b - d) * w

    try:
        res = optimize.least_squares(resid, [px, py, flux0, bkg0], x_scale=[0.1, 0.1, flux0, max(abs(bkg0), 1e-3)],
                                     bounds=([px - 1.5, py - 1.5, 0, -np.inf], [px + 1.5, py + 1.5, np.inf, np.inf]),
                                     max_nfev=300)
        dof = max(g.sum() - 4, 1)
        chi2 = float(np.sum(res.fun**2) / dof)
        cov = np.linalg.inv(res.jac.T @ res.jac) * max(chi2, 1.0)
    except (ValueError, np.linalg.LinAlgError):
        return None
    x, y, fl, b = (float(v) for v in res.x)
    model = fl * epsf_values(psf, gx - x, gy - y)
    return {"x": x, "y": y, "x_err": float(np.sqrt(cov[0, 0])), "y_err": float(np.sqrt(cov[1, 1])),
            "flux": fl, "flux_err": float(np.sqrt(cov[2, 2])), "background": b, "chi2_red": chi2,
            "n_pix": int(g.sum()), "success": bool(res.success) and abs(x - px) < 1.49 and abs(y - py) < 1.49,
            "resid_frac": float(np.sum(np.abs(model + b - d)) / max(np.sum(np.abs(model)), 1e-30))}
