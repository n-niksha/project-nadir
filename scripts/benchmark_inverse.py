#!/usr/bin/env python
"""Reproducible controlled experiments; no network, no real-data substitutes."""
import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np
from scipy.stats import beta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nadir.science.deblend import Stamp,response,fit_model,compare_sources
from nadir.science.reconstruction import coadd
from nadir.science.sources import detect_sources
from nadir.science.synthetic import render_observation
from nadir.science.fieldtrack import linear_fit
from nadir.ml.evidence import screen,gaussian_tails,needs_expensive_analysis


def simulate(seed,separation=0,ratio=1,flux=500,shift=(0.,0.),sigma=.8,fit_sigma=None):
    rng=np.random.default_rng(seed)
    s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),str(seed),shift,sigma)
    center=np.array([5.,5.])+rng.uniform(-.35,.35,2)
    angle=rng.uniform(0,np.pi)
    delta=.5*separation*np.array([np.cos(angle),np.sin(angle)])
    positions=np.array([center-delta,center+delta])
    yy,xx=np.indices(s.image.shape)
    truth=flux/(1+ratio)*(response(s,*positions[0])+ratio*response(s,*positions[1]))
    s.image=truth+3+.03*(xx-5)-.02*(yy-5)+rng.normal(size=s.image.shape)
    if fit_sigma is not None:s.sigma=fit_sigma
    return s,positions,truth


def baseline_count(s):
    obs=render_observation([[150,20,1]],n=11,seed=0)
    obs.image=s.image.copy();obs.variance=s.variance.copy()
    return len(detect_sources(obs))


