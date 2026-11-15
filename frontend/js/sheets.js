// Discover (ranked investigation leads), Learn (short explainers), Help (data & provenance).
import { S, TIERS } from "./state.js";
import { esc, fmt, pm, displayName } from "./fmt.js";

// ------------------------------------------------------------------ discover
export function discoverHTML() {
  const f = S.field;
  if (!f) return `<p class="eyebrow">Discover</p><h1>Load a field first.</h1><p class="lede">Choose a target in the Observatory, then come back to see what stands out.</p>`;
  const src = f.sources.filter((s) => S.tiers.has(s.tier));
  const ranked = src.filter((s) => s.priority > 0 && s.tier !== "rejected").sort((first, second) => second.priority - first.priority);
  const rejected = src.filter((s) => s.tier === "rejected");
  const counts = f.tier_counts, usable = f.epochs.filter((e) => e.usable).length;
  let h = `<p class="eyebrow">Discover · ${esc(S.query?.name || "this field")}</p><h1>What moves in <em>this</em> patch of sky?</h1>
    <p class="lede">NADIR followed all ${f.sources.length} sources it found through ${usable} SPHEREx ${esc(f.band)} images, and asked one question of each: does a single straight-line motion explain <i>every</i> position across independent visits — not just two images?</p>`;
  h += `<div class="callout"><b>These are investigation leads, not discoveries.</b> Each one shows its evidence tier. Open it to inspect the measurements, the uncertainty and the comparison with independent catalogs.</div>`;
  h += `<div class="chips" aria-label="Filter by evidence">${Object.entries(TIERS).filter(([k]) => counts[k]).map(([k, t]) => `<button class="chip" data-discovery-tier="${k}" aria-pressed="${S.tiers.has(k)}"><span class="dot" style="background:${t.color}"></span>${t.label}<span class="n">${counts[k]}</span></button>`).join("")}</div>`;
  h += `<h3>Worth investigating</h3>`;
  h += ranked.length ? `<div class="cand-grid">${ranked.slice(0, 24).map(card).join("")}</div>`
    : `<div class="empty">Nothing in this field passes the motion or variability thresholds. That is the normal, honest outcome for most of the sky.</div>`;
  h += `<h3>Rejected by the multi-epoch check</h3>`;
  h += rejected.length ? `<p class="small" style="max-width:720px">These passed a classic two-image test (≥5σ displacement between the best-matched pair) but are not reproduced by the other visits. Typical causes: blends, wavelength-dependent centroids, pixel-phase effects. This is NADIR's scientific safety net.</p><div class="cand-grid">${rejected.map(card).join("")}</div>`
    : `<p class="small">No two-image detection in this field was contradicted by the other visits.</p>`;
  h += `<h3>How the ranking works</h3><div class="card"><p>Priority = min(motion S/N, 50) × consistency × quality + min(var_z, 30) × clean fraction. Consistency = 1 when the straight-line fit has χ²ᵣ ≤ 5, else 5/χ²ᵣ. Quality = 1 (clean), 0.6 (saturated but consistent), 0.3 × clean fraction (candidates). Variability counts only when var_z ≥ 5 from ≥ 6 matched-wavelength comparisons.</p><p><b>This is a transparent ordering heuristic, not a probability</b>, and it was not trained on labels. See docs/SCIENCE.md for why no machine-learning model is used yet.</p></div>`;
  const references = src.filter(source => source.tier === "stationary").sort((first, second) => second.clean_fraction - first.clean_fraction).slice(0, 12);
  h += `<details class="evidence"><summary>Compare with low-motion field sources (${references.length} examples)</summary><p>These sources have no significant fitted motion. This is a comparison list, not a guarantee that each was used in every registration.</p>${table(references)}</details>`;
  return h;
}

function card(s) {
  const c = s.catalog?.pm_comparison, f = s.fit, T = TIERS[s.tier];
  return `<button class="cand ${s.tier}" data-sid="${s.id}">
    <span class="tier ${s.tier}" style="justify-self:start">${esc(T?.label || s.tier)}</span>
    <span class="cand-name">${esc(displayName(s))}</span>
    <span class="cand-pm">${f ? `${fmt(f.pm_total, 2)}<small> ″/yr · ${fmt(f.snr, 1)}σ</small>` : `—<small> no motion fit</small>`}</span>
    <span class="cand-stats"><span><b>${s.n_visits}</b> visits</span>${s.variability?.var_z != null ? `<span>var_z <b>${fmt(s.variability.var_z, 1)}</b></span>` : ""}${c ? `<span>Gaia <b>${c.consistent ? "agrees" : "differs"}</b> (${fmt(c.z_total, 1)}σ)</span>` : ""}<span>priority <b>${fmt(s.priority, 1)}</b></span></span>
    <span class="cand-why">${esc((s.reasons || []).join("; ") || "Consistent multi-epoch fit under current quality gates.")}</span>
    <span class="cand-go">Open investigation →</span></button>`;
}

