"""Reliability regressions and read-only evidence contract, all offline."""
import copy
import hashlib
import json
import time
from unittest.mock import patch
import numpy as np
import pytest
from fastapi.testclient import TestClient
from nadir import field
from nadir.science.synthetic import render_observation
from nadir.science.fieldtrack import SourceTrack
from nadir.data import irsa
from nadir.jobs import Job,JobManager
from nadir.evidence import investigation_report,canonical_json


def sample_field():
    f=field.Field('evidence-test',150.,20.,.2,'D2')
    f.observations=[render_observation([[150,20,300]],n=31,obs_id='test-real-layout')]
    f.usable=[True];f.unusable_reason=[''];f.ref=0;f.reg_stats={0:{'ok':True,'reference':True}}
    f.tracks=[SourceTrack(4,0,150.,20.,15.,15.)]
    f.built_at='2026-11-14T00:00:00Z'
    return f


def test_report_is_deterministic_and_never_fits_or_fetches(monkeypatch):
    f=sample_field();before=copy.deepcopy(f.tracks[0].__dict__)
    with patch('requests.sessions.Session.request',side_effect=AssertionError('network forbidden')):
        a=investigation_report(f,4);b=investigation_report(f,4)
    assert a==b and f.tracks[0].__dict__==before
    digest=a.pop('report_sha256')
    assert hashlib.sha256(canonical_json(a).encode()).hexdigest()==digest
    assert a['classification']['experimental_results_included'] is False
    assert a['motion']['measurement'] is None
    assert a['spectrum']['state']=='not_computed'
    first=a['source']['source_key'];f.observations[0].obs_id='different-exposure'
    assert investigation_report(f,4)['source']['source_key']!=first


def test_evidence_api_download_and_missing_ids(monkeypatch):
    from nadir.api.server import app
    f=sample_field();monkeypatch.setattr(field,'get_field',lambda fid:f if fid==f.fid else None)
    c=TestClient(app);path=f'/api/science/fields/{f.fid}/sources/4/evidence'
    r=c.get(path);assert r.status_code==200
    assert r.headers['etag'].strip('"')==r.json()['report_sha256']
    m=c.get(path+'?format=markdown');assert m.status_code==200
    assert 'attachment' in m.headers['content-disposition']
    assert 'No confirmed discovery claim' in m.text
    assert c.get(path+'?format=html').status_code==422
    assert c.get(path.replace('/4/','/999/')).status_code==404
    assert c.get(path.replace(f.fid,'missing')).status_code==404


def test_incomplete_catalog_motion_and_unloaded_catalog_spectrum():
    f=sample_field();t=f.tracks[0]
    t.catalog={'gaia':{'pmra':100.,'pmdec':None}}
    f.spectral_obs=[]
    assert field.source_spectrum(f,4)['pm_used'] is None
    del t.catalog;f.spectra.clear()
    assert field.source_spectrum(f,4)['n_measured']==0
    assert field._nearest_epoch_matched([],t,{'pmra':100.,'pmdec':None},61000) is None


def test_catalog_retry_is_possible_and_stale_history_clears(monkeypatch):
    from nadir.api.server import app
    f=sample_field();f.catalogs={'data':{},'errors':{'gaia':'timeout'}}
    f.tracks[0].history=[{'catalog':'old'}]
    field._crossmatch(f,{})
    assert f.tracks[0].history is None and f.tracks[0].catalog is None
    monkeypatch.setattr(field,'get_field',lambda fid:f)
    monkeypatch.setattr(field,'load_catalogs',lambda job,f:{'fid':f.fid,'errors':{}})
    r=TestClient(app).post(f'/api/fields/{f.fid}/catalogs')
    assert r.status_code==200 and r.json()['ready'] is False


def test_job_machine_readable_no_data_failure():
    manager=JobManager(workers=1)
    def fail(job):raise irsa.DataAccessError('No epochs',code='insufficient_epochs',retryable=False)
    job=manager.submit('field','missing',['discover'],fail)
    for _ in range(100):
        if job.status=='error':break
        time.sleep(.01)
    snap=job.snapshot()
    assert snap['error']=='No epochs'
    assert snap['failure']=={'code':'insufficient_epochs','retryable':False,'stage':'discover','message':'No epochs'}


def test_tap_timeout_is_actually_forwarded():
    from nadir.data.http import TimeoutSession
    import requests
    with patch.object(requests.Session,'request',return_value=object()) as call:
        TimeoutSession(7).get('https://example.invalid',timeout=None)
        assert call.call_args.kwargs['timeout']==7
        TimeoutSession(7).get('https://example.invalid',timeout=3)
        assert call.call_args.kwargs['timeout']==3


def test_stale_query_cache_recovers_without_fabricating(tmp_path,monkeypatch):
    from nadir import config
    monkeypatch.setattr(config,'CACHE_DIR',tmp_path)
    rows,_=irsa.cached_query('test',lambda:[{'measurement':12}],'fake',0)
    def outage():raise irsa.DataAccessError('timeout')
    recovered,prov=irsa.cached_query('test',outage,'fake',0)
    assert recovered==rows and prov['stale'] and prov['archive_error']=='timeout'
    with pytest.raises(irsa.DataAccessError):irsa.cached_query('uncached',outage,'fake',0)


def test_no_epoch_field_has_availability_code(monkeypatch):
    monkeypatch.setattr(irsa,'find_all_bands',lambda *a,**k:({'D2':[]},{}))
    with pytest.raises(irsa.DataAccessError) as exc:
        field.build_field(Job('field','none',field.STAGES),151,21,.2,'D2')
    assert exc.value.code=='no_observations' and exc.value.retryable is False


def test_spectral_failure_details_survive_partial_download(monkeypatch):
    f=sample_field()
    record=irsa.ArchiveRecord('fake','D3',61000,61001,'fake','','')
    f.records_all={'D2':[],'D3':[record]}
    monkeypatch.setattr(irsa,'is_cached',lambda *a:False)
    monkeypatch.setattr(irsa,'fetch_cutouts',lambda *a,**k:[{'record':record,'error':'service busy'}])
    result=field.load_spectral_set(Job('spectral','test',['download','read']),f)
    assert result['failures']==1 and result['failure_details'][0]['code']=='cutout_fetch_failed'
    assert len(f.spectral_obs)==1  # usable native-band observation retained


def test_missing_timestamp_and_partial_coverage_are_data_limits():
    from nadir.science.pairing import is_usable
    obs=render_observation([[150,20,100]],n=31)
    obs.mjd=None
    assert is_usable(obs,31**2)==(False,'no timestamp')
    obs.mjd=61000
    assert is_usable(obs,117**2)==(False,'cutout truncated by detector edge')


def test_all_catalog_failures_are_optional(monkeypatch):
    from nadir.data import catalogs
    f=sample_field()
    def failed(*a,**k):raise irsa.DataAccessError('simulated outage')
    for name in ('gaia_cone','simbad_region','twomass_cone','allwise_cone'):monkeypatch.setattr(catalogs,name,failed)
    before=copy.deepcopy(f.tracks[0].fit)
    result=field.load_catalogs(Job('catalogs','outage',['catalogs']),f)
    assert len(result['errors'])==4 and f.tracks[0].fit==before
    assert investigation_report(f,4)['failure_reasons'][1]['code']=='catalog_query_failed'
