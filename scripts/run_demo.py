#!/usr/bin/env python
"""Real-data demonstration: Barnard's Star (or any position) end to end.

    python scripts/run_demo.py                       # Barnard's Star, band D2
    python scripts/run_demo.py --ra 10.68 --dec 41.27 --band D3
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nadir import pipeline  # noqa: E402

# Gaia DR3 4472832130942575872 propagated to epoch 2026.0 (see docs/SCIENCE.md).
BARNARD = (269.44627, 4.76824)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ra", type=float, default=BARNARD[0])
    ap.add_argument("--dec", type=float, default=BARNARD[1])
    ap.add_argument("--size", type=float, default=0.2, help="cutout size in degrees")
    ap.add_argument("--band", default="D2")
    ap.add_argument("--pair", type=int, nargs=2, metavar=("A", "B"), help="observation indices (default: best pair)")
    ap.add_argument("--track", action="store_true", help="also run the multi-epoch track of the target")
    ap.add_argument("--json", type=Path, help="write the full result (without images) to this file")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    s = pipeline.search(args.ra, args.dec, args.size, args.band)
    summ = pipeline.session_summary(s)
    print(f"Position RA={s.ra:.5f} Dec={s.dec:.5f}  band {s.band}  cutout {s.size_deg} deg")
    print(f"Archive: {summ['n_in_archive']} images cover the position; loaded {summ['n_loaded']}, "
          f"usable {summ['n_usable']}, failed {len(summ['failures'])}")
    print(f"Timings: {json.dumps({k: round(v, 2) for k, v in s.timings.items()})}")
    print(f"\n{'idx':>3} {'date (UTC, mid-exposure)':26} {'MJD':>10} {'lambda um':>9} rel   use  obs_id")
    for o in summ["observations"]:
        print(f"{o['index']:>3} {str(o['date']):26} {o['mjd'] or float('nan'):>10.3f} "
              f"{o['wavelength_um'] or float('nan'):>9.4f} {str(o['release']):5} "
              f"{'yes' if o['usable'] else 'NO ':3}  {o['obs_id']} {o['unusable_reason']}")
    if summ["pair_note"]:
        print("\nNOTE:", summ["pair_note"])
    if args.pair:
        ia, ib = args.pair
    elif summ["suggested_pairs"]:
        ia, ib = summ["suggested_pairs"][0]["a"], summ["suggested_pairs"][0]["b"]
    else:
        print("No usable pair; stopping.")
        return 1

    r = pipeline.analyze(s, ia, ib)
    r.pop("arrays")
    print(f"\n=== Pair A=[{r['a']['index']}] {r['a']['date']}  B=[{r['b']['index']}] {r['b']['date']} ===")
    print(f"A: {r['a']['source_url']}\nB: {r['b']['source_url']}")
    print(f"Baseline {r['dt_days']:.2f} d; wavelengths {r['a']['wavelength_um']:.4f} / {r['b']['wavelength_um']:.4f} um "
          f"(rel. diff {r['rel_dlambda']:.3%})")
    print(r["message"])
    if r["ok"]:
        print("Registration:", json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                            for k, v in r["registration"].items()}))
        print("Cross-correlation check:", r["xcorr_check"])
        print("Gaia:", {k: v for k, v in r["gaia"].items() if k != "provenance"})
        print("\nVERDICT:", r["verdict"]["headline"])
        print("TARGET :", r["verdict"]["target"])
        by_id = {x["id"]: x for x in r["sources"]}
        for cid in r["verdict"]["candidates"][:8]:
            c = by_id[cid]
            print(f"  candidate #{cid} RA={c['ra']:.5f} Dec={c['dec']:.5f} [{c['status']}]\n    "
                  + pipeline.describe_source(c, r["dt_days"]))
    print("Timings:", json.dumps({k: round(v, 3) for k, v in r["timings"].items()}))

    if args.track:
        tgt = next((x for x in r["sources"] if x["id"] == r["target_id"]), None)
        tra, tdec = (tgt["ra"], tgt["dec"]) if tgt else (s.ra, s.dec)
        tr = pipeline.track(s, r["a"]["index"], tra, tdec)
        r["track"] = tr
        print(f"\n=== Multi-epoch track ({len(tr['points'])} epochs, {len(tr['skipped'])} skipped, "
              f"{tr['elapsed_s']:.1f} s) ===")
        for p in tr["points"]:
            print(f"  {p['date']}  E {p['east_arcsec']:+7.2f} ± {p['sigma_east_arcsec']:.2f}   "
                  f"N {p['north_arcsec']:+7.2f} ± {p['sigma_north_arcsec']:.2f}  "
                  f"{'SAT' if p['saturated'] else ''}{' REF' if p['is_reference_epoch'] else ''}")
        if tr["fit"]:
            f = tr["fit"]
            print(f"  Linear fit over {f['baseline_days']:.0f} d: "
                  f"pm_E = {f['east']['pm_arcsec_per_yr']:+.2f} ± {f['east']['pm_err_arcsec_per_yr']:.2f} \"/yr, "
                  f"pm_N = {f['north']['pm_arcsec_per_yr']:+.2f} ± {f['north']['pm_err_arcsec_per_yr']:.2f} \"/yr "
                  f"(chi2_red {f['east']['chi2_red']:.1f}/{f['north']['chi2_red']:.1f})")
        else:
            print(" ", tr.get("fit_note"))
        if tr.get("catalog"):
            c = tr["catalog"]
            print(f"  Gaia DR3 {c['source_id']}: pm_E = {c['pm_east_arcsec_per_yr']:+.3f}, "
                  f"pm_N = {c['pm_north_arcsec_per_yr']:+.3f} \"/yr, parallax {c['parallax_mas']:.1f} mas")
    if args.json:
        args.json.write_text(json.dumps(r, indent=1))
        print(f"\nFull result written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
