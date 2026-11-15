"""Optional simultaneous forced PSF amplitudes at independently supplied positions.

Keeps the full amplitude covariance for confused sources. Positions/PSF are
fixed; this does not identify objects or independently calibrate the flux.
"""
import numpy as np
from .deblend import response


def forced_components(stamp, positions):
    stamp.validate()
    positions=np.asarray(positions,float)
    if positions.ndim!=2 or positions.shape[1]!=2 or not 1<=len(positions)<=4 or not np.isfinite(positions).all():
        raise ValueError('one to four finite (x,y) positions required')
    mask=stamp.good & np.isfinite(stamp.image) & np.isfinite(stamp.variance) & (stamp.variance>0)
    if mask.sum()<len(positions)+8:
        return dict(state='insufficient_data',flux=None,covariance=None)
    yy,xx=np.indices(stamp.image.shape)
    a=np.column_stack([response(stamp,*p)[mask] for p in positions]+[np.ones(mask.sum()),(xx-xx.mean())[mask],(yy-yy.mean())[mask]])
    w=1/np.sqrt(stamp.variance[mask]);a=a*w[:,None];d=stamp.image[mask]*w
    condition=float(np.linalg.cond(a))
    if condition>1e8:
        return dict(state='degenerate',flux=None,covariance=None,condition_number=condition)
    # Unconstrained amplitudes preserve unbiased measurements; negatives are not detections.
    coef=np.linalg.lstsq(a,d,rcond=None)[0];cov=np.linalg.inv(a.T@a)
    chi2=float(np.sum((a@coef-d)**2)/(len(d)-len(coef)))
    n=len(positions)
    return dict(state='model_mismatch' if chi2>3 else 'conditional_measurement',maturity='experimental',
                flux=coef[:n].tolist(),covariance=(cov[:n,:n]*max(1,chi2)).tolist(),
                flux_unit='image surface-brightness unit times native pixel',
                chi2_red=chi2,condition_number=condition,observation_id=stamp.obs_id,
                assumptions=['Fixed independently supplied positions and PSF; diagonal pixel covariance.',
                             'Flux covariance excludes position and calibration uncertainty.'])
