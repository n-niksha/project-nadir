# A 90-second judge demonstration

## Before presenting

Start one server without auto-reload:

```bash
.venv/bin/python -m uvicorn nadir.api.server:app --port 8000
```

Prepare the Barnard and van Maanen caches while online if needed, then run
`scripts/validate_cached.py` and `scripts/verify_milestone3.py`. Do not clear
`data/cache`. Spectra start cached-only; missing images are disclosed. A cold
checkout cannot demonstrate observations offline. Use a wide browser window.

## Script

**0–12 seconds — Real sky.** Open the app, choose Clean, and say: “These are
NASA SPHEREx infrared observations, with the original archive provenance.
NADIR makes those observations accessible without hiding uncertainty.”

**12–25 seconds — A nearby star moves.** Choose Discovery and Blink. Select
Barnard's Star: “The field stars align; this nearby star changes position.
Its bright core saturates, so we label its motion systematics-limited.”

**25–38 seconds — Time is more than two images.** Use Animate in Motion mode,
pause, then step. Point to the calendar gaps and wavelength labels: “These are
actual exposures. No frames are invented between visits. Comparable wavelengths
reduce misleading brightness changes.”

**38–50 seconds — Check the evidence.** Open Motion: “The trajectory uses
SPHEREx positions; Gaia is a comparison. Barnard helped calibrate our error
floor, so agreement here is not an independent validation.” Briefly show
Explain this observation → Explore, then Research.

**50–73 seconds — A real spectrum.** Choose van Maanen's Star, Spectrum, then
Load cached spectral observations. Hover a point: “Each point has a date,
wavelength, uncertainty and provenance. The colour-model temperature is not
the star's effective temperature.” Optionally toggle feature markers and say:
“These mark where features can occur; they are not molecular detections.”

**73–90 seconds — Participate responsibly.** Open Discover, inspect a candidate
and its reasons, then Notes: “The ordering is a transparent scoring rule, not
an unvalidated ML discovery claim. Anyone can inspect evidence, ask a better
question and share the same investigation.”

If a catalog service is unavailable, use SPHEREx measurements and disclose the
missing comparison. If spectral caches are incomplete, show their explicit
missing-image count; do not silently download during the presentation.
