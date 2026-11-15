# Experimental science API

All additions are optional. Existing endpoint paths and field processing remain
available. No frontend assets were changed. New endpoints use already-loaded
native observations and never start archive downloads. Start a local server
with `.venv/bin/uvicorn nadir.api.server:app --port 8002` if needed.

## Availability and measurements

`GET /api/science/fields/{fid}/diagnostics` returns `schema_version`,
`maturity: experimental`, per-observation diagnostics and existing field failures.
Diagnostics distinguish `available`, `missing_data`, `missing_metadata`,
`contamination`, and `software_or_metadata_failure`. `available` describes
metadata/core usability, not validated scientific accuracy. Target coordinates
are the field center; they are not automatically propagated across epochs.
Subpixel phase, detector, epoch, approximate wavelength, flags, and provenance
are included. Reference count is null unless independently supplied to the
Python adapter; an unknown count must not be rendered as zero.

`GET /api/science/fields/{fid}/sources/{sid}/features` exposes measurements from
the existing motion fit, feature definitions, units, uncertainty when supplied,
provenance, missing reasons, quality fractions, and the existing heuristic rank.
No real-field tail probabilities are invented: `screening.state` remains
`insufficient_data` until calibrated marginal nulls are available. Spectral and
residual features absent from the track stay null. This endpoint performs no
new PSF/spectral fits.

## Native-pixel decomposition

`POST /api/science/fields/{fid}/decomposition`

```json
{
  "observation_index": 0,
  "ra": 12.30018,
  "dec": 5.36902,
  "bootstrap": 39,
  "seed": 0
}
```

Returns `{ "job": { "id": "j…", "status": "queued", "…": "…" },
"maturity": "experimental" }`. Poll the existing `GET /api/jobs/{id}`.
On completion, `job.result` is validated by `DecompositionResult` in
`nadir/api/science.py`. This is a one-observation endpoint; Python callers may
supply compatible multiple `Stamp` objects with known translations.

Bootstrap defaults to **zero**, which cannot produce a multiple-source-favored
state. The request accepts 0–199 null simulations and a reproducible seed.
At least 39 simulations are needed for the exploratory 5% decision gate; 199
provide finer Monte Carlo resolution. This is a local conditional test, not a
survey false-discovery guarantee. Jobs reuse identical in-flight requests and
cache up to 32 results per field. Cache keys include field instance, coordinates,
observation index, seed and simulation budget. Field eviction drops its cache.
Cancellation is checked between bootstrap simulations. Optimizer work already
in progress completes before the next cancellation check.

The result contains:

| Field | Interpretation |
|---|---|
| `schema_version`, `maturity` | Version 1, always experimental |
| `source_id`, `ra`, `dec` | Observation selection identifier and requested ICRS position |
| `state` | One of the five states below |
| `best_fit_source_count` | Lower descriptive BIC among H1/H2; always subordinate to state and shape alternatives |
| `separation_px`, `separation_arcsec` | H2 fitted separation, not necessarily a resolved object pair |
| `flux_ratio` | Median faint/bright fitted amplitude ratio across supplied frames |
| `uncertainty` | Null unless optional Python H2 bootstrap draws requested; conditional, not model-selection-adjusted |
| `models` | H1/H2/broadened single source; elliptical single source when H2 improves BIC by >10 |
| `models.*.frames` | Fluxes, fixed-position conditional flux errors, background plane, normalized residual RMS, usable pixels |
| `evidence` | Chi-square improvement, descriptive BIC differences, bootstrap tail, sample count, seed, resolution and support rule |
| `observations_supporting` | Frames with chi-square improvement >10; descriptive count, not independent confirmations |
| `flags`, `reasons_to_doubt` | Contamination and inference limitations |
| `catalog_matches` | Empty when no catalog evidence is supplied; not proof of catalog absence |
| `diagnostics`, `provenance` | Availability checks and original archive metadata |

States and rendering requirements:

- `consistent_with_one_source`: no adequate evidence for added complexity;
  unresolved companions are not excluded.
- `multiple_source_model_favored`: an **experimental model hypothesis**, never
  a discovery notification or confirmed new-object marker.
- `ambiguous`: competing shape model, inadequate simulation budget, optimizer
  boundary, or otherwise conflicting evidence.
- `insufficient_data`: missing PSF/metadata, too few good pixels, or weak signal.
- `invalid_or_contaminated_observation`: flagged contamination or excess residuals
  in all tested models; do not render component positions as detections.

The endpoint intentionally exposes summaries rather than large residual image
arrays. Python `fit_model` returns per-frame model images for research diagnostics.
The catalog matcher and existing source investigation routes remain separate.
No error is silently converted into a confident result: invalid request values
return 422, unknown fields/sources/observations return 404, and unexpected
scientific failures appear in the existing job failure channel.

## Python research interfaces

```python
from nadir.science.deblend import Stamp, compare_sources
from nadir.science.reconstruction import coadd
from nadir.science.photometry import forced_components
from nadir.ml.evidence import extract_features, gaussian_tails, screen

# Native arrays, fixed variance, explicit usable-pixel mask and physical exposure ID.
# shift maps reference coordinates to native coordinates; no hidden resampling.
stamp = Stamp(image, variance, good, obs_id, shift=(dx, dy), epsf=epsf)
result = compare_sources([stamp], bootstrap=39, seed=12, uncertainty_draws=30)
product = coadd(compatible_stamps, scale=2)
fluxes = forced_components(stamp, independently_known_positions)
```

A Gaussian PSF is the explicit synthetic/research fallback when `epsf=None`;
the real-observation adapter refuses missing archive ePSFs. No pixels are
integrated twice: Gaussian PSFs are integrated, archive ePSFs are sampled.
`coadd` returns image, diagonal variance, coverage and sparse operator A;
full propagated covariance is `A diag(input_variance) A.T`. The output is
surface brightness, **not** flux per smaller output pixel. Summed photometry
requires output pixel area and covariance. It is not a deconvolved image or
claimed optical super-resolution. Callers must establish common bandpass,
static sky, orientation and registration before using it.

`forced_components` preserves the amplitude covariance for up to four fixed
positions. Degenerate models return an explicit status. Negative amplitudes
remain measurements, not detections. Errors omit position and PSF uncertainty.

`screen` combines declared marginal null tails with a Bonferroni bound, including
missing tests in the planned family. It does not accept arbitrary scores as
probabilities. The synthetic benchmark evaluates this contract; real-field
calibration remains unsupported.
