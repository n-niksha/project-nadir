import numpy as np
import pytest
from nadir.science.deblend import Stamp, response, compare_sources, fit_model
from nadir.science.reconstruction import coadd
from nadir.ml.evidence import screen, extract_features


def make_stamp(separation=0,ratio=1,seed=1,flux=500,shift=(0.,0.),sigma=.8):
    s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),str(seed),shift,sigma)
    positions=[(5-separation/2,5),(5+separation/2,5)]
    s.image=3+flux/(1+ratio)*(response(s,*positions[0])+ratio*response(s,*positions[1]))
    s.image+=np.random.default_rng(seed).normal(size=s.image.shape)
    return s


def test_deblend_recovers_truth_and_conservative_without_bootstrap():
    r=compare_sources([make_stamp(2, .5)])
    assert r['state']=='ambiguous'
    assert abs(r['separation_px']-2)<.15
    assert abs(r['flux_ratio']-.5)<.1
    assert r['evidence']['delta_bic']>10
    assert r['maturity']=='experimental'


def test_single_and_bad_data():
    r=compare_sources([make_stamp()])
    assert r['state']=='consistent_with_one_source'
    s=make_stamp();s.good[:]=False
    assert compare_sources([s])['state']=='insufficient_data'
    s=make_stamp(2);s.flags=['OVERFLOW']
    assert compare_sources([s])['state']=='invalid_or_contaminated_observation'
    with pytest.raises(ValueError):compare_sources([s,s])


def test_joint_fitting_shifts_and_independent_flux():
    a=make_stamp(seed=10,shift=(.2,-.3),flux=500)
    b=make_stamp(seed=11,shift=(-.4,.1),flux=1000)
    r=fit_model([a,b])
    assert np.linalg.norm(np.array(r['positions'][0])-[5,5])<.03
    assert abs(r['frames'][1]['flux'][0]/r['frames'][0]['flux'][0]-2)<.1


def test_coadd_exact_weighted_mean_and_covariance():
    a=make_stamp();b=make_stamp(seed=2)
    b.variance*=4
    r=coadd([a,b])
    assert np.allclose(r['image'],(a.image+b.image/4)/1.25)
    assert np.allclose(r['variance'],.8)
    a.shift=(.25,.25)
    r=coadd([a,b],2)
    v=np.r_[a.variance.ravel(),b.variance.ravel()]
    covariance=(r['operator'].multiply(v)@r['operator'].T).toarray()
    assert np.allclose(np.diag(covariance)[r['covered'].ravel()],r['variance'][r['covered']])
    assert np.any(np.triu(covariance,1)>0)
    assert r['metadata']['optical_super_resolution'] is False


def test_features_missing_and_multiple_testing():
    r=screen({'motion':.004,'variability':None})
    assert r['adjusted_tail']==.012
    assert not r['is_candidate']
    assert screen({})['state']=='insufficient_data'
    assert not screen({'motion':0.},contaminated=True)['is_candidate']
    assert extract_features({})[0]['value'] is None
    with pytest.raises(ValueError):screen({'motion':float('nan')})


def test_bootstrap_decision_reproducible_and_null_not_overstated():
    s=make_stamp(2,.5,seed=31)
    r=compare_sources([s],bootstrap=39,seed=19)
    assert r['state']=='multiple_source_model_favored'
    assert r['evidence']['bootstrap_p']>=1/40
    assert r['evidence']['bootstrap_n']==39
    assert r['uncertainty'] is None
    null=compare_sources([make_stamp(seed=31)],bootstrap=3,seed=19)
    assert null['state']!='multiple_source_model_favored'
    with pytest.raises(ValueError):compare_sources([s],bootstrap=200)


def test_broadened_psf_is_not_a_binary_claim():
    s=make_stamp(sigma=1.1)
    s.sigma=.8
    r=compare_sources([s])
    assert r['state']!='multiple_source_model_favored'
    assert r['models']['broadened_one']['bic']<r['models']['two']['bic']


