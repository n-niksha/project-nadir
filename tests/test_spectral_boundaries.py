"""Photometry must reject incomplete windows, never invent missing pixels."""
from dataclasses import replace

import numpy as np
import pytest
from astropy.wcs import WCS
from fastapi.testclient import TestClient

from nadir import field
from nadir.api.server import app
from nadir.science import fieldtrack, psf, spectral, synthetic
from nadir.science.observation import Observation, DEFAULT_FLAG_BITS


def observation(x, y, shape=(17, 17), epsf=True):
    wcs = synthetic.make_wcs(150, 20, 17)
    ra, dec = wcs.all_pix2world([[x, y]], 0)[0]
    # Use the recovered subpixel position for exact model/uncertainty assertions.
    x, y = wcs.all_world2pix([[ra, dec]], 0)[0]
    yy, xx = np.indices(shape, dtype=float)
    py, px = np.indices((33, 33), dtype=float)
    model = np.exp(-((px-16)**2+(py-16)**2)/(2*5**2))
    grid = psf.EPSFGrid(np.array([0.]), np.array([0.]), model[None], np.array([1.5]))
    profile = psf.epsf_values(grid.at(0, 0), xx-x, yy-y)
    obs = Observation('boundary', 200*profile+3, np.zeros(shape, dtype=np.int64),
                      np.full(shape, 4.), wcs, dict(DEFAULT_FLAG_BITS),
                      61000., None, 2, 100., 6., 1.5, .03,
                      epsf=grid if epsf else None, wcs_det=WCS(naxis=2) if epsf else None,
                      pixel_sr=1e-9)
    return obs, (ra, dec), profile


@pytest.mark.parametrize('epsf', [True, False])
@pytest.mark.parametrize('xy', [
    (12.8, 8), (8, 12.8), (12.8, 12.8),  # passed old float guard, rounded window overflowed
    (3.2, 8), (8, 3.2), (3.2, 3.2), (3.2, 12.8), (12.8, 3.2),
])
def test_incomplete_edge_and_corner_windows_rejected(xy, epsf):
    obs, sky, _ = observation(*xy, epsf=epsf)
    original = obs.image.copy()
    result = spectral.forced_photometry(obs, *sky)
    assert not result['ok'] and result['reason'] == 'too close to the cutout edge'
    assert 'flux_mjy' not in result and 'flux_err_mjy' not in result
    np.testing.assert_array_equal(obs.image, original)


@pytest.mark.parametrize('xy', [(4.2, 4.2), (12.2, 4.2), (4.2, 12.2), (12.2, 12.2), (12.49, 8)])
def test_complete_edge_windows_preserve_flux_masks_and_covariance(xy):
    obs, sky, profile = observation(*xy)
    x, y = obs.wcs.all_world2pix([sky], 0)[0]
    ix, iy = int(round(x)), int(round(y))
    # Non-core bad pixels must remain excluded from flux and uncertainty.
    obs.image[iy-3, ix] = np.nan
    obs.variance[iy, ix-3] = 0
    obs.flags[iy+3, ix] = 1 << DEFAULT_FLAG_BITS['HOT']
    obs.image[iy+3, ix] = 1e12
    result = spectral.forced_photometry(obs, *sky)
    assert result['ok'] and result['method'] == 'epsf_forced'
    assert result['flux_mjy'] == pytest.approx(200, rel=1e-10)
    sl = np.s_[iy-4:iy+5, ix-4:ix+5]
    good = ~obs.bad_mask[sl]
    p = profile[sl][good]
    # Closed-form variance of a slope with a fitted intercept, sigma^2 = 4.
    expected_error = np.sqrt(4 / np.sum((p-p.mean())**2))
    assert result['flux_err_mjy'] == pytest.approx(expected_error, rel=1e-10)


@pytest.mark.parametrize('shape,xy', [((8, 17), (8, 4.2)), ((17, 8), (4.2, 8)), ((8, 8), (4.2, 4.2))])
def test_partial_cutouts_smaller_than_window_are_rejected(shape, xy):
    obs, sky, _ = observation(*xy, shape=shape)
    result = spectral.forced_photometry(obs, *sky)
    assert not result['ok'] and 'edge' in result['reason']
    assert 'flux_mjy' not in result


@pytest.mark.parametrize('epsf', [True, False])
def test_insufficient_valid_core_pixels_are_not_measured(epsf):
    obs, sky, _ = observation(8, 8, epsf=epsf)
    obs.variance[:] = 0
    obs.variance[8, 8] = 4
    result = spectral.forced_photometry(obs, *sky)
    assert not result['ok'] and result['reason'] == spectral.SAT_REASON
    assert 'flux_mjy' not in result


def test_aperture_with_insufficient_background_pixels_is_rejected():
    obs, sky, _ = observation(8, 8, epsf=False)
    yy, xx = np.indices(obs.image.shape)
    obs.variance[np.hypot(xx-8, yy-8) > 3] = 0
    result = spectral.forced_photometry(obs, *sky)
    assert not result['ok'] and result['reason'] == 'too few usable pixels for aperture photometry'
    assert 'flux_mjy' not in result


@pytest.mark.parametrize('include_valid', [True, False])
def test_spectrum_endpoint_keeps_valid_exposures_and_reports_edge_rejections(monkeypatch, include_valid):
    obs, sky, _ = observation(12.8, 8, shape=(21, 21))
    partial = replace(obs, obs_id='partial', image=obs.image[:17, :17],
                      variance=obs.variance[:17, :17], flags=obs.flags[:17, :17])
    f = field.Field('boundary', *sky, .2, 'D2')
    f.ref = 0
    f.observations = [obs]
    f.tracks = [fieldtrack.SourceTrack(21, 0, *sky, 12.8, 8)]
    f.spectral_obs = [('D2', partial)] + ([('D2', obs)] if include_valid else [])
    monkeypatch.setitem(field._fields, f.fid, f)
    response = TestClient(app).get('/api/fields/boundary/sources/21/spectrum')
    assert response.status_code == 200
    result = response.json()
    assert result['n_measured'] == int(include_valid)
    assert len(result['rejected']) == 1
    assert result['rejected'][0]['reason'] == 'too close to the cutout edge'
    assert 'flux_mjy' not in result['rejected'][0]
    if include_valid:
        assert result['points'][0]['flux_mjy'] == pytest.approx(200)
        assert result['points'][0]['flux_err_mjy'] > 0
