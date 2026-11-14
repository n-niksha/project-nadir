#!/usr/bin/env python
"""Additional controlled failure and measurement experiments, no archive I/O."""
import json
import sys
import time
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nadir.science.deblend import Stamp,response,fit_model,compare_sources
from nadir.science.photometry import forced_components
from nadir.science.reconstruction import coadd
from scipy.ndimage import gaussian_filter


def main():
    rows=[];start=time.perf_counter()
    for separation in (0.,.75,1.5):
        single=[];joint=[];se=[];je=[];heldout=[]
        for seed in range(24):
            rng=np.random.default_rng(seed+48000);stamps=[];target=np.array([5.14,4.83])
            xy=np.array([target-[separation/2,0],target+[separation/2,0]])
            for j in range(4):
                s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),str(j),tuple(rng.uniform(-.5,.5,2)),.8)
                s.image=3+100/1.5*(response(s,*xy[0])+.5*response(s,*xy[1]))+rng.normal(size=(11,11))
                stamps.append(s)
            r1=compare_sources(stamps[:1]);r4=compare_sources(stamps)
            single.append(r1['evidence']['delta_bic']>10);joint.append(r4['evidence']['delta_bic']>10)
            se.append(r1['separation_px']-separation);je.append(r4['separation_px']-separation)
            train_one=fit_model(stamps[:3]);train_two=fit_model(stamps[:3],2)
            h1=forced_components(stamps[3],train_one['positions'])
            h2=forced_components(stamps[3],train_two['positions'])
            if h1.get('chi2_red') is not None and h2.get('chi2_red') is not None:
                heldout.append(h1['chi2_red']*(121-4)-h2['chi2_red']*(121-5))
        rows.append(dict(separation_px=separation,ratio=.5,flux_per_exposure=100,n=24,
                         single_bic_favors_two=sum(single),joint_bic_favors_two=sum(joint),
                         single_separation_rmse=float(np.sqrt(np.mean(np.square(se)))),
                         joint_separation_rmse=float(np.sqrt(np.mean(np.square(je)))),
                         heldout_successful=len(heldout),heldout_delta_chi2_gt10=sum(v>10 for v in heldout),
                         heldout_median_delta_chi2=float(np.median(heldout)) if heldout else None))
        print(rows[-1],flush=True)
    phot=[]
    for separation in (.5,1.,2.):
        single=[];joint=[];coverage=[]
        for seed in range(100):
            rng=np.random.default_rng(10000+seed)
            s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),str(seed))
            xy=[(5-separation/2,5),(5+separation/2,5)]
            s.image=3+200*response(s,*xy[0])+100*response(s,*xy[1])+rng.normal(size=(11,11))
            a=forced_components(s,xy[:1]);b=forced_components(s,xy)
            single.append(a['flux'][0]-200);joint.append(b['flux'][0]-200)
            coverage.append(abs(joint[-1])<1.96*np.sqrt(b['covariance'][0][0]))
        phot.append(dict(separation_px=separation,n=100,single_bias=float(np.mean(single)),joint_bias=float(np.mean(joint)),
                         single_rmse=float(np.sqrt(np.mean(np.square(single)))),joint_rmse=float(np.sqrt(np.mean(np.square(joint)))),
                         joint_coverage95=float(np.mean(coverage))))
    failures=[]
    for case in ('nominal','correlated_noise','psf_ellipticity','saturated','cosmic_ray','faint','masked','structured_background'):
        rng=np.random.default_rng(567);s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),case)
        model=500*response(s,5.1,4.8);noise=rng.normal(size=(11,11))
        if case=='correlated_noise':
            noise=gaussian_filter(noise,.8);noise/=np.std(noise)
        if case=='psf_ellipticity':model=gaussian_filter(model,(.1,.6))
        if case=='faint':model*=.02
        if case=='structured_background':
            yy,xx=np.indices((11,11));model+=2*np.cos(xx)*np.cos(yy)
        s.image=3+model+noise
        if case=='cosmic_ray':s.image[5,7]+=100;s.flags=['TRANSIENT']
        if case=='saturated':s.good[s.image>30]=False;s.flags=['OVERFLOW']
        if case=='masked':s.good[:]=False
        r=compare_sources([s]);failures.append(dict(case=case,state=r['state'],evidence=r['evidence'],flags=r['flags']))
    # Registration errors degrade shared-position fits despite known pixel dithers.
    pointing=[]
    for jitter in (0.,.05,.2):
        errors=[]
        for seed in range(50):
            rng=np.random.default_rng(81000+seed);stamps=[]
            for j in range(4):
                s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),str(j),tuple(rng.uniform(-.5,.5,2)))
                xy=np.array([5.1,4.8])+rng.normal(0,jitter,2)
                s.image=3+500*response(s,*xy)+rng.normal(size=(11,11));stamps.append(s)
            fit=fit_model(stamps);errors.append(np.linalg.norm(np.array(fit['positions'][0])-[5.1,4.8]))
        pointing.append(dict(unmodeled_registration_sigma_px=jitter,n=50,joint_rmse_px=float(np.sqrt(np.mean(np.square(errors))))))
    s=Stamp(np.ones((11,11)),np.ones((11,11)),np.ones((11,11),bool),'timing')
    s.image=3+500*response(s,5.1,4.8)+np.random.default_rng(907).normal(size=(11,11))
    costs={}
    for name,fn in [('one_source',lambda:fit_model([s])),('two_source',lambda:fit_model([s],2)),
                    ('coaddition',lambda:coadd([s])),('forced_photometry',lambda:forced_components(s,[(5,5)]))]:
        ts=[]
        for _ in range(5):
            t=time.perf_counter();fn();ts.append(time.perf_counter()-t)
        costs[name+'_median_s']=float(np.median(ts))
    out=dict(joint_separation=rows,crowded_photometry=phot,failure_matrix=failures,pointing=pointing,timings=costs,
             caveats=['BIC selection is descriptive, not calibrated significance.',
                      'Forced component positions are true injected positions; real catalog uncertainty will worsen performance.',
                      'PSF mismatch or correlated noise can pass simple residual tests.'],elapsed_s=time.perf_counter()-start)
    p=Path('docs/benchmarks/inverse_extensions.json');p.write_text(json.dumps(out,indent=2,allow_nan=False,default=lambda v:v.item())+'\n');print('Wrote',p,flush=True)
if __name__=='__main__':main()
