"""HTTP API + static frontend.  Thin presentation layer over ``nadir.field``
(and the milestone-1 ``nadir.pipeline`` endpoints, kept for reproducibility).

    uvicorn nadir.api.server:app --port 8000
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field as PField

from .. import __version__, config, field, pipeline
from ..data import catalogs, irsa
from ..data.irsa import DataAccessError
from ..jobs import manager
from . import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"

app = FastAPI(title="Project NADIR", version=__version__,
              description="SPHEREx time-domain explorer: real NASA SPHEREx images, registered across epochs.")
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.on_event("startup")
def _prewarm_demo():
    """Rebuild the demo field from the local cache in the background, so the
    first visitor does not wait.  Uses only cached data if it exists."""
    demo = next((p for p in config.PRESETS if p.get("demo")), None)
    if demo is None:
        return
    key = f"SIA spherex dr1+qr3 {demo['ra']:.6f} {demo['dec']:.6f}"
    if irsa._query_cache_path(key).exists():
        fid = field.field_id(demo["ra"], demo["dec"], demo["size"], demo["band"])
        manager.submit("field", f"field:{fid}:False", field.STAGES,
                       lambda j: field.build_field(j, demo["ra"], demo["dec"], demo["size"], demo["band"]))

RA = Query(..., ge=0, lt=360, description="Right ascension, ICRS degrees")
DEC = Query(..., ge=-90, le=90, description="Declination, ICRS degrees")
SIZE = Query(config.DEFAULT_CUTOUT_SIZE_DEG, ge=0.02, le=0.5)
BAND = Query(config.DEFAULT_BAND, pattern="^D[1-6]$")
IMMUTABLE = {"Cache-Control": "public, max-age=86400"}


# ------------------------------------------------------------------ general
@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/config")
def app_config():
    return {"version": __version__, "hips_url": config.SPHEREX_HIPS, "bands": {
        "D1": [0.75, 1.09], "D2": [1.10, 1.62], "D3": [1.63, 2.41], "D4": [2.42, 3.82], "D5": [3.83, 4.41],
        "D6": [4.42, 5.00]}, "pixel_scale_arcsec": config.PIXEL_SCALE_ARCSEC}


@app.get("/api/presets")
def presets():
    """Curated targets and whether their discovery query is already cached."""
    out = []
    for p in config.PRESETS:
        key = f"SIA spherex dr1+qr3 {p['ra']:.6f} {p['dec']:.6f}"
        out.append(dict(p, cached=irsa._query_cache_path(key).exists()))
    return out


@app.get("/api/resolve")
def resolve(name: str, epoch: float = 2026.0):
    """SIMBAD name lookup, proper-motion-propagated to ``epoch``."""
    try:
        return catalogs.resolve_name(name, epoch)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except DataAccessError as exc:
        raise HTTPException(404 if "does not know" in str(exc) else 502, str(exc))


# --------------------------------------------------------------------- jobs
@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job (it may have expired)")
    return job.snapshot()


@app.delete("/api/jobs/{job_id}")
def job_cancel(job_id: str):
    return {"cancelled": manager.cancel(job_id)}


# ------------------------------------------------------------------- fields
class FieldRequest(BaseModel):
    ra: float = PField(..., ge=0, lt=360)
    dec: float = PField(..., ge=-90, le=90)
    size: float = PField(config.DEFAULT_CUTOUT_SIZE_DEG, ge=0.05, le=0.5)
    band: str = PField(config.DEFAULT_BAND, pattern="^D[1-6]$")
    refresh: bool = False


@app.post("/api/fields")
def field_build(req: FieldRequest = Body(...)):
    fid = field.field_id(req.ra, req.dec, req.size, req.band)
    if field.get_field(fid) is not None and not req.refresh:
        return {"fid": fid, "job": None, "ready": True}
    job = manager.submit("field", f"field:{fid}:{req.refresh}", field.STAGES,
                         lambda j: field.build_field(j, req.ra, req.dec, req.size, req.band, req.refresh))
    return {"fid": fid, "job": job.snapshot(), "ready": False}


def _field(fid: str) -> field.Field:
    f = field.get_field(fid)
    if f is None:
        raise HTTPException(404, "Field not built (or evicted); POST /api/fields again")
    return f


@app.get("/api/fields/{fid}")
def field_summary(fid: str):
    return _field(fid).summary()


@app.get("/api/fields/{fid}/frames/{index}.png")
def field_frame(fid: str, index: int):
    f = _field(fid)
    if index not in f.display:
        raise HTTPException(404, "No registered frame for this observation")
    return Response(field.frame_png(f, index), media_type="image/png", headers=IMMUTABLE)


@app.get("/api/fields/{fid}/diff.png")
def field_diff(fid: str, a: int, b: int):
    f = _field(fid)
    if a not in f.display or b not in f.display or a == b:
        raise HTTPException(400, "Pick two different registered observations")
    png, _ = field.diff_png(f, a, b)
    return Response(png, media_type="image/png", headers=IMMUTABLE)


@app.get("/api/fields/{fid}/pair")
def field_pair(fid: str, a: int, b: int):
    f = _field(fid)
    if a == b:
        raise HTTPException(400, "Pick two different observations")
    try:
        return field.pair_view(f, a, b)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/fields/{fid}/sources/{sid}")
def field_source(fid: str, sid: int):
    try:
        return _field(fid).source_detail(sid)
    except KeyError:
        raise HTTPException(404, "No such source in this field")


@app.get("/api/fields/{fid}/epochs/{index}/provenance")
def field_provenance(fid: str, index: int):
    f = _field(fid)
    if not 0 <= index < len(f.observations):
        raise HTTPException(404, "No such observation")
    o = f.observations[index]
    return {"observation": field.clean(f.epoch_info(index)), "provenance": o.provenance,
            "archive_query": f.provenance.get("query")}


@app.post("/api/fields/{fid}/catalogs")
def field_catalogs_build(fid: str):
    f = _field(fid)
    if f.catalogs is not None:
        return {"job": None, "ready": True}
    job = manager.submit("catalogs", f"catalogs:{fid}", ["catalogs"], lambda j: field.load_catalogs(j, f))
    return {"job": job.snapshot(), "ready": False}


@app.get("/api/fields/{fid}/catalogs")
def field_catalogs(fid: str):
    return field.catalog_overlay(_field(fid))


@app.post("/api/fields/{fid}/spectral")
def field_spectral_build(fid: str, cached_only: bool = True):
    f = _field(fid)
    if f.spectral_obs is not None and (cached_only or not f.spectral_missing):
        return {"job": None, "ready": True}
    job = manager.submit("spectral", f"spectral:{fid}:{cached_only}", ["download", "read"], lambda j: field.load_spectral_set(j, f, cached_only))
    return {"job": job.snapshot(), "ready": False}


@app.get("/api/fields/{fid}/sources/{sid}/spectrum")
def field_spectrum(fid: str, sid: int):
    f = _field(fid)
    try:
        return field.source_spectrum(f, sid)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except StopIteration:
        raise HTTPException(404, "No such source")


# ---------------------------------------------- milestone-1 endpoints (kept)
def _session(ra, dec, size, band):
    try:
        return pipeline.search(ra, dec, size, band)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except DataAccessError as exc:
        raise HTTPException(502, f"Archive unavailable: {exc}")


@app.get("/api/search")
def search(ra: float = RA, dec: float = DEC, size: float = SIZE, band: str = BAND):
    return pipeline.session_summary(_session(ra, dec, size, band))


@app.get("/api/analyze")
def analyze(a: int, b: int, ra: float = RA, dec: float = DEC, size: float = SIZE, band: str = BAND):
    session = _session(ra, dec, size, band)
    try:
        out = pipeline.analyze(session, a, b)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    arr = out.pop("arrays")
    lo, hi = render.stretch_limits(arr["a"].image, arr["b"].image)
    soft = 5.0 * (out["difference"]["robust_sigma"] or 1.0)
    finite = np.abs(arr["diff"][np.isfinite(arr["diff"])])
    vmax = max(float(np.percentile(finite, 99.9)) if finite.size else 1.0, 2 * soft)
    out["images"] = {
        "a": render.gray_png(arr["a"].image, lo, hi, arr["a"].saturated),
        "b": render.gray_png(arr["b"].image, lo, hi, arr["b"].saturated),
        "b_wcs": render.gray_png(arr["b_wcs"].image, lo, hi, arr["b_wcs"].saturated),
        "diff": render.diverging_png(arr["diff"], soft, vmax),
        "stretch": {"lo": lo, "hi": hi, "unit": "MJy/sr", "scaling": "asinh", "diff_range": vmax, "diff_soft": soft},
    }
    return out


@app.get("/api/track")
def track(ref: int, sra: float, sdec: float, ra: float = RA, dec: float = DEC, size: float = SIZE,
          band: str = BAND):
    session = _session(ra, dec, size, band)
    try:
        return pipeline.track(session, ref, sra, sdec)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/provenance")
def provenance(index: int, ra: float = RA, dec: float = DEC, size: float = SIZE, band: str = BAND):
    session = _session(ra, dec, size, band)
    if not 0 <= index < len(session.observations):
        raise HTTPException(404, "No such observation")
    obs = session.observations[index]
    return {"observation": obs.summary(), "provenance": obs.provenance, "archive_query": session.query}


# ---------------------------------------------------------------- frontend
@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
