#!/usr/bin/env python
"""Processing benchmark.

Stage timings on a synthetic 117x117 field (same size as a 0.2 deg SPHEREx
cutout), and -- with --real -- the end-to-end timings on the Barnard's Star
demonstration (network time is included only on a cold cache).

    python scripts/benchmark.py [--real] [--repeat 5]
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nadir.api import render  # noqa: E402
from nadir.science import motion, synthetic  # noqa: E402
from nadir.science.difference import difference_image  # noqa: E402
from nadir.science.reprojection import make_grid, reproject_observation  # noqa: E402
from nadir.science.sources import detect_sources  # noqa: E402


def timeit(fn, repeat):
    out, ts = None, []
    for _ in range(repeat):
        t = time.perf_counter()
        out = fn()
        ts.append(time.perf_counter() - t)
    return out, statistics.median(ts) * 1e3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="also time the real Barnard's Star demonstration")
    ap.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args()

    n, size = 117, 0.2
    stars = synthetic.random_field(120, 150.0, 20.0, size * 0.45, np.random.default_rng(0))
    oa = synthetic.render_observation(stars, n=n, mjd=61000.0, seed=1)
    ob = synthetic.render_observation(stars, n=n, mjd=61365.0, seed=2, rotation_deg=1.0, wcs_error_arcsec=(1, -1))
    grid = make_grid(150.0, 20.0, size)
    rows = []
    sa, t = timeit(lambda: detect_sources(oa), a.repeat); rows.append((f"detect + centroid ({len(sa)} sources)", t))
    sb = detect_sources(ob)
    res, t = timeit(lambda: motion.analyze_pair(oa, ob, grid, sa, sb), a.repeat); rows.append(("match + register + classify", t))
    ra_, t = timeit(lambda: reproject_observation(oa, grid), a.repeat); rows.append((f"reproject one image to {grid.shape[0]}^2 grid", t))
    rb_ = reproject_observation(ob, grid)
    d, t = timeit(lambda: difference_image(ra_, rb_), a.repeat); rows.append(("difference image", t))
    _, t = timeit(lambda: (render.gray_png(ra_.image, 0, 50), render.diverging_png(d["diff"], 1, 50)), a.repeat)
    rows.append(("render 2 PNGs", t))
    print(f"Synthetic {n}x{n} pair, 120 stars (median of {a.repeat}):")
    for name, ms in rows:
        print(f"  {name:45s} {ms:9.1f} ms")
    print(f"  {'TOTAL pair analysis (2 detects, 3 reprojections)':45s} "
          f"{2 * rows[0][1] + rows[1][1] + 3 * rows[2][1] + rows[3][1] + 2 * rows[4][1]:9.1f} ms")

    if a.real:
        from nadir import pipeline
        t = time.perf_counter()
        s = pipeline.search(269.44627, 4.76824, 0.2, "D2")
        t_search = time.perf_counter() - t
        cold = not s.query.get("cached")
        best = pipeline.session_summary(s)["suggested_pairs"][0]
        s.gaia()  # separate the (cached after first use) catalog query from the science timing
        t = time.perf_counter(); r = pipeline.analyze(s, best["a"], best["b"]); t_an = time.perf_counter() - t
        t = time.perf_counter(); r2 = pipeline.analyze(s, best["a"], best["b"]); t_an2 = time.perf_counter() - t
        t = time.perf_counter(); tr = pipeline.track(s, best["a"], s.ra, s.dec); t_tr = time.perf_counter() - t
        print(f"\nReal data, Barnard's Star D2 ({len(s.observations)} cutouts, {'COLD' if cold else 'warm'} cache):")
        print(f"  search (TAP + fetch + parse)                  {t_search:9.2f} s   {s.timings}")
        print(f"  analyze pair, first call                      {t_an:9.2f} s   {r['timings']}")
        print(f"  analyze pair, detections memoised             {t_an2:9.2f} s")
        print(f"  track over {len(tr['points'])} epochs (detects all images)     {t_tr:9.2f} s")


if __name__ == "__main__":
    main()
