"""Optional experimental endpoints over already-loaded native observations."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .. import field
from ..jobs import manager
from ..science.diagnostics import decompose_observation, observation_diagnostics
from ..ml.evidence import extract_features, screen

router=APIRouter(prefix='/api/science',tags=['experimental science'])


class DecompositionRequest(BaseModel):
    observation_index: int = Field(...,ge=0)
    ra: float = Field(...,ge=0,lt=360,allow_inf_nan=False)
    dec: float = Field(...,ge=-90,le=90,allow_inf_nan=False)
    bootstrap: int = Field(0,ge=0,le=199)
    seed: int = Field(0,ge=0,le=2**32-1)


class DecompositionResult(BaseModel):
    schema_version: int
    maturity: Literal['experimental']
    source_id: str
    state: Literal['consistent_with_one_source','multiple_source_model_favored','ambiguous',
                   'insufficient_data','invalid_or_contaminated_observation']
    ra: float | None
    dec: float | None
    best_fit_source_count: int | None
    separation_px: float | None
    flux_ratio: float | None
    uncertainty: dict | None
    observations_supporting: int
    observation_ids: list[str]
    flags: list[str]
    catalog_matches: list[dict]
    reasons_to_doubt: list[str]
    models: dict
    evidence: dict
    diagnostics: dict
    provenance: dict
    separation_arcsec: float | None = None
    elapsed_s: float | None = None


def get_field(fid):
    f=field.get_field(fid)
    if f is None:
        raise HTTPException(404,'Field not built; POST /api/fields first')
    return f


@router.get('/fields/{fid}/diagnostics')
def diagnostics(fid: str):
    f=get_field(fid)
    return dict(schema_version=1, maturity='experimental',fid=fid,
                observations=[observation_diagnostics(o,f.ra,f.dec) for o in f.observations],
                failures=f.failures)


@router.get('/fields/{fid}/sources/{sid}/features')
def features(fid: str, sid: int):
    f=get_field(fid)
    track=next((t for t in f.tracks if t.id==sid),None)
    if track is None:
        raise HTTPException(404,'No such source')
    fit=track.fit or {}
    measured={'rate_arcsec_per_yr':fit.get('pm_total'),'significance':fit.get('snr')}
    return dict(schema_version=1,maturity='experimental',source_id=sid,
                measurements=extract_features(measured,{'field_id':fid,'source_id':sid,
                                                        'method':'existing visit-aware motion pipeline'}),
                screening=screen({}),
                note='Real-field marginal nulls are not calibrated; no probability is derived from heuristic scores.',
                quality={'core_masked_fraction':track.core_masked_fraction,'blended_fraction':track.blended_fraction,
                         'artifact_fraction':track.artifact_fraction},
                existing_priority={'value':track.priority,'interpretation':'existing heuristic rank, not probability'})


@router.post('/fields/{fid}/decomposition')
def decomposition(fid: str,req: DecompositionRequest):
    f=get_field(fid)
    if req.observation_index>=len(f.observations):
        raise HTTPException(404,'No such observation')
    obs=f.observations[req.observation_index]
    key=f'decomposition:v1:{fid}:{id(f)}:{req.model_dump_json()}'
    # Existing bounded field LRU owns this small result cache; no archive I/O.
    def run(job):
        with f.lock:
            cache=getattr(f,'decomposition_cache',{})
            if key in cache:
                return cache[key]
        job.set_stage('fit','Experimental native-pixel decomposition')
        result=decompose_observation(obs,req.ra,req.dec,req.bootstrap,req.seed,check=job.check)
        result=DecompositionResult.model_validate(result).model_dump()
        with f.lock:
            if not hasattr(f,'decomposition_cache'):f.decomposition_cache={}
            if len(f.decomposition_cache)>=32:f.decomposition_cache.pop(next(iter(f.decomposition_cache)))
            f.decomposition_cache[key]=result
        return result
    job=manager.submit('decomposition',key,['fit'],run)
    return {'job':job.snapshot(),'maturity':'experimental'}
