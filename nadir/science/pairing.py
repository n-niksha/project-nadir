"""Select observation pairs suitable for time-domain comparison.

SPHEREx images through linear variable filters, so the wavelength at a fixed
sky position differs from exposure to exposure.  A fair image difference needs
two exposures whose wavelength *at the target* agree to within a fraction of a
resolution element, separated by a useful time baseline.
"""
from __future__ import annotations

import numpy as np

from .observation import Observation


def coverage_fraction(obs: Observation) -> float:
    """Fraction of usable pixels in the cutout."""
    return float(1.0 - obs.bad_mask.mean())


def is_usable(obs: Observation, expected_pixels: int, min_fill: float = 0.7) -> tuple[bool, str]:
    if obs.mjd is None:
        return False, "no timestamp"
    if obs.wavelength_um is None:
        return False, "no wavelength"
    if obs.image.size < min_fill * expected_pixels:
        return False, "cutout truncated by detector edge"
    if coverage_fraction(obs) < min_fill:
        return False, "too many unusable pixels"
    return True, ""


def rank_pairs(observations: list[Observation], usable: list[bool], max_rel_dlambda: float = 0.02,
               min_dt_days: float = 30.0, top: int = 12) -> list[dict]:
    """All usable pairs within the wavelength tolerance and above the minimum
    baseline, ranked by baseline (longest first), then wavelength agreement.
    Indices refer to ``observations``; ``a`` is always the earlier epoch."""
    idx = [i for i, u in enumerate(usable) if u]
    pairs = []
    for n, i in enumerate(idx):
        for j in idx[n + 1:]:
            a, b = (i, j) if observations[i].mjd <= observations[j].mjd else (j, i)
            oa, ob = observations[a], observations[b]
            dt = ob.mjd - oa.mjd
            rel = abs(oa.wavelength_um - ob.wavelength_um) / (0.5 * (oa.wavelength_um + ob.wavelength_um))
            if dt >= min_dt_days and rel <= max_rel_dlambda:
                pairs.append({"a": a, "b": b, "dt_days": float(dt), "rel_dlambda": float(rel)})
    # Bin the baseline to 30 d so wavelength agreement breaks ties within a visit.
    pairs.sort(key=lambda p: (-np.floor(p["dt_days"] / 30.0), p["rel_dlambda"]))
    return pairs[:top]
