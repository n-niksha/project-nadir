# Project NADIR — scientific engine, milestone 1

A working vertical slice for the 2026 NASA Space Apps Challenge: real SPHEREx
ingestion → time-separated image comparison → a baseline relative-motion
detector, with a minimal web interface.

Given a sky position, NADIR finds every SPHEREx Level-2 spectral image (DR1 +
QR3) covering it, downloads small cutouts from IRSA, picks epochs that match in
wavelength, aligns them on a common grid, and measures how far each source
moved relative to the field stars — with uncertainties, quality caveats and a
Gaia DR3 cross-check. If something cannot be measured reliably, it says so.

## Install

Requires Python 3.10+ and internet access to `irsa.ipac.caltech.edu`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
# 1. Tests (offline, synthetic data with known injected shifts) — ~5 s
.venv/bin/python -m pytest tests -q

# 2. Real-data demonstration on Barnard's Star (first run downloads ~60 cutouts, 1–2 min)
.venv/bin/python scripts/run_demo.py --track

# 3. Web interface
.venv/bin/python -m uvicorn nadir.api.server:app --port 8000
#    then open http://127.0.0.1:8000  and press "Search SPHEREx"
#    (the Barnard's Star preset is pre-filled)

# 4. Benchmark
.venv/bin/python scripts/benchmark.py --real
```

Other targets: `scripts/run_demo.py --ra 43.2796 --dec 16.8538 --track`
(Teegarden's Star), or type a name in the UI and press **Resolve** (SIMBAD;
high-proper-motion objects are propagated to epoch 2026 — Barnard's Star is
4.6′ from its J2000 position). Links are shareable:
`http://127.0.0.1:8000/?ra=269.44627&dec=4.76824&band=D2&mode=blink`.

### Using the interface

1. Enter RA/Dec (decimal degrees, ICRS), choose a detector band, **Search**.
2. The best pair (longest baseline, matching wavelength) is compared
   automatically; pick other dates from *Suggested pair* / *Epoch A* / *Epoch B*.
3. **Epoch A / Epoch B / Blink / Difference** switch the view. *star-registered B*
   toggles between WCS-only alignment and alignment refined with field stars.
4. Click a source for its positions, timestamps, displacement, caveats and Gaia
   match. **Track across all epochs** measures it in every image of the band.

## What you get on real data (run 2026-11-13)

Barnard's Star, band D2, epochs 2025-08-25 vs 2026-09-22 (393 d, Δλ/λ = 0.4 %):

| | East | North |
|---|---|---|
| Measured displacement | −1.79 ± 1.23″ | +11.06 ± 1.23″ |
| Gaia DR3 prediction (PM + parallax) | −0.92″ | +11.03″ |
| 58-epoch linear fit | −0.83 ± 0.50 ″/yr | +10.26 ± 0.28 ″/yr |
| Gaia DR3 proper motion | −0.80 ″/yr | +10.36 ″/yr |

The star is **saturated** in SPHEREx, so the pair measurement is reported as a
*low-confidence* candidate with an explicit systematic floor — see
[docs/SCIENCE.md](docs/SCIENCE.md) for what that means and for all limitations.

## Layout

```
nadir/data/       archive + catalog access, cache, provenance   (no science)
nadir/science/    FITS parsing, detection, registration, motion (no I/O, no web)
nadir/pipeline.py orchestration used by both the API and the scripts
nadir/api/        FastAPI endpoints + PNG rendering              (presentation)
nadir/ml/         feature interface for future models (nothing trained)
frontend/         static HTML/JS/CSS, no build step
scripts/          run_demo.py, benchmark.py
tests/            synthetic-truth tests, fully offline
data/cache/       downloaded cutouts + provenance JSON (git-ignored)
```

Design rationale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Method, validation and limitations: [docs/SCIENCE.md](docs/SCIENCE.md).

## Data

SPHEREx data are served by the NASA/IPAC Infrared Science Archive (IRSA).
Nothing is committed to Git: cutouts live in `data/cache/` (override with
`NADIR_CACHE_DIR`), each with a JSON provenance record (source URL, release,
retrieval time, SHA-256 of the response, archive checksum of the parent file).
Delete the directory to force fresh downloads.
