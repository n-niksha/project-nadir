# Method, validation and scientific limitations

## Current multi-epoch interpretation (Milestone 3)

The original pair method below remains available. The current UI additionally
fits field-wide tracks and groups visits after gaps greater than 30 days.
Motion uses native-pixel centroids, a catalog-free reference-star alignment,
and east/north tangent-plane offsets. East is `−x` on the north-up display;
`μE = μα cos δ`, and rates use 365.25 days per year. The linear fit does not
solve for parallax. Gaia prediction curves include geocentric parallax.

Significant motion requires a combined axis-error S/N ≥ 5, at least three
visits, reduced χ² ≤ 5 in both axes and the existing blend/artifact gates.
Masked-core sources use visit means with a 1.2″ systematic per visit. This
floor was calibrated on Barnard's Star: its Gaia agreement is **not independent
validation** of that floor. Cross-matching does not calibrate the alignment,
but the floor's empirical history must still be disclosed. The Gaia comparison
statistic uses NADIR rate errors and neglects catalog rate errors.

The `stationary` tier means no significant fitted motion, not physical rest.
Rejected two-image signals have no established physical explanation. No tier
constitutes a newly discovered astronomical object. Discovery ranking uses
`min(S/N,50) × consistency × quality + min(var_z,30) × clean_fraction`, with
motion counted only above 5, consistency `min(1,5/max(χ²E,χ²N))`, quality 1 for
significant, 0.6 for systematics-limited, otherwise 0.3×clean fraction. The
variability term requires the existing flag; rejected/insufficient scores are
zero. Pair comparisons can share epochs and are not independent experiments.
This heuristic has no training set or calibrated discovery probability.

### Playback is a display, not a new measurement

Only real registered frames are shown. The overview uses actual MJD positions
and labels unobserved gaps. Fixed-rate stepping is explicitly one exposure per
step; it does not imply equally spaced observing times. Motion playback uses
the symmetric 2% wavelength tolerance around anchor A, at the field center.
The LVF wavelength changes across each image, so this does not ensure matching
spectra everywhere. Spectral playback allows all wavelengths and warns that
brightness changes can be spectral. Display frames share reference-star flux
normalization; quantitative photometry remains on native observations.

The existing frame renderer applies the registration shift evaluated at the
field center; the astrometric analysis uses the full similarity transform.
Small rotation/scale residuals can therefore remain at display edges. Do not
measure astrometry from the canvas or treat a difference residual as a detection.

### Spectral laboratory

The archive ePSF, when present, supplies a forced flux fit. Otherwise the result
is labelled `aperture_r3px_uncorrected`; it is not presented as an ePSF fit or
an aperture-corrected total flux. A masked core is rejected. Surviving flagged
points remain visible as hollow diamonds with their flags and uncertainties;
all flagged points are conservatively excluded from the colour-model fit.
This policy may remove usable measurements and is not a calibrated flag-weighting
scheme. Epoch colors are derived from the actual timestamps, not a fixed demo.

The single-temperature model fits `Fν = A Bν(T)` using positive, finite, clean
points with S/N ≥ 5, at least six points and positive errors. It searches 400
log-spaced temperatures from 500 to 30000 K with a 3% relative error floor.
Its Δχ² ≤ 1 interval is scaled by max(reduced χ²,1). Grid-boundary intervals
and intervals containing only one temperature sample are explicitly qualified;
a single sample means unresolved uncertainty, not zero error. Colour temperature
is **not effective temperature**. Extinction, contamination, variability and
absorption structure are not separately fitted. High reduced χ² is a model
warning, not proof of a particular physical explanation.

Vertical bars show ±1σ flux errors; horizontal bars show channel half-bandwidth,
not wavelength uncertainty. The spectrum is assembled across epochs. Optional
molecular/atomic markers are illustrative rest wavelengths, without redshift
correction or a detection test. No chemical percentages or molecular detections
are inferred. 2MASS/AllWISE comparisons involve different filters and epochs;
no universal percentage agreement threshold is claimed.

