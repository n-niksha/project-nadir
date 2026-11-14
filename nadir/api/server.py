"""HTTP API + static frontend.  Thin presentation layer over ``nadir.pipeline``.

    uvicorn nadir.api.server:app --port 8000
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, config, pipeline
from ..data import catalogs
from ..data.irsa import DataAccessError
from . import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"

app = FastAPI(title="Project NADIR scientific engine", version=__version__)

RA = Query(..., ge=0, lt=360, description="Right ascension, ICRS degrees")
DEC = Query(..., ge=-90, le=90, description="Declination, ICRS degrees")
SIZE = Query(config.DEFAULT_CUTOUT_SIZE_DEG, ge=0.02, le=0.5)
BAND = Query(config.DEFAULT_BAND, pattern="^D[1-6]$")


def _session(ra, dec, size, band):
    try:
        return pipeline.search(ra, dec, size, band)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except DataAccessError as exc:
        raise HTTPException(502, f"Archive unavailable: {exc}")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/resolve")
def resolve(name: str, epoch: float = 2026.0):
    """SIMBAD name lookup, proper-motion-propagated to ``epoch``."""
    try:
        return catalogs.resolve_name(name, epoch)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except DataAccessError as exc:
        raise HTTPException(404 if "does not know" in str(exc) else 502, str(exc))


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
        "stretch": {"lo": lo, "hi": hi, "unit": "MJy/sr", "scaling": "asinh",
                    "diff_range": vmax, "diff_soft": soft, "diff_scaling": "symmetric asinh",
                    "diff_definition": "B x flux_scale - A, backgrounds removed"},
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


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
