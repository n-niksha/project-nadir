"""Presentation helpers: arrays -> PNG (base64) for the web frontend."""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image


def stretch_limits(*images, lo_pct: float = 20.0, hi_pct: float = 99.7) -> tuple[float, float]:
    vals = np.concatenate([im[np.isfinite(im)].ravel() for im in images]) if images else np.array([])
    if vals.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(vals, [lo_pct, hi_pct])
    return float(lo), float(hi if hi > lo else lo + 1.0)


def _encode(rgb: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgb[::-1], "RGB").save(buf, format="PNG")  # flip: row 0 at bottom = north up
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def gray_png(image, lo, hi, saturated=None) -> str:
    """asinh-stretched grayscale; invalid pixels dark blue, saturated cores white."""
    x = np.clip((image - lo) / (hi - lo), 0, None)
    soft = 0.03
    g = np.arcsinh(x / soft) / np.arcsinh(1 / soft)
    g8 = np.nan_to_num(np.clip(g, 0, 1) * 255).astype(np.uint8)
    rgb = np.stack([g8, g8, g8], axis=-1)
    bad = ~np.isfinite(image)
    rgb[bad] = (20, 24, 60)
    if saturated is not None:
        rgb[bad & saturated] = (255, 255, 255)
    return _encode(rgb)


def diverging_png(diff, soft, vmax) -> str:
    """Blue (negative) - black - orange (positive), symmetric asinh stretch:
    linear below ``soft`` (so noise is visible), compressed up to ``vmax``."""
    x = np.arcsinh(np.nan_to_num(diff) / soft) / np.arcsinh(vmax / soft)
    x = np.clip(x, -1, 1)
    pos, neg = np.clip(x, 0, 1), np.clip(-x, 0, 1)
    rgb = np.stack([255 * pos, 150 * pos + 110 * neg, 255 * neg], axis=-1).astype(np.uint8)
    rgb[~np.isfinite(diff)] = (40, 40, 40)
    return _encode(rgb)
