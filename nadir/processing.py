"""Compute-heavy per-image steps with a disk cache and a process pool.

Source detection + centroiding costs ~0.3 s per 117 x 117 cutout and is pure
Python/SciPy (GIL-bound), so it runs in worker processes.  Results are cached
next to the cutout, keyed by the cutout file and ``DETECT_VERSION``; changing
the algorithm bumps the version and invalidates the cache.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import multiprocessing
import os
from dataclasses import asdict
from pathlib import Path

from . import config
from .science.sources import Source

log = logging.getLogger(__name__)
DETECT_VERSION = 4  # 4: Moffat wings (ePSF experiment reverted)
_pool = None


def _workers() -> int:
    env = os.environ.get("NADIR_DETECT_PROCESSES")
    if env is not None:
        return int(env)
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _cache_path(fits_path: str) -> Path:
    p = Path(fits_path)
    return config.CACHE_DIR / "detections" / f"{p.stem}_d{DETECT_VERSION}.json"


def _detect_one(fits_path: str, ra: float, dec: float, provenance: dict | None) -> list[dict]:
    from .science.observation import load_observation
    from .science.sources import detect_sources
    obs = load_observation(fits_path, (ra, dec), provenance)
    return [asdict(s) for s in detect_sources(obs)]


def _get_pool():
    global _pool
    if _pool is None:
        _pool = concurrent.futures.ProcessPoolExecutor(max_workers=_workers(),
                                                       mp_context=multiprocessing.get_context("spawn"))
    return _pool


def detect_many(items: list[tuple[str, float, float, dict | None]], progress=None, check=None) -> list[list[Source]]:
    """Detections for many cutouts: cached ones are read, the rest computed in parallel."""
    results: list = [None] * len(items)
    todo = []
    for i, (path, *_rest) in enumerate(items):
        cp = _cache_path(path)
        if cp.exists():
            try:
                results[i] = [Source(**d) for d in json.loads(cp.read_text())]
                continue
            except (json.JSONDecodeError, TypeError):
                log.warning("Corrupt detection cache %s; recomputing", cp)
        todo.append(i)
    done = len(items) - len(todo)
    if progress:
        progress(done, len(items), cached=done)

    def store(i, dicts):
        cp = _cache_path(items[i][0])
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(".tmp")
        tmp.write_text(json.dumps(dicts, allow_nan=True))
        tmp.replace(cp)
        results[i] = [Source(**d) for d in dicts]

    if todo and _workers() <= 1:
        for i in todo:
            if check:
                check()
            store(i, _detect_one(*items[i]))
            done += 1
            if progress:
                progress(done, len(items))
    elif todo:
        pool = _get_pool()
        futs = {pool.submit(_detect_one, *items[i]): i for i in todo}
        try:
            for fut in concurrent.futures.as_completed(futs):
                store(futs[fut], fut.result())
                done += 1
                if progress:
                    progress(done, len(items))
                if check:
                    check()
        except BaseException:
            for f in futs:
                f.cancel()
            raise
    return results
