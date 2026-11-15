#!/usr/bin/env python
"""Read-only audit of an explicitly selected cache. Never fetches archive data.

Selects up to --limit distinct D2 epochs per preset, evenly across time. Missing
cache is not evidence that the archive has no data. Writes only --output.
"""
import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np
from astropy.io import fits
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nadir.config import PRESETS
from nadir.science.observation import load_observation
from nadir.science.diagnostics import observation_diagnostics,decompose_observation
from nadir.science.sources import detect_sources
from nadir.science.spectral import forced_photometry
from nadir.science.motion import track_source,parallax_factors
from nadir.science.reprojection import make_grid
from nadir.science.psf import detector_xy,epsf_values


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cache',type=Path,required=True)
    ap.add_argument('--limit',type=int,default=12);ap.add_argument('--output',default='docs/benchmarks/real_cache.json')
    args=ap.parse_args();groups={p['id']:[] for p in PRESETS};errors=[];start=time.perf_counter()
    for p in sorted((args.cache/'cutouts').glob('*.json')):
        try:
            meta=json.loads(p.read_text());request=meta.get('request',{});record=meta.get('archive_record',{})
            if record.get('band')!='D2':continue
            for target in PRESETS:
                if abs(request.get('ra_deg',-1000)-target['ra'])<.001 and abs(request.get('dec_deg',-1000)-target['dec'])<.001:
                    path=p.with_suffix('.fits')
                    if path.exists():groups[target['id']].append((path,meta))
                    else:errors.append(dict(file=p.name,reason='metadata exists but FITS missing'))
        except (ValueError,OSError) as exc:errors.append(dict(file=p.name,reason=str(exc)))
    reports=[]
    for target in PRESETS:
        records=groups[target['id']];unique={}
        for path,meta in records:
            key=meta.get('archive_record',{}).get('obs_id',path.stem)
            # Prefer DR1 if same physical exposure appears in multiple releases.
            if key not in unique or meta.get('release')=='dr1':unique[key]=(path,meta)
        records=sorted(unique.values(),key=lambda p:p[1].get('archive_record',{}).get('mjd_start',0))
        ids=np.unique(np.linspace(0,len(records)-1,min(args.limit,len(records))).astype(int)) if records else []
        selected=[records[i] for i in ids];rows=[];observations=[];detections=[];photometry=[];decompositions=[];times=[]
        for path,meta in selected:
            t=time.perf_counter()
            try:
                obs=load_observation(path,(target['ra'],target['dec']),meta)
                src=detect_sources(obs);observations.append(obs);detections.append(src)
                clean=sum(s.fit_ok and not s.core_masked and not s.blended for s in src)
                diag=observation_diagnostics(obs,target['ra'],target['dec'],clean)
                diag.update(cache_file=path.name,detector_pa_deg=obs.detector_pa_deg,
                            n_detected=len(src),metadata_issues=obs.issues)
                with fits.open(path,memmap=False) as hdul:
                    diag['epsf_header']={k:hdul['EPSF'].header.get(k) for k in ('OVSMPX','OVSMPY','DETCOORD')} if 'EPSF' in hdul else {}
                x,y=obs.wcs.all_world2pix([[target['ra'],target['dec']]],0)[0]
                near=sorted(src,key=lambda s:(s.x-x)**2+(s.y-y)**2)
                measured=near[0] if near and np.hypot(near[0].x-x,near[0].y-y)<3 else None
                diag['target_detected']=measured is not None
                if measured:
                    # Association is tentative, not independently catalog-confirmed.
                    diag['target_match']='nearest detected source within 3 pixels; crowded-field identity unverified'
                    diag['measured_snr']=measured.snr
                    diag['measured_core_masked']=measured.core_masked
                    diag['measured_saturated']=measured.saturated
                    tphoto=time.perf_counter();phot=forced_photometry(obs,measured.ra,measured.dec)
                    phot['elapsed_s']=time.perf_counter()-tphoto;photometry.append(phot)
                    if len(decompositions)<2:
                        decompositions.append(decompose_observation(obs,measured.ra,measured.dec))
                else:
                    diag['target_failure']='insufficient_signal_or_association_failure'
                if obs.epsf is not None and obs.wcs_det is not None:
                    psf=obs.epsf.at(*detector_xy(obs.wcs_det,x,y));yy,xx=np.mgrid[-5:6,-5:6]
                    sums=[float(epsf_values(psf,xx-dx,yy-dy).sum()) for dx in (0,.25,.5,.75) for dy in (0,.25,.5,.75)]
                    diag['epsf_phase_sum_range']=[min(sums),max(sums)]
                rows.append(diag)
            except Exception as exc:
                # Retained as software/data failure, never silently skipped.
                rows.append(dict(cache_file=path.name,state='software_or_metadata_failure',reason=f'{type(exc).__name__}: {exc}'))
            times.append(time.perf_counter()-t)
        track=None
        if len(observations)>=3:
            try:
                ref=max(range(len(observations)),key=lambda i:sum(s.fit_ok and not s.core_masked and not s.blended for s in detections[i]))
                t=time.perf_counter();track=track_source(observations,ref,make_grid(target['ra'],target['dec'],target['size']),
                                                       target['ra'],target['dec'],detections)
                track['elapsed_s']=time.perf_counter()-t
            except Exception as exc:track={'failure':f'{type(exc).__name__}: {exc}'}
        wavelengths=[o.wavelength_um for o in observations if o.wavelength_um]
        mjds=[o.mjd for o in observations if o.mjd]
        compatible=sum(abs(a.wavelength_um-b.wavelength_um)<.5*min(a.bandwidth_um,b.bandwidth_um)
                       for i,a in enumerate(observations) for b in observations[i+1:]
                       if a.wavelength_um and b.wavelength_um and a.bandwidth_um and b.bandwidth_um)
        reconstruction_pairs=[]
        for i,a in enumerate(observations):
            for j,b in enumerate(observations[i+1:],i+1):
                if not all((a.wavelength_um,b.wavelength_um,a.bandwidth_um,b.bandwidth_um,a.mjd,b.mjd)):
                    continue
                if (abs(a.wavelength_um-b.wavelength_um)<.5*min(a.bandwidth_um,b.bandwidth_um)
                    and abs(a.mjd-b.mjd)<7
                    and abs((a.detector_pa_deg-b.detector_pa_deg+180)%360-180)<3):
                    reconstruction_pairs.append([i,j])
        parallax=None
        if track and len(track.get('points',[]))>=5:
            p=track['points'];tm=np.mean([v['mjd'] for v in p]);design=[]
            for v in p:
                pe,pn=parallax_factors(target['ra'],target['dec'],v['mjd']);dt=(v['mjd']-tm)/365.25
                design.extend([np.array([1,0,dt,0,pe])/max(v['sigma_east_arcsec'],.001),
                               np.array([0,1,0,dt,pn])/max(v['sigma_north_arcsec'],.001)])
            design=np.array(design)
            if np.linalg.matrix_rank(design)==5:
                covariance=np.linalg.inv(design.T@design)
                parallax=dict(design_condition=float(np.linalg.cond(design)),
                              formal_parallax_sigma_arcsec=float(np.sqrt(covariance[-1,-1])),
                              state='feasibility_only_not_fitted',
                              limitation='Ignores common registration covariance and model mismatch; not a parallax measurement.')
        report=dict(target=target,available_cached_files=len(groups[target['id']]),unique_cached_D2_exposures=len(records),
                    examined=len(selected),status='examined_cached_data' if selected else 'missing_cached_data',
                    archive_availability='Not queried; missing cache does not establish missing archive coverage.',
                    selection='evenly spaced cached D2 epochs; duplicate obs_ids removed',
                    observations=rows,spectrum=photometry,decompositions=decompositions,motion=track,
                    wavelength_range_um=[min(wavelengths),max(wavelengths)] if wavelengths else None,
                    baseline_days=max(mjds)-min(mjds) if mjds else None,approximate_bandpass_overlap_pairs=int(compatible),
                    reconstruction_feasibility=dict(candidate_pairs=reconstruction_pairs,
                        rule='approximate passband overlap, <7 days, <3 degrees orientation difference; not sufficient alone',
                        state='requires_registration_and_variability_validation' if reconstruction_pairs else 'insufficient_compatible_sampled_exposures',
                        output_image_generated=False),parallax_feasibility=parallax,
                    median_read_detect_audit_s=float(np.median(times)) if times else None)
        reports.append(report);print(target['id'],len(records),'cached,',len(selected),'examined',flush=True)
    result=dict(schema_version=1,mode='read-only existing cache; no network',targets=reports,cache_errors=errors,
                limitations=['No independent labels or crowded-field catalog truth acquired.',
                             'Subpixel phases from WCS alone do not establish accurate relative registration.',
                             'Wavelength overlap is approximate WCS-WAVE, not calibrated passband equivalence.'],
                elapsed_s=time.perf_counter()-start)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print('Wrote',path,flush=True)

if __name__=='__main__':main()
