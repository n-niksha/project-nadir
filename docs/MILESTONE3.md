# Milestone 3 — implementation and verification

Completed against real cached SPHEREx fields on 2026-11-14. No astrometric
engine rewrite, trained classifier, generated sky frames or inferred chemical
abundances were introduced.

## Preservation

The starting checkout included the previous recovery fixes. They were kept.
The pre-M3 diff and source archive are in `artifacts/milestone3/checkpoint/`;
no commit, reset or branch operation was performed. Baseline: 39 passing Python
tests and the existing core Chrome verification.

## Working behavior

- Linear coverage overview with real time gaps; visit-grouped exposure list,
  wavelength/quality metadata, unavailable-epoch reasons and visit jump controls.
- Matched-wavelength Motion playback and explicitly labelled all-wavelength
  Spectral exploration. Real frames only; decoding before advance, pause,
  rate control, chronological stepping, and preserved A/B, blink, overlay/difference.
- Clean, Discovery and Research presets with individual layer controls,
  bounded marker sizes, zoom-aware labels and label-overlap suppression.
- Compact source identity, quality and rate summary; deterministic Explore /
  Research explanations; collapsible evidence and catalog/calibration caveats.
- Spectral date colors derived from real visits; flux/channel error bars,
  focus/hover inspection, flagged-point shapes, rejected-measurement ledger,
  IRSA provenance, optional illustrative features and qualified colour fits.
- Cached-only spectral loading with missing-image counts and explicit optional
  archive fetch. Field/source isolation and cache invalidation on catalog/data changes.
- Discovery tier filtering, keyboard-accessible inspection, ranking reasons and
  low-motion comparison sources. Notes and shared Notes URLs survive reload.
- Optional welcome/tour; poetic intro opt-in via `?intro`. Narrow layouts put
  the sky first with a Targets & layers toggle. Primary controls are native buttons.
- External atlas repairs: load the pinned Aladin bundle as an ES module, start
  directly with SPHEREx rather than an unrelated DSS fallback, disable optional
  telemetry, and select actual cube indices only after the layer loads.
  Aladin 3.8.2's `setSliceNumber` converted indices using a wavelength formula
  inconsistent with its frequency indexing (requested channel 60 displayed 9).
  Selection now uses the midpoint of Aladin's linear frequency-index bin;
  this is a viewer addressing convention, not the channel's physical frequency.
  Labels still use IRSA's nonuniform wavelength array. This
  version-dependent integration is covered by real IRSA tile-request checks.

## Scientific validation

`scripts/validate_cached.py` forbids Requests network access and exercises the
existing pipeline on three fields. The run made **zero network attempts**.

| Target | Images / usable | East motion ″/yr | North motion ″/yr | Multi-epoch tier |
|---|---:|---:|---:|---|
| Barnard's Star | 61 / 59 | −0.57 ± 3.62 | +10.08 ± 1.72 | systematics-limited, 5.88 S/N |
| Teegarden's Star | 56 / 54 | +3.42 ± 1.77 | −3.70 ± 1.69 | no significant motion, 2.92 S/N |
| van Maanen's Star | 56 / 54 | +1.186 ± 0.116 | −2.750 ± 0.129 | significant, 23.63 S/N |

Gaia comparisons are consistent under the current error model (difference
statistics 0.17, 0.06 and 0.49 respectively). Barnard calibrated the masked-core
floor and is not independent validation. Teegarden's known motion is below
the current significance threshold: a useful sensitivity limitation rather
than a reason to change the threshold to fit a familiar star. This small
sample is a sanity check, not a calibrated population-level performance estimate.

Van Maanen's cached spectrum contains **147 accepted measurements, 5 rejected**;
142 clean points enter the colour fit, about 6503 K with χ²ᵣ ≈ 1.02. The formal
interval is unresolved at the temperature-grid spacing and is labelled so.
In the initial offline validation two planned cutouts were absent and disclosed;
that run made no downloads. The older broad smoke test subsequently invoked the
explicit missing-image fetch; it now distinguishes that button from cache-only
loading, so repeat tests do not fetch missing observations. Teegarden had 11
absent spectral cutouts and the selected source is
masked in most epochs, so the public UI does not offer a reliable spectrum.

