# Project NADIR pre-release audit

**Audited 2026-11-14. Recommendation: fix P1-01 and complete the cache/deployment check below before release.** The prepared Barnard Observatory and Journey demonstration works offline. No P0 defect was reproduced. One serious spectrum defect remains reproducible despite all unit and standard browser suites passing.

## Checkout and scope

- Branch: `integration-nadir`.
- Commit: `ffc9ef2f76ba3e874e33eb72e1ddb5710d7ebfb5` — Integrate NADIR scientific engine and experimental STIR.
- Initial `git status --short`: empty. Application code was not changed. This report is the only tracked-file addition.
- Used this checkout only; no history reset, other-worktree changes, refactoring, or new features.
- A fresh server on `127.0.0.1:8002` imported the merged backend and served its own frontend. The existing server on port 8001 was left untouched.
- Audit server blocked external socket connections. A separate browser probe also blocked all nonlocal browser requests. Live atlas and a direct SIMBAD resolver check were explicitly separate online checks.
- Evidence, screenshots, environment versions, and audit-only probes are in `artifacts/release-audit/` (Git-ignored). Initial sandbox launch failures were environmental; successful reruns are the results below.

## Executed checks

| Check | Result | Evidence relative to `artifacts/release-audit/` |
|---|---|---|
| `.venv/bin/python -m pytest tests -q` | **80 passed**, 19.41 s; 3 dependency/lifecycle deprecation warnings | `python-tests.log` |
| `node --test tests/*.test.mjs` | **15 passed**, no failures | `javascript-tests.log` |
| `scripts/verify_viewer.py --url http://127.0.0.1:8002 --out artifacts/release-audit/viewer` | **7 check groups passed**, no page or unexpected HTTP errors | `viewer/report.json` |
| `scripts/verify_milestone3.py --url http://127.0.0.1:8002` | **7 check groups passed**, no page or HTTP errors | `observatory-report.json`, `milestone3.log` |
| `scripts/verify_journey.py --url http://127.0.0.1:8002 --out artifacts/release-audit/journey` | **34/34 passed**, no page errors | `journey/verify_journey.json`, `journey.log` |
| `scripts/verify_atlas.py --url http://127.0.0.1:8002` | Passed online; initial and channel-60 tiles returned 200, label 3.118 µm | `atlas-report.json`, `atlas.log` |
| Additional offline browser workflow | Cached name/coordinate search, invalid inputs, failed external name resolution, Journey spectrum all exercised | `workflow.json`, `workflow_probe.py` |
| Real-data backend probe with Requests forbidden | Barnard field, catalogs, spectra loaded with **zero network attempts** | `science.json`, `science_probe.py` |
| API failure probes | Known spectrum 500 reproduced; expected 400/404/422 responses elsewhere | `api.json`, `api_probe.py`, `server.log` |
| WCS comparisons across cached FITS | **952 files**; measurement-path comparisons below | `wcs.json`, `wcs_probe.py` |

## Findings by severity

### P0 — blocks submission or core functionality

**None reproduced on the prepared presentation checkout.** This is not a claim that a clean clone works offline: downloaded data are intentionally Git-ignored. Shipping only the source tree to an offline judge machine would leave the demonstration unprepared.

### P1-01 — spectrum endpoint crashes for an ordinary selectable source

**Reproduced:** `GET /api/fields/e1c5362f0d9d/sources/21/spectrum` returns HTTP 500, `Internal Server Error`, after loading Barnard's cached spectral images. It failed in approximately 0.38 s. Source 0 returns 200; an unknown source returns 404.

Reproduction from a fresh server:

1. `POST /api/fields` with `{"ra":269.44627,"dec":4.76820,"size":0.2,"band":"D2"}`; wait for its job to finish.
2. `POST /api/fields/e1c5362f0d9d/catalogs`; wait for completion.
3. `POST /api/fields/e1c5362f0d9d/spectral?cached_only=true`; wait for completion.
4. `GET /api/fields/e1c5362f0d9d/sources/21/spectrum`.

**Root cause:** `nadir/science/spectral.py:forced_photometry` checks the floating-point position against the image boundary before rounding it to an integer-centered 9×9 extraction window. In cached cutout `0f6d87fecbf00b08b6cf3df2.fits`, the position is `(10.8748241113, 112.8274111341)` in a 117×117 image. It passes `y < ny - half` (113), but rounds to `iy=113`. The slice is rows `109:118`, columns `7:16`: the actual image and mask have shape **8×9**, while `np.mgrid` creates a **9×9** model. `model[good]` raises `IndexError` at line 63. This is independent of SIP warnings.

**Impact:** a judge exploring this neighbour's Spectrum tab cannot obtain its spectrum. The dossier catches the fetch error and presents an error callout; the backend and other controls remain usable. Journeys catch individual candidate errors and can continue, so this does not prevent the tested flagship Journey from finishing. Existing tests do not cover this real edge-window case.

