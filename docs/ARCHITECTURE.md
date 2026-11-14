# Architecture decisions

## Shape

```
browser (static JS) ──HTTP/JSON──► nadir.api.server ──► nadir.pipeline ──► nadir.science.*   (pure numpy/astropy)
                                                              └──────────► nadir.data.*      (IRSA TAP, cutouts, Gaia, SIMBAD, cache)
```

Three layers with one-way dependencies:

| Layer | Knows about | Does not know about |
|---|---|---|
| `nadir.data` | HTTP, TAP, the cache directory, provenance | algorithms, the web |
| `nadir.science` | arrays, WCS, statistics | where data came from, HTTP, JSON |
| `nadir.api` + `frontend/` | how to draw results | how results are computed |

`nadir.pipeline` is the only module that touches both data and science; the
CLI scripts and the API call the same three functions (`search`, `analyze`,
`track`), so the UI cannot show something the documented commands cannot reproduce.

## Choices and why

- **Python + Astropy + reproject + SciPy.** Standard, well-tested astronomy
  stack; WCS with SIP distortion and the `WAVE-TAB` spectral lookup are handled
  by Astropy rather than re-implemented.
- **IRSA TAP (`spherex.plane` ⨝ `spherex.artifact`) for discovery, cutout
  service for pixels.** Verified live on 2026-11-13: TAP serves DR1 and QR3
  together and is updated before SIA; `?center=…&size=…` returns a 7-HDU MEF.
  Full images are 58 MB; a 0.2° cutout is 4.2 MB, of which 3.9 MB is the EPSF
  table. HTTP Range is not honoured, so the EPSF is downloaded and then dropped
  before caching (cached cutout ≈ 0.3 MB, noted in provenance).
- **Wavelength is evaluated per exposure at the target.** SPHEREx uses linear
  variable filters, so "same band" does not mean "same wavelength". Pairs are
  ranked by time baseline among those agreeing within 2 % (under one
  resolution element).
- **Astrometry on native pixels, display on a resampled grid.** Centroids are
  never measured on interpolated data. Reprojection (bilinear, 2× oversampled
  TAN grid, north up) exists only for the viewer and the difference image.
- **Catalog-free registration.** The transform between epochs comes from the
  SPHEREx sources themselves with robust clipping. Gaia is used only as an
  independent cross-check, never to produce the measurement.
- **FastAPI + static vanilla JS.** No build step, no framework; images are
  rendered server-side to PNG and drawn on a `<canvas>`. Recommended next step
  if the UI grows (sky atlas, spectra): keep the JSON API and move the frontend
  to a small Vite + TypeScript app; consider Aladin Lite for the atlas.
- **Caching.** Cutouts + provenance on disk (keyed by URL); TAP results on disk
  with a 7-day TTL (the archive ingests weekly); detections memoised per
  session in memory.
- **Failure handling.** Per-item download errors are collected, not fatal;
  non-FITS "200 OK" bodies are rejected; missing header keywords become
  `issues` on the observation; a missing Gaia/SIMBAD service degrades the
  cross-reference only (SIMBAD has a mirror fallback).
- **ML is an interface, not a model.** `nadir/ml/features.py` defines the
  per-source feature record. Training anything now would be unjustified: there
  is no labelled sample and one validated field.

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /api/search?ra&dec&size&band` | observations, usability, suggested pairs |
| `GET /api/analyze?…&a&b` | registration, per-source motion, verdict, PNGs |
| `GET /api/track?…&ref&sra&sdec` | one source across all epochs + linear fit |
| `GET /api/provenance?…&index` | provenance record of one observation |
| `GET /api/resolve?name` | SIMBAD name → position propagated to 2026.0 |

Interactive schema at `/docs` when the server is running.

## Performance (Apple-silicon laptop, 2026-11-13)

| Step | Time |
|---|---|
| TAP discovery (cold) | 15–40 s (IRSA-side; cached 7 d) |
| 61 cutouts, 8 parallel downloads (cold) | ~40 s |
| Search (warm cache) | 1.5 s |
| Pair analysis: detect ×2, register, reproject ×3, difference | 0.9 s (0.3 s once detections are memoised) |
| Gaia cone via IRSA TAP (cold) | 10–25 s (cached 1 yr) |
| 58-epoch track | 18 s (≈0.3 s detection per image, single-threaded) |

`scripts/benchmark.py` reproduces these. The obvious optimisation is running
per-image detection in a process pool.

## Known engineering gaps

- Sessions live in server memory (single process, no eviction).
- The first search of a position blocks for about a minute with no progress bar.
- scipy 1.15.x wheels fail to import on macOS 27/arm64 (`_spropack` dyld
  error); `requirements.txt` excludes that series.
- The frontend was verified with headless Chrome screenshots and DOM dumps,
  not with an automated browser test suite.
