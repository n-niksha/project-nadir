"""Transparent linear reconstruction with an explicit sparse sampling operator.

Bilinear deposition preserves a constant surface brightness; no invented
high-frequency information. Diagonal variances are propagated with squared
weights. Neighboring output pixels are correlated: the operator is returned
for scientific callers; diagonal-only errors must not be used in PSF fits.
"""
from __future__ import annotations
import numpy as np
from scipy.sparse import coo_matrix


def coadd(stamps, scale=1):
    if scale not in (1, 2):
        raise ValueError('only native or 2x sampling is supported')
    if not stamps or len({s.obs_id for s in stamps}) != len(stamps):
        raise ValueError('at least one uniquely identified exposure required')
    shape = stamps[0].image.shape
    if any(s.image.shape != shape for s in stamps):
        raise ValueError('matching stamp shapes required')
    for s in stamps:
        s.validate()
    outshape = tuple(v*scale for v in shape)
    yy, xx = np.indices(shape)
    rows, cols, weights = [], [], []
    data, variances = [], []
    offset = 0
    for s in stamps:
        good = s.good & np.isfinite(s.image) & np.isfinite(s.variance) & (s.variance>0)
        x = (xx[good]-s.shift[0]+.5)*scale-.5
        y = (yy[good]-s.shift[1]+.5)*scale-.5
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        v = s.variance[good]
        ids = np.arange(len(v))+offset
        for dx,dy in ((0,0),(1,0),(0,1),(1,1)):
            ix,iy = x0+dx,y0+dy
            w = (1-np.abs(x-ix))*(1-np.abs(y-iy))/v
            keep = (ix>=0)&(iy>=0)&(ix<outshape[1])&(iy<outshape[0])&(w>0)
            rows.extend((iy[keep]*outshape[1]+ix[keep]).tolist())
            cols.extend(ids[keep].tolist()); weights.extend(w[keep].tolist())
        data.extend(s.image[good]); variances.extend(v); offset += len(v)
    a = coo_matrix((weights,(rows,cols)),shape=(int(np.prod(outshape)),offset)).tocsr()
    normalization = np.asarray(a.sum(axis=1)).ravel()
    a = a.multiply((1/np.maximum(normalization,1e-300))[:,None]).tocsr()
    image = np.asarray(a@np.asarray(data)).reshape(outshape)
    variance = np.asarray(a.power(2)@np.asarray(variances)).reshape(outshape)
    covered = normalization.reshape(outshape)>0
    image[~covered],variance[~covered] = np.nan,np.nan
    return dict(image=image, variance=variance, covered=covered, operator=a,
                metadata=dict(maturity='experimental', method='inverse-variance bilinear deposition',
                              sampling_scale=scale, optical_super_resolution=False,
                              observation_ids=[s.obs_id for s in stamps],
                              shifts_reference_to_native=[list(s.shift) for s in stamps],
                              covariance='A diag(input_variance) A.T; off-diagonal terms not in variance image',
                              assumptions=['Same bandpass, static sky, known translations, calibrated surface brightness.',
                                           'No rotation/PSF homogenization or pointing uncertainty propagation.',
                                           'Use native-pixel joint fitting for quantitative source decomposition.']))