**Minimum fix:** validate the rounded integer window bounds before extracting pixels and return a structured rejected photometry point when a full window is unavailable. Add focused regression coverage at the upper/right rounding boundary and for this endpoint's partial-spectrum response. Preserve valid exposures and scientific rejection reasons; do not merely catch `IndexError` at the route or hide the error in the UI. No broad photometry refactor is needed.

### P1-02 — offline demonstration depends on prepared, sufficiently fresh caches

**Current machine passes:** 61 Barnard D2 observations, 59 usable registered epochs, zero downloads; catalogs load without errors. Its spectral set contains 159 images with **zero missing images and zero failures**. The Journey light scene renders a clearly labeled different star, **TYC 425-262-1**, with 152 measured spectral fluxes across D1–D6. This works with external access blocked at both browser and backend.

**Release risk:** the cache is not in Git. Additionally, `nadir/data/irsa.py:cached_query` attempts a live fetch after the discovery cache's seven-day TTL before serving stale data. `_prewarm_demo` checks only that the query cache exists; `build_field` does not enforce cache-only operation. Thus “preloaded” is not an unconditional guarantee of no live archive attempts, particularly with old or incomplete caches.

**Verified without changing cache files:** advancing the cache-time check by eight days and simulating archive failure caused **one fetch attempt**, followed by correctly labeled stale-cache fallback. This proves the attempt, not its duration on a blackholed network. The configured archive timeout/retries can make that fallback materially slower than the fresh-cache demonstration.

**Minimum launch action:** prepare Barnard and van Maanen, including spectral data and catalogs, on the actual presentation machine while online; verify freshness and complete cutout coverage; restart the merged server and rehearse offline shortly before judging. The existing command is:

```sh
.venv/bin/python scripts/prepare_demo.py --only barnard vanmaanen --spectra barnard vanmaanen
```

No download was needed or performed by this audit's offline checks. If release acceptance requires *arbitrarily old* caches to operate without any live attempt, a narrowly scoped cache-only discovery/catalog path needs a separate approved fix. For an imminent controlled presentation, preparation and rehearsal are the minimum mitigation.

### P2-01 — alternate-WCS SIP messages are noisy, but current measurement paths are unaffected

See the scientific investigation below. Do not remove celestial SIP coefficients or globally silence Astropy. If cleaning this up later, construct alternate WCS objects from a copied header with only the celestial SIP cards removed, and assert numerical equivalence first.

### P2-02 — name-resolution failure exposes an oversized technical error

Searching an uncached name while offline leaves the current field usable and reports a failed SIMBAD lookup, but the toast includes both mirror URLs and nested Requests connection exceptions. Prefer a concise user-facing message with detailed diagnostics retained separately. This does not block cached preset/coordinate search or the demo.

### P2-03 — dependency deprecations and release-environment reproducibility

The Python run emits Starlette/httpx test-client and FastAPI `on_event` deprecations. They do not fail the current release. Requirements are lower bounds rather than a locked environment, and browser tests require separately installed Playwright/Chrome. Preserve the tested environment for judging; dependency modernization can wait. Actual installed versions are recorded in `environment.json`.

## SIP/WCS scientific investigation

The observed message says SIP coefficients are present while CTYPE lacks `-SIP`. This is concerning in general, but its origin and the functions actually used matter:

1. **All 952 inspected cached IMAGE headers have primary celestial CTYPE `RA---TAN-SIP`, `DEC--TAN-SIP`.** The celestial transform intentionally uses SIP via `all_pix2world`/`all_world2pix`. It is not the inconsistent alternate CTYPE described by the message.
2. The same headers contain alternate detector WCS **A** with `LINEAR`, `LINEAR` CTYPE and spectral WCS **W**. Astropy notices the unqualified celestial SIP cards while constructing those alternate WCS objects.
3. Detector coordinates in `psf.detector_xy` and ePSF trimming use **`wcs_pix2world`**, which uses the basic transform without SIP distortion. Spectral `wavelength_at` explicitly clears `swcs.sip` and also uses `wcs_pix2world`. Source centroiding and spectral PSF selection use that detector helper.
4. Compared those paths against alternate WCS objects constructed from copies of each header with SIP cards removed. At the center and four corners of every cutout, the maximum detector-coordinate difference was **0.0 pixels** and the maximum wavelength/bandwidth difference was **0.0**.
5. Primary celestial pixel→sky→pixel round-trip maximum residual was **1.4863×10⁻⁶ pixels**. This demonstrates numerical consistency, not independent absolute astrometric calibration.
6. As a negative control, applying `all_pix2world` to the contaminated alternate A object changed coordinates by as much as **39.49 detector pixels**. Removing SIP from the primary celestial WCS changed sampled coordinate components by as much as **207.58 arcsec** (maximum component difference, not great-circle separation). Either indiscriminate change would be unsafe.

