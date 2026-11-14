"""Robust image registration from stationary reference sources.

Sources from two epochs are expressed in a common pixel grid, cross-matched,
and a translation or similarity transform (epoch B -> epoch A) is fitted with
iterative MAD clipping, so genuinely moving sources and mismatches drop out of
the reference set instead of biasing it.  An independent FFT cross-correlation
estimate is provided as a sanity check.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class Transform:
    """p_A = M @ (p_B - origin) + origin + t"""
    matrix: np.ndarray  # 2x2
    offset: np.ndarray  # 2
    origin: np.ndarray  # 2
    model: str
    n_matched: int
    n_used: int
    rms_x: float  # robust per-axis scatter of inlier residuals (pixels)
    rms_y: float
    cov: np.ndarray | None  # parameter covariance
    inliers: np.ndarray  # bool per matched pair
    converged: bool

    def apply(self, xy):
        xy = np.atleast_2d(np.asarray(xy, float))
        return (xy - self.origin) @ self.matrix.T + self.origin + self.offset

    def position_sigma(self, xy) -> np.ndarray:
        """1-sigma uncertainty (x, y) of the transformed position due to the fit."""
        xy = np.atleast_2d(np.asarray(xy, float))
        if self.cov is None:
            return np.full(xy.shape, np.nan)
        out = np.empty(xy.shape)
        for i, (x, y) in enumerate(xy - self.origin):
            if self.model == "translation":
                jx, jy = np.array([1.0, 0.0]), np.array([0.0, 1.0])
            else:  # params: a, b, tx, ty ; x' = a x - b y + tx ; y' = b x + a y + ty
                jx, jy = np.array([x, -y, 1.0, 0.0]), np.array([y, x, 0.0, 1.0])
            out[i] = np.sqrt(jx @ self.cov @ jx), np.sqrt(jy @ self.cov @ jy)
        return out

    @property
    def rotation_deg(self) -> float:
        return float(np.degrees(np.arctan2(self.matrix[1, 0], self.matrix[0, 0])))

    @property
    def scale(self) -> float:
        return float(np.hypot(self.matrix[0, 0], self.matrix[1, 0]))


def match_sources(xy_a, xy_b, radius: float) -> np.ndarray:
    """Mutual nearest-neighbour matches within ``radius``.  Returns (n, 2) index pairs."""
    xy_a, xy_b = np.atleast_2d(xy_a), np.atleast_2d(xy_b)
    if xy_a.size == 0 or xy_b.size == 0:
        return np.empty((0, 2), int)
    ta, tb = cKDTree(xy_a), cKDTree(xy_b)
    d_ab, i_ab = tb.query(xy_a, distance_upper_bound=radius)
    _, i_ba = ta.query(xy_b, distance_upper_bound=radius)
    pairs = [(i, j) for i, j in enumerate(i_ab) if np.isfinite(d_ab[i]) and i_ba[j] == i]
    return np.array(pairs, int).reshape(-1, 2)


def _solve(model, src, dst, origin):
    s = src - origin
    d = dst - origin
    if model == "translation":
        t = np.mean(d - s, axis=0)
        return np.eye(2), t, np.eye(2) / len(s)
    A = np.zeros((2 * len(s), 4))
    A[0::2] = np.c_[s[:, 0], -s[:, 1], np.ones(len(s)), np.zeros(len(s))]
    A[1::2] = np.c_[s[:, 1], s[:, 0], np.zeros(len(s)), np.ones(len(s))]
    params, *_ = np.linalg.lstsq(A, d.ravel(), rcond=None)
    a, b, tx, ty = params
    return np.array([[a, -b], [b, a]]), np.array([tx, ty]), np.linalg.inv(A.T @ A)


def _mad_sigma(v):
    return 1.4826 * np.median(np.abs(v - np.median(v)))


def fit_transform(xy_b, xy_a, model: str = "auto", clip: float = 3.0, max_iter: int = 20,
                  min_similarity: int = 10) -> Transform:
    """Robustly fit the transform taking matched epoch-B positions onto epoch A."""
    xy_a, xy_b = np.atleast_2d(np.asarray(xy_a, float)), np.atleast_2d(np.asarray(xy_b, float))
    n = len(xy_a)
    if n < 3:
        raise ValueError(f"Need at least 3 matched reference sources for registration, got {n}")
    if model == "auto":
        model = "similarity" if n >= min_similarity else "translation"
    origin = xy_b.mean(axis=0)
    # Robust start: median translation, so a minority of movers / mismatches
    # cannot drag the first least-squares solution.
    res0 = xy_b + np.median(xy_a - xy_b, axis=0) - xy_a
    sig0 = max(np.sqrt(0.5 * (_mad_sigma(res0[:, 0]) ** 2 + _mad_sigma(res0[:, 1]) ** 2)), 1e-4)
    inl = np.hypot(res0[:, 0], res0[:, 1]) < max(clip * sig0 * np.sqrt(2.0), 0.05)
    if inl.sum() < 3:
        inl = np.ones(n, bool)
    converged = False
    for _ in range(max_iter):
        m = model if inl.sum() >= (min_similarity if model == "similarity" else 3) else "translation"
        M, t, unit_cov = _solve(m, xy_b[inl], xy_a[inl], origin)
        res = (xy_b - origin) @ M.T + origin + t - xy_a
        r = np.hypot(res[:, 0], res[:, 1])
        # robust radial scale; floor avoids clipping everything in noise-free data
        sig = max(np.sqrt(0.5 * (_mad_sigma(res[inl, 0]) ** 2 + _mad_sigma(res[inl, 1]) ** 2)), 1e-4)
        new = r < clip * sig * np.sqrt(2.0)
        if new.sum() < 3:
            break
        if np.array_equal(new, inl):
            converged = True
            break
        inl = new
    m = model if inl.sum() >= (min_similarity if model == "similarity" else 3) else "translation"
    M, t, unit_cov = _solve(m, xy_b[inl], xy_a[inl], origin)
    res = ((xy_b - origin) @ M.T + origin + t - xy_a)[inl]
    k = 2 if m == "translation" else 4
    dof = max(2 * inl.sum() - k, 1)
    s2 = float(np.sum(res**2) / dof)
    rms_x = float(np.sqrt(np.mean(res[:, 0] ** 2)))
    rms_y = float(np.sqrt(np.mean(res[:, 1] ** 2)))
    return Transform(M, t, origin, m, n, int(inl.sum()), rms_x, rms_y, unit_cov * s2, inl, converged)


def cross_correlation_shift(img_a, img_b, valid=None, max_shift: int = 6) -> tuple[float, float] | None:
    """Sub-pixel shift (dx, dy) such that img_b(x - dx, y - dy) ~ img_a(x, y),
    from the peak of the FFT cross-correlation.  Images are asinh-compressed so
    a single bright (possibly moving or saturated) star does not dominate.
    Returns None if no usable peak exists."""
    a, b = np.array(img_a, float), np.array(img_b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if valid is not None:
        ok &= valid
    if ok.sum() < 0.3 * a.size:
        return None

    def prep(im):
        med = np.median(im[ok])
        sig = 1.4826 * np.median(np.abs(im[ok] - med)) or 1.0
        out = np.arcsinh(np.clip((im - med) / sig, -3, 30))
        out[~ok] = 0.0
        out[ok] -= out[ok].mean()
        return out

    fa, fb = np.fft.rfft2(prep(a)), np.fft.rfft2(prep(b))
    cc = np.fft.fftshift(np.fft.irfft2(fa * np.conj(fb), s=a.shape))
    cy, cx = a.shape[0] // 2, a.shape[1] // 2
    win = cc[cy - max_shift:cy + max_shift + 1, cx - max_shift:cx + max_shift + 1]
    py, px = np.unravel_index(np.argmax(win), win.shape)
    if not (0 < py < win.shape[0] - 1 and 0 < px < win.shape[1] - 1):
        return None

    def parabola(m, c, p):
        den = m - 2 * c + p
        return 0.0 if den == 0 else 0.5 * (m - p) / den

    dx = px - max_shift + parabola(win[py, px - 1], win[py, px], win[py, px + 1])
    dy = py - max_shift + parabola(win[py - 1, px], win[py, px], win[py + 1, px])
    return float(dx), float(dy)
