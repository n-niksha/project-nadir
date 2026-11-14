# Milestone 4 — inverse-imaging research engine

This milestone delivers an optional backend research prototype, not confirmed
new astronomical objects. Native-pixel source modeling improves controlled
blend measurements; real SPHEREx tests expose PSF mismatch and contamination
that prevent discovery claims. No existing motion estimator was replaced.
No frontend assets, other worktrees, or branches were modified or merged.

## Reproduce and inspect

Checkpoint: `b44a38b` on `science-engine`, before implementation. Baseline:
**52 tests passed**, with three existing FastAPI/Starlette deprecation warnings.
Final regression: **66 tests passed**. Use the existing requirements and virtual
environment; no dependencies added.

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/benchmark_inverse.py --trials 12 --calibration-trials 8
.venv/bin/python scripts/benchmark_inverse_extensions.py
.venv/bin/python scripts/audit_science_cache.py --cache /path/to/read-only/data/cache
.venv/bin/python scripts/benchmark.py --repeat 3
```

The cache audit explicitly reads files; it does not invoke download/cache-write
functions. It writes derived JSON only into this worktree. Raw FITS data remain
uncommitted. Missing cached observations do not establish missing archive
coverage. JSON benchmark files record seeds, trial counts, assumptions, failed
cases and timings; timings are hardware/load dependent.

- [Synthetic ground truth](benchmarks/inverse.json): separation/contrast grid,
  null simulations, localization, motion and anomaly tests.
- [Additional experiments](benchmarks/inverse_extensions.json): held-out
  consistency, crowded photometry, registration errors and failure matrix.
- [Preserved false-split experiment](benchmarks/elliptical_psf_failure.json):
  failure before and after adding an elliptical alternative.
- [Real cache audit](benchmarks/real_cache.json): original source URLs/hashes,
  metadata, flags, per-target measurements and explicit failures.
- [Backend contract](MILESTONE4_API.md).
- [Instrument and statistical references](../reports/SPHEREx%20inverse%20imaging%20feasibility.md).

## Phase A: baseline and reliability

The existing engine already has robust registration, visit-aware motion,
mask-aware source fitting, forced ePSF photometry and spectral quality gates.
Replacing them without comparative evidence would be unjustified. Existing
synthetic 117×117-pixel, 120-star benchmark, median of three:

| Operation | Baseline time |
|---|---:|
| Detect and centroid 102 measured sources | 141.7 ms |
| Match, register and classify | 3.4 ms |
| Reproject one image to 235×235 | 61.3 ms |
| Difference image | 20.3 ms |
| Render two PNGs | 11.2 ms |
| Estimated complete pair analysis | 513.5 ms |

The initial implementation comments incorrectly generalized the ePSF tile
layout: D3 uses 11×41, others 21×21. The parser already supports arbitrary row
counts; only its documentation changed. The archive explicitly describes
WCS-WAVE as visualization-grade. Spectral outputs now disclose this and the
lack of full noise covariance. Excess ePSF photometry residuals are marked
`model_mismatch` and excluded from blackbody inference, while retaining the
measurement for inspection. Existing saturated-core refusal remains intact.

## Phase B: forward model and decomposition

For each native exposure, the model is a sum of nonnegative point-source
amplitudes plus a free sky plane. Positions share a reference pixel frame;
known translations map them into each exposure. Fluxes and backgrounds vary
independently by exposure, allowing variability without forcing a constant
color or brightness. Gaussian test PSFs are pixel-integrated. Archive ePSFs
already include pixel response and are sampled directly. No additional
resolution is attributed to their fivefold sampling.

Variable projection solves amplitudes/backgrounds inside bounded nonlinear
position optimization. H2 uses nine separation/orientation starts, identically
repeated in null simulations. All hypotheses use the same valid pixels and
fixed supplied variances. Planar backgrounds are used in every hypothesis;
a circularly broadened source and an adaptively fitted elliptical source
challenge H2. The latter is attempted when descriptive ΔBIC exceeds 10.

H2 may be favored only with ΔBIC >10, sufficient null draws (≥39), conditional
bootstrap tail ≤0.05, BIC advantage >6 over both shape alternatives, acceptable
residuals, nondegenerate separation/flux ratio, convergence, and no contamination
flags. This is a deliberately conservative **exploratory rule**, not a
calibrated survey detection threshold. BIC is descriptive in this singular
mixture problem. Bootstrap tail frequency is `(1 + exceedances)/(B + 1)`;
it is not the probability that two objects exist. Failed null fits count
against significance. No asymptotic chi-square likelihood-ratio claim is made.

Optional H2 parametric simulations produce conditional separation and flux
ratio percentile ranges. They exclude PSF/registration uncertainty and
model-selection bias; missing intervals remain null. Fixed-position flux errors
also omit position covariance. A single exposure cannot establish repeatability.
Catalog match lists remain empty unless independent evidence is supplied.

### Controlled separation grid

288 stamps: 12 random phases/orientations per separation, contrast and total
flux, with Gaussian sigma 0.8 native pixel, noise sigma 1 per pixel. These are
transparent instrument-scale simulations, not a calibrated SPHEREx noise model.
The following counts use **descriptive ΔBIC >10**, not the final calibrated
four-model decision. The existing detector suppresses nearby peaks (its minimum
separation policy is 3 pixels), which partly explains the difference.

| Total flux | Secondary/primary | Separation (px) | H2 descriptive support | Existing detector finds ≥2 | Separation RMSE (px) |
|---:|---:|---:|---:|---:|---:|
| 100 | 0.2 | 1.5 | 0/12 | 0/12 | 0.545 |
| 100 | 0.2 | 2.0 | 7/12 | 0/12 | 0.281 |
| 100 | 1.0 | 1.5 | 12/12 | 0/12 | 0.088 |
| 500 | 0.2 | 1.0 | 12/12 | 0/12 | 0.163 |
| 500 | 1.0 | 0.5 | 6/12 | 0/12 | 0.271 |
| 500 | 1.0 | 2.0 | 12/12 | 0/12 | 0.018 |

The H2 separation estimate is meaningless when H1 is appropriate: fits can
place a weak component far away, yielding ~2-pixel errors for single-source
truth. Do not display those components as detected objects. Low-contrast,
low-flux cases fail substantially; bright matched-PSF subpixel fits alone do
not demonstrate real optical super-resolution.

Null calibration uses eight independent realizations each of single sources,
2-pixel 1:2 blends, and broadened singles, with 39 null draws per realization.
Final state counts and exact binomial intervals are in the JSON. The final
four-model implementation recovered 8/8 blends and favored 0/8 isolated
and 0/8 circularly broadened singles. Zero in eight still admits an upper 95% bound of **36.9%**;
eight in eight has a lower bound of **63.1%**. These are demonstrations, not
precise completeness/false-positive certification.

### A retained negative result changed the algorithm

A stationary single source broadened anisotropically produced ΔBIC ≈342 and
bootstrap tail 0.025. The original circular-width alternative incorrectly
allowed `multiple_source_model_favored`. The added elliptical alternative
improved BIC by 9.72 relative to H2, changing the result to `ambiguous`.
The original failure, corrected result, and regression test are retained.
Arbitrary PSF asymmetry, correlated noise and detector effects remain possible
confounders even after this fix.

## Phase C: reconstruction and joint fitting

The reconstruction baseline is inverse-variance bilinear deposition at native
or 2× sampling. It preserves surface brightness, propagates variance with
squared weights, records coverage/shifts/exposure IDs, and returns the sparse
operator needed to calculate full output covariance. It does not deconvolve,
fill absent information, or label optical super-resolution. Quantitative
source fitting uses native pixels rather than the correlated output image.

Four-exposure, flux-100 experiments (20 trials each):

| Sampling | Single-exposure localization RMSE | Joint native fit RMSE | Coadd moment RMSE |
|---|---:|---:|---:|
| Identical phases | 0.0533 px | 0.0301 px | 0.2512 px |
| Random subpixel shifts | 0.0433 px | 0.0223 px | 0.2775 px |

Joint photometric fractional RMSE was 1.74% / 0.84%, respectively. The moment
baseline is intentionally simple and noise-sensitive; its poorer result does
not prove superiority over every possible coadd estimator. Different random
noise realizations between phase treatments and small sample size prevent a
claim that dithering itself outperforms noise averaging. Identical-phase gains
already demonstrate the importance of averaging independent noise.

For 24 1.5-pixel, 1:2, flux-100 blends, four exposures reduced separation RMSE
from 0.144 to 0.093 pixels, while descriptive H2 support rose from 21/24 to
24/24. Positions fitted on three exposures improved held-out fourth-exposure
chi-square by >10 in 24/24 trials; flux/background nuisance parameters were
refit on that held-out frame, so this is not a fully parameter-free prediction.
At 0.75 pixels, neither single nor joint fits achieved ΔBIC >10 in these 24
trials. No single-source case achieved that threshold (0/24 for both methods).

Unmodeled per-exposure pointing errors of 0, 0.05 and 0.2 pixels raised joint
localization RMSE from 0.0052 to 0.0346 and 0.1374 pixels (50 bright-source
trials each). Registration precision is therefore a material feasibility limit.

Real exposures have wavelength-dependent ePSFs, changing orientation, motion,
saturation and non-simultaneous colors. The audit checks approximate bandpass,
epoch and orientation compatibility and records every candidate pair. No real
super-resolution image is claimed; none of the sampled pairs passed the additional <7-day and <3-degree
orientation gates. These deliberately restrictive gates do not establish that
all archive data are unsuitable. A regularized inverse image was not
adopted because these unresolved systematics do not justify additional detail.

## Motion and spectral checks

The existing robust straight-line fit was retained. In 100 independent
simulations with one gross positional outlier, rate RMSE was 0.0815 versus
0.1436 arcsec/year for ordinary least squares; reported 95% interval coverage
was 99%, conservative in this tested case. This does not establish calibration
for reference-star motion, all cadences, or correlated real astrometry.

A static pair separated by one native pixel (6.15 arcsec), whose secondary/primary
flux ratio rises from 0.2 to 1 over a year, shifts its photocenter by 2.05 arcsec.
A linear fit reports **2.01 arcsec/year despite zero physical motion**. This
analytic ground-truth experiment demonstrates a real degeneracy; decomposition
flags should accompany motion interpretation. No existing motion correction is
applied without evidence. The real audit computes only a joint-motion/parallax
design feasibility and formal uncertainty, never a parallax estimate. Shared
registration covariance, sparse visits and systematics remain unresolved.

The optional simultaneous forced-photometry module fits up to four independent
positions and retains amplitude covariance. Across 100 simulations per separation,
with true primary/secondary amplitudes 200/100:

| Separation | Single-source primary-flux RMSE | Joint primary-flux RMSE | Joint 95% coverage |
|---:|---:|---:|---:|
| 0.5 px | 91.14 | 7.18 | 96% |
| 1.0 px | 68.87 | 4.06 | 96% |
| 2.0 px | 20.04 | 3.01 | 96% |

Positions and PSFs were exactly known here. This validates conditional linear
amplitudes, not blind deblending, real crowded-field accuracy, or detailed
physical inference. Degenerate positions are rejected; negative forced fluxes
are retained as measurements. The existing real photometry is not automatically
replaced. No abundances, object classes, or effective temperatures are inferred
from these experiments.

## Phase D: multi-feature analysis and ML feasibility

Measurements carry definitions, units, uncertainty when available, provenance
and explicit missing reasons. A transparent screen combines valid marginal
null tails using a Bonferroni union bound; it never sums incompatible physical
units and does not assume feature independence. Missing planned tests remain
in the multiplicity factor. A staged policy recommends expensive work only for
clean sources with sufficient S/N and permissive initial evidence.

In 4,000 independent synthetic objects (2,000 null, 1,000 injected motion,
1,000 injected variability):

| Screen | Recall | False-positive rate | Precision |
|---|---:|---:|---:|
| Motion alone | 42.75% | 1.15% | 97.38% |
| Three-feature statistical screen | 80.55% | 0.95% | 98.83% |
| Same screen, half the variability measurements absent | 59.60% | 0.70% | 98.84% |

These synthetic class proportions determine precision and must not be presented
as survey precision. The permissive staged gate selected 52.35% of objects and
retained 95.05% of injected candidates: a potential **47.65% reduction in
expensive fits at 4.95% sensitivity loss** on this constructed population.
This is a measured selection fraction, not measured end-to-end runtime savings.
Missing data materially reduces recall. Real cross-field stability is unknown.

The real cache does not supply independent labels for blends/anomalies. Five
preselected demonstration targets do not constitute a training/test population.
Isolation Forest or other novelty scores would not establish correctness;
no trained classifier is delivered or advertised. The available scientific
result is a tested feature/evidence framework and explicit ML infeasibility
assessment. Future training must split by source and field, never by repeated
exposure of the same source. Real API features retain uncalibrated screening as
unavailable instead of manufacturing probabilities from the existing rank.

## Phase E: real target reliability and integration

The audit found 466 unique cached D2 exposures across five presets; it selected
12 per target evenly across time, deduplicating physical observation IDs and
preferring DR1 when duplicated. Metadata counts are not archive-completeness
claims. Association is the nearest detection within 3 pixels and is explicitly
not independent catalog validation.

| Target | Unique cached D2 | Examined | Finding |
|---|---:|---:|---|
| Barnard's Star | 181 | 12 | All sampled target detections saturated; reliable flux refused |
| Teegarden's Star | 112 | 12 | 11/12 detected targets saturated; one aperture fallback; identity needs external validation |
| van Maanen's Star | 56 | 12 | Unmasked; ePSF photometry reduced chi-square ~36–46, so model-dependent flux is unreliable |
| Wolf 359 | 45 | 12 | 11/12 saturated; one unsaturated photometric fit has reduced chi-square ~197 |
| Kapteyn's Star | 72 | 12 | 11 saturated detections; one selected cutout misses the target |

The existing motion tracker returned fits for all five sampled target sets,
but fits are not automatically trustworthy: saturation, association and shared
reference errors remain. These audit fits use the existing `track_source`
estimator, not the public field-wide visit estimator; do not compare their
numbers as if the methods/sample selection were identical. No Gaia data used
for calibration are presented as independent validation.

Only 1–5 of the 66 possible sampled pairs per target have approximate overlapping
bandpasses. ePSF native-lattice sums vary with phase by roughly percent levels
in examined examples, another photometric systematic. Current behavior is
measured and disclosed rather than silently renormalized. Archive ePSFs are
missing in some older cached files; the new real adapter refuses those fits.

The required matrix is covered by the high-motion targets, saturation cases,
synthetic faint and crowded cases, missing/invalid metadata tests and unknown
catalog associations (empty, not fabricated). Unknown classification remains
unknown. Real crowded-field component truth and a reliable real faint-source
benchmark remain unavailable. All audit failures are retained in JSON.

The optional API uses existing jobs, cancellation and bounded caches. Tests
cover schemas, invalid requests, missing fields/observations, caching, native
model recovery, masks, background/covariance behavior and failure states.
Large image arrays stay out of API responses. Measured selected-stamp timings: single-source fit ~2.0 ms, two-source fit
~123 ms, coaddition ~0.24 ms, fixed-position photometry ~0.09 ms; 39-draw
calibration takes ~4–5 seconds. Candidate screening processed 4,000 synthetic
objects in roughly 0.3 seconds. Cache reuse is functionally verified by API
tests; no wall-clock cache speedup is claimed. Full timings are in the JSON. Archive download
latency is **not measured**: no additional retrieval was necessary or performed.

## Scientific review and next steps

1. **Better separation than baseline?** Yes for tested matched-PSF synthetic
   blends, including cases suppressed by the existing detector's peak-spacing
   policy. No independently confirmed improvement on real companions yet.
2. **Failure region?** Low contrast and low flux, subpixel degeneracy, masks,
   PSF shape error, background structure and poor registration. The grid
   quantifies sampled cases; it is not a universal resolution limit.
3. **Multi-frame improvement?** Demonstrated conditional localization and
   separation gains. A distinct super-resolution benefit from subpixel sampling
   and a real-data gain remain unestablished.
4. **False detections?** Elongated/mismatched PSFs can give very strong false
   evidence; saturation, artifacts, correlations and variable blends add risks.
5. **Multi-feature benefit?** Demonstrated in the synthetic motion/variability
   population, with missing-data and staging losses quantified. Real-field
   calibration and stability remain open.
6. **ML justified?** No validated learned classifier with the current labels.
   The feature and transparent-screen infrastructure is the defensible deliverable.
7. **Original contribution?** The integrated native-pixel comparison, competing
   PSF-shape checks, retained false-split experiment, and provenance-aware quality
   contract. The individual numerical/statistical techniques are established;
   no novel fundamental algorithm is claimed.
8. **NASA-ready results?** Reproducible controlled recovery, covariance-aware
   reconstruction baseline, explicit false-motion mechanism and honest real-target
   diagnostics. Present them as experiments with their denominators and assumptions.
9. **Required disclosure?** All advanced inference remains experimental; no new
   object, real super-resolution, trustworthy parallax, chemical inference or
   validated ML classifier has been established. Small null samples and conditional
   noise/PSF models limit significance claims.
10. **Next work?** Obtain independent high-resolution companion truth, per-field
    PSF/registration residual models, science CWAVE/CBAND maps and empirical noise
    covariance. Expand injection/recovery across detector, wavelength and source
    selection; use source/field-held-out validation and survey multiplicity control.

**Validated within tested conditions:** analytic pixel response, conditional
linear amplitudes/covariance, linear reconstruction propagation, robust-fit
outlier experiment, schema/missing-data behavior. **Experimental:** decomposition,
shape alternatives, native joint fits and anomaly screening. **Unsupported by
current evidence:** real new companions, optical super-resolution, reliable
measured parallax, detailed composition and supervised discovery classification.