**Conclusion:** the reproduced alternate-WCS messages do not perturb the current detector or wavelength measurement paths; no SIP-induced bias was found in these checks. Retain celestial SIP. The result covers this cached data and these code paths, not all future archive products or all possible WCS callers. Existing global logger-level changes and warning suppression in `observation.py` were observed but not modified. This audit added no warning suppression.

Scientific caveats remain valid: Barnard is saturated and systematics-limited; its Gaia agreement is a consistency check because the floor was calibrated using Barnard. Spectrum samples are non-simultaneous, WCS-WAVE calibration is approximate, and blackbody colour temperature is model-dependent. Passing coordinate tests does not remove those limitations.

## Workflow, compatibility, and performance assessment

| Workflow | Observed result |
|---|---|
| Object search | Barnard text resolves locally through preset matching; decimal coordinates load the cached field; out-of-range coordinates show a validation message. Nonpreset offline names report failure without losing the current field. Separate live SIMBAD `Vega` resolution succeeded in 0.70 s and returned epoch-2026 coordinates; a full new Vega archive download was not attempted. |
| Observation loading | Real DR1/QR3 provenance, 61 cached epochs, hashes and source URLs verified. Unusable epochs are labeled and cannot play as fabricated frames. |
| Time Machine | Matched-wavelength playback, pause, stepping, speed, wavelength exploration, A/B assignment, blink, overlay and difference all passed. Real visit gaps remain labeled. |
| Source selection and motion | Canvas selection opens Barnard; motion plots, uncertainty and saturation caveats render. Discover filtering and deterministic explanations agree with API data. |
| Spectra | Cached van Maanen chart, point inspection, provenance, illustrative-feature warnings and qualified temperature passed. Barnard's saturation refusal is intentional. Source 21 is the P1 exception. |
| Catalogs | Cached overlays and cross-matches work; no Barnard catalog errors. Independent reference labels remain visible. |
| Notes | Saved across tab switches and full reload; Journey lesson notes use the notebook storage. |
| Journeys | All five scenes, real frames, pause/resume, rapid skip/back, replay/end actions, evidence handoff, lessons, Esc and exact Observatory restoration pass. Forced spectrum failure narrates the gap and continues. Non-Barnard journey avoids an unsupported motion claim. |
| Layout/accessibility checks | 900/400 px Observatory layouts pass overflow checks. 390 px Journey controls remain onscreen and Evidence toggles; reduced motion passes. Mobile measurement screenshot visually inspected. This is not a full accessibility audit. |
| Error recovery | Injected config/presets/fields 404s show restart guidance and recover using Retry/demo controls. Missing IDs return 404; invalid pairs 400; invalid field coordinates 422; empty resolve name 400. No unexpected HTTP failures in standard browser suites. |
| Optional atlas | Online CDN/IRSA tiles passed; atlas is not part of the offline guarantee. |

Measured on this machine, not a deployment SLA:

- Cold-process Barnard build **from existing disk data**: **4.84 s**, zero Requests calls for field/catalog/spectral preparation.
- Browser demo-ready check: **1.78 s**, with startup prewarming already running.
- Cached frame stepping mean: **16.47 ms**.
- van Maanen cached spectral loading/render readiness: **7.36 s**.
- Browser checks shared the audit server, so timings are acceptance observations rather than isolated benchmarks. New-target archive download latency, multiuser capacity, long-running memory growth and production hosting were not load-tested.

The merged static frontend and backend are compatible across all tested endpoints and flows. An older backend process can still serve newly changed static files; use a fresh process from this checkout, as done here. Source 21's fault is a scientific extraction error, not a frontend/backend schema mismatch.

## Minimum reliable-launch checklist

1. Fix the rounded photometry-window boundary (P1-01), add the focused regression, then rerun both unit suites, the source-21 endpoint, Observatory spectra and Journey browser checks.
2. Complete P1-02's presentation-machine cache preparation and offline rehearsal. Keep Barnard and van Maanen as the planned demo targets. Confirm catalogs and all spectral bands, not just the presence of a query-cache file.
3. Launch a fresh server from the audited integration revision using the tested environment. Use same-origin frontend/backend and prewarm before judging.
4. Keep saturation, uncertainty, rejected-point and calibration caveats visible. Do not suppress the SIP messages as a substitute for the verified coordinate analysis.

With those narrow fixes/operational steps, the observed core demonstration is reliable. No new features or broad refactoring are necessary for launch. Voice synthesis was not exercised in headless Chrome; no claim is made about a missing-cache offline clean install or all external-service outage modes.
