"""Algorithm validation against synthetic images with known truth.

These tests are deliberately independent of real SPHEREx data: every number
asserted here comes from a controlled, injected shift.
"""
import numpy as np
import pytest

from nadir.science import motion, synthetic
from nadir.science.difference import difference_image
from nadir.science.registration import cross_correlation_shift, fit_transform, match_sources
from nadir.science.reprojection import make_grid, reproject_observation
from nadir.science.sources import detect_sources

RA0, DEC0, N = 150.0, 20.0, 101
SIZE = N * 6.15 / 3600.0


def field(n=60, seed=1):
    rng = np.random.default_rng(seed)
    return synthetic.random_field(n, RA0, DEC0, SIZE * 0.45, rng)


def shifted(stars, index, east_arcsec, north_arcsec):
    out = stars.copy()
    out[index, 0] += east_arcsec / 3600.0 / np.cos(np.radians(DEC0))
    out[index, 1] += north_arcsec / 3600.0
    return out


def isolated_index(stars, min_sep_arcsec=40.0):
    """A bright star with no neighbour within min_sep (so the truth is unambiguous)."""
    for i in np.argsort(-stars[:, 2]):
        d = np.hypot((stars[:, 0] - stars[i, 0]) * np.cos(np.radians(DEC0)), stars[:, 1] - stars[i, 1]) * 3600
        d[i] = np.inf
        edge = max(abs(stars[i, 0] - RA0) * np.cos(np.radians(DEC0)), abs(stars[i, 1] - DEC0))
        if d.min() > min_sep_arcsec and edge < SIZE * 0.35:
            return int(i)
    raise RuntimeError("no isolated star in synthetic field")


def test_centroid_accuracy_on_undersampled_psf():
    stars = np.array([[RA0 + dx / 3600, DEC0 + dy / 3600, 3000.0]
                      for dx, dy in [(-120, -90), (-31.3, 47.9), (55.5, -12.2), (101.1, 99.9), (10.7, 140.3)]])
    obs = synthetic.render_observation(stars, n=N, seed=3)
    src = [s for s in detect_sources(obs) if s.fit_ok]
    assert len(src) == len(stars)
    truth = synthetic.make_wcs(RA0, DEC0, N).all_world2pix(stars[:, :2], 0)
    for s in src:
        d = np.hypot(truth[:, 0] - s.x, truth[:, 1] - s.y).min()
        assert d < 0.03, f"centroid error {d:.3f} px"
        assert s.x_err < 0.05 and not s.saturated


def test_centroid_errors_are_statistically_honest():
    """Pull distribution of (measured - true)/sigma should have ~unit width."""
    pulls = []
    truth_wcs = synthetic.make_wcs(RA0, DEC0, N)
    for seed in range(8):
        stars = field(25, seed=100 + seed)
        stars[:, 2] = 400.0
        obs = synthetic.render_observation(stars, n=N, seed=seed)
        txy = truth_wcs.all_world2pix(stars[:, :2], 0)
        for s in detect_sources(obs):
            if not s.fit_ok or s.blended:
                continue
            d = np.hypot(txy[:, 0] - s.x, txy[:, 1] - s.y)
            k = d.argmin()
            if d[k] < 0.5 and np.sort(d)[1] > 4:  # isolated
                pulls.append((s.x - txy[k, 0]) / s.x_err)
    pulls = np.array(pulls)
    assert len(pulls) > 80
    assert 0.7 < 1.4826 * np.median(np.abs(pulls - np.median(pulls))) < 1.5


def test_match_and_transform_recover_known_shift_rotation_with_outliers():
    rng = np.random.default_rng(0)
    a = rng.uniform(0, 200, (80, 2))
    theta = np.radians(0.3)
    R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    t = np.array([0.7, -0.4])
    b = (a - 100) @ np.linalg.inv(R).T + 100 - t + rng.normal(0, 0.02, a.shape)  # so R(b-100)+100+t ~ a
    b[:6] += rng.uniform(1.0, 2.0, (6, 2))  # six "moving" outliers
    pairs = match_sources(a, b, 4.0)
    assert len(pairs) >= 78
    tf = fit_transform(b[pairs[:, 1]], a[pairs[:, 0]])
    assert tf.model == "similarity" and tf.converged
    assert tf.n_used <= len(pairs) - 6
    assert abs(tf.rotation_deg - 0.3) < 0.02
    centre = np.array([[100.0, 100.0]])
    np.testing.assert_allclose(tf.apply(centre) - centre, [t], atol=0.02)
    assert tf.rms_x < 0.04 and tf.rms_y < 0.04
    assert not tf.inliers[np.isin(pairs[:, 0], np.arange(6))].any()


def test_transform_requires_three_references():
    with pytest.raises(ValueError):
        fit_transform(np.zeros((2, 2)), np.zeros((2, 2)))


