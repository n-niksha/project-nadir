"""Experimental native-pixel forward modeling. Never a discovery classifier.

Positions are shared in a reference pixel frame; shifts map reference -> native.
Fluxes and planar backgrounds are independent per exposure. Gaussian PSFs are
pixel-integrated; archive ePSFs already include the pixel response. Variances
must be fixed before fitting (including any independently estimated systematic
variance). Bootstrap calibration is conditional on this PSF/noise model.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import time
import numpy as np
from scipy.optimize import least_squares, nnls
from scipy.special import erf
from scipy.ndimage import gaussian_filter
from scipy.signal import fftconvolve
from .psf import epsf_values

_QUAD_X, _QUAD_W = np.polynomial.legendre.leggauss(4)


@dataclass
class Stamp:
    image: np.ndarray
    variance: np.ndarray
    good: np.ndarray
    obs_id: str
    shift: tuple[float, float] = (0., 0.)
    sigma: float = .8
    epsf: np.ndarray | None = None
    flags: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def validate(self):
        if self.image.ndim != 2 or self.image.shape != self.variance.shape or self.image.shape != self.good.shape:
            raise ValueError('image, variance and mask must be matching 2-D arrays')
        if self.good.dtype.kind != 'b':
            raise ValueError('good must be a boolean usable-pixel mask')
        if not np.isfinite([*self.shift, self.sigma]).all() or self.sigma <= 0:
            raise ValueError('finite shifts and positive PSF sigma required')
        if self.epsf is not None and (self.epsf.shape != (33, 33) or not np.isfinite(self.epsf).all() or self.epsf.sum() <= 0):
            raise ValueError('ePSF must be finite, positive-sum 33x33 array')


def response(stamp, x, y, width=0.):
    yy, xx = np.indices(stamp.image.shape, dtype=float)
    dx, dy = xx - x - stamp.shift[0], yy - y - stamp.shift[1]
    if np.ndim(width):
        major,minor,angle=width
        c,s=np.cos(angle),np.sin(angle)
        if stamp.epsf is not None:
            ky,kx=np.mgrid[-16:17,-16:17]/5.
            u,v=c*kx+s*ky,-s*kx+c*ky
            kernel=np.exp(-.5*((u/max(major,.03))**2+(v/max(minor,.03))**2))
            p=fftconvolve(stamp.epsf,kernel/kernel.sum(),mode='same')
            return epsf_values(p,dx,dy)
        # Rotated Gaussian integrated over each pixel by fixed quadrature.
        a,b=np.sqrt(stamp.sigma**2+major**2),np.sqrt(stamp.sigma**2+minor**2)
        value=np.zeros_like(dx)
        for qx,wx in zip(_QUAD_X,_QUAD_W):
            for qy,wy in zip(_QUAD_X,_QUAD_W):
                u=c*(dx+.5*qx)+s*(dy+.5*qy)
                v=-s*(dx+.5*qx)+c*(dy+.5*qy)
                value+=wx*wy*np.exp(-.5*((u/a)**2+(v/b)**2))/(8*np.pi*a*b)
        return value
    if stamp.epsf is not None:
        p = gaussian_filter(stamp.epsf, width * 5) if width > 0.01 else stamp.epsf
        return epsf_values(p, dx, dy)
    s = np.sqrt(2 * (stamp.sigma**2 + width**2))
    return .25 * (erf((dx+.5)/s)-erf((dx-.5)/s)) * (erf((dy+.5)/s)-erf((dy-.5)/s))


def _prepare(stamps):
    prepared = []
    for s in stamps:
        s.validate()
        mask = s.good & np.isfinite(s.image) & np.isfinite(s.variance) & (s.variance > 0)
        yy, xx = np.indices(s.image.shape, dtype=float)
        bg = np.stack([np.ones_like(xx), xx-xx.mean(), yy-yy.mean()], axis=-1)
        w = 1 / np.sqrt(s.variance[mask])
        b = bg[mask] * w[:, None]
        q, _ = np.linalg.qr(b, mode='reduced')
        prepared.append((mask, bg, w, b, q))
    return prepared


def _noise_sigma(stamp):
    return np.sqrt(np.where(np.isfinite(stamp.variance) & (stamp.variance>0),stamp.variance,0.))


def fit_model(stamps, count=1, extended=False, center=None, elliptical=False):
    """Variable projection: bounded positions, nonnegative flux, free sky plane.

    H2 multistarts are fixed and reused in every null simulation. Full arrays
    stay internal; public summaries contain residual diagnostics only.
    """
    prep = _prepare(stamps)
    if count not in (1, 2) or ((extended or elliptical) and count != 1):
        raise ValueError('supported models: H1, H2, broadened H1')
    if center is None:
        center = ((stamps[0].image.shape[1]-1)/2, (stamps[0].image.shape[0]-1)/2)
    center = np.asarray(center, float)
    def evaluate(p, details=False):
        positions = p[:2*count].reshape(count, 2)
        width = p[-3:] if elliptical else (p[-1] if extended else 0.)
        residuals, frames = [], []
        for s, (mask, bg, w, b, q) in zip(stamps, prep):
            ps = np.stack([response(s, *xy, width) for xy in positions], axis=-1)
            a = ps[mask] * w[:, None]
            d = s.image[mask] * w
            ap, dp = a-q@(q.T@a), d-q@(q.T@d)
            flux, _ = nnls(ap, dp)
            sky = np.linalg.lstsq(b, d-a@flux, rcond=None)[0]
            model = ps@flux + bg@sky
            residual = (model[mask]-s.image[mask])*w
            residuals.append(residual)
            if details:
                flux_cov=np.linalg.pinv(ap.T@ap)
                flux_err=np.sqrt(np.maximum(np.diag(flux_cov),0))
                frames.append(dict(model=model, flux=flux.tolist(), background=sky.tolist(),
                                   conditional_flux_error=flux_err.tolist(),
                                   conditional_flux_snr=(flux/np.maximum(flux_err,1e-30)).tolist(),
                                   residual_rms=float(np.sqrt(np.mean(residual**2))), n_pixels=int(mask.sum())))
        return (np.concatenate(residuals), frames) if details else np.concatenate(residuals)
    lo, hi = np.tile(center-2.5, count), np.tile(center+2.5, count)
    starts = [np.tile(center, count)]
    if count == 2:
        starts = []
        for sep in (.7, 1.6, 2.8):
            for angle in (0, np.pi/3, 2*np.pi/3):
                delta = .5*sep*np.array([np.cos(angle), np.sin(angle)])
                starts.append(np.r_[center-delta, center+delta])
    if elliptical:
        starts=[np.r_[center,.4,.1,0.],np.r_[center,.4,.1,np.pi/3]]
        lo,hi=np.r_[lo,0.,0.,-np.pi],np.r_[hi,1.5,1.5,np.pi]
    elif extended:
        starts = [np.r_[center, .3]]
        lo, hi = np.r_[lo, 0.], np.r_[hi, 1.5]
    solutions = [least_squares(evaluate, p, bounds=(lo, hi), max_nfev=120,
                               ftol=1e-6, xtol=1e-6, gtol=1e-6) for p in starts]
    converged = [r for r in solutions if r.success and np.isfinite(r.cost)]
    best = min(converged or solutions, key=lambda r:r.cost)
    residual, frames = evaluate(best.x, True)
    n = len(residual)
    k = 2*count + len(stamps)*(count+3) + (3 if elliptical else int(extended))
    chi2 = float(residual@residual)
    return dict(positions=best.x[:2*count].reshape(count,2).tolist(),
                width=best.x[-3:].tolist() if elliptical else (float(best.x[-1]) if extended else 0.),
                chi2=chi2, bic=float(chi2+k*np.log(n)), n_parameters=k, n_pixels=n,
                reduced_chi2=chi2/max(n-k,1), success=bool(converged),
                boundary=bool(np.any(np.minimum(best.x-lo, hi-best.x)[:2*count]<.02)), frames=frames)


def compare_sources(stamps, source_id='selected', ra=None, dec=None, bootstrap=0, seed=0, center=None,
                    uncertainty_draws=0, check=None):
    """Compare point hypotheses with a broadened-source veto and null bootstrap.

    At least 39 bootstrap replicates required to favor H2 at conditional p<=.05.
    BIC is descriptive because mixtures are singular; it is not a Bayes factor.
    """
    if not 0 <= bootstrap <= 199:
        raise ValueError('bootstrap must be between 0 and 199')
    if not 0 <= uncertainty_draws <= 99:
        raise ValueError('uncertainty_draws must be between 0 and 99')
    start = time.perf_counter()
    result = dict(schema_version=1, maturity='experimental', source_id=str(source_id), ra=ra, dec=dec,
                  state='insufficient_data', best_fit_source_count=None, separation_px=None,
                  flux_ratio=None, uncertainty=None, observations_supporting=0,
                  observation_ids=[s.obs_id for s in stamps], flags=[], catalog_matches=[],
                  reasons_to_doubt=['Conditional on PSF, registration and independent Gaussian pixel noise.',
                                    'Modeled components are not independently confirmed objects.',
                                    'No independent catalog association supplied to this fit.'], models={}, evidence={})
    if not stamps:
        return result
    prep = _prepare(stamps)
    if len(set(result['observation_ids'])) != len(stamps):
        raise ValueError('duplicate observations cannot count as independent evidence')
    result['flags'] = sorted({f for s in stamps for f in s.flags})
    if any(m.sum() < 25 for m,*_ in prep):
        if result['flags']:
            result['state']='invalid_or_contaminated_observation'
        result['reasons_to_doubt'].append('Fewer than 25 usable pixels in at least one observation.')
        return result
    models = [fit_model(stamps, 1, center=center), fit_model(stamps, 2, center=center),
              fit_model(stamps, 1, True, center=center)]
    one, two, extended = models
    delta = max(0., one['chi2']-two['chi2'])
    dbic = one['bic']-two['bic']
    elliptical=fit_model(stamps,1,center=center,elliptical=True) if dbic>10 else None
    if elliptical is not None:
        models.append(elliptical)
    positions = np.asarray(two['positions'])
    separation = float(np.linalg.norm(positions[0]-positions[1]))
    ratios = [min(f['flux'])/max(max(f['flux']),1e-30) for f in two['frames']]
    supports = sum(a['residual_rms']**2*a['n_pixels']-b['residual_rms']**2*b['n_pixels'] > 10
                   for a,b in zip(one['frames'], two['frames']))
    null = []
    rng = np.random.default_rng(seed)
    for _ in range(bootstrap):
        if check is not None:
            check()
        simulated = [Stamp(f['model']+rng.normal(size=s.image.shape)*_noise_sigma(s),
                           s.variance, s.good, s.obs_id, s.shift, s.sigma, s.epsf) for s,f in zip(stamps, one['frames'])]
        a, b = fit_model(simulated, 1, center=center), fit_model(simulated, 2, center=center)
        # Failed fits count against significance, never disappear from the denominator.
        null.append(max(0., a['chi2']-b['chi2']) if a['success'] and b['success'] else float('inf'))
    p = (1+sum(v>=delta for v in null))/(bootstrap+1) if bootstrap else None
    result['evidence'] = dict(delta_chi2=delta, delta_bic=dbic, extended_minus_two_bic=extended['bic']-two['bic'],
                              bootstrap_p=p, bootstrap_n=bootstrap, bootstrap_seed=seed,
                              bootstrap_resolution=1/(bootstrap+1) if bootstrap else None,
                              support_rule='per-exposure chi2 reduction > 10; descriptive, not independently calibrated',
                              selected_count_rule='lower descriptive BIC among H1 and H2, subject to state and alternative-model caveats',
                              p_interpretation='plug-in null tail frequency, not probability of a blend')
    state = 'consistent_with_one_source' if dbic < 0 else 'ambiguous'
    if (dbic > 10 and bootstrap >= 39 and p <= .05 and extended['bic']-two['bic'] > 6
            and elliptical is not None and elliptical['bic']-two['bic'] > 6
            and two['reduced_chi2'] < 2 and separation > .3 and min(ratios) > .05):
        state = 'multiple_source_model_favored'
    if not all(m['success'] for m in models) or two['boundary']:
        state = 'ambiguous'
        result['reasons_to_doubt'].append('Optimizer failure or positional search boundary reached.')
    if max(f['conditional_flux_snr'][0] for f in one['frames'])<5:
        state='insufficient_data'
        result['reasons_to_doubt'].append('No exposure has single-source conditional flux S/N >= 5.')
    if result['flags']:
        state = 'invalid_or_contaminated_observation'
    if min(one['reduced_chi2'],two['reduced_chi2'],extended['reduced_chi2']) > 2:
        state = 'invalid_or_contaminated_observation'
        result['reasons_to_doubt'].append('All tested models leave excess residual variance.')
    if extended['bic'] <= two['bic']+6:
        result['reasons_to_doubt'].append('A broadened single source competes with the two-point model.')
    if elliptical is not None:
        result['evidence']['elliptical_minus_two_bic']=elliptical['bic']-two['bic']
        if elliptical['bic']<=two['bic']+6:
            result['reasons_to_doubt'].append('An elliptical single-source/PSF-mismatch alternative competes with H2.')
    if bootstrap < 39:
        result['reasons_to_doubt'].append('Insufficient null simulations for a multiple-source decision.')
    if len(stamps)==1:
        result['reasons_to_doubt'].append('One exposure only; no held-out observation confirmation.')
    result.update(state=state, best_fit_source_count=1 if dbic<=0 else 2,
                  separation_px=separation, flux_ratio=float(np.median(ratios)), observations_supporting=int(supports))
    result['reasons_to_doubt'].append('Separation/flux-ratio intervals require additional alternative-model simulations; unavailable.')
    if uncertainty_draws:
        draws = []
        for _ in range(uncertainty_draws):
            if check is not None:
                check()
            simulated = [Stamp(f['model']+rng.normal(size=s.image.shape)*_noise_sigma(s),
                               s.variance,s.good,s.obs_id,s.shift,s.sigma,s.epsf)
                         for s,f in zip(stamps,two['frames'])]
            fit = fit_model(simulated,2,center=center)
            if fit['success'] and not fit['boundary']:
                xy=np.asarray(fit['positions'])
                draws.append([float(np.linalg.norm(xy[0]-xy[1])),
                              float(np.median([min(f['flux'])/max(max(f['flux']),1e-30) for f in fit['frames']]))])
        result['uncertainty'] = dict(method='conditional H2 parametric bootstrap percentile range',
                                     requested_draws=uncertainty_draws, successful_draws=len(draws),
                                     interpretation='Not model-selection adjusted; excludes PSF/registration systematics.',
                                     separation_px_95=None,flux_ratio_95=None)
        if len(draws)>=20 and len(draws)==uncertainty_draws:
            bounds=np.percentile(draws,[2.5,97.5],axis=0)
            result['uncertainty'].update(separation_px_95=bounds[:,0].tolist(),flux_ratio_95=bounds[:,1].tolist())
            result['reasons_to_doubt'].pop()
    for name,m in zip(('one','two','broadened_one','elliptical_one'), models):
        result['models'][name] = {k:v for k,v in m.items() if k!='frames'}
        result['models'][name]['frames'] = [{k:v for k,v in f.items() if k!='model'} for f in m['frames']]
    result['elapsed_s'] = time.perf_counter()-start
    return result
