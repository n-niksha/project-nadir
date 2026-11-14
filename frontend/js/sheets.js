// Discover (ranked investigation list), Learn (short explainers), Help (data & provenance).
import { S, TIERS, emit } from "./state.js";
import { esc, fmt, pm } from "./fmt.js";
import { displayName } from "./inspector.js";

export function discoverHTML() {
  const f = S.field;
  if (!f) return `<h1>Discover</h1><p class="lede">Load a field first.</p>`;
  const src = f.sources.filter((s) => S.tiers.has(s.tier));
  const ranked = src.filter((s) => s.priority > 0).sort((first, second) => second.priority-first.priority);
  const rejected = src.filter((s) => s.tier === "rejected");
  const counts = f.tier_counts;
  let h = `<h1>Discover in this field</h1><p class="lede">Every source NADIR found in ${f.epochs.filter((e) => e.usable).length} SPHEREx ${esc(f.band)} images of this field, judged by whether one straight-line motion explains <i>all</i> its positions across independent visits — not just two images.</p>`;
  h += `<p>These are investigation priorities, not verified astronomical discoveries. Inspect the evidence and compare with reference sources.</p><div class="chips" aria-label="Filter candidates by evidence">${Object.entries(TIERS).filter(([k]) => counts[k]).map(([k, t]) => `<button class="chip" data-discovery-tier="${k}" aria-pressed="${S.tiers.has(k)}">${t.label} · ${counts[k]}</button>`).join(" ")}</div>`;
  h += `<h3>Worth investigating (ranked)</h3>`;
  if (!ranked.length) h += `<p class="small">Nothing in this field passes the motion or variability thresholds. That is the normal, honest outcome for most of the sky.</p>`;
  else h += table(ranked);
  h += `<h3>Rejected by the multi-epoch check</h3>`;
  h += rejected.length ? `<p class="small">These passed a classic two-image test (≥5σ displacement between the best-matched pair) but are not reproduced by the other visits. Typical causes: blends, wavelength-dependent centroids, pixel-phase effects. This is the scientific safety net.</p>` + table(rejected)
    : `<p class="small">No two-image detection in this field was contradicted by the other visits.</p>`;
  h += `<h3>How the ranking works</h3><div class="card"><p>Priority = min(motion S/N, 50) × consistency × quality + min(var_z, 30) × clean fraction. Consistency = 1 when the straight-line fit has χ²ᵣ ≤ 5, else 5/χ²ᵣ. Quality = 1 (clean), 0.6 (saturated but consistent), 0.3 × clean fraction (candidates). Variability counts only when var_z ≥ 5 from ≥ 6 matched-wavelength comparisons.</p><p><b>This is a transparent ordering heuristic, not a probability</b>, and it was not trained on labels. See docs/SCIENCE.md for why no machine-learning model is used yet.</p></div>`;
  const references = src.filter(source => source.tier === "stationary").sort((first, second) => second.clean_fraction-first.clean_fraction).slice(0,12);
  h += `<details class="evidence"><summary>Compare with low-motion field sources (${references.length} examples)</summary><p>These sources have no significant fitted motion. This is a comparison list, not a guarantee that each was used in every registration.</p>${table(references)}</details>`;
  return h;
}

function table(list) {
  let h = `<table class="data"><thead><tr><th>Object</th><th>Evidence</th><th>Proper motion ″/yr</th><th>S/N</th><th>visits</th><th>var_z</th><th>Gaia agrees?</th><th>priority</th></tr></thead><tbody>`;
  for (const s of list.slice(0, 80)) {
    const c = s.catalog?.pm_comparison, f = s.fit;
    h += `<tr><td><button class="learn-link" data-sid="${s.id}">${esc(displayName(s))}</button><p class="small">${esc((s.reasons || []).join('; ') || 'Consistent multi-epoch fit under current quality gates.')}</p></td><td><span class="tier ${s.tier}">${TIERS[s.tier]?.label}</span></td>
      <td class="num">${f ? `${fmt(f.pm_total, 2)} (E ${pm(f.pm_east, f.pm_east_err, 1)}, N ${pm(f.pm_north, f.pm_north_err, 1)})` : "—"}</td><td class="num">${f ? fmt(f.snr, 1) : "—"}</td>
      <td class="num">${s.n_visits}</td><td class="num">${s.variability?.var_z != null ? fmt(s.variability.var_z, 1) : "—"}</td>
      <td>${c ? (c.consistent ? `yes (${fmt(c.z_total, 1)}σ)` : `<b>no</b> (${fmt(c.z_total, 1)}σ)`) : "<span class='small'>no Gaia PM</span>"}</td><td class="num">${fmt(s.priority, 1)}</td></tr>`;
  }
  return h + `</tbody></table>`;
}