## Tests and browser evidence

- **52 Python tests pass**, preserving all prior tests. New coverage includes
  multivisit east/north conventions, quality tiers, rejected pair signals,
  deterministic explanations, known-temperature recovery, contaminated-point
  exclusion, saturation refusal, aperture labels, PM-propagated catalog matching
  and cached-only spectral omission.
- **4 Node tests pass** for symmetric wavelength filtering, real visit gaps,
  stepping around an unmatched manual epoch and TAN coordinate round trips.
- The core browser regression passes with no unexpected HTTP or JavaScript
  errors, including real-image blinking, difference colors, actual canvas source
  clicks, five tabs and recovery from config/preset/field 404s.
- The M3 browser suite passes on the real Barnard and van Maanen fields: pan,
  zoom, presets, every epoch, playback/pause/stepping, A/B, explanations tied to
  source values, Discovery filtering/selection, notes across reload, spectrum
  inspection and 900px/400px layouts without document overflow.
- The broader Chrome smoke test completes 22 captured states with no console
  errors, including the optional introduction, education/help sheets and live
  IRSA atlas. Actual successful tile URLs verify channel 23 (1.284 µm) and
  channel 60 (3.118 µm); these are not mocked images.
- Screenshots were visually reviewed. Timeline overflow was corrected. Tests
  also exposed and fixed the pre-existing shared-Notes-URL restoration bug.

Local reports and screenshots (git-ignored):

- `artifacts/milestone3/pytest.txt`, `frontend-tests.txt`
- `artifacts/milestone3/cached-validation.json`
- `artifacts/milestone3/core-browser/report.json`
- `artifacts/milestone3/browser/report.json`
- `artifacts/milestone3/legacy-browser/ui_check.json`, `atlas/report.json`
- [Live IRSA atlas, channel 60](../artifacts/milestone3/atlas/channel60.png)
- [Clean sky](../artifacts/milestone3/browser/clean.png), [Research](../artifacts/milestone3/browser/research.png), [explanations](../artifacts/milestone3/browser/explanation.png)
- [Real spectrum](../artifacts/milestone3/browser/spectrum.png), [400px sky](../artifacts/milestone3/browser/sky-400.png), [400px investigation](../artifacts/milestone3/browser/investigation-400.png)

## Performance

On this machine, median synthetic pair-analysis benchmark (three repeats):
451.6 ms before and 489.0 ms after. No science-engine speedup is claimed; these
short runs are sensitive to concurrent browser/server activity. The relevant
UI changes are bounded prefetch and in-place timeline updates, verified by
checking that epoch DOM nodes survive playback.

Real browser runs measured warm demo readiness at 0.43–0.48 seconds, cached
frame stepping at 16–32 ms per step, first cached spectrum computation/display
at 6.83 seconds, and spectrum reuse at 48 ms. These are local warm-cache results,
not cold-network guarantees. Every original timestamp and provenance record
remains accessible.

## Limits retained

Difference images are qualitative; display alignment uses a center shift while
astrometric measurements use the full similarity transform. Source spectra
combine epochs, aperture fallback lacks aperture correction, and colour-model
temperature is not Teff. Molecule markers are illustrative and not redshift
adjusted. The scoring rules, floors and cutoffs remain experimental; no ML is
trained. The optional external sky atlas and live name/archive/catalog services
still depend on network access. No-data conditions do not produce substitute
images. Existing FastAPI/Starlette deprecation warnings remain.

See [the 90-second presentation](DEMO.md), [methodology](SCIENCE.md), and
[data sources](DATA_SOURCES.md) for the submission narrative and qualifications.
