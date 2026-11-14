#!/usr/bin/env python
"""Pre-fetch and pre-compute the curated demo fields so the demonstration runs
from the local cache even if the archive is slow or unreachable.

    python scripts/prepare_demo.py                 # all presets, spectra for the first three
    python scripts/prepare_demo.py --only barnard  # one preset

Prints per-stage timings (cold on a fresh cache, warm afterwards).  All data
are real SPHEREx cutouts from IRSA, cached under data/cache/ (not in Git).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nadir import config, field  # noqa: E402
from nadir.jobs import Job  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="preset ids")
    ap.add_argument("--spectra", nargs="*", default=["barnard", "teegarden", "vanmaanen"],
                    help="presets for which all six detectors are downloaded")
    ap.add_argument("--json", type=Path, help="write a timing/validation summary here")
    a = ap.parse_args()
    report = []
    for p in config.PRESETS:
        if a.only and p["id"] not in a.only:
            continue
        row = {"id": p["id"], "name": p["name"]}
        t = time.perf_counter()
        job = Job("field", p["id"], field.STAGES)
        try:
            r = field.build_field(job, p["ra"], p["dec"], p["size"], p["band"])
        except Exception as exc:
            print(f"{p['name']}: FAILED {exc}")
            row["error"] = str(exc)
            report.append(row)
            continue
        f = field.get_field(r["fid"])
        row["build_s"] = round(time.perf_counter() - t, 2)
        row["stages"] = f.timings
        row["provenance"] = {k: f.provenance[k] for k in ("n_from_cache", "n_downloaded")}
        t = time.perf_counter()
        field.load_catalogs(Job("cat", p["id"], ["catalogs"]), f)
        row["catalogs_s"] = round(time.perf_counter() - t, 2)
        row["catalog_errors"] = f.catalogs["errors"]
        if p["id"] in a.spectra:
            t = time.perf_counter()
            res = field.load_spectral_set(Job("spec", p["id"], ["download", "read"]), f)
            row["spectral_s"] = round(time.perf_counter() - t, 2)
            row["spectral_images"] = res["n_images"]
        s = f.summary()
        row["tiers"] = s["tier_counts"]
        row["n_epochs"] = len(s["epochs"])
        print(f"{p['name']:28s} build {row['build_s']:7.1f}s  catalogs {row['catalogs_s']:5.1f}s  "
              f"spectral {row.get('spectral_s', 0):6.1f}s  epochs {row['n_epochs']}  tiers {row['tiers']}", flush=True)
        report.append(row)
    if a.json:
        a.json.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
