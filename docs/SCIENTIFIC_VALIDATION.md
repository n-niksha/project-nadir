# Milestone 5 scientific validation

## Scope and data availability

The bounded validation resolved Epsilon Indi B, WISE 0855, RW Aurigae and R Draconis and queried SPHEREx coverage. Barnard's Star and Teegarden's Star were also included using existing cached observations. The run selected at most six primary-band and two cross-band exposures for each newly queried target. IRSA returned **12 HTTP 503 “Server busy, retry later” responses** for selected cutouts. These 12 observations are unavailable external data, not successful measurements and not evidence of absent archive coverage. Their URLs and errors are retained in `docs/validation/targets_manifest.json` and `live_results.json`.

The available subset built and was replayed with network access blocked. `offline_results.json` records six successful field builds and zero network attempts. This verifies replay from the local FITS files only; it does not validate the failed cutouts or constitute complete archive coverage.

| Target | Usable primary epochs | Existing motion tier | Forced photometry accepted / rejected | Interpretation |
|---|---:|---|---:|---|
| Barnard's Star | 12 D2 | systematics_limited | 0 / 18 | Saturation prevents flux measurement; motion remains systematics-limited. |
| Epsilon Indi B | 3 D2 | stationary | 4 / 0 | Sparse epochs; no useful motion conclusion. |
| WISE 0855 | 3 D6 | insufficient | 4 / 0 | Insufficient motion evidence; NADIR assigns no object class. |
| RW Aurigae | 4 D2 | stationary | 1 / 4 | Insufficient spectral coverage. |
| R Draconis | 5 D2 | insufficient | 0 / 7 | Insufficient usable spectral signal. |
| Teegarden's Star | 12 D2 | stationary | 4 / 14 | Few accepted samples; no spectral characterization. |

The nearest-track match within one native pixel is provisional, not independent source identity confirmation. “Stationary” describes the pipeline's evidence tier, not proof of zero motion. Spectral samples are non-simultaneous; WCS-WAVE wavelengths are approximate. No real discovery, chemical inference, precise parallax, or optical super-resolution is established.

## Controlled benchmark evidence

The existing synthetic motion experiment used 100 trials with one gross outlier. Robust rate RMSE was 0.0815 arcsec/year versus 0.1436 for ordinary least squares, with 99% interval coverage in that experiment. The synthetic blend experiment improved separation RMSE from 0.144 to 0.093 pixels for a 1.5-pixel, 1:2 blend over 24 trials; all 24 held-out frames had Δχ² > 10. These results validate only the stated synthetic assumptions, not real companions.

## Reliability behavior

The Milestone 5 changes add bounded TAP timeouts, typed job failure details, per-cutout spectral failure records, retry after partial catalog failures, and deterministic JSON/Markdown evidence export. Existing endpoint fields are retained with additive response fields. The complete real-data and offline outputs are in `docs/validation/`.
