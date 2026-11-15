"""NADIR-STIR research prototype: spectro-temporal native-pixel scene fit.

This fits one/two point-source hypotheses directly to pixels, with per-frame
band-limited amplitudes and shared spectral slopes. It is a constrained
hypothesis test, not image super-resolution and not a discovery classifier.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from scipy.optimize import least_squares, nnls
from .deblend import Stamp, response, _prepare

@dataclass
class SpectralFrame:
    stamp: Stamp
    wavelength_um: float
    bandwidth_um: float | None = None
    mjd: float | None = None
    held_out: bool = False
    def validate(self):
        self.stamp.validate()
        if not np.isfinite(self.wavelength_um) or self.wavelength_um<=0: raise ValueError('positive wavelength required')
        if self.bandwidth_um is not None and (not np.isfinite(self.bandwidth_um) or self.bandwidth_um<=0): raise ValueError('positive bandwidth required')
        if self.mjd is not None and not np.isfinite(self.mjd): raise ValueError('finite MJD required')


def fit(frames, count=2, spectral=True, motion=False, regularization=0., center=None, reference_mjd=None):
    """Fit scene geometry on training frames; return prediction for held-out frames.

    Spectral term: log-linear F(lambda)=A*(lambda/lambda0)^alpha, one slope per
    source; amplitudes are still free per frame. Thus it constrains relative
    component colors without assuming equal cross-band brightness. Optional
    motion is linear in pixels/year. Regularization shrinks alpha toward zero
    through lambda*alpha^2; no automatic tuning is performed.
    """
    frames=list(frames)
    if count not in (1,2) or not frames: raise ValueError('one or two sources and frames required')
    for f in frames:f.validate()
    train=[f for f in frames if not f.held_out]
    if not train:raise ValueError('at least one training frame required')
    lam=np.array([f.wavelength_um for f in train]);l0=float(np.exp(np.mean(np.log(lam))))
    t=np.array([f.mjd if f.mjd is not None else 0. for f in train]);t0=float(np.mean(t) if reference_mjd is None else reference_mjd)
    prep=_prepare([f.stamp for f in train])
    if center is None:center=((train[0].stamp.image.shape[1]-1)/2,(train[0].stamp.image.shape[0]-1)/2)
    center=np.asarray(center,float)
    npos=2*count; nalpha=count if spectral else 0; nvel=2*count if motion else 0
    def geom(p,k):
        pos=p[:npos].reshape(count,2).copy()
        if motion:pos+=p[npos+nalpha:].reshape(count,2)*((k.mjd or t0)-t0)/365.25
        return pos
    def fit_amplitudes(p, frame, prepared, idx):
        mask,bg,w,b,q=prepared;pos=geom(p,frame);cols=[]
        for j,xy in enumerate(pos):
            col=response(frame.stamp,*xy)
            if spectral:col=col*np.exp(p[npos+j]*np.log(frame.wavelength_um/l0))
            cols.append(col[mask])
        a=np.column_stack(cols)*w[:,None];d=frame.stamp.image[mask]*w
        ap=a-q@(q.T@a);dp=d-q@(q.T@d);amp,_=nnls(ap,dp)
        sky=np.linalg.lstsq(b,d-a@amp,rcond=None)[0]
        resid=(a@amp+b@sky-d)
        return resid,amp,sky
    def residual(p):
        pieces=[]
        for idx,(frame,pr) in enumerate(zip(train,prep)):
            r,_,_=fit_amplitudes(p,frame,pr,idx);pieces.append(r)
        if spectral and regularization>0:pieces.append(np.sqrt(regularization)*p[npos:npos+nalpha])
        return np.concatenate(pieces)
    lo=np.r_[np.tile(center-2.5,count),np.full(nalpha,-4.),np.full(nvel,-2.)]
    hi=np.r_[np.tile(center+2.5,count),np.full(nalpha,4.),np.full(nvel,2.)]
    starts=[]
    if count==1:starts=[np.r_[center,np.zeros(nalpha+nvel)]]
    else:
        for sep in (.7,1.6,2.8):
            for angle in (0,np.pi/3,2*np.pi/3):
                d=.5*sep*np.array([np.cos(angle),np.sin(angle)])
                starts.append(np.r_[center-d,center+d,np.zeros(nalpha+nvel)])
    opts=[least_squares(residual,p,bounds=(lo,hi),max_nfev=100) for p in starts]
    best=min(opts,key=lambda x:x.cost);p=best.x
    train_predictions=[]; train_chi=[]
    for idx,(frame,pr) in enumerate(zip(train,prep)):
        r,amp,sky=fit_amplitudes(p,frame,pr,idx);train_chi.append(float(r@r))
        train_predictions.append((frame,pr,amp,sky))
    held=[]
    for frame in [f for f in frames if f.held_out]:
        mask,bg,w,b,q=_prepare([frame.stamp])[0]
        r,amp,sky=fit_amplitudes(p,frame,(mask,bg,w,b,q),0)
        held.append(dict(obs_id=frame.stamp.obs_id,chi2=float(r@r),n_pixels=int(mask.sum()),fluxes=amp.tolist()))
    positions=geom(p,SpectralFrame(train[0].stamp,l0,None,t0))
    hessian=best.jac.T@best.jac
    cov=np.linalg.pinv(hessian)*max(float(2*best.cost/max(len(best.fun)-len(p),1)),1.)
    return dict(mode='stir_experimental',source_count=count,positions=positions.tolist(),
        spectral_slopes=p[npos:npos+nalpha].tolist() if spectral else None,
        velocities_px_per_year=p[npos+nalpha:].reshape(count,2).tolist() if motion else None,
        parameter_covariance=cov.tolist(),training_chi2=train_chi,held_out=held,
        predicted_observations=[dict(obs_id=f.stamp.obs_id,wavelength_um=f.wavelength_um,
          model=(response(f.stamp,*geom(p,f)[j]) * amp[j] for j in range(count))) for f,pr,amp,sky in []],
        lambda0_um=l0,regularization=regularization,regularization_term='lambda * sum(alpha^2), slope shrinkage toward flat Fnu',
        objective='diagonal-variance pixel chi-square plus declared slope penalty',
        limitations=['No bandpass response integration; wavelength is central value approximation.',
          'Per-frame amplitudes absorb variability; no temporal variability prior.',
          'No correlated pixel covariance, calibration uncertainty, or pointing uncertainty.',
          'Linear motion only; pixel shifts are assumed known.',
          'Fisher covariance is local and excludes model-selection/systematic uncertainty.'],
        success=bool(best.success),elapsed_s=float(best.cost))