See [data sources](DATA_SOURCES.md) and [the three-field validation](MILESTONE3.md).
The older sections below describe the original milestone-1 benchmark and its
limitations; the current ePSF cache retains overlapping tiles for spectroscopy.

## Data used

SPHEREx Level-2 spectral images from IRSA, releases **DR1** and **QR3**
(pipeline 7.0.5, product version `l2b-v27`). Each cutout provides `IMAGE`
(MJy/sr), `FLAGS`, `VARIANCE`, `ZODI`, a TAN-SIP celestial WCS and a `WAVE-TAB`
spectral WCS. Pixels are 6.15″; the PSF FWHM is ~7.5″ in band D2, i.e. the
images are strongly undersampled. Timestamps are exposure midpoints
(`MJD-AVG`, UTC).

## Pipeline

1. **Discovery** — TAP query for all images whose footprint contains the position.
2. **Cutouts** — 0.2° by default; cached with provenance.
3. **Parsing** — wavelength and bandwidth are evaluated at the target pixel
   from the spectral WCS; flag bit names are read from the header.
   Pixels flagged `OVERFLOW`, `NONFUNC`, `MISSING_DATA`, `HOT` or non-finite
   are excluded; other flags are carried as warnings.
4. **Pair selection** — both epochs usable, Δλ/λ ≤ 2 %, baseline ≥ 30 d;
   longest baseline first.
5. **Detection and centroiding (native pixels)** — local maxima above 5σ;
   each is fitted with a *pixel-integrated* circular Gaussian + background,
   weighted by the pipeline variance. Sources with a masked core are fitted
   with a Moffat profile on the PSF wings instead.
6. **Registration** — positions go through each image's own WCS to a common
   tangent-plane grid; mutual nearest neighbours within 3 native pixels are
   matched; a similarity transform (shift, rotation, scale; translation only
   below 10 stars) is fitted to clean, unsaturated, unblended stars with a
   median start and iterative 3σ MAD clipping. An FFT cross-correlation of the
   reprojected images gives an independent whole-field shift.
7. **Displacement** — registered position at B minus position at A, east/north.
8. **Uncertainty** — per axis:
   `max(formal centroid error, empirical scatter of the reference stars)`
   in quadrature with the transform uncertainty at that position. The
   empirical term matters: formal errors ignore pixel-phase effects of an
   undersampled PSF.
9. **Classification** (threshold: 2-D significance ≥ 5):
   `reference` · `no_significant_motion` · `candidate_motion` ·
   `candidate_motion_low_confidence` (significant, but saturated / core
   masked / blended / poor PSF fit / inconsistent flux / artifact flags).
   Sources seen in one epoch only get no displacement at all.
10. **Cross-reference** — Gaia DR3 (IRSA mirror), propagated to epoch A; the
    predicted displacement includes proper motion and parallax for a geocentric
    observer. Shown next to the measurement, never mixed into it.
11. **Track** — every usable epoch is registered to the reference epoch and a
    straight line is fitted per axis; errors are scaled by √χ²ᵣ when > 1.

## Validation

### Synthetic (tests/, offline)

Images are rendered with a pixel-integrated PSF at SPHEREx sampling, noise, a
per-epoch roll angle and a deliberately wrong WCS, then:

- centroids are recovered to < 0.03 px and the error bars have unit pulls;
- injected shifts of (0, 11), (−6, 3) and (2, −2.5)″ are recovered to < 0.5″
  with zero false candidates, through a 1.5° roll and a 2.5″ WCS error;
- an unmoved field and a 1σ shift produce no candidates;
- a saturated mover is found but labelled low-confidence;
- a field with two stars, or an image without a timestamp, yields an explicit
  refusal rather than numbers;
- a Barnard-like 10.4″/yr track is recovered from nine epochs.

### Real data (2026-11-13)

**Barnard's Star**, D2, 61 images (59 usable), three visits: Aug 2025,
Mar 2026, Aug–Sep 2026. Pair 2025-08-25T17:09 (DR1, 1.517 µm) vs
2026-09-22T15:05 (QR3, 1.511 µm), 392.9 d:

