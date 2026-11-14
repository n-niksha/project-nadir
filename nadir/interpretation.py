"""Deterministic interpretation of published results; never fits new physics."""
from __future__ import annotations

import math


def number(value, digits=2):
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) and math.isfinite(value) else "unknown"


def explain_source(source: dict, context: dict) -> dict:
    fit = source.get("fit")
    catalog = source.get("catalog") or {}
    simbad, gaia = catalog.get("simbad"), catalog.get("gaia")
    tier = source.get("tier", "insufficient")
    identity = "Classification unknown: no supporting catalog classification is available."
    if simbad:
        identity = f"SIMBAD reports object type {simbad['otype']} for {simbad['id']}. This is a catalog classification, not a NADIR classifier."
    elif gaia:
        probabilities = [(label, gaia.get(key)) for label, key in (("star", "p_star"), ("galaxy", "p_galaxy"), ("quasar", "p_quasar"))]
        available = [(label, probability) for label, probability in probabilities if probability is not None]
        if available:
            label, probability = max(available, key=lambda item: item[1])
            identity = f"Gaia DR3 DSC assigns its largest class probability to {label} ({number(probability, 3)}). This catalog estimate is not a confirmed physical type."
    status = {
        "significant": "The repeated positions support motion under the current quality gates. This is not a claim of a newly discovered object.",
        "systematics_limited": "Motion is supported across visits, but masked or saturated cores limit accuracy. More exposures within one visit do not remove a shared bias.",
        "candidate": "The fitted motion passes the significance threshold but fails at least one consistency or quality requirement. Treat it as a hypothesis to investigate.",
        "rejected": "An apparent two-image shift is not reproduced by the multi-epoch fit. The cause has not been established; blends or wavelength effects are possibilities.",
        "stationary": "No motion is significant at the adopted threshold. This does not prove that the object is physically stationary.",
        "insufficient": "There are too few independent visits or usable positions to support a reliable motion interpretation.",
    }.get(tier, "Evidence tier unavailable.")
    motion = "Proper motion is unknown because no supported multi-epoch fit is available."
    research_motion = motion
    if fit:
        motion = (f"NADIR fits an angular drift of {number(fit.get('pm_total'))} arcseconds per year across "
                  f"{source.get('n_epochs', 0)} measured epochs in {source.get('n_visits', 0)} visits. "
                  "An arcsecond is 1/3600 of a degree. This is motion across our sky, not a speed in kilometres per second.")
        research_motion = (f"East: {number(fit.get('pm_east'))} ± {number(fit.get('pm_east_err'))} arcsec/yr; "
                           f"north: {number(fit.get('pm_north'))} ± {number(fit.get('pm_north_err'))} arcsec/yr. "
                           f"Fit statistic S/N = {number(fit.get('snr'))}; reduced χ² E/N = "
                           f"{number(fit.get('chi2_red_east'))}/{number(fit.get('chi2_red_north'))}. "
                           "Offsets follow θ(t) = θ₀ + μ(t − t₀), with time in Julian years. "
                           "μE = μα cos δ; east is left and north is up on the image. "
                           "S/N = √[(μE/σE)² + (μN/σN)²] uses axis errors, not a discovery probability. "
                           f"Fit model: {fit.get('model', 'not specified')}.")
    alignment = ("Positions are measured on native SPHEREx pixels and aligned using other field sources. "
                 "Gaia is not used to determine that alignment. Motion is relative to the field-star frame; "
                 "the linear fit does not separate parallax.")
    quality = status + " " + "; ".join(source.get("reasons") or [])
    comparison = catalog.get("pm_comparison")
    catalog_check = "No catalog proper-motion comparison is available."
    if comparison:
        catalog_check = (f"Gaia reports east {number(comparison.get('gaia_pm_east'), 3)}, north "
                         f"{number(comparison.get('gaia_pm_north'), 3)} arcsec/yr. The difference statistic is "
                         f"{number(comparison.get('z_total'))} using NADIR's fit errors. "
                         "Catalog uncertainty is not included in that statistic.")
    calibration = ("The 1.2 arcsec per-visit systematic floor for masked cores was calibrated on Barnard's Star. "
                   "Agreement for Barnard is therefore a consistency check, not independent validation. "
                   "Quality thresholds and floors remain experimental.")
    def section(title, evidence, text):
        return {"title": title, "evidence": evidence, "text": text}
    return {
        "schema": "nadir.explanation.v1", "source_id": source["id"], "tier": tier,
        "classification": identity, "summary": status,
        "explore": [section("What changed?", "measured" if fit else "unknown", motion),
                    section("How do we know?", "measured", alignment),
                    section("How certain is it?", "measured", quality),
                    section("What is this object?", "catalog" if simbad or gaia else "unknown", identity),
                    section("What remains unknown?", "unknown", "These images alone do not establish composition, physical speed or a new discovery. A spectrum requires valid flux measurements.")],
        "research": [section("Motion model and conventions", "measured" if fit else "unknown", research_motion),
                     section("Reference frame", "measured", alignment),
                     section("Quality gates", "measured", quality + " Significant motion requires S/N ≥ 5, at least 3 visits and reduced χ² ≤ 5 in both axes, with blending and artifact checks."),
                     section("Catalog comparison", "catalog" if comparison else "unknown", catalog_check),
                     section("Calibration limitation", "inferred", calibration),
                     section("Provenance", "measured", f"Field {context['fid']}; band {context['band']}; reference observation {context['reference_obs_id']}. Open Observations for archive URLs, timestamps and checksums.")],
    }


def explain_spectrum(spectrum: dict) -> dict:
    points = spectrum.get("points", [])
    model = spectrum.get("blackbody")
    text = (f"{len(points)} accepted flux measurements and {len(spectrum.get('rejected', []))} rejected measurements. "
            "Flux density measures received light per frequency interval; 1 mJy = 10⁻²⁹ W m⁻² Hz⁻¹. "
            "These wavelengths were observed at different times, not in a single simultaneous spectrum.")
    model_text = "No supported blackbody fit: at least six clean positive measurements at S/N ≥ 5 are required."
    if model:
        model_text = (f"The single-blackbody colour model gives {number(model['T_color_K'], 0)} K with reduced χ² "
                      f"{number(model['chi2_red'])} from {model['n_points']} points. "
                      "This is an inferred colour temperature, not the physical effective temperature. "
                      "Extinction, variability, blends and absorption bands can alter the continuum. "
                      "A poor fit does not identify a molecule.")
    return {"explore": text + " " + model_text,
            "research": model_text + " The model minimizes Σ[(Fν − A Bν(T))/σ]² on a 500–30000 K grid, "
            "with a fitted normalization and a 3% error floor. The Δχ² ≤ 1 interval is scaled by max(χ²ᵣ,1); "
            "it is conditional on the model and does not include all calibration systematics. "
            "An interval containing only one grid sample is unresolved, not zero uncertainty. "
            "Flagged points are displayed but excluded from fitting. Feature markers are illustrative rest wavelengths, not detections or abundances."}
