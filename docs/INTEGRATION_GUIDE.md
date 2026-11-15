# Backend integration guide

Existing field, job, catalog, spectrum, frame, pair and source endpoints remain compatible. Integer source IDs are local to a field build; use `source_key` from an evidence snapshot for saved investigations.

| Endpoint | Behavior |
|---|---|
| `POST /api/fields` | Existing `{ra,dec,size,band,refresh?}` build/job flow. |
| `GET /api/jobs/{id}` | Existing progress and `error`; adds `failure: {code,retryable,stage,message}` on errors. |
| `GET /api/fields/{fid}` | Existing field summary, registration and per-epoch quality. |
| `POST /api/fields/{fid}/catalogs` | Retries incomplete catalog loads. |
| `POST /api/fields/{fid}/spectral?cached_only=true` | Existing job; adds per-URI `failure_details`. |
| `GET /api/fields/{fid}/sources/{sid}/spectrum` | Existing measured and rejected points. |
| `GET /api/science/fields/{fid}/diagnostics` | Optional observation availability and quality. |
| `GET /api/science/fields/{fid}/sources/{sid}/features` | Feature definitions, units, provenance, missing values and existing heuristic rank. Real-field calibrated screening remains unavailable. |
| `POST /api/science/fields/{fid}/decomposition` | Optional experimental job. It does not modify field detections or tracks. |
| `GET /api/science/fields/{fid}/sources/{sid}/evidence` | Deterministic JSON evidence snapshot with report hash ETag. |
| `GET …/evidence?format=markdown` | Downloadable Markdown snapshot. |

Evidence export reads stored measurements only; it does not query catalogs or compute new spectra or experimental fits. It includes provenance, FITS/code hashes, motion uncertainties, registration, catalog comparisons, quality tier and limitations. A 404 means the field/source is not resident. A 422 means invalid parameters. A failed job preserves its human-readable error and adds a typed failure object. Render absent or contaminated measurements as unavailable or uncertain; do not substitute zero. Catalog results can be partial and spectral points can be rejected individually.

Cold archive calls dominate latency and can fail. Evidence export hashes local FITS and can take tens to hundreds of milliseconds depending on exposure count. Decomposition is optional and expensive; do not invoke it for every source. Start the backend on port 8002 for local demonstrations. Offline validation requires the FITS files named in the manifest; their local paths are machine-specific.

Validated within tested conditions: existing API contracts, explicit failure behavior and deterministic offline evidence export. Experimental: source decomposition, anomaly ranking and reconstruction. Unsupported: confirmed discoveries from modeled components, real optical super-resolution, reliable parallax in these fields and supervised ML classification.