- registration: 113 reference stars, scatter 0.25″ E / 0.38″ N (0.05 px);
  catalog-free shift (−0.05″, −0.19″) vs cross-correlation (−0.10″, −0.18″);
- measured: E −1.79 ± 1.23″, N +11.06 ± 1.23″ (9.1σ, low-confidence because saturated);
- Gaia DR3 4472832130942575872 predicts E −0.92″, N +11.03″ → agreement at 0.7σ;
- 58-epoch fit: μ_E = −0.83 ± 0.50, μ_N = +10.26 ± 0.28 ″/yr (Gaia −0.80, +10.36).

**Teegarden's Star**, D2, 56 images: the pair measurement (E +3.72, N −4.33 ±
1.23″) is only 4.6σ because the star is `OVERFLOW`-flagged in one epoch, so the
pair verdict is correctly "no reliable motion"; the 54-epoch fit gives
μ_E = +3.30 ± 0.24, μ_N = −3.90 ± 0.26 ″/yr (Gaia +3.43, −3.81).

## Limitations — read before trusting a number

1. **Bright stars saturate.** Barnard's Star (K≈4.5) has NaN/`OVERFLOW` core
   pixels in every D2 image; Teegarden's Star (K≈8.4) is marginal. The
   wing-fit centroid repeats to 0.4–0.7″ within a visit but carries a
   detector-frame bias: in the March 2026 visit, when the detector is rotated
   180° on the sky (PA 0° instead of 180°), Barnard's mean east offset sits
   ~2″ from the Gaia prediction (measured +2.4″, predicted +0.6″), while the
   two August visits (same orientation) agree with Gaia to 0.3″. We therefore
   apply a 0.2 px (1.2″) systematic floor to any core-masked source and never
   promote it above low-confidence. The floor is an empirical estimate from
   one star in one band, not a calibrated error model.
2. **The sensitivity floor is high.** Reference-star scatter is 0.05–0.1 px
   (0.3–0.6″) per pair. Over the ~1.1 yr currently available, only motions
   ≳ 2–3″/yr are detectable in a single pair: a handful of nearby stars, plus
   solar-system objects (which move too far to be matched between visits and
   appear as one-epoch detections).
3. **Crowding and blends.** With 6″ pixels many "sources" are blends. Blends
   shift their centroid when seeing different wavelengths or pixel phases. In
   the Barnard field two further formally significant displacements appear;
   both are flagged (masked core; flux mismatch with two Gaia stars in one
   pixel) and both disagree with Gaia — they are false positives that the
   quality gates demote but do not remove.
4. **Gaussian PSF model.** The real PSF is asymmetric and varies over the
   detector. The archive's `EPSF` extension is not used yet (and is dropped
   from the cache); PSF fitting with it is the main route to better astrometry
   and to de-biasing saturated stars.
5. **Reference frame.** Motions are relative to the mean of the field stars
   (not absolute); their own proper motions (mas/yr) are negligible here.
6. **Parallax is not fitted.** It is included in the catalog prediction only.
   The linear track fit absorbs or is biased by parallax for very nearby stars.
7. **Difference images are qualitative.** Bilinear resampling of an
   undersampled PSF leaves dipole-like residuals on every bright star, and the
   two epochs differ slightly in wavelength. Use the difference view to see
   *where* something changed; use the centroid measurements for *how much*.
   Proper image subtraction would need PSF matching.
8. **Variance after reprojection** is interpolated and ignores pixel
   correlations; it is used only for display statistics.
9. **Thresholds are heuristic.** 5σ, the 3-pixel match radius, the blend and
   flux gates were tuned on synthetic data and two real fields.
10. **Chromatic effects.** For the track, epochs at different wavelengths
    (1.1–1.65 µm in D2) are combined; wavelength-dependent PSF and blend
    centroids add scatter that the χ² rescaling only partly captures.
