"""Feature schema handed from the scientific engine to future ML models."""
from __future__ import annotations

import math

FEATURE_SCHEMA_VERSION = 1
FEATURES = (
    "displacement_arcsec", "significance", "rate_arcsec_per_yr", "position_angle_deg",
    "log_flux_ratio", "snr_min", "resid_frac_max", "saturated", "blended", "n_warn_flags",
    "has_gaia", "gaia_residual_sigma",
)


def source_features(source: dict, dt_days: float) -> dict:
    """Flatten one ``SourceMotion`` dict (as returned by ``pipeline.analyze``).
    Missing inputs become None -- never a fabricated default."""
    a, b = source["a"], source["b"]
    yrs = abs(dt_days) / 365.25
    cat = source.get("catalog") or {}
    pred = cat.get("predicted")
    resid = None
    if pred:
        resid = math.hypot((source["d_east_arcsec"] - pred["east_arcsec"]) / source["sigma_east_arcsec"],
                           (source["d_north_arcsec"] - pred["north_arcsec"]) / source["sigma_north_arcsec"])
    ok_flux = a.get("flux") and b.get("flux") and a["flux"] > 0 and b["flux"] > 0
    return {
        "schema": FEATURE_SCHEMA_VERSION,
        "displacement_arcsec": source["total_arcsec"],
        "significance": source["significance"],
        "rate_arcsec_per_yr": source["total_arcsec"] / yrs if yrs > 0 else None,
        "position_angle_deg": math.degrees(math.atan2(source["d_east_arcsec"], source["d_north_arcsec"])) % 360,
        "log_flux_ratio": math.log(b["flux"] / a["flux"]) if ok_flux else None,
        "snr_min": min(a["snr"], b["snr"]),
        "resid_frac_max": max(a["resid_frac"], b["resid_frac"]) if a.get("resid_frac") and b.get("resid_frac") else None,
        "saturated": bool(a["saturated"] or b["saturated"]),
        "blended": bool(a["blended"] or b["blended"]),
        "n_warn_flags": len(set(a["warn_flags"]) | set(b["warn_flags"])),
        "has_gaia": bool(cat),
        "gaia_residual_sigma": resid,
    }
