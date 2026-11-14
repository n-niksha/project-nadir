# Project NADIR — Milestone 3: explore the infrared sky

A scientific exploration instrument for the 2026 NASA Space Apps Challenge:
real SPHEREx observations, multi-epoch motion, contextual explanations and a
spectral laboratory. There is no trained machine-learning classifier.

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
.venv/bin/python -m uvicorn nadir.api.server:app --reload --port 8000
#    then open http://127.0.0.1:8000; the Barnard's Star demo loads automatically

# 4. Benchmark
.venv/bin/python scripts/benchmark.py --real
```

Other targets: `scripts/run_demo.py --ra 43.2796 --dec 16.8538 --track`
(Teegarden's Star), or type a name in the UI and press **Go** (SIMBAD;
high-proper-motion objects are propagated to epoch 2026 — Barnard's Star is
4.6′ from its J2000 position). Links are shareable:
`http://127.0.0.1:8000/?ra=269.44627&dec=4.76820&band=D2&mode=blink`.

### Using the interface

The current interface uses background jobs (`POST /api/fields`, then
`GET /api/jobs/{id}`) and registered frame PNGs. Run the frontend from the
same FastAPI server. If an API endpoint reports **Not Found**, stop any older
NADIR server and restart the command above from this checkout; an old running
backend can serve new static files while still exposing only its old routes.
The error panel's **Retry** and **Open cached Barnard's Star demo** buttons
retry startup after the server is restored.

For a browser regression check (requires Playwright and installed Google Chrome):

```bash
.venv/bin/python scripts/verify_viewer.py
```

This checks the real cached demo, blinking pixels, difference rendering,
source clicks, investigation tabs, and recovery after API 404s. Screenshots
and the report go to `artifacts/viewer-verification/`. See
[verification notes](docs/VERIFICATION.md) for the takeover findings.

1. The real Barnard demo loads automatically. **Guide me** is optional; tools
   are available immediately. On narrow screens use **Targets & layers**.
2. Click an exposure on the timeline to view it. The upper axis preserves
   calendar spacing; click a visit label to jump its exposure list into view.
   Unusable observations remain visible with their exclusion reason.
3. **Animate**, play/pause, speed and step buttons show actual registered images.
   **Motion** keeps wavelengths within 2% of A at the field center;
   **Spectral exploration** includes changing wavelengths. Playback is one
   observation per step, not a uniform physical-time movie or interpolation.
4. Use **Set current as A/B**, Shift-click for A, or Alt-click for B. Blink,
   opacity overlay and B−A remain available; differences are qualitative.
5. Switch between **Clean**, **Discovery**, and **Research** layers, then
   customize checkboxes. Click a source for its measurements. **Explain this
   observation** provides Explore and Research interpretations of its own data.
6. In **Discover**, filter evidence tiers and inspect candidates or low-motion
   comparison sources. Scores are transparent heuristics, not probabilities.
7. For an unsaturated spectrum, select **van Maanen's Star**, open Spectrum,
   then **Load cached spectral observations**. Missing cutouts are disclosed;
   fetching them is an explicit optional action. Hover/focus points, inspect
   the provenance ledger, and distinguish colour-model temperature from Teff.

### Validation and demonstration

```bash
.venv/bin/python -m pytest tests -q
node --test tests/frontend.test.mjs  # Node 22+; no npm dependencies
.venv/bin/pip install playwright   # optional browser-test dependency
# Install Google Chrome; launch the FastAPI server before browser checks.
.venv/bin/python scripts/verify_viewer.py
.venv/bin/python scripts/verify_milestone3.py --url http://127.0.0.1:8000
.venv/bin/python scripts/verify_atlas.py --url http://127.0.0.1:8000  # optional: live CDN/IRSA access
.venv/bin/python scripts/validate_cached.py  # requires existing caches; network forbidden
.venv/bin/python scripts/benchmark.py --repeat 3
```

Use a server without `--reload` during acceptance testing. A clean checkout
has no downloaded data: prepare a demo while online with
`scripts/prepare_demo.py --only barnard vanmaanen --spectra vanmaanen` before
an offline presentation (this can download uncached observations).

[90-second judge demo](docs/DEMO.md) · [Milestone 3 verification](docs/MILESTONE3.md)
· [Science and limitations](docs/SCIENCE.md) · [Data sources](docs/DATA_SOURCES.md).

## Historical milestone-1 pair benchmark (2026-11-13)

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
nadir/field.py    field jobs, registered epochs, tracks, catalogs and spectra
nadir/interpretation.py deterministic explanations of structured results
nadir/api/        FastAPI endpoints + PNG rendering              (presentation)
nadir/ml/         feature interface for future models (nothing trained)
frontend/         static HTML/JS/CSS, no build step
scripts/          run_demo.py, benchmark.py
tests/            synthetic-truth tests, fully offline
data/cache/       downloaded cutouts + provenance JSON (git-ignored)
```

Design rationale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Method, validation and limitations: [docs/SCIENCE.md](docs/SCIENCE.md).

Milestone 4 optional research engine: [experiments and scientific review](docs/MILESTONE4.md)
and [backend API contract](docs/MILESTONE4_API.md). Native-pixel decomposition,
joint fitting, reconstruction covariance and multi-feature screening are
experimental; no new object or real-data super-resolution is claimed.

## Data

SPHEREx data are served by the NASA/IPAC Infrared Science Archive (IRSA).
Nothing is committed to Git: cutouts live in `data/cache/` (override with
`NADIR_CACHE_DIR`), each with a JSON provenance record (source URL, release,
retrieval time, SHA-256 of the response, archive checksum of the parent file).
Delete the directory to force fresh downloads.