def test_cross_correlation_sign_and_magnitude():
    rng = np.random.default_rng(5)
    base = rng.normal(0, 1, (128, 128))
    from scipy import ndimage
    base = ndimage.gaussian_filter(base, 2.0)
    moved = ndimage.shift(base, (-1.5, 2.25), order=3, mode="wrap")  # b(y, x) = base(y+1.5, x-2.25)
    dx, dy = cross_correlation_shift(base, moved)
    # p_A = p_B + (dx, dy): content of B must move by (-2.25, +1.5) to land on A
    assert abs(dx + 2.25) < 0.15 and abs(dy - 1.5) < 0.15


@pytest.mark.parametrize("east,north", [(0.0, 11.0), (-6.0, 3.0), (2.0, -2.5)])
def test_injected_motion_is_recovered(east, north):
    stars = field()
    mover = isolated_index(stars)
    obs_a = synthetic.render_observation(stars, n=N, mjd=61000.0, seed=1, obs_id="A")
    # Epoch B: different roll angle AND a 2.5" WCS pointing error that registration must absorb.
    obs_b = synthetic.render_observation(shifted(stars, mover, east, north), n=N, mjd=61365.25, seed=2,
                                         rotation_deg=1.5, wcs_error_arcsec=(2.0, -1.5), obs_id="B")
    grid = make_grid(RA0, DEC0, SIZE)
    res = motion.analyze_pair(obs_a, obs_b, grid)
    assert res.ok and abs(res.dt_days - 365.25) < 1e-6
    # registration found and removed the injected WCS error (to well under 0.1 px)
    assert res.registration["rms_native_px"] < 0.05
    gx, gy = grid.world_to_pixel(stars[mover, 0], stars[mover, 1])[0]
    hit = min(res.sources, key=lambda s: np.hypot(s.x - gx, s.y - gy))
    assert np.hypot(hit.x - gx, hit.y - gy) < 1.0
    assert hit.status == "candidate_motion", hit.reasons
    assert abs(hit.d_east_arcsec - east) < max(3 * hit.sigma_east_arcsec, 0.3)
    assert abs(hit.d_north_arcsec - north) < max(3 * hit.sigma_north_arcsec, 0.3)
    assert abs(hit.d_east_arcsec - east) < 0.5 and abs(hit.d_north_arcsec - north) < 0.5
    false_pos = [s for s in res.sources if s.status == "candidate_motion" and s is not hit]
    assert not false_pos


def test_no_motion_yields_no_candidates():
    stars = field(seed=7)
    a = synthetic.render_observation(stars, n=N, mjd=61000.0, seed=11)
    b = synthetic.render_observation(stars, n=N, mjd=61200.0, seed=12, wcs_error_arcsec=(-1.0, 3.0))
    res = motion.analyze_pair(a, b, make_grid(RA0, DEC0, SIZE))
    assert res.ok
    assert [s for s in res.sources if s.status == "candidate_motion"] == []
    refs = [s for s in res.sources if s.is_reference]
    assert len(refs) > 30
    pulls = np.array([s.d_east_arcsec / s.sigma_east_arcsec for s in refs])
    assert np.abs(pulls).max() < 5


def test_sub_threshold_motion_is_not_claimed():
    """A real but tiny shift (~1 sigma of the centroid noise) must not be reported as motion."""
    stars = field(seed=4)
    stars[:, 2] = np.clip(stars[:, 2], None, 600.0)
    faint = isolated_index(stars)
    kw = dict(n=N, read_noise=8.0)  # very noisy images
    a = synthetic.render_observation(stars, mjd=61000.0, seed=21, **kw)
    grid = make_grid(RA0, DEC0, SIZE)
    probe = motion.analyze_pair(a, synthetic.render_observation(stars, mjd=61300.0, seed=23, **kw), grid)
    sigma = probe.registration["rms_east_arcsec"]
    assert sigma > 0.15  # arcsec; the injected shift below equals this 1-sigma scatter
    b = synthetic.render_observation(shifted(stars, faint, sigma, 0.0), mjd=61300.0, seed=22, **kw)
    res = motion.analyze_pair(a, b, grid)
    assert res.ok and not [s for s in res.sources if s.status == "candidate_motion"]


def test_saturated_mover_is_flagged_low_confidence():
    stars = field(seed=9)
    mover = isolated_index(stars)
    stars[mover, 2] = 2.0e5  # far brighter than everything else
    a = synthetic.render_observation(stars, n=N, mjd=61000.0, seed=31, saturation=2000.0)
    b = synthetic.render_observation(shifted(stars, mover, 0.0, 12.0), n=N, mjd=61365.0, seed=32, saturation=2000.0)
    grid = make_grid(RA0, DEC0, SIZE)
    res = motion.analyze_pair(a, b, grid)
    assert res.ok
    gx, gy = grid.world_to_pixel(stars[mover, 0], stars[mover, 1])[0]
    hit = min(res.sources, key=lambda s: np.hypot(s.x - gx, s.y - gy))
    assert hit.a["saturated"] and hit.a["method"] == "moffat_wings"
    assert hit.status == "candidate_motion_low_confidence"
    assert any("saturated" in r for r in hit.reasons)
    assert not hit.is_reference
    assert hit.sigma_north_arcsec >= motion.CORE_MASKED_FLOOR_PX * 6.15 * 0.99
    assert abs(hit.d_north_arcsec - 12.0) < 3 * hit.sigma_north_arcsec