def interval(k,n):
    return [float(beta.ppf(.025,k,n-k+1)) if k else 0.,
            float(beta.ppf(.975,k+1,n-k)) if k<n else 1.]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--trials',type=int,default=12)
    ap.add_argument('--calibration-trials',type=int,default=8)
    ap.add_argument('--output',default='docs/benchmarks/inverse.json');args=ap.parse_args()
    started=time.perf_counter();rows=[]
    for flux in (100,500):
        for ratio in (.2,1.):
            for sep in (0.,.5,1.,1.5,2.,3.):
                trials=[]
                for k in range(args.trials):
                    s,truth,_=simulate(1000+k,sep,ratio,flux)
                    r=compare_sources([s])
                    xy=np.asarray(r['models']['two']['positions'])
                    error=min(np.mean(np.sum((xy-truth)**2,axis=1)),np.mean(np.sum((xy[::-1]-truth)**2,axis=1)))**.5
                    trials.append(dict(delta_bic=r['evidence']['delta_bic'],sep_error=r['separation_px']-sep,
                                       ratio_error=r['flux_ratio']-ratio,astrometric_error_px=error,
                                       baseline_two=baseline_count(s)>=2,time_s=r['elapsed_s']))
                k=sum(t['delta_bic']>10 for t in trials)
                rows.append(dict(flux=flux,noise_sigma=1,psf_sigma_px=.8,separation_px=sep,flux_ratio=ratio,
                                 n=args.trials,descriptive_bic_two_count=k,bic_two_rate=k/args.trials,
                                 bic_two_rate_interval95=interval(k,args.trials),
                                 baseline_two_count=sum(t['baseline_two'] for t in trials),
                                 separation_rmse_px=float(np.sqrt(np.mean([t['sep_error']**2 for t in trials]))),
                                 component_position_rmse_px=float(np.sqrt(np.mean([t['astrometric_error_px']**2 for t in trials]))),
                                 ratio_rmse=float(np.sqrt(np.mean([t['ratio_error']**2 for t in trials]))),
                                 median_time_s=float(np.median([t['time_s'] for t in trials]))))
                print(f'grid flux={flux} ratio={ratio} sep={sep} BIC H2={k}/{args.trials}',flush=True)
    calibrated=[]
    for label,sep,sigma in [('null',0,.8),('blend',2.,.8),('broadened_null',0,1.05)]:
        states=[];ps=[];times=[]
        for k in range(args.calibration_trials):
            s,_,_=simulate(5000+k,sep,.5,500,sigma=sigma,fit_sigma=.8)
            r=compare_sources([s],bootstrap=39,seed=9000+k)
            states.append(r['state']);ps.append(r['evidence']['bootstrap_p']);times.append(r['elapsed_s'])
        count=states.count('multiple_source_model_favored')
        calibrated.append(dict(case=label,n=len(states),favored=count,rate=count/len(states),
                               interval95=interval(count,len(states)),states=states,pvalues=ps,
                               median_time_s=float(np.median(times))))
        print('calibration',calibrated[-1],flush=True)
    # Identical phases versus dither: isolate averaging from sampling benefits.
    reconstruction=[]
    for dither in (False,True):
        errors=[];phot=[];single=[];coadd_error=[];cost=[]
        for k in range(max(20,args.trials)):
            rng=np.random.default_rng(7000+k)
            target=np.array([5.17,4.79]);stamps=[]
            for j in range(4):
                shift=tuple(rng.uniform(-.5,.5,2)) if dither else (0.,0.)
                s=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),f'{k}-{j}',shift,.8)
                s.image=3+100*response(s,*target)+rng.normal(size=s.image.shape)
                stamps.append(s)
            t=time.perf_counter();fit=fit_model(stamps);cost.append(time.perf_counter()-t)
            errors.append(np.linalg.norm(np.asarray(fit['positions'][0])-target))
            phot.append(np.mean([f['flux'][0] for f in fit['frames']])/100-1)
            one=fit_model(stamps[:1]);single.append(np.linalg.norm(np.asarray(one['positions'][0])-target))
            c=coadd(stamps)
            # Moment baseline only: do not ignore resampling covariance in a likelihood.
            yy,xx=np.indices(c['image'].shape);signal=np.nan_to_num(c['image']-3)
            coadd_error.append(np.linalg.norm([np.sum(xx*signal)/signal.sum()-target[0],np.sum(yy*signal)/signal.sum()-target[1]]))
        reconstruction.append(dict(dither=dither,n=len(errors),single_rmse_px=float(np.sqrt(np.mean(np.square(single)))),
                                   joint_rmse_px=float(np.sqrt(np.mean(np.square(errors)))),
                                   coadd_moment_rmse_px=float(np.sqrt(np.mean(np.square(coadd_error)))),
                                   joint_photometric_fraction_rmse=float(np.sqrt(np.mean(np.square(phot)))),
                                   median_joint_seconds=float(np.median(cost))))
    # A static pair with a varying secondary produces a moving photocenter.
    t=np.linspace(0,1,12);ratios=np.linspace(.2,1.,12);sep=1.
    photocenter=sep*ratios/(1+ratios)*6.15
    motion=linear_fit(t,photocenter,np.full(12,.05))
    # Existing robust PM estimator under a single gross outlier; independent repeats.
    pulls=[];ols_errors=[];robust_errors=[]
    for k in range(100):
        rng=np.random.default_rng(30000+k);values=2+3*t+rng.normal(0,.1,len(t));values[5]+=3
        fit=linear_fit(t,values,np.full(len(t),.1));pulls.append((fit['rate']-3)/fit['rate_err'])
        robust_errors.append(fit['rate']-3);ols_errors.append(np.polyfit(t,values,1)[0]-3)
    motion_validation=dict(n=100,robust_rate_rmse=float(np.sqrt(np.mean(np.square(robust_errors)))),
                           ols_rate_rmse=float(np.sqrt(np.mean(np.square(ols_errors)))),
                           uncertainty_coverage95=float(np.mean(np.abs(pulls)<=1.96)))
    # Independent synthetic objects; no trained ML and no repeated-source leakage.
    rng=np.random.default_rng(8128);features=rng.normal(size=(4000,5));features[2000:3000,0]+=4
    features[3000:,2]+=4;labels=np.arange(4000)>=2000
    single=[];multi=[];staged=[];missing=[];timing=time.perf_counter()
    for i,z in enumerate(features):
        ps=gaussian_tails(z[:2],z[2],z[3:]);r=screen(ps)
        single.append(ps['motion']<=.01);multi.append(r['is_candidate'])
        staged.append(needs_expensive_analysis(r,10))
        ps['variability']=None if i%2 else ps['variability'];missing.append(screen(ps)['is_candidate'])
    rank_seconds=time.perf_counter()-timing
    def metrics(pred):
        pred=np.asarray(pred);tp=int(np.sum(pred&labels));fp=int(np.sum(pred&~labels))
        return dict(tp=tp,fp=fp,precision=tp/max(tp+fp,1),recall=tp/labels.sum(),false_positive_rate=fp/(~labels).sum())
    anomaly=dict(n=4000,synthetic_only=True,single_feature=metrics(single),multi_feature=metrics(multi),
                 missing_half_variability=metrics(missing),expensive_stage_fraction=float(np.mean(staged)),
                 stage_candidate_recall=float(np.mean(np.asarray(staged)[labels])),screen_seconds=rank_seconds,
                 ml='Unsupported by current real labeled data; no classifier trained.')
    s,_,_=simulate(98,2,.5,500)
    uncertainty=compare_sources([s],uncertainty_draws=30,seed=999)['uncertainty']
    output=dict(schema_version=1,seed_policy='fixed per experiment in script',numpy_version=np.__version__,
                model='pixel-integrated Gaussian, fixed known PSF and independent unit-variance Gaussian noise',
                caveats=['BIC grid is descriptive, not calibrated detection sensitivity.',
                         'Small calibration sample: report binomial intervals; not survey FPR certification.',
                         'Matched-model simulations do not validate real SPHEREx PSFs.'],
                grid=rows,calibration=calibrated,reconstruction=reconstruction,uncertainty_example=uncertainty,
                false_motion=dict(separation_px=sep,flux_ratios=ratios.tolist(),photocenter_arcsec=photocenter.tolist(),
                                  fitted_motion=motion,physical_motion_arcsec_per_year=0),
                motion_validation=motion_validation,anomaly=anomaly,elapsed_s=time.perf_counter()-started)
    p=Path(args.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(output,indent=2,allow_nan=False,default=lambda v:v.item())+'\n')
    print('Wrote',p,'in',output['elapsed_s'],'seconds',flush=True)

if __name__=='__main__':main()
