"""Measured features and an interpretable, missing-aware anomaly screen.

A Bonferroni union bound combines valid marginal tail probabilities without
assuming independence. It is not an astrophysical discovery probability.
Missing tests remain in the planned family (conservative, no imputation).
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import math
from scipy.stats import norm, chi2


@dataclass
class Measurement:
    name: str
    value: float | None
    unit: str
    uncertainty: float | None
    definition: str
    usefulness: str
    provenance: dict
    missing_reason: str | None = None


def extract_features(source, provenance=None):
    provenance = provenance or {}
    definitions = [
        ('rate_arcsec_per_yr','arcsec/year','Magnitude of fitted proper motion','Motion screening'),
        ('significance','dimensionless','Reported two-axis motion significance; estimator identified in provenance','Noise-normalized motion'),
        ('log_flux_ratio','dimensionless','Natural logarithm of epoch B/A flux','Variability screening'),
        ('resid_frac_max','dimensionless','Largest normalized PSF residual','Model mismatch or blending'),
        ('snr_min','dimensionless','Minimum epoch signal-to-noise','Measurement reliability'),
        ('gaia_residual_sigma','dimensionless','Catalog displacement residual norm','Catalog disagreement'),
    ]
    out = []
    for name, unit, definition, use in definitions:
        value = source.get(name)
        if value is not None and not math.isfinite(value):
            value = None
        error = source.get(name+'_error')
        if error is not None and (not math.isfinite(error) or error<=0):
            error = None
        out.append(asdict(Measurement(name,value,unit,error,definition,use,provenance,
                                     'not measured or nonfinite' if value is None else None)))
    return out


def screen(pvalues, planned_tests=('motion','variability','catalog'), alpha=.01, contaminated=False):
    if not 0 < alpha < 1 or not planned_tests or len(set(planned_tests)) != len(planned_tests):
        raise ValueError('valid alpha and distinct nonempty planned tests required')
    if set(pvalues)-set(planned_tests):
        raise ValueError('undeclared tests would invalidate multiplicity control')
    observed = {k:float(v) for k,v in pvalues.items() if v is not None}
    if any(not math.isfinite(v) or not 0<=v<=1 for v in observed.values()):
        raise ValueError('tail probabilities must be finite in [0,1]')
    p = min(1.,len(planned_tests)*min(observed.values())) if observed else None
    return dict(maturity='experimental', method='Bonferroni minimum marginal tail',
                adjusted_tail=p, is_candidate=bool(p is not None and p<=alpha and not contaminated),
                state='contaminated' if contaminated else ('screened' if observed else 'insufficient_data'),
                measured_tests=observed, missing_tests=[k for k in planned_tests if k not in observed],
                alpha=alpha, interpretation='null screening tail bound, not discovery probability',
                limitations=['Requires calibrated marginal nulls; selection and underestimated errors can invalidate calibration.'])


def gaussian_tails(motion_xy=None, variability_z=None, catalog_xy=None):
    """Inputs are whitened residuals with independently justified covariance."""
    out = {}
    for name, v in (('motion',motion_xy),('catalog',catalog_xy)):
        if v is not None and (len(v)!=2 or not all(math.isfinite(x) for x in v)):
            raise ValueError('each whitened position residual must have two finite coordinates')
        out[name] = None if v is None else float(chi2.sf(sum(float(x)**2 for x in v),2))
    if variability_z is not None and not math.isfinite(variability_z):
        raise ValueError('finite variability residual required')
    out['variability'] = None if variability_z is None else float(2*norm.sf(abs(variability_z)))
    return out


def needs_expensive_analysis(result, snr, contaminated=False, threshold=.1):
    """Explicit cost policy; sensitivity must be measured for a target population."""
    return bool(not contaminated and snr is not None and snr>=5 and
                result['adjusted_tail'] is not None and result['adjusted_tail']<=threshold)