def test_unmeasurable_field_reports_failure_instead_of_numbers():
    stars = np.array([[RA0, DEC0, 2000.0], [RA0 + 0.02, DEC0 + 0.02, 2000.0]])
    a = synthetic.render_observation(stars, n=N, mjd=61000.0, seed=1)
    b = synthetic.render_observation(stars, n=N, mjd=61100.0, seed=2)
    res = motion.analyze_pair(a, b, make_grid(RA0, DEC0, SIZE))
    assert not res.ok and res.sources == [] and "Too few" in res.message


def test_missing_timestamp_is_refused():
    stars = field()
    a = synthetic.render_observation(stars, n=N, seed=1)
    b = synthetic.render_observation(stars, n=N, seed=2)
    b.mjd = None
    assert not motion.analyze_pair(a, b, make_grid(RA0, DEC0, SIZE)).ok


def test_reprojection_aligns_rotated_images_and_difference_is_clean():
    stars = field(seed=3)
    a = synthetic.render_observation(stars, n=N, seed=41)
    b = synthetic.render_observation(stars, n=N, seed=42, rotation_deg=3.0)
    grid = make_grid(RA0, DEC0, SIZE * 0.6)
    ra_, rb_ = reproject_observation(a, grid), reproject_observation(b, grid)
    d = difference_image(ra_, rb_)
    assert d["valid_fraction"] > 0.95
    # residuals of undersampled stars remain, but the bulk must cancel
    peak = np.nanmax(ra_.image)
    assert np.nanpercentile(np.abs(d["diff"]), 99) < 0.2 * peak
    assert abs(d["median"]) < 0.02


def test_reprojection_shift_moves_content_by_requested_pixels():
    stars = np.array([[RA0, DEC0, 5000.0]])
    obs = synthetic.render_observation(stars, n=41, seed=1)
    grid = make_grid(RA0, DEC0, 41 * 6.15 / 3600 * 0.8)
    r0 = reproject_observation(obs, grid).image
    r1 = reproject_observation(obs, grid, (4.0, -2.0)).image
    y0, x0 = np.unravel_index(np.nanargmax(r0), r0.shape)
    y1, x1 = np.unravel_index(np.nanargmax(r1), r1.shape)
    assert (x1 - x0, y1 - y0) == (4, -2)


def test_track_recovers_injected_proper_motion():
    stars = field(seed=5)
    mover = isolated_index(stars)
    pm_e, pm_n = -0.8, 10.4  # arcsec / yr, Barnard-like
    obs, det = [], []
    for k, mjd in enumerate([61000, 61001, 61003, 61180, 61182, 61183, 61365, 61366, 61368]):
        yr = (mjd - 61000) / 365.25
        o = synthetic.render_observation(shifted(stars, mover, pm_e * yr, pm_n * yr), n=N, mjd=float(mjd),
                                         seed=50 + k, rotation_deg=(k % 3) * 0.5, obs_id=f"T{k}")
        obs.append(o), det.append(detect_sources(o))
    out = motion.track_source(obs, 0, make_grid(RA0, DEC0, SIZE), stars[mover, 0], stars[mover, 1], det)
    assert len(out["points"]) == 9 and out["fit"] is not None
    fe, fn = out["fit"]["east"], out["fit"]["north"]
    assert abs(fe["pm_arcsec_per_yr"] - pm_e) < max(4 * fe["pm_err_arcsec_per_yr"], 0.1)
    assert abs(fn["pm_arcsec_per_yr"] - pm_n) < max(4 * fn["pm_err_arcsec_per_yr"], 0.1)
    assert fn["pm_err_arcsec_per_yr"] < 0.5


def test_track_declines_to_fit_short_baseline():
    stars = field(seed=5)
    obs = [synthetic.render_observation(stars, n=N, mjd=61000.0 + k, seed=k, obs_id=f"S{k}") for k in range(4)]
    det = [detect_sources(o) for o in obs]
    i = isolated_index(stars)
    out = motion.track_source(obs, 0, make_grid(RA0, DEC0, SIZE), stars[i, 0], stars[i, 1], det)
    assert out["fit"] is None and "30 days" in out["fit_note"]


def test_predicted_displacement_matches_barnards_star_catalog_values():
    star = {"ra": 269.4485, "dec": 4.7394, "pmra": -801.551, "pmdec": 10362.394, "parallax": 546.976}
    p = motion.predicted_displacement(star, 60912.7, 60912.7 + 365.25)
    # exactly one year apart: parallax cancels, pure proper motion remains
    assert abs(p["east_arcsec"] + 0.8016) < 0.02 and abs(p["north_arcsec"] - 10.362) < 0.02
    half = motion.predicted_displacement(star, 60912.7, 60912.7 + 182.6)
    assert 0.3 < abs(half["parallax_east_arcsec"]) <= 2 * 0.547 + 0.01
    pe, pn = motion.parallax_factors(269.45, 4.74, 60912.7)
    assert np.hypot(pe, pn) <= 1.02
