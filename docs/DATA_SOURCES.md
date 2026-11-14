# Data sources and evidence

NADIR uses real NASA/IPAC archive data. Synthetic images exist only in tests
and benchmarks and are never substituted for a failed archive request.

| Source | Use | Limits |
|---|---|---|
| SPHEREx DR1 + QR3 Level-2 spectral images | Native IMAGE, FLAGS, VARIANCE, celestial/spectral WCS, timestamps and ePSF tiles | Non-simultaneous wavelength sampling; undersampled PSF; saturation and artifacts |
| Gaia DR3 via IRSA | Position, PM, parallax, classification probabilities, optional temperature model | Catalog quantities, not NADIR measurements; matching can be ambiguous in blends |
| SIMBAD | Names, object types, spectral types, name resolution | Catalog classifications; positions require epoch handling |
| 2MASS | Historical positions and J/H/K photometry | Older epoch, broad bands, different resolution |
| AllWISE | Historical positions and W1/W2 photometry | Older epoch, broad bands, blends |

Archive discovery uses SIA with TAP fallback. Each cached cutout records its
original source URL, retrieval time, release, original-response SHA-256 and
parent archive checksum. The response hash is **not** a hash of the trimmed
cached FITS file: ePSF trimming changes that file, not the science pixels.
The Observations tab exposes provenance JSON; spectral ledger links reopen the
original IRSA cutout. No API key or credential is needed for these public data.

Official documentation used to verify the scientific presentation:

- [IRSA SPHEREx overview](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/overview.html): each image position has its own wavelength because of the linear variable filter.
- [SPHEREx data products](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/): spectral-image products and spatial cutouts.
- [IRSA spectrophotometry](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/sp.html): spectra assembled from measurements across spectral images.
- [IRSA SPHEREx FAQ](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/faq.html): bright-source saturation and examining spectral points by observation date.
- [IRTF stellar spectral library](https://irsa.ipac.caltech.edu/data/IRTF_Extra/SpectralLibrary/overview.html): empirical stellar spectra for further comparison. NADIR does not fit this library or claim template-based chemical identifications.

No hosted language model or trained ML classifier is used. Explanation text
is a deterministic transformation of available source results. Unknown values
stay unknown; catalog, measured, inferred and illustrative quantities are
labelled separately. See SCIENCE.md for calibration and model limitations.
