"""Common celestial grid and reprojection of observations onto it."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS
from reproject import reproject_interp

from .. import config
from .observation import Observation


@dataclass
class Grid:
    wcs: WCS
    shape: tuple[int, int]  # (ny, nx)
    pixel_scale_arcsec: float
    center: tuple[float, float]

    def world_to_pixel(self, ra, dec) -> np.ndarray:
        return self.wcs.all_world2pix(np.c_[np.atleast_1d(ra), np.atleast_1d(dec)], 0)

    def pixel_to_world(self, x, y) -> np.ndarray:
        return self.wcs.all_pix2world(np.c_[np.atleast_1d(x), np.atleast_1d(y)], 0)


def make_grid(ra: float, dec: float, size_deg: float, oversample: int = 2,
              native_scale_arcsec: float = config.PIXEL_SCALE_ARCSEC) -> Grid:
    """North-up, East-left gnomonic (TAN) grid centred on (ra, dec)."""
    scale = native_scale_arcsec / oversample
    n = int(np.ceil(size_deg * 3600.0 / scale))
    n += (n + 1) % 2  # odd, so the target sits on a pixel centre
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
    w.wcs.cdelt = [-scale / 3600.0, scale / 3600.0]
    w.wcs.cunit = ["deg", "deg"]
    return Grid(w, (n, n), scale, (ra, dec))


@dataclass
class Reprojected:
    image: np.ndarray  # NaN where no valid data
    variance: np.ndarray
    saturated: np.ndarray  # bool
    footprint: np.ndarray  # bool, inside the input image


def reproject_observation(obs: Observation, grid: Grid, shift_px=(0.0, 0.0)) -> Reprojected:
    """Bilinear reprojection.  ``shift_px`` moves the output by (dx, dy) grid
    pixels, used to apply a registration correction (resampling happens once)."""
    w = grid.wcs
    if shift_px[0] or shift_px[1]:
        w = grid.wcs.deepcopy()
        w.wcs.crpix = [w.wcs.crpix[0] + shift_px[0], w.wcs.crpix[1] + shift_px[1]]
    bad = obs.bad_mask
    img = np.where(bad, np.nan, obs.image)
    out, fp = reproject_interp((img, obs.wcs), w, shape_out=grid.shape, order="bilinear")
    var, _ = reproject_interp((np.where(bad, np.nan, obs.variance), obs.wcs), w, shape_out=grid.shape,
                              order="bilinear")
    sat, _ = reproject_interp((obs.saturated_mask.astype(float), obs.wcs), w, shape_out=grid.shape,
                              order="nearest-neighbor")
    return Reprojected(out, var, np.nan_to_num(sat) > 0.5, fp > 0)
