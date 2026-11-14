"""Difference imaging on the common grid."""
from __future__ import annotations

import numpy as np

from .reprojection import Reprojected
from .sources import robust_background


def difference_image(a: Reprojected, b: Reprojected, flux_scale: float = 1.0) -> dict:
    """B*scale - A after removing each image's robust background level.

    Returns the difference, its per-pixel sigma from the propagated variances,
    and robust statistics.  Pixels invalid in either image are NaN."""
    valid = np.isfinite(a.image) & np.isfinite(b.image)
    bkg_a, _ = robust_background(a.image, ~valid)
    bkg_b, _ = robust_background(b.image, ~valid)
    diff = np.where(valid, (b.image - bkg_b) * flux_scale - (a.image - bkg_a), np.nan)
    sigma = np.sqrt(np.where(valid, a.variance + flux_scale**2 * b.variance, np.nan))
    med, rob = robust_background(diff, ~valid)
    return {"diff": diff, "sigma": sigma, "valid": valid, "robust_sigma": rob, "median": med,
            "background_a": bkg_a, "background_b": bkg_b, "flux_scale": flux_scale,
            "valid_fraction": float(valid.mean())}
