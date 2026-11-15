#!/usr/bin/env python
"""Bounded real-target validation. Acquire once, then replay without network.

--acquire resolves four uncached targets and selects six primary-band exposures
plus two other-band exposures per target (maximum 32 new cutouts). Existing
Barnard/Teegarden data are read from --existing-cache, never written there.
--offline replays the manifest with every Requests network call blocked.
"""
import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['NADIR_DETECT_PROCESSES']='1'
from nadir import config,field
from nadir.data import irsa,catalogs
from nadir.jobs import Job
from nadir.evidence import investigation_report
from nadir.science.diagnostics import decompose_observation

OUT=Path('docs/validation')
TARGETS=[('barnard',"Barnard's Star",'D2'),('epsilon_indi_b','eps Ind B','D2'),
         ('wise0855','WISE J085510.83-071442.5','D6'),('rw_aur','RW Aur','D2'),
         ('r_dra','R Dra','D2'),('teegarden',"Teegarden's Star",'D2')]


def sample(items,n):
    return [items[i] for i in np.unique(np.linspace(0,len(items)-1,min(n,len(items))).astype(int))] if items else []


def acquire(existing):
    OUT.mkdir(parents=True,exist_ok=True)
    (config.CACHE_DIR/'queries').mkdir(parents=True,exist_ok=True)
    for p in (existing/'queries').glob('*.json'):
        dest=config.CACHE_DIR/'queries'/p.name
        if not dest.exists():shutil.copyfile(p,dest)
    result=[]
    for key,name,band in TARGETS:
        row=dict(key=key,name=name,band=band,size=.2,files=[],failures=[],timings={})
        start=time.perf_counter()
        if key in ('barnard','teegarden'):
            preset=next(p for p in config.PRESETS if p['id']==key)
            row.update(ra=preset['ra'],dec=preset['dec'],position_epoch=2026.0,position_source='existing verified preset')
        else:
            try:
                identity=catalogs.resolve_name(name,epoch_year=2026.0)
                row.update(ra=identity['ra'],dec=identity['dec'],position_epoch=identity['epoch'],identity=identity,
                           position_source='SIMBAD TAP; linear proper motion to 2026.0')
            except Exception as exc:
                row['failures'].append(dict(stage='resolve',code='name_resolution_failed',message=str(exc)))
                result.append(row);continue
        row['timings']['resolve_s']=time.perf_counter()-start
        start=time.perf_counter()
        try:
            # One SIA query; no six-band TAP fallback during bounded acquisition.
            query_key=f"SIA spherex dr1+qr3 {row['ra']:.6f} {row['dec']:.6f}"
            rows,prov=irsa.cached_query(query_key,lambda:irsa._sia_sync(row['ra'],row['dec']),config.IRSA_SIA,config.TAP_CACHE_TTL_S)
            records=[irsa._record_from_sia(r) for r in rows]
            grouped={b:[] for b in config.BANDS}
            unique={}
            for r in records:
                k=(r.obs_id,r.band)
                if k not in unique or r.release=='dr1':unique[k]=r
            for r in unique.values():grouped[r.band].append(r)
            for v in grouped.values():v.sort(key=lambda r:r.mjd_start)
            row['discovery']={'provenance':prov,'counts':{b:len(v) for b,v in grouped.items()},
                              'mjd_range':[min(r.mjd_start for r in records),max(r.mjd_start for r in records)] if records else None}
        except Exception as exc:
            row['failures'].append(dict(stage='discovery',code='archive_discovery_failed',message=str(exc)))
            grouped={b:[] for b in config.BANDS}
        row['timings']['discovery_s']=time.perf_counter()-start
        available={}
        for root in (existing,config.CACHE_DIR):
            for p in (root/'cutouts').glob('*.json'):
                try:
                    meta=json.loads(p.read_text());req=meta.get('request',{})
                    if abs(req.get('ra_deg',-999)-row['ra'])<1e-6 and abs(req.get('dec_deg',-999)-row['dec'])<1e-6 and req.get('size_deg')==.2 and p.with_suffix('.fits').exists():
                        available[meta['source_url'].split('?')[0].removeprefix(config.IRSA_BASE)]=(p.with_suffix('.fits'),meta)
                except (ValueError,KeyError):continue
        if key in ('barnard','teegarden'):
            # Prefer available cached primary observations for an offline judge run.
            primary=[r for r in grouped[band] if r.uri in available]
            selected=sample(primary,12)
            extras=[r for b,v in grouped.items() if b!=band for r in v if r.uri in available]
            selected+=sample(extras,6)
        else:
            selected=sample(grouped[band],6)
            extras=[r for b,v in grouped.items() if b!=band for r in v]
            selected+=sample(extras,2)
        start=time.perf_counter()
        for rec in selected:
            try:
                if rec.uri in available:
                    path,prov=available[rec.uri];cached=True
                else:
                    # Fetch one cutout at a time; bounded retries set in main.
                    path,prov=irsa.fetch_cutout(rec,row['ra'],row['dec'],.2);cached=False
                row['files'].append(dict(record=rec.__dict__,path=str(path),provenance=prov,already_cached=cached))
            except Exception as exc:
                row['failures'].append(dict(stage='cutout',uri=rec.uri,code='cutout_unavailable',message=str(exc)))
        row['timings']['cutouts_s']=time.perf_counter()-start
        result.append(row)
        (OUT/'targets_manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        print(key,'files',len(row['files']),'failures',len(row['failures']),flush=True)
    (OUT/'targets_manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


def validate(manifest,offline):
    from fastapi.testclient import TestClient
    from nadir.api.server import app
    client=TestClient(app)
    results=[]
    for row in manifest:
        result={k:row.get(k) for k in ('key','name','ra','dec','band','discovery','failures','timings')}
        files=row['files'];grouped={b:[] for b in config.BANDS};lookup={}
        for item in files:
            rec=irsa.ArchiveRecord(**item['record']);grouped[rec.band].append(rec);lookup[rec.uri]=item
        def fetch(records,*args,**kwargs):
            return [dict(record=r,path=Path(lookup[r.uri]['path']),provenance=lookup[r.uri]['provenance'],error=None) for r in records]
        def is_cached(rec,*args,**kwargs):return rec.uri in lookup
        start=time.perf_counter()
        try:
            if 'ra' not in row:raise irsa.DataAccessError('Unresolved target',code='name_resolution_failed',retryable=True)
            with patch.object(irsa,'find_all_bands',return_value=(grouped,{'validation_manifest':True,'original':row.get('discovery',{})})),patch.object(irsa,'fetch_cutouts',side_effect=fetch),patch.object(irsa,'is_cached',side_effect=is_cached):
                built=field.build_field(Job('field',row['key'],field.STAGES),row['ra'],row['dec'],.2,row['band'])
                f=field.get_field(built['fid']);result['build_s']=time.perf_counter()-start
                field.load_catalogs(Job('catalogs',row['key'],['catalogs']),f)
                field.load_spectral_set(Job('spectral',row['key'],['download','read']),f,cached_only=True)
            result.update(state='field_built',epochs=len(f.observations),usable_epochs=sum(f.usable),
                          wavelengths_um=[o.wavelength_um for o in f.observations],registration=f.reg_stats,
                          unusable_reasons=f.unusable_reason,field_timings=f.timings,catalog_errors=f.catalogs['errors'],
                          tier_counts={tier:sum(t.tier==tier for t in f.tracks) for tier in set(t.tier for t in f.tracks)})
            ra,dec=row['ra'],row['dec'];ident=row.get('identity',{})
            if ident.get('pmra_masyr') is not None and ident.get('pmdec_masyr') is not None:
                dt=2000+(f.observations[f.ref].mjd-51544.5)/365.25-row['position_epoch']
                ra+=ident['pmra_masyr']*dt/3.6e6/np.cos(np.radians(dec));dec+=ident['pmdec_masyr']*dt/3.6e6
            if not f.tracks:raise ValueError('No measurable tracks in field')
            target=min(f.tracks,key=lambda t:float(field._sep_arcsec(t.ra,t.dec,ra,dec)))
            distance=float(field._sep_arcsec(target.ra,target.dec,ra,dec));result['nearest_track_arcsec']=distance
            if distance>6.15:
                result['target_state']='not_detected_within_one_native_pixel'
            else:
                result['target_state']='provisional_position_match_not_independent_identity_confirmation'
                spectrum=field.source_spectrum(f,target.id)
                result['source']=f.source_summary(target)
                result['spectrum']={'n_measured':len(spectrum['points']),'n_rejected':len(spectrum['rejected']),
                                    'n_model_mismatch':sum(p.get('quality')=='model_mismatch' for p in spectrum['points']),
                                    'blackbody':spectrum.get('blackbody'),'rejection_reasons':sorted(set(p['reason'] for p in spectrum['rejected']))}
                before=json.dumps(field.clean(target.__dict__),sort_keys=True)
                t=time.perf_counter();response=client.get(f'/api/science/fields/{f.fid}/sources/{target.id}/evidence');result['evidence_export_s']=time.perf_counter()-t
                assert response.status_code==200,response.text
                report=response.json();(OUT/(row['key']+'_evidence.json')).write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
                optional=decompose_observation(f.observations[f.ref],target.ra,target.dec)
                assert before==json.dumps(field.clean(target.__dict__),sort_keys=True)
                result['optional_decomposition']={'state':optional['state'],'measurements_unchanged':True}
                result['evidence_hash']=report['report_sha256'];result['selected_obs_ids']=[o.obs_id for o in f.observations]
                result['api_verified']={'field':client.get(f'/api/fields/{f.fid}').status_code,
                                        'source':client.get(f'/api/fields/{f.fid}/sources/{target.id}').status_code,
                                        'spectrum':client.get(f'/api/fields/{f.fid}/sources/{target.id}/spectrum').status_code}
            t=time.perf_counter();reuse=field.build_field(Job('field','reuse',field.STAGES),row['ra'],row['dec'],.2,row['band'])
            result['reuse_s']=time.perf_counter()-t;result['reused']=reuse['reused']
        except Exception as exc:
            result.update(state='unavailable',failure={'code':getattr(exc,'code','processing_failure'),'message':str(exc)})
        result['total_s']=time.perf_counter()-start;results.append(field.clean(result))
        print(row['key'],result.get('state'),result.get('target_state'),result.get('failure'),flush=True)
    return results


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--existing-cache',type=Path,default=Path('../project-nadir/data/cache'))
    ap.add_argument('--acquire',action='store_true');ap.add_argument('--offline',action='store_true');args=ap.parse_args()
    logging.disable(logging.CRITICAL);config.HTTP_TIMEOUT_S=20;config.HTTP_RETRIES=1;config.HTTP_RETRIES_BUSY=1
    rows=acquire(args.existing_cache) if args.acquire else json.loads((OUT/'targets_manifest.json').read_text())
    if args.offline:
        with patch('requests.sessions.Session.request',side_effect=irsa.DataAccessError('Offline validation: remote service disabled',code='offline_cache_miss')) as network:
            results=validate(rows,True)
        attempts=network.call_count
    else:
        results=validate(rows,False);attempts=None
    payload=dict(schema_version=1,mode='offline_replay' if args.offline else 'bounded_live_validation',
                 targets=results,blocked_network_attempts=attempts,subset_policy='12 cached or 6 newly acquired primary exposures; extra bands limited',
                 validated_code_base='984a742 plus milestone 5 reliability changes')
    (OUT/('offline_results.json' if args.offline else 'live_results.json')).write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
if __name__=='__main__':main()
