# SPHEREx instrument constraints on inverse imaging

## What forward model is supported by DR1 and QR3?

### Takeaway
Fit the delivered effective PSF directly at native pixel centers. Its finer grid represents subpixel response, not a fivefold improvement in telescope resolution.

### Cited Findings
- The ePSF includes the pixel response; the official tutorial renders it at native pixel centers using the source's subpixel phase and optionally normalizes that rendered stamp. Image versions from 7.0 contain EPSF; earlier versions contain the superseded optical PSF. The calibration is detector-dependent and currently treated as time-independent. The tutorial identifies zones using original-image coordinates, with `x_original = 1 + x_cutout - CRPIX1A` and the corresponding y formula. Its example header gives `OVSMPX=OVSMPY=5`, `TDIM11=(33,33)`, `DETCOORD='sky'`, and describes the latter as the MWIR x flip matching the SWIR view. This is L2 image orientation, not a celestial north-up reprojection. — [Official ePSF tutorial](https://caltech-ipac.github.io/irsa-tutorials/spherex-psf/)
- DR1/QR3 use 21×21 zones for detectors 1, 2, 4, 5, 6 and 11×41 for detector 3. Each ePSF is 33×33, sampled five times finer than native pixels. — [Archive product specification](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/)

### Inferences
- Do not convolve the effective PSF with a pixel window a second time. For synthetic optical Gaussian tests, integrate the optical profile over each pixel; for archive ePSF tests, sample the ePSF.
- Native-frame fitting avoids an extra interpolation operation. Forward-transform source positions into each exposure and use that exposure's detector zone. A single common PSF on north-up resampled images is not automatically justified.
- Compare phase-dependent sums of interpolated ePSFs. Normalizing only the zero-phase lattice, as existing NADIR code does, is not equivalent to normalizing every rendered stamp. Any modification needs measured flux-conservation tests before replacing the baseline.

### Gaps
- The tutorial's prose claims a five-native-pixel footprint despite the 33-sample, fivefold grid. Its explicit array shape and oversampling imply 6.4 native pixels between endpoint centers; use metadata rather than that sentence.
- No per-exposure ePSF uncertainty covariance was found. Spatial tile interpolation, temporal stability, truncation and centroid conventions require empirical sensitivity tests.

## What metadata and quality controls matter?

### Takeaway
Preserve native units, masks, calibration versions and exposure identity. Cutout wavelength coordinates alone do not support precision spectral inference.

### Cited Findings
- IMAGE is calibrated MJy/sr and includes zodiacal emission; VARIANCE is in (MJy/sr)². WCS-WAVE is intended for visualization; science analysis should use CWAVE and CBAND calibration images. The solid-angle calibration map is in square arcseconds. LVF observations move a source through different detector positions and wavelengths. — [Archive product specification](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/)
- The official forced-photometry workflow fits flux and local background with specified positions/morphologies. It can jointly fit specified companions. It excludes SUR_ERROR, NONFUNC, MISSING_DATA, HOT, COLD, NONLINEAR, PERSIST, BLOOM, SNOWBALL, HALO and SATELLITE_HALO pixels. Its output retains exposure, detector, MJD and collection. DICHROIC measurements deserve particular calibration skepticism. — [Official spectrophotometry documentation](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/sp.html)
- IRSA warns that near-Jansky brightness may cause saturation-driven spectral oscillations; spectra combine epochs, so temporal variability also matters. Forced positions wrong by 1.5–2 arcseconds can lose significant signal. Crowded Galactic-center astrometry may be problematic. — [IRSA FAQ](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/faq.html)

### Inferences
- Add experimental-fit masks without silently changing the established motion baseline. Treat saturated/masked cores as contaminated or insufficient for source-count inference unless independent validation establishes otherwise.
- Record BUNIT, detector, pipeline VERSION, EPSF HISTORY, oversampling, zone coordinates, WCS, MJD, flags, wavelength provenance and original cutout URL/hash. Deduplicate the same physical exposure before evaluating independent support.
- Coaddition across a detector band is not automatically monochromatic. Require overlapping effective bandpasses and assess temporal variability; otherwise fit exposure-specific fluxes and decline a common static reconstruction.
- A simple blackbody fit to WCS-WAVE points remains approximate; no chemical abundance or detailed class is justified by that fit.

### Gaps
- Real cache coverage, useful matched wavelengths, subpixel phase diversity and registration precision must be measured in this repository. Archive descriptions do not establish these for NADIR targets.
- A median solid angle is only an approximation to the per-pixel map; inspect field variation before precision photometry claims.

## What limits reconstruction and statistical confidence?

### Takeaway
Subpixel offsets can constrain a forward model, but improved sampling is not proof of recoverable fine structure. Noise and model mismatch require separate tests.

### Cited Findings
- SPHEREx deliberately undersamples its optical PSF. The instrument paper describes interpixel capacitance as part of effective PSF formation and electronic 1/f noise as a source of spatial correlation. Row chopping relocates correlated-noise power in spatial frequency without reducing the image RMS by itself. For compact-source photometry, the authors expect read noise to be subdominant to zodiacal photon noise. — [Korngut et al., instrument performance, sections III.1, V.2 and VII.1](https://arxiv.org/html/2603.29835v1)
- DR1 reprocesses Surveys 1–2 using the improved calibration/pipeline shared with QR3; QR3 covers Surveys 3–4. — [Official release description](https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-releases/)

### Inferences
- The delivered variance image is not a pixel covariance matrix. Independent-Gaussian simulations are a transparent conditional benchmark, not real-data false-positive calibration. Also inject correlated noise, PSF width/asymmetry mismatch, pointing error, background gradients, masks and variable blend ratios.
- Native-frame joint source fitting is a useful first inverse-model baseline. Compare to a single exposure and weighted coaddition with matched total integration, and separately test whether phase diversity adds information beyond increased photon counts.
- Separate registration/calibration stars from validation sources. Use independent visits or detector orientations for confirmation; repeated pixels or shared systematics do not create independent evidence.
- Bootstrap source-count selection must repeat the search and optimization under the one-source null. A usual chi-square likelihood-ratio reference is unsafe at zero secondary flux or zero separation. This is a methodological recommendation, not a result measured on SPHEREx here.

### Gaps
- The full current explanatory supplement could not be fetched by the browser: its approximately 30 MB response exceeded the tool limit. Search-extracted header examples corroborate ePSF orientation, but do not substitute for reading all instrument-systematics sections. No large FITS or PDF was downloaded.
- No release-specific pixel covariance kernel or quantitative astrometric floor for these selected cutouts was established. Do not assign a universal value from the mission's global performance claims.