def test_masked_nonfinite_pixels_never_enter_likelihood():
    a=make_stamp(2,.5);a.good[0,0]=False
    b=make_stamp(2,.5);b.good[0,0]=False;b.image[0,0]=np.nan;b.variance[0,0]=np.nan
    assert np.allclose(fit_model([a],2)['positions'],fit_model([b],2)['positions'])
    b.variance[2,2]=0
    assert np.isfinite(fit_model([b],1)['chi2'])


def test_science_api_contract_and_cache(monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from nadir.api.server import app
    from nadir.api.science import DecompositionResult
    from nadir.field import Field
    from nadir.science.synthetic import render_observation
    from nadir import field
    f=Field('inverse-test',150,20,.2,'D2')
    f.observations=[render_observation([[150,20,300]],n=31)]
    monkeypatch.setattr(field,'get_field',lambda fid:f if fid==f.fid else None)
    client=TestClient(app)
    assert client.get('/api/science/fields/missing/diagnostics').status_code==404
    assert client.get(f'/api/science/fields/{f.fid}/diagnostics').json()['observations'][0]['state']=='missing_metadata'
    route=f'/api/science/fields/{f.fid}/decomposition'
    req=dict(observation_index=0,ra=150,dec=20)
    assert client.post(route,json=dict(req,bootstrap=200)).status_code==422
    assert client.post(route,json=dict(req,observation_index=1)).status_code==404
    job=client.post(route,json=req).json()['job']
    for _ in range(100):
        job=client.get('/api/jobs/'+job['id']).json()
        if job['status'] in ('done','failed'):break
        time.sleep(.01)
    assert job['status']=='done',job
    result=DecompositionResult.model_validate(job['result'])
    assert result.state=='insufficient_data'
    assert len(f.decomposition_cache)==1
    second=client.post(route,json=req).json()['job']
    assert second['id']==job['id']


def test_missing_and_contaminated_synthetic_target_matrix():
    from nadir.science.synthetic import render_observation
    from nadir.science.diagnostics import observation_diagnostics
    obs=render_observation([[150,20,1000]],n=31,saturation=10)
    diag=observation_diagnostics(obs,150,20,2)
    assert diag['state']=='contamination' and diag['saturated']
    assert any('reference' in s for s in diag['reasons'])
    assert observation_diagnostics(obs,180,20)['state']=='missing_data'
    obs.wcs.wcs.crval=[np.nan,20]
    assert observation_diagnostics(obs,150,20)['state']=='software_or_metadata_failure'


def test_forced_blend_flux_and_degeneracy():
    from nadir.science.photometry import forced_components
    s=make_stamp(1.5,.5,flux=900,seed=12)
    single=forced_components(s,[(4.25,5)])
    joint=forced_components(s,[(4.25,5),(5.75,5)])
    assert abs(joint['flux'][0]-600)<15
    assert abs(joint['flux'][0]-600)<abs(single['flux'][0]-600)
    assert np.asarray(joint['covariance'])[0,1]<0
    assert forced_components(s,[(5,5),(5,5)])['state']=='degenerate'


def test_blackbody_excludes_model_mismatch():
    from nadir.science.spectral import blackbody_fit
    points=[dict(wavelength_um=1+i*.3,flux_mjy=100,flux_err_mjy=1,snr=100,quality='model_mismatch') for i in range(10)]
    assert blackbody_fit(points) is None


def test_elliptical_psf_false_split_regression():
    from scipy.ndimage import gaussian_filter
    s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),'elliptical')
    s.image=3+gaussian_filter(500*response(s,5.1,4.8),(.1,.6))+np.random.default_rng(567).normal(size=(11,11))
    r=compare_sources([s],bootstrap=39,seed=0)
    assert r['evidence']['bootstrap_p']<=.05
    assert r['evidence']['delta_bic']>100
    assert r['state']=='ambiguous'
    assert r['evidence']['elliptical_minus_two_bic']<6


def test_elliptical_integrator_matches_circular_and_conserves_flux():
    s=make_stamp()
    a=response(s,5.2,4.7,.4)
    b=response(s,5.2,4.7,(.4,.4,.7))
    assert np.allclose(a,b,atol=1e-7)
    assert abs(b.sum()-1)<1e-6
