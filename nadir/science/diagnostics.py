"""Read-only observation diagnostics and conservative native stamp adapter."""
from __future__ import annotations
import numpy as np
from .observation import WARN_FLAGS
from .deblend import Stamp, compare_sources
from .psf import detector_xy


def observation_diagnostics(obs, ra, dec, reference_count=None):
    out = dict(obs_id=obs.obs_id, state='available', reasons=[], wavelength_um=obs.wavelength_um,
               bandwidth_um=obs.bandwidth_um, mjd=obs.mjd, detector=obs.detector,
               reference_count=reference_count, provenance=obs.provenance or {})
    try:
        if not np.isfinite(obs.wcs.wcs.crval).all():
            raise ValueError('nonfinite WCS reference coordinates')
        x,y = obs.wcs.all_world2pix([[ra,dec]],0)[0]
        if not np.isfinite([x,y]).all():
            raise ValueError('nonfinite WCS position')
    except (ValueError,TypeError) as exc:
        return dict(out,state='software_or_metadata_failure',reasons=[str(exc)])
    out.update(x=float(x), y=float(y), subpixel_phase=[float(x%1),float(y%1)])
    ny,nx=obs.image.shape
    if not (5<=x<nx-5 and 5<=y<ny-5):
        return dict(out,state='missing_data',reasons=['Target outside usable cutout footprint.'])
    ix,iy=int(round(x)),int(round(y))
    sl=np.s_[iy-1:iy+2,ix-1:ix+2]
    flags=int(np.bitwise_or.reduce(obs.flags[sl].ravel()))
    out.update(saturated=bool(obs.saturated_mask[sl].any()),core_bad=bool(obs.bad_mask[sl].any()),
               flags=[f for f in obs.flag_names(flags) if f not in ('SOURCE','FULLSAMPLE')], epsf_available=obs.epsf is not None)
    if out['core_bad'] or out['flags']:
        out['state']='contamination';out['reasons'].append('Core masked or instrument warning flags present.')
    if obs.mjd is None or obs.wavelength_um is None or obs.epsf is None:
        out['reasons'].append('Required timestamp, wavelength or ePSF metadata missing.')
        if out['state']=='available': out['state']='missing_metadata'
    if reference_count is not None and reference_count<3:
        out['reasons'].append('Fewer than three reference stars; registered motion unavailable.')
    out['spectral_calibration']='WCS-WAVE approximate; science CWAVE/CBAND calibration not loaded'
    return out


def observation_stamp(obs, ra, dec, half=5):
    diag=observation_diagnostics(obs,ra,dec)
    if diag['state'] in ('missing_data','software_or_metadata_failure','missing_metadata'):
        return None,diag
    x,y=diag['x'],diag['y'];ix,iy=int(round(x)),int(round(y))
    sl=np.s_[iy-half:iy+half+1,ix-half:ix+half+1]
    if obs.epsf is None or obs.wcs_det is None:
        return None,dict(diag,state='contamination' if diag['state']=='contamination' else 'missing_metadata',
                         reasons=diag['reasons']+['Archive ePSF or detector WCS missing.'])
    psf=obs.epsf.at(*detector_xy(obs.wcs_det,x,y))
    strict=obs.bad_mask | ((obs.flags & obs.bitmask((*WARN_FLAGS,'SATELLITE_HALO')))!=0)
    stamp=Stamp(obs.image[sl].copy(),obs.variance[sl].copy(),~strict[sl],obs.obs_id,
                epsf=psf, flags=diag['flags'],provenance=obs.provenance or {})
    return stamp,diag


def decompose_observation(obs,ra,dec,bootstrap=0,seed=0,check=None):
    stamp,diag=observation_stamp(obs,ra,dec)
    result=compare_sources([] if stamp is None else [stamp],f'ICRS:{ra:.7f},{dec:.7f}',ra,dec,bootstrap,seed,check=check)
    result['diagnostics']=diag
    result['provenance']=obs.provenance or {}
    if diag['state']=='contamination':
        result['state']='invalid_or_contaminated_observation'
    if result['separation_px'] is not None:
        from astropy.wcs.utils import proj_plane_pixel_scales
        scale=float(np.mean(proj_plane_pixel_scales(obs.wcs.celestial))*3600)
        result['separation_arcsec']=result['separation_px']*scale
    return result
