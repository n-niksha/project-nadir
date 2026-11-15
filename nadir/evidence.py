"""Read-only, deterministic scientific evidence snapshots. No network or fits."""
from __future__ import annotations
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
from . import field, __version__


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def investigation_report(f, sid):
    track = next((t for t in f.tracks if t.id == sid), None)
    if track is None:
        raise KeyError(sid)
    observations = []
    for i, obs in enumerate(f.observations):
        row = obs.summary()
        row.update(index=i, usable=f.usable[i] if i < len(f.usable) else None,
                   unavailable_reason=f.unusable_reason[i] if i < len(f.unusable_reason) else None,
                   provenance=copy.deepcopy(obs.provenance or {}), local_sha256=None)
        if obs.path:
            try:
                row['local_sha256'] = hashlib.sha256(Path(obs.path).read_bytes()).hexdigest()
            except OSError:
                row['local_file_status'] = 'unavailable_for_hashing'
        else:
            row['local_file_status'] = 'in_memory_only'
        observations.append(row)
    dataset = hashlib.sha256(canonical_json(field.clean(observations)).encode()).hexdigest()
    source = copy.deepcopy(f.source_summary(track))
    source['points'] = copy.deepcopy(track.points)
    source['coordinate_frame'] = 'ICRS'
    source['coordinate_epoch_mjd'] = f.observations[f.ref].mjd if f.ref is not None else None
    source['source_key'] = f'{f.fid}:{dataset[:16]}:{sid}'
    source['id_scope'] = 'Integer ID is local to this field build; use source_key and dataset fingerprint for saved evidence.'
    spectrum = copy.deepcopy(f.spectra.get(sid))
    catalog = copy.deepcopy(getattr(track, 'catalog', None))
    failure_reasons = []
    if not track.fit:
        failure_reasons.append({'code':'insufficient_motion_evidence','retryable':False})
    if f.catalogs is None:
        failure_reasons.append({'code':'catalogs_not_loaded','retryable':True})
    elif f.catalogs.get('errors'):
        failure_reasons.append({'code':'catalog_query_failed','retryable':True,'services':copy.deepcopy(f.catalogs['errors'])})
    if spectrum is None:
        failure_reasons.append({'code':'spectrum_not_computed','retryable':True})
    if track.core_masked_fraction > 0:
        failure_reasons.append({'code':'core_masked_astrometric_systematics','retryable':False})
    source_files = ['field.py','science/motion.py','science/fieldtrack.py','science/registration.py',
                    'science/sources.py','science/spectral.py','science/observation.py','evidence.py']
    root = Path(__file__).parent
    code_hash = hashlib.sha256(b''.join(name.encode()+b'\0'+(root/name).read_bytes() for name in source_files)).hexdigest()
    versions = {name:importlib.metadata.version(name) for name in ('numpy','scipy','astropy','fastapi')}
    report = field.clean(dict(
        schema_version=1, report_type='nadir_scientific_investigation',
        field={'fid':f.fid,'ra':f.ra,'dec':f.dec,'size_deg':f.size_deg,'band':f.band,'reference_index':f.ref},
        source=source, observations=observations,
        motion={'measurement':copy.deepcopy(track.fit),'units':'arcsec/year; positional residuals arcsec',
                'method':'existing visit-aware relative astrometry',
                'limitations':['Includes empirical floors; reference-star motion and shared registration covariance remain limitations.',
                               'No independently measured parallax. Saturated/blended centroids may move without physical motion.']},
        alignment={'reference_index':f.ref,'per_observation':copy.deepcopy(f.reg_stats)},
        catalog_comparisons={'matches':catalog,'services':None if f.catalogs is None else {
                             'errors':copy.deepcopy(f.catalogs.get('errors',{})),
                             'catalogs':{name:{'n_rows':len(data['rows']),'provenance':copy.deepcopy(data['provenance'])}
                                         for name,data in f.catalogs.get('data',{}).items()}},
                             'role':'external comparison; catalog PM may be used for epoch matching and spectral positioning, not the motion fit'},
        variability=copy.deepcopy(track.variability),
        spectrum={'state':'available' if spectrum is not None else 'not_computed','result':spectrum,
                  'missing_images':f.spectral_missing,'failures':copy.deepcopy(f.spectral_failures),
                  'limitations':['Non-simultaneous wavelength sampling; WCS-WAVE approximate.',
                                 'Photometry/model estimates are not chemical abundances or confirmed classifications.']},
        classification={'tier':track.tier,'reasons':list(track.reasons),'discovery_confirmed':False,
                        'experimental_results_included':False},
        failure_reasons=failure_reasons, processing_failures=copy.deepcopy(f.failures),
        reproducibility={'nadir_version':__version__,'dependency_versions':versions,
                         'scientific_code_sha256':code_hash,'dataset_sha256':dataset,
                         'field_built_at':f.built_at,'query':copy.deepcopy(f.provenance),
                         'hash_notes':'local_sha256 hashes actual cached FITS; response_sha256 in provenance hashes original archive response.',
                         'reproduction':'Use observation source URLs and parameters; verify local/response hashes and scientific code digest.',
                         'snapshot_policy':'No new catalogs, downloads, spectra, deblending or reconstruction run while exporting.'}))
    report['report_sha256'] = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    return report


def markdown_report(report):
    """Plain Markdown download; links/content remain data, never executable HTML."""
    source=report['source']
    return ('# NADIR scientific investigation\n\n'
            f"Source key: `{source['source_key']}`\n\n"
            f"ICRS coordinates: {source['ra']}°, {source['dec']}°\n\n"
            f"Quality: **{report['classification']['tier']}**. No confirmed discovery claim.\n\n"
            'This snapshot contains existing measurements only. Experimental decomposition and reconstruction are excluded.\n\n'
            '## Full reproducible evidence\n\n```json\n'+json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n```\n')
