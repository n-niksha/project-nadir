# Viewer verification — 2026-11-14

## Findings

The current backend defines `/api/config`, `/api/presets`, `POST /api/fields`,
job polling, registered PNGs and source investigation routes. The frontend
uses those routes correctly. No server was reachable at port 8000 when this
investigation began. Starting the current checkout restored the demo without
any changes to the scientific engine.

The reported generic `Not Found` is consistent with a stale milestone-1
backend serving the newer static frontend (or the frontend being opened on
the wrong server). The original process was unavailable, so its exact route
table could not be inspected. This is a deployment diagnosis, not evidence
of missing routes in the current source.

A confirmed frontend defect compounded that failure: when config or presets
failed, `presets` remained empty, but the demo button called `loadPreset` with
`undefined`. A Chrome reproduction using the original JavaScript and an
injected config 404 produced `Cannot read properties of undefined (reading
'ra')`, no preset buttons, and no investigation panel. Startup Retry also
attempted to build the dummy error-display coordinates rather than retrying
initialization. These paths now reload config/presets or restart initialization.
Generic route 404s explain how to restart the matching backend. The difference
renderer also checks that an image decoded before passing it to `drawImage`.

## Checks completed

- All 39 pytest tests pass (38 existing tests plus a field API regression).
  The old `/static/app.js` assertion was updated to the module actually loaded
  by the HTML, `/static/js/main.js`.
- The new API regression exercises a real background job over synthetic FITS,
  field summary, distinct frame PNGs, difference PNG, measured source motion,
  provenance, invalid source/pair handling and reuse of a completed field.
- Rebuilt Barnard's Star with all Requests network access forbidden:
  **61 cached observations, 59 usable, zero network calls**, DR1 and QR3.
  FITS image arrays contain finite data and have original IRSA provenance.
- Chrome loaded the real demo and confirmed its provenance URL and response
  SHA-256. Epoch A and B render different canvas pixels; blinking renders both.
  Difference mode renders the server PNG with blue and orange residuals.
- A physical canvas click at Barnard's measured position reopened the
  investigation panel. Overview, motion plots, observation rows, spectrum
  refusal for saturation, and note persistence all passed.
- Injected 404s for config, presets and fields recovered via the demo or Retry
  buttons once the endpoint was restored. No uncaught JavaScript errors or
  unexpected HTTP failures occurred in the passing browser run.

Browser evidence: `artifacts/viewer-verification/report.json`, `blink.png`,
`difference.png`, and `investigation-motion.png` (local, git-ignored).

## Remaining limitations

Barnard's Star remains saturated: its motion is labelled systematics-limited
and no reliable spectrum is claimed. Difference images remain qualitative.
The newer field-wide fit reports about 10.10 arcsec/year; the README's older
58-epoch milestone-1 fit is a different estimator, not a new measurement from
this repair. No scientific algorithms or thresholds were changed.

The test suite reports existing FastAPI startup-event and Starlette/httpx
deprecation warnings. External archive outages and uncached targets were not
part of the real-data browser verification. An unavailable backend still
requires starting the server; a browser-only recovery cannot create API routes.
