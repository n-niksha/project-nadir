# Statistical safeguards for inverse imaging

## How should one versus two sources be compared?

### Takeaway
Use simulation-calibrated improvements in native-pixel likelihood, plus model-adequacy and repeat-observation checks. A fitted second component is a hypothesis; neither a nominal chi-square likelihood-ratio tail nor a BIC difference establishes its reality.

### Cited Findings
- Additional positive-amplitude components put the null at a parameter boundary; standard likelihood-ratio and F-test reference distributions need not apply. Protassov et al. explicitly treat source-above-background and spectral-component detection, and discuss posterior predictive alternatives. [Protassov et al. 2002](https://arxiv.org/abs/astro-ph/0201547).
- Mixture models can be singular, with noninvertible Fisher information; ordinary BIC's penalty generally does not have its usual marginal-likelihood interpretation in such models. [Drton and Plummer](https://arxiv.org/abs/1309.0911).
- Randomized Monte Carlo tail calculations should not report zero merely because no simulated statistic exceeds the observed one; finite simulation resolution matters especially for multiple tests. [Phipson and Smyth 2010](https://gksmyth.github.io/pubs/PermPValuesPreprint.pdf).
- Accurate ePSF modeling is central to avoiding undersampling-induced astrometric bias; Anderson and King demonstrated it on HST, not SPHEREx. Their ePSF includes the information relevant to pixel measurements and varies spatially. [Anderson and King 2000](https://arxiv.org/abs/astro-ph/0006325).

### Inferences
The following are proposed NADIR implementation rules, rather than universal literature thresholds:

1. Fit identical unmasked native pixels under H1 and H2, with the same background family and externally supplied variance. Use multi-start optimization; retain convergence and boundary diagnostics. A plane background under both hypotheses helps avoid rewarding H2 for a gradient. Additional broadening/ellipticity is a useful adversarial single-source alternative.
2. Let `T = chi2_H1 - chi2_H2`. Fit H1, simulate B images from the fitted H1 and assumed noise model, and refit both hypotheses for every simulated image. Preserve masks, parameter bounds, initialization strategy, and model-selection procedure. Report `(1 + count(T_sim >= T_obs))/(B + 1)` as **conditional plug-in Monte Carlo tail evidence**, with B, seed, exceedance count and minimum resolvable value. Estimating the null parameters means this is not an exact randomization test; the plus-one convention does not cure misspecified noise, PSF or nuisance parameters.
3. B=99 has minimum tail resolution 0.01; B=19 has resolution 0.05. Neither supports very small false-alarm claims. Include binomial uncertainty of the exceedance proportion, and do not relabel the number as posterior probability of a companion.
4. Record BIC only as a descriptive penalized-fit diagnostic. Do not turn `exp(-Delta BIC/2)` into model odds here. Near-zero flux/separation can cause degeneracy; a Hessian inverse is then unreliable. Return null uncertainty or empirical bootstrap intervals, and report the degenerate fraction rather than hiding it.
5. Require acceptable H2 residuals, no influential masked core, no optimizer boundary hit, and stability to plausible PSF/background perturbations before even a provisional `multiple_source_model_favored` state. If H2 wins against a wrong H1 but also fits poorly, choose `ambiguous` or `invalid_or_contaminated`, not a detection.
6. Independent exposure consistency means separation and sky direction agree after coordinate transformation, allowing wavelength-dependent flux ratios. Repeated PSF error can survive repeated observations; this is necessary but not sufficient evidence. Leave-one-exposure-out predictive checks are preferable to fitting every frame and calling each frame validation.
7. Report per-target evidence separately from survey-wide discovery significance. If expensive fits run only after a data-driven screening step, simulate the whole selection pipeline or use independent follow-up observations; otherwise calibration omits selection effects.

### Gaps
- No universal SPHEREx split threshold or robust PSF uncertainty distribution was established by this literature review. Those require instrument-specific injection/recovery and isolated-star residual studies.
- A small synthetic null suite cannot validate a 1% false-positive rate. Zero false positives in n trials still has a one-sided 95% binomial upper bound `1 - 0.05**(1/n)`; this algebra gives about 14% for n=20, not zero.

## What can multi-frame reconstruction establish?

### Takeaway
Dithered exposures can add sampling information, but improved localization must be separated from improved resolving power. Validate reconstruction by original-pixel predictions and injected truth, not sharpness.

### Cited Findings
- Drizzle is a linear reconstruction approach for undersampled, dithered images, designed to preserve photometry and account for statistical weights and distortion. [Fruchter and Hook 2002](https://arxiv.org/abs/astro-ph/9808087).
- Splitting an input pixel among several output pixels correlates output noise; naive output-pixel variance does not give aperture-scale uncertainty. Correlation depends on geometry and drizzle parameters. With zero-sized drops, a given independent input pixel contributes to only one output pixel. [STScI DrizzlePac correlated-noise documentation](https://hst-docs.stsci.edu/drizzpac/chapter-3-description-of-the-drizzle-algorithm/3-3-weight-maps-and-correlated-noise).

### Inferences
- Start with a weighted coadd and joint native-pixel point-source fit. Joint fitting can improve positions through independent noise averaging even with identical pixel phases; that alone is not evidence of super-resolution.
- Compare no dither, diverse subpixel dithers, and identical total integration/noise budgets. Add known registration jitter and PSF mismatch to expose where gains disappear.
- A finer output grid changes sampling only. Output variance needs squared interpolation weights, coverage, and a covariance warning; multiplying interpolated inverse variances is insufficient.
- For drizzling, preserve brightness units, track output pixel area, and test flux conservation on complete coverage. Uncovered pixels must remain missing, not fabricated zero measurements.
- Hold source structure fixed only when appropriate. Different bandpasses and variable flux ratios can invalidate a static coadd; a joint model can permit per-exposure amplitudes while retaining shared sky coordinates.
- Use native-image residual likelihood, centroid RMS, relative flux bias, false splits, completeness, runtime, and split direction stability. Cross-validate regularization strength on held-out exposures rather than choosing the sharpest reconstruction.

### Gaps
- These sources establish general methods, not evidence that available NADIR exposures have sufficient dither diversity, registration precision or bandpass overlap. Metadata must decide that.

## Is ML justified, and how should multiple features be screened?

### Takeaway
A tiny unlabeled demonstration sample supports an auditable feature framework and experimental ranking, not a scientifically validated classifier. Report known-label metrics only for actual labels, and keep all epochs of a source in one validation group.

### Cited Findings
- Outlier detection identifies low-density deviations within a training sample; novelty detection assumes a clean reference population and evaluates new observations. Algorithmic outlier labels follow score thresholds, often controlled by a contamination setting. [scikit-learn outlier documentation](https://scikit-learn.org/stable/modules/outlier_detection.html).
- Grouped validation keeps dependent observations out of both train and test simultaneously; `GroupKFold` ensures groups do not overlap across those partitions. [scikit-learn grouped validation](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data).

### Inferences
- An isolation score is not a probability of astrophysical novelty. Choosing contamination=0.05 forces a ranking threshold without measuring a 5% physical anomaly rate.
- Use feature definitions with units, provenance, uncertainty, validity and missing reason. Convert eligible measurements to comparable standardized residuals or null tail evidence; do not sum arcseconds, magnitudes and residual fractions directly.
- A conservative transparent screen can use valid marginal p-values with a Bonferroni minimum-p adjustment. Dependence does not invalidate that bound, but uncalibrated marginal inputs do. Otherwise label the aggregate as a heuristic ranking.
- Missing data should not become zero anomaly evidence. Report feature coverage; compare rankings under masks and with/without individual features. Apparent performance gains caused by availability differences are not scientific improvements.
- Fit medians/scales/imputation only on training groups; split by source, then additionally hold out fields or visits to test domain shift. A stable ranking under bootstrap resampling is useful but is not precision/recall without labels.
- Synthetic positive/negative source labels can quantify controlled pipeline behavior. They cannot establish real-sky classifier precision. Suitable next data are independently vetted isolated stars, confirmed blends and known artifact examples spanning brightness, detector, wavelength, field density and pixel phase.
- Adaptive computation should record both saved runtime and missed injected cases. Screening thresholds need a held-out injection set; do not tune and evaluate on the same realization.

### Gaps
- No available label census was inspected by this research subtask. The parent must count independent objects and verified labels before concluding whether any ML experiment is supported.