export const LEARN = {
  wavelength: { t: "Light, wavelength and SPHEREx's six detectors", b: `<p>Visible light spans 0.4–0.7 µm. SPHEREx looks just beyond, at 0.75–5 µm, where cool stars are brightest, dust becomes transparent, and ices and molecules leave fingerprints.</p><p>Its six detectors (D1–D6) each cover a slice of that range; together they record <b>102 wavelength channels</b> across the whole sky every six months.</p>`, demo: "wavelength" },
  "proper-motion": { t: "Proper motion: stars drift", b: `<p>Stars orbit the Galaxy, so nearby ones slowly shift against the distant background. Barnard's Star moves 10.4″ per year — the width of a SPHEREx pixel (6.15″) in about seven months.</p><p>NADIR measures this by locating each star in every image and fitting a straight line through time.</p>` },
  parallax: { t: "Parallax: the yearly wobble", b: `<p>As Earth circles the Sun, nearby stars appear to trace a small ellipse — 0.55″ for Barnard's Star. It is how distances are measured.</p><p>SPHEREx has only ~3 visits so far, too few to separate parallax from motion reliably, so NADIR does not fit it; the Gaia prediction curve includes it for comparison.</p>` },
  registration: { t: "Image registration: the stars are the ruler", b: `<p>Each image's coordinate solution is good to a fraction of an arcsecond, not perfect. NADIR aligns every epoch using the <b>field stars themselves</b>: it matches ~100 stars, fits a shift + rotation + scale, and discards outliers (including real movers) by robust clipping.</p><p>The leftover scatter of those reference stars (typically 0.2–0.4″) is the floor for every motion measurement.</p>` },
  spectroscopy: { t: "Infrared spectroscopy with a rainbow filter", b: `<p>SPHEREx has no prism. Its filters change colour across the detector (a linear variable filter), so each pixel sees a slightly different wavelength. As the telescope steps across the sky, a star lands on different colours in different exposures.</p><p>Measuring the star in many exposures gives its spectrum. Molecules absorb at specific wavelengths: water near 1.4, 1.9 and 2.7 µm, CO at 2.3 and 4.6 µm, ices at 3.0 and 4.3 µm.</p>` },
  uncertainty: { t: "Uncertainty and evidence tiers", b: `<p>Every number has an error bar, and errors come in two kinds: <b>random</b> (shrinks with more data) and <b>systematic</b> (does not). Saturated stars carry a 1–2″ systematic per visit; NADIR fits visit averages to respect it.</p><p>A two-image detection becomes <b>significant</b> only if repeat visits reproduce it. Anything less stays a <i>candidate</i> — never a discovery.</p>` },
  evidence: { t: "Reading the evidence labels", b: `<p><span class="tag measured">measured</span> from SPHEREx pixels by NADIR. <span class="tag cat">catalog</span> reported by Gaia, SIMBAD, 2MASS or AllWISE. <span class="tag inferred">inferred</span> from an explicit model (e.g. blackbody). <span class="tag illustrative">illustrative</span> educational context, not a measurement. <span class="tag unknown">unknown</span> not supported by the available data.</p>` },
};

export function learnHTML(focus) {
  let h = `<h1>Learn</h1><p class="lede">Short, optional explanations of what NADIR shows. Each takes under a minute.</p><div class="cards">`;
  for (const [k, c] of Object.entries(LEARN)) {
    h += `<article class="card" id="learn-${k}" ${focus === k ? 'style="border-color:var(--amber)"' : ""}><h4>${c.t}</h4>${c.b}`;
    if (c.demo === "wavelength") h += `<div class="demo"><input type="range" id="wl-demo" min="0" max="101" value="23" aria-label="Wavelength channel"> <span id="wl-out" class="num"></span> <button class="btn" id="wl-explore">See this wavelength on the sky</button></div>`;
    h += `</article>`;
  }
  return h + `</div>`;
}

export function helpHTML(cfg) {
  const f = S.field, q = f?.provenance?.query;
  return `<h1>Data, provenance &amp; help</h1><p class="lede">NADIR shows only real observations. Everything below is traceable to an archive request.</p>
  <div class="cards">
   <div class="card"><h4>SPHEREx images</h4><p>Level-2 spectral images, releases DR1 and QR3, from the NASA/IPAC Infrared Science Archive (IRSA). Discovery via IRSA SIA v2; pixels via the IRSA cutout service. Each cutout's URL, retrieval time and SHA-256 are kept in a provenance record (Observations tab → prov).</p>
   ${q ? `<p class="small">This field: query ${q.cached ? (q.stale ? "served from an <b>expired cache</b> because the archive failed" : "from local cache") : "fresh from IRSA"}, retrieved ${esc(q.retrieved_utc)}.</p>` : ""}</div>
   <div class="card"><h4>All-sky view</h4><p>SPHEREx DR1 all-sky HiPS cube (102 wavelength frames) served by IRSA, displayed with Aladin Lite (CDS). Used for exploration only; measurements always use the original images.</p></div>
   <div class="card"><h4>Catalogs</h4><p>Gaia DR3, 2MASS PSC and AllWISE via IRSA TAP; SIMBAD via TAP (CDS, with the CfA mirror as fallback). Catalog values are labelled <span class="tag cat">catalog</span> and never mixed into NADIR measurements.</p></div>
   <div class="card"><h4>Keyboard</h4><p><kbd>Space</kbd> blink A/B · <kbd>D</kbd> difference · <kbd>P</kbd> animate all epochs · <kbd>←</kbd>/<kbd>→</kbd> step epoch B · <kbd>+</kbd>/<kbd>−</kbd>/<kbd>0</kbd> zoom · <kbd>Shift</kbd>+arrows pan · <kbd>Esc</kbd> close panel. Timeline: click sets B, Shift+click sets A.</p></div>
   <div class="card"><h4>What is production-ready</h4><p><b>Ready:</b> archive search, caching, registration, blink/difference/animation, multi-epoch proper motions and evidence tiers, catalog cross-match. <b>Experimental:</b> spectra (flux calibration checked against 2MASS/AllWISE only), variability statistic, saturated-star astrometry. <b>Planned:</b> parallax fitting, PSF-matched subtraction, trained anomaly model.</p></div>
   <div class="card"><h4>Privacy</h4><p>Notes stay in your browser's local storage. The server only talks to public NASA/IPAC and CDS services.</p><p class="small">NADIR ${esc(cfg?.version || "")}</p></div>
  </div>`;
}
