import math

import numpy as np
import pytest

from nadir import field, interpretation
from nadir.science import fieldtrack, spectral, synthetic


@pytest.mark.parametrize("masked,blended,expected", [(False, False, "significant"), (True, False, "systematics_limited"), (False, True, "candidate")])
def test_multivisit_motion_conventions_and_quality(masked, blended, expected):
    frames = []
    for index, days in enumerate([0, 1, 2, 180, 181, 182, 365, 366, 367]):
        years = days / 365.25
        frames.append(fieldtrack.EpochFrame(index, 61000+days, 1.5,
            np.array([[50-2*years, 50+12*years], [100, 100]]), np.full((2, 2), 0.1),
            np.ones(2)*100, np.array([masked, False]), np.array([blended, False]),
            np.zeros(2, bool), index == 0))
    tracks = fieldtrack.build_tracks(frames, 0, np.array([[150, 20], [150.01, 20.01]]), 1, 20)
    mover, reference = tracks
    assert mover.fit["pm_east"] == pytest.approx(2, abs=0.01)
    assert mover.fit["pm_north"] == pytest.approx(12, abs=0.01)
    assert mover.tier == expected and mover.n_visits == 3
    assert reference.tier == "stationary"
    assert mover.fit["pm_north_err"] > 0
    if masked:
        assert "visit" in mover.fit["model"] and mover.fit["pm_north_err"] > 1
    fieldtrack.apply_pair_check([reference], {reference.ref_index: 8})
    assert reference.tier == "rejected" and fieldtrack.priority_score(reference) == 0


@pytest.mark.parametrize("tier", ["significant", "systematics_limited", "candidate", "rejected", "stationary", "insufficient"])
def test_explanations_use_supplied_evidence(tier):
    source = {"id": 7, "tier": tier, "reasons": ["measured quality reason"], "n_epochs": 9, "n_visits": 3,
              "fit": {"pm_total": 8.25, "pm_east": -2.5, "pm_north": 7.8, "pm_east_err": 0.3,
                      "pm_north_err": 0.4, "snr": 20.1, "chi2_red_east": 1.1, "chi2_red_north": 1.2}}
    explanation = interpretation.explain_source(source, {"fid": "test", "band": "D2", "reference_obs_id": "actual-observation"})
    assert explanation["source_id"] == 7 and explanation["tier"] == tier
    assert "8.25" in explanation["explore"][0]["text"]
    assert "-2.50 ± 0.30" in explanation["research"][0]["text"]
    assert "Classification unknown" in explanation["classification"]
    assert "not independent validation" in explanation["research"][-2]["text"]
    assert "actual-observation" in explanation["research"][-1]["text"]
    source["fit"] = None
    assert interpretation.explain_source(source, {"fid": "test", "band": "D2", "reference_obs_id": "obs"})["explore"][0]["evidence"] == "unknown"


def test_blackbody_recovers_colour_temperature_and_excludes_contamination():
    wavelengths = np.linspace(0.8, 4.9, 25)
    fluxes = spectral.planck_fnu(wavelengths, 3500)
    fluxes = fluxes / fluxes.max() * 20
    points = [dict(wavelength_um=float(wavelength), flux_mjy=float(flux), flux_err_mjy=float(flux)*0.04,
                   snr=25, flags=[]) for wavelength, flux in zip(wavelengths, fluxes)]
    model = spectral.blackbody_fit(points)
    assert model["T_color_K"] == pytest.approx(3500, rel=0.015)
    assert model["T_lo_K"] <= 3500 <= model["T_hi_K"]
    contaminated = dict(points[0], flux_mjy=10000, flags=["NONLINEAR"])
    flagged = spectral.blackbody_fit(points+[contaminated])
    assert flagged["T_color_K"] == model["T_color_K"] and flagged["excluded_points"] == 1
    assert spectral.blackbody_fit(points[:5]) is None
    assert spectral.blackbody_fit([dict(point, flags=["GHOST"]) for point in points]) is None
    explanation = interpretation.explain_spectrum({"points": points, "blackbody": model, "rejected": []})
    assert "not the physical effective temperature" in explanation["explore"]
    assert "not detections or abundances" in explanation["research"]


def test_saturated_photometry_refused_and_aperture_method_preserved():
    stars = np.array([[150, 20, 1000]])
    clean = synthetic.render_observation(stars)
    clean.pixel_sr = (6.15/206265)**2
    result = spectral.forced_photometry(clean, 150, 20)
    assert result["ok"] and result["method"] == "aperture_r3px_uncorrected"
    assert result["flux_err_mjy"] > 0 and math.isfinite(result["flux_mjy"])
    saturated = synthetic.render_observation(stars, saturation=1)
    saturated.pixel_sr = clean.pixel_sr
    rejected = spectral.forced_photometry(saturated, 150, 20)
    assert not rejected["ok"] and rejected["saturated"]
    assert "flux_mjy" not in rejected


def test_catalog_match_propagates_east_with_cos_dec():
    track = fieldtrack.SourceTrack(0, 0, 150, 60, 0, 0)
    row = {"ra": 150-10/3600/np.cos(np.radians(60)), "dec": 60+20/3600, "mjd": 60000-365.25}
    match = field._nearest_epoch_matched([row], track, {"pmra": 10000, "pmdec": -20000}, 60000)
    assert match["pm_used_for_match"] and match["separation_arcsec"] < 1e-6
    assert field._nearest_epoch_matched([row], track, None, 60000) is None


def test_cached_spectrum_never_requests_missing_cutouts(monkeypatch):
    from nadir.data import irsa
    from nadir.jobs import Job
    loaded = field.Field("cache-test", 150, 20, 0.2, "D2")
    observation = synthetic.render_observation(np.array([[150,20,1000]]))
    loaded.observations = [observation]
    loaded.usable = [True]
    record = object()
    loaded.records_all = {"D2": [], "D3": [record]}
    monkeypatch.setattr(irsa, "is_cached", lambda *args: False)
    requests = []
    monkeypatch.setattr(irsa, "fetch_cutouts", lambda records, *args, **kwargs: requests.extend(records) or [])
    result = field.load_spectral_set(Job("spectral","cached",["download","read"]), loaded, cached_only=True)
    assert not requests and result["missing"] == 1
    assert loaded.spectral_obs == [("D2", observation)]