function table(list) {
  let h = `<table class="data"><thead><tr><th>Object</th><th>Evidence</th><th>Proper motion ″/yr</th><th>S/N</th><th>Visits</th><th>Gaia agrees?</th></tr></thead><tbody>`;
  for (const s of list.slice(0, 80)) {
    const c = s.catalog?.pm_comparison, f = s.fit;
    h += `<tr><td><button class="learn-link" data-sid="${s.id}">${esc(displayName(s))}</button></td><td><span class="tier ${s.tier}">${TIERS[s.tier]?.label}</span></td>
      <td class="num">${f ? `${fmt(f.pm_total, 2)} (E ${pm(f.pm_east, f.pm_east_err, 1)}, N ${pm(f.pm_north, f.pm_north_err, 1)})` : "—"}</td><td class="num">${f ? fmt(f.snr, 1) : "—"}</td>
      <td class="num">${s.n_visits}</td><td>${c ? (c.consistent ? `yes (${fmt(c.z_total, 1)}σ)` : `<b>no</b> (${fmt(c.z_total, 1)}σ)`) : "<span class='small'>no Gaia PM</span>"}</td></tr>`;
  }
  return h + `</tbody></table>`;
}

// --------------------------------------------------------------------- learn
// Diagrams are illustrations (labelled as such), never data.
const ILLO = {
  wavelength: `<svg class="illo" viewBox="0 0 320 120" role="img" aria-label="Illustration: the visible band and SPHEREx's 0.75 to 5 micron range"><defs><linearGradient id="vis" x1="0" x2="1"><stop offset="0" stop-color="#7b5cff"/><stop offset=".25" stop-color="#3d8bff"/><stop offset=".5" stop-color="#3ddc84"/><stop offset=".75" stop-color="#ffd23d"/><stop offset="1" stop-color="#ff4d3d"/></linearGradient><linearGradient id="ir" x1="0" x2="1"><stop offset="0" stop-color="#8a3a2e"/><stop offset="1" stop-color="#1c1416"/></linearGradient></defs><rect x="20" y="46" width="48" height="22" rx="3" fill="url(#vis)"/><rect x="68" y="46" width="232" height="22" rx="3" fill="url(#ir)"/><path d="M76 38v-8h224v8" fill="none" stroke="#f2b350" stroke-width="1.5"/><text x="188" y="24" fill="#ffd699" font-size="12" text-anchor="middle" font-family="IBM Plex Sans">SPHEREx · 0.75–5 µm · 102 colours</text><text x="44" y="86" fill="#b6bbc8" font-size="11" text-anchor="middle" font-family="IBM Plex Sans">eyes</text><g font-family="IBM Plex Mono" font-size="10.5" fill="#8a91a3"><text x="20" y="104">0.4</text><text x="62" y="104">0.7</text><text x="118" y="104">1.5</text><text x="200" y="104">3</text><text x="288" y="104">5 µm</text></g></svg>`,
  "proper-motion": `<svg class="illo" viewBox="0 0 320 120" role="img" aria-label="Illustration: one star drifting against fixed background stars"><g fill="#b6bbc8">${[[30, 30], [70, 90], [110, 20], [250, 30], [280, 95], [200, 100], [60, 60], [300, 60], [150, 95]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2"/>`).join("")}</g><g fill="#f2b350"><circle cx="150" cy="78" r="3" opacity=".3"/><circle cx="168" cy="62" r="3" opacity=".55"/><circle cx="186" cy="46" r="4.5"/></g><path d="M150 78L182 49" stroke="#f2b350" stroke-width="1.4" stroke-dasharray="3 3"/><text x="196" y="44" fill="#ffd699" font-size="11.5" font-family="IBM Plex Sans">2026</text><text x="114" y="84" fill="#8a91a3" font-size="11.5" font-family="IBM Plex Sans">2025</text></svg>`,
  parallax: `<svg class="illo" viewBox="0 0 320 120" role="img" aria-label="Illustration: Earth's orbit makes a nearby star trace a small ellipse"><ellipse cx="90" cy="64" rx="60" ry="22" fill="none" stroke="#8fa8e8" stroke-opacity=".5"/><circle cx="90" cy="64" r="7" fill="#f2b350"/><circle cx="150" cy="64" r="4" fill="#8fa8e8"/><circle cx="30" cy="64" r="4" fill="#8fa8e8" opacity=".5"/><ellipse cx="262" cy="56" rx="18" ry="7" fill="none" stroke="#ffd699" stroke-dasharray="2 3"/><circle cx="280" cy="56" r="3.5" fill="#ffd699"/><path d="M150 64L280 56M30 64L244 56" stroke="#b6bbc8" stroke-opacity=".35"/><text x="262" y="88" fill="#8a91a3" font-size="11" text-anchor="middle" font-family="IBM Plex Sans">yearly wobble</text></svg>`,
  registration: `<svg class="illo" viewBox="0 0 320 120" role="img" aria-label="Illustration: two images aligned using the same stars"><g fill="none" stroke-width="1.2"><g stroke="#8fa8e8">${[[60, 30], [100, 70], [150, 40], [200, 85], [250, 35], [130, 95]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="5"/>`).join("")}</g><g stroke="#f2b350" stroke-dasharray="2 2">${[[60, 30], [100, 70], [150, 40], [200, 85], [250, 35], [130, 95]].map(([x, y]) => `<circle cx="${x + 9}" cy="${y + 5}" r="5"/>`).join("")}</g></g><path d="M268 70l18 10" stroke="#ffd699" stroke-width="1.4"/><text x="214" y="112" fill="#8a91a3" font-size="11" font-family="IBM Plex Sans">shift + rotate + scale</text></svg>`,
  spectroscopy: `<svg class="illo" viewBox="0 0 320 120" role="img" aria-label="Illustration: a linear variable filter, colour changing across the detector"><defs><linearGradient id="lvf" x1="0" x2="1"><stop offset="0" stop-color="#968cf0"/><stop offset=".35" stop-color="#78aaf0"/><stop offset=".6" stop-color="#5ec8b0"/><stop offset=".85" stop-color="#f2c878"/><stop offset="1" stop-color="#ef8c64"/></linearGradient></defs><rect x="20" y="20" width="280" height="44" rx="4" fill="url(#lvf)" opacity=".55"/>${[50, 110, 170, 230, 280].map((x, i) => `<circle cx="${x}" cy="42" r="4" fill="#fff"/><path d="M${x} 70v14" stroke="#b6bbc8" stroke-opacity=".5"/><circle cx="${x}" cy="${100 - [8, 14, 12, 6, 3][i]}" r="3" fill="#f2b350"/>`).join("")}<path d="M50 92L110 86L170 88L230 94L280 97" stroke="#f2b350" fill="none" stroke-opacity=".5"/></svg>`,
  uncertainty: `<svg class="illo" viewBox="0 0 320 120" role="img" aria-label="Illustration: measurements with error bars and a fitted line"><path d="M30 96L290 26" stroke="#f2b350" stroke-width="1.6"/>${[[50, 86, 10], [90, 80, 14], [130, 62, 9], [170, 60, 16], [210, 44, 10], [250, 40, 12]].map(([x, y, e]) => `<path d="M${x} ${y - e}V${y + e}" stroke="#8fa8e8"/><circle cx="${x}" cy="${y}" r="3.5" fill="#8fa8e8"/>`).join("")}<text x="40" y="22" fill="#8a91a3" font-size="11" font-family="IBM Plex Sans">±1σ: where the truth probably lies</text></svg>`,
  anomalies: `<svg class="illo" viewBox="0 0 320 120" role="img" aria-label="Illustration: NADIR's marker shapes for moving, candidate and rejected sources"><g font-family="IBM Plex Sans" font-size="11.5" fill="#b6bbc8" text-anchor="middle"><circle cx="60" cy="52" r="15" fill="none" stroke="#f2b350" stroke-width="2"/><path d="M60 52l26-20" stroke="#f2b350" stroke-width="1.6"/><text x="60" y="98">moving</text><circle cx="160" cy="52" r="15" fill="none" stroke="#ab98ec" stroke-width="2" stroke-dasharray="2 4"/><text x="160" y="98">candidate</text><path d="M252 44l16 16M268 44l-16 16" stroke="#ef7d6d" stroke-width="2"/><text x="260" y="98">rejected</text></g></svg>`,
};

export const LEARN = {
  wavelength: { t: "Light beyond the rainbow", k: "Infrared light", b: `<p>Visible light spans 0.4–0.7 µm. SPHEREx looks just beyond, at 0.75–5 µm, where cool stars are brightest, dust becomes transparent, and ices and molecules leave fingerprints.</p><p>Its six detectors (D1–D6) each cover a slice of that range; together they record <b>102 wavelength channels</b> across the whole sky every six months.</p>`, demo: "wavelength" },
  "proper-motion": { t: "Stars drift", k: "Proper motion", b: `<p>Stars orbit the Galaxy, so nearby ones slowly shift against the distant background. Barnard's Star moves 10.4″ per year — the width of a SPHEREx pixel (6.15″) in about seven months.</p><p>NADIR locates each star in every image and fits a straight line through time.</p>` },
  parallax: { t: "The yearly wobble", k: "Parallax", b: `<p>As Earth circles the Sun, nearby stars appear to trace a small ellipse — 0.55″ for Barnard's Star. It is how distances are measured.</p><p>SPHEREx has only ~3 visits so far, too few to separate parallax from motion reliably, so NADIR does not fit it; the Gaia prediction curve includes it for comparison.</p>` },
  registration: { t: "The stars are the ruler", k: "Image registration", b: `<p>Each image's coordinate solution is good to a fraction of an arcsecond, not perfect. NADIR aligns every epoch using the <b>field stars themselves</b>: it matches ~100 stars, fits a shift + rotation + scale, and discards outliers (including real movers) by robust clipping.</p><p>The leftover scatter of those reference stars (typically 0.2–0.4″) is the floor for every motion measurement.</p>` },
  spectroscopy: { t: "A rainbow filter", k: "Spectroscopy", b: `<p>SPHEREx has no prism. Its filters change colour across the detector (a linear variable filter), so each pixel sees a slightly different wavelength. As the telescope steps across the sky, a star lands on different colours in different exposures.</p><p>Measuring the star in many exposures gives its spectrum. Molecules absorb at specific wavelengths: water near 1.4, 1.9 and 2.7 µm, CO at 2.3 and 4.6 µm, ices at 3.0 and 4.3 µm.</p>` },
  uncertainty: { t: "How sure are we?", k: "Uncertainty", b: `<p>Every number has an error bar, and errors come in two kinds: <b>random</b> (shrinks with more data) and <b>systematic</b> (does not). Saturated stars carry a 1–2″ systematic per visit; NADIR fits visit averages to respect it.</p><p>“5σ” means the signal is five times its uncertainty — NADIR's bar for calling something motion.</p>` },
  anomalies: { t: "Finding the unusual — carefully", k: "Anomalies", b: `<p>A star that seems to jump between two images is exciting, but blends, pixel effects and colour changes can fake it. NADIR only calls motion <b>significant</b> when every independent visit agrees.</p><p>Anything weaker stays a <i>candidate</i>; contradicted detections are <i>rejected</i> and shown, not hidden. None of these is a claim of a new object.</p>` },
  evidence: { t: "Where each number comes from", k: "Evidence labels", b: `<p><span class="tag measured">measured</span> from SPHEREx pixels by NADIR.</p><p><span class="tag cat">catalog</span> reported by Gaia, SIMBAD, 2MASS or AllWISE.</p><p><span class="tag inferred">inferred</span> from an explicit model (e.g. blackbody).</p><p><span class="tag illustrative">illustrative</span> educational context, not a measurement.</p><p><span class="tag unknown">unknown</span> not supported by the available data.</p>` },
};

export function learnHTML(focus) {
  let h = `<p class="eyebrow">Learn</p><h1>The science, <em>one minute</em> at a time.</h1><p class="lede">Short, optional explanations of what NADIR shows. Diagrams are illustrations, not data.</p><div class="btnrow"><button class="btn accent" data-learn-go="tour">Take the guided demo</button><button class="btn ghost" data-learn-go="time">Back to the Observatory</button></div><div class="cards">`;
  for (const [k, c] of Object.entries(LEARN)) {
    h += `<article class="card ${focus === k ? "focus" : ""}" id="learn-${k}"><p class="eyebrow">${esc(c.k)}</p>${ILLO[k] || ""}<h4>${c.t}</h4>${c.b}`;
    if (c.demo === "wavelength") h += `<div class="demo"><input type="range" id="wl-demo" min="0" max="101" value="23" aria-label="Wavelength channel"> <span id="wl-out" class="num"></span> <button class="btn sm" id="wl-explore">See this wavelength on the sky</button></div>`;
    h += `</article>`;
  }
  return h + `</div>`;
}

// ---------------------------------------------------------------------- help
export function helpHTML(cfg) {
  const f = S.field, q = f?.provenance?.query;
  return `<p class="eyebrow">Data &amp; help</p><h1>Real observations, <em>fully traceable</em>.</h1><p class="lede">NADIR shows only real NASA SPHEREx observations. Every image, measurement and catalog value below can be traced to an archive request.</p>
  <div class="btnrow"><button class="btn accent" data-tour>Start the guided demo</button></div>
  <div class="cards">
   <div class="card"><h4>Investigate a source</h4><ol><li>Pick a target from the field name at the top left of the sky, or search.</li><li>Press <b>play</b> (or <kbd>P</kbd>) to watch the real observations in time order.</li><li>Click any star. The dossier opens with its headline result and evidence tier.</li><li>Open <b>Motion</b> for every measurement, the fit and Gaia's prediction.</li><li>Use <b>Explain this observation</b> for a plain-language or research-level account.</li></ol></div>
   <div class="card"><h4>Controls</h4><p><kbd>Space</kbd> blink A/B · <kbd>P</kbd> timelapse · <kbd>D</kbd> difference · <kbd>A</kbd>/<kbd>B</kbd> show one epoch · <kbd>←</kbd>/<kbd>→</kbd> step · <kbd>+</kbd>/<kbd>−</kbd>/<kbd>0</kbd> zoom · <kbd>Shift</kbd>+arrows pan · <kbd>/</kbd> search · <kbd>Esc</kbd> close.</p><p>Timeline: click a tick to view it; <kbd>Shift</kbd>-click sets A, <kbd>Alt</kbd>-click sets B. The chevron at the right of the Time Machine lists every exposure.</p></div>
   <div class="card"><h4>SPHEREx images</h4><p>Level-2 spectral images, releases DR1 and QR3, from the NASA/IPAC Infrared Science Archive (IRSA). Pixels via the IRSA cutout service. Each cutout's URL, retrieval time and SHA-256 are kept in a provenance record (Observations tab → prov).</p>
   ${q ? `<p class="small">This field: query ${q.cached ? (q.stale ? "served from an <b>expired cache</b> because the archive failed" : "from local cache") : "fresh from IRSA"}, retrieved ${esc(q.retrieved_utc)}.</p>` : ""}</div>
   <div class="card"><h4>What the image rendering does — and doesn't</h4><p><b>Presentation</b> fills isolated flagged pixels from their neighbours, applies a tone curve and resamples smoothly. It cannot add detail the telescope did not record. <b>Scientific pixels</b> shows the registered samples exactly, with flagged pixels in navy and saturated cores in white. All measurements use the original FITS pixels, never the display.</p></div>
   <div class="card"><h4>Time Machine honesty</h4><p>Every frame is one real exposure. Nothing is interpolated. Visits are shown as segments with their real gaps labelled; a thin line above shows true calendar spacing. <b>Motion · matched λ</b> keeps wavelengths within 2% so brightness changes are not colour changes.</p></div>
   <div class="card"><h4>Sky atlas &amp; catalogs</h4><p>The atlas shows the SPHEREx DR1 all-sky HiPS cube (IRSA) in Aladin Lite (CDS), with an optional, clearly labelled 2MASS context layer. It is for exploring only; measurements always use the original images.</p><p>Gaia DR3, 2MASS PSC and AllWISE via IRSA TAP; SIMBAD via CDS. Catalog values are labelled <span class="tag cat">catalog</span> and never mixed into NADIR measurements.</p></div>
   <div class="card"><h4>Limitations</h4><p><b>Ready:</b> archive search, caching, registration, blink/difference/timelapse, multi-epoch proper motions and evidence tiers, catalog cross-match. <b>Experimental:</b> spectra (flux checked against 2MASS/AllWISE only), variability statistic, saturated-star astrometry. <b>Not yet:</b> parallax fitting, PSF-matched subtraction, trained anomaly model.</p></div>
   <div class="card"><h4>Privacy</h4><p>Notes stay in your browser's local storage. The server only talks to public NASA/IPAC and CDS services.</p><p class="small">NADIR ${esc(cfg?.version || "")}</p></div>
  </div>`;
}
