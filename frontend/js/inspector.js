// Object Investigation Lab.  Every quantity carries an evidence label:
// Measured (NADIR, from SPHEREx pixels), Catalog (external survey),
// Inferred (explicit model), Illustrative (education), Unknown.
import { S, TIERS, emit } from "./state.js";
import { getJSON, postJSON, pollJob } from "./api.js";
import { esc, fmt, pm, sexa, dateTime, mjdToDate, VISIT_COLORS, OTYPE, lambdaColor } from "./fmt.js";
import { plot } from "./plots.js";

const L = { measured: '<span class="tag measured">measured</span>', catalog: '<span class="tag cat">catalog</span>',
  inferred: '<span class="tag inferred">inferred</span>', illustrative: '<span class="tag illustrative">illustrative</span>', unknown: '<span class="tag unknown">unknown</span>' };

export class Inspector {
  constructor() {
    this.el = document.getElementById("inspector"); this.body = document.getElementById("insp-body");
    document.getElementById("insp-close").onclick = () => emit("select", null);
    this.el.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => this.tab(b.dataset.tab)));
    this.el.querySelector(".tabs").addEventListener("keydown", (e) => {
      const tabs = [...this.el.querySelectorAll(".tabs button")], i = tabs.findIndex((t) => t.dataset.tab === S.tab);
      if (e.key === "ArrowRight") { this.tab(tabs[(i + 1) % tabs.length].dataset.tab); tabs[(i + 1) % tabs.length].focus(); }
      if (e.key === "ArrowLeft") { this.tab(tabs[(i + tabs.length - 1) % tabs.length].dataset.tab); tabs[(i + tabs.length - 1) % tabs.length].focus(); }
    });
    this.spectra = new Map();
  }
  open(d) {
    this.d = d; this.el.hidden = false;
    const name = displayName(d);
    document.getElementById("insp-title").textContent = name;
    document.getElementById("insp-kicker").textContent = `NADIR source #${d.id} · ${TIERS[d.tier]?.label || d.tier}`;
    this.tab(S.tab || "overview");
  }
  close() { this.el.hidden = true; this.d = null; }
  tab(t) {
    S.tab = t;
    this.el.querySelectorAll(".tabs button").forEach((b) => { b.classList.toggle("active", b.dataset.tab === t); b.setAttribute("aria-selected", b.dataset.tab === t); });
    if (!this.d) return;
    const fn = { overview: this.overview, motion: this.motion, spectrum: this.spectrum, obs: this.observations, notes: this.notes }[t];
    fn.call(this);
    emit("urlchange");
  }

  // ---------------------------------------------------------------- overview
  overview() {
    const d = this.d, c = d.catalog || {}, g = c.gaia, sb = c.simbad, f = d.fit, T = TIERS[d.tier] || TIERS.insufficient;
    let h = verdict(d);
    h += `<h4>Identity</h4><dl class="kv">`;
    if (sb) h += `<dt>SIMBAD</dt><dd>${esc(sb.id.replace(/^NAME /, ""))} ${L.catalog}<br><span class="small">${esc(OTYPE[sb.otype] || sb.otype)}${sb.sp_type ? " · spectral type " + esc(sb.sp_type) : ""}</span></dd>`;
    if (g) h += `<dt>Gaia DR3</dt><dd><span class="num">${esc(g.source_id)}</span> ${L.catalog}<br><span class="small">G = ${fmt(g.gmag, 2)}${g.bp_rp != null ? ` · BP−RP ${fmt(g.bp_rp, 2)}` : ""} · ${gaiaClass(g)}${g.variable ? " · flagged variable" : ""}${g.n_within_6arcsec > 1 ? ` · <b>${g.n_within_6arcsec} Gaia sources within 6″ (blend risk)</b>` : ""}</span></dd>`;
    if (!sb && !g) h += `<dt>Catalogs</dt><dd>No Gaia DR3 (G&lt;18) or SIMBAD counterpart within 6″ ${L.unknown}</dd>`;
    h += `<dt>Position</dt><dd><span class="num">${sexa(d.ra, d.dec)}</span><br><span class="num small">${fmt(d.ra, 6)}°, ${fmt(d.dec, 6)}° (ICRS, ref. epoch)</span> ${L.measured}</dd>`;
    if (g?.parallax > 0) h += `<dt>Distance</dt><dd>${fmt(1000 / g.parallax, g.parallax > 100 ? 2 : 0)} pc (${fmt(3.2616 * 1000 / g.parallax, 1)} light-years) ${L.catalog}<br><span class="small">1/parallax from Gaia DR3 (${fmt(g.parallax, 2)} mas)</span></dd>`;
    if (g?.teff_gspphot) h += `<dt>Temperature</dt><dd>${fmt(g.teff_gspphot, 0)} K ${L.catalog}<br><span class="small">Gaia GSP-Phot model estimate</span></dd>`;
    h += `</dl><h4>What SPHEREx shows</h4><dl class="kv">`;
    if (f) {
      h += `<dt>Proper motion</dt><dd><span class="num">${fmt(f.pm_total, 2)}″/yr</span> toward PA ${fmt(f.pm_pa_deg, 0)}° · ${fmt(f.snr, 1)}σ ${L.measured}<br><span class="small">E ${pm(f.pm_east, f.pm_east_err)}, N ${pm(f.pm_north, f.pm_north_err)} ″/yr</span></dd>`;
      if (c.pm_comparison) { const p = c.pm_comparison; h += `<dt>vs Gaia</dt><dd>Gaia: E ${fmt(p.gaia_pm_east, 3)}, N ${fmt(p.gaia_pm_north, 3)} ″/yr ${L.catalog}<br><span class="small">difference ${fmt(p.z_total, 1)}σ → ${p.consistent ? "consistent" : "<b>inconsistent</b>"}</span></dd>`; }
    } else h += `<dt>Proper motion</dt><dd>Not measurable: ${esc((d.reasons || []).join("; "))} ${L.unknown}</dd>`;
    const v = d.variability;
    if (v?.var_z != null) h += `<dt>Brightness</dt><dd>${v.flag ? "<b>changes relative to the field</b>" : "stable relative to the field"} · var_z ${fmt(v.var_z, 1)} from ${v.n_pairs} matched-wavelength pairs ${L.measured}<br><span class="small">largest change ${fmt(v.max_abs_mag_change, 2)} mag · var_z ≈ 1 for constant stars; it is a test statistic, not a probability</span></dd>`;
    else h += `<dt>Brightness</dt><dd>${esc(v?.note || "not enough matched-wavelength pairs")} ${L.unknown}</dd>`;
    h += `<dt>Data quality</dt><dd>${d.n_epochs} epochs in ${d.n_visits} visits over ${fmt(d.baseline_days, 0)} days<br><span class="small">clean ${fmt(100 * d.clean_fraction, 0)}% · saturated ${fmt(100 * d.core_masked_fraction, 0)}% · blended ${fmt(100 * d.blended_fraction, 0)}%</span></dd>`;
    h += `</dl>`;
    if (d.pair) h += `<h4>Two-image test (best pair)</h4><p class="small">Displacement ${fmt(Math.hypot(d.pair.d_east_arcsec, d.pair.d_north_arcsec), 2)}″ at ${fmt(d.pair.significance, 1)}σ between the two highlighted epochs. ${d.tier === "rejected" ? "<b>The other visits do not confirm it.</b>" : ""}</p>`;
    h += `<div class="btnrow"><button class="btn" data-go="motion">See the motion →</button><button class="btn" data-go="spectrum">Spectrum →</button><button class="learn-link" data-learn="evidence">How to read these labels</button></div>`;
    this.body.innerHTML = h;
    this._wire();
  }

  // ------------------------------------------------------------------ motion
  motion() {
    const d = this.d, f = d.fit, P = d.points || [];
    let h = verdict(d);
    if (!P.length) { this.body.innerHTML = h + "<p>No measurements.</p>"; return; }
    const ref = P.find((p) => p.is_reference_epoch) || P[0], t0 = ref.mjd;
    const yr = (m) => (m - t0) / 365.25;
    const pts = (ax) => P.map((p) => ({ x: yr(p.mjd), y: p[ax + "_arcsec"], ey: p["sigma_" + ax + "_arcsec"], color: VISIT_COLORS[p.visit % VISIT_COLORS.length], opacity: p.used_in_fit === false ? 0.3 : 0.9,
      title: `${mjdToDate(p.mjd)} · λ ${fmt(p.wavelength_um, 3)} µm · ${ax} ${fmt(p[ax + "_arcsec"], 2)} ± ${fmt(p["sigma_" + ax + "_arcsec"], 2)}″${p.core_masked ? " · saturated" : ""}${p.blended ? " · blended" : ""}` }));
    const ty = P.map((p) => yr(p.mjd)), tl = [Math.min(...ty) - 0.05, Math.max(...ty) + 0.05];
    const fitLine = (ax) => f ? [{ x: tl[0], y: f["offset_" + ax] + f["pm_" + ax] * tl[0] }, { x: tl[1], y: f["offset_" + ax] + f["pm_" + ax] * tl[1] }] : [];
    const gaia = (ax) => (d.gaia_curve || []).map((g) => ({ x: yr(g.mjd), y: g[ax + "_arcsec"] }));
    const visitPts = (ax) => (f?.visit_points || []).map((v) => ({ x: yr(v.mjd), y: v[ax + "_arcsec"], ey: v["sigma_" + ax + "_arcsec"], color: "#ffffff", r: 4, shape: "square", hollow: true, title: `visit mean ± (stat ⊕ ${fmt(1.2, 1)}″ systematic)` }));
    // sky-plane path (east to the left, as on the sky)
    const path = plot({ id: "path", w: 392, h: 250, equal: true, x: { label: "East offset (″) — east is left", reverse: true }, y: { label: "North offset (″)" },
      series: [
        { type: "line", data: gaia("east").map((g, i) => ({ x: g.y, y: gaia("north")[i].y })), color: "#7aa2ff", dash: "5 4", title: "Gaia DR3 prediction (proper motion + parallax)" },
        { type: "points", data: P.map((p) => ({ x: p.east_arcsec, y: p.north_arcsec, ex: p.sigma_east_arcsec, ey: p.sigma_north_arcsec, color: VISIT_COLORS[p.visit % VISIT_COLORS.length], opacity: .85, title: `${mjdToDate(p.mjd)} · E ${fmt(p.east_arcsec, 2)} N ${fmt(p.north_arcsec, 2)}″` })) },
        ...(f && f.snr >= 5 ? [{ type: "arrow", x0: f.offset_east + f.pm_east * tl[0], y0: f.offset_north + f.pm_north * tl[0], x1: f.offset_east + f.pm_east * tl[1], y1: f.offset_north + f.pm_north * tl[1] }] : []),
      ] });
    const tplot = (ax, id) => plot({ id, w: 392, h: 170, x: { label: "Years since reference epoch" }, y: { label: `${ax === "east" ? "East" : "North"} offset (″)` },
      series: [{ type: "line", data: gaia(ax), color: "#7aa2ff", dash: "5 4" }, { type: "line", data: fitLine(ax), color: "#ffb547", width: 1.6 },
        { type: "points", data: pts(ax) }, { type: "points", data: visitPts(ax) }] });
    h += `<div class="legend-row">${[...new Set(P.map((p) => p.visit))].map((v) => `<span><i style="background:${VISIT_COLORS[v % VISIT_COLORS.length]}"></i>visit ${v + 1} (${mjdToDate(Math.min(...P.filter((p) => p.visit === v).map((p) => p.mjd))).slice(0, 7)})</span>`).join("")}<span><i style="background:#ffb547;border-radius:0;height:2px"></i>NADIR fit</span>${d.gaia_curve ? '<span><i style="background:#7aa2ff;border-radius:0;height:2px"></i>Gaia prediction</span>' : ""}</div>`;
    h += `<h4>Path on the sky ${L.measured}</h4>${path}`;
    h += `<h4>Position versus time</h4>${tplot("east", "te")}${tplot("north", "tn")}`;
    if (f) {
      h += `<h4>Numbers</h4><dl class="kv"><dt>NADIR proper motion</dt><dd class="num">μE ${pm(f.pm_east, f.pm_east_err)} · μN ${pm(f.pm_north, f.pm_north_err)} ″/yr ${L.measured}</dd>`;
      h += `<dt>Fit quality</dt><dd class="num">χ²ᵣ E ${fmt(f.chi2_red_east, 2)} · N ${fmt(f.chi2_red_north, 2)} · ${f.n_used} ${f.visit_points ? "visits" : "epochs"} used</dd>`;
      h += `<dt>Model</dt><dd class="small">${esc(f.model)}</dd>`;
      const c = d.catalog?.pm_comparison;
      if (c) h += `<dt>Gaia DR3</dt><dd class="num">μE ${fmt(c.gaia_pm_east, 3)} · μN ${fmt(c.gaia_pm_north, 3)} ″/yr ${L.catalog}<br>difference ${fmt(c.z_total, 1)}σ (${c.consistent ? "consistent" : "inconsistent"})</dd>`;
      h += `</dl>`;
      if (d.core_masked_fraction > 0.5) h += `<div class="callout warn"><b>Why the error bars are large.</b> This star saturates SPHEREx's detector, so its position comes from the PSF wings. Positions within one visit agree to ~0.5″, but whole visits can be offset by 1–2″ (the detector is rotated between visits). NADIR therefore fits visit averages with a 1.2″ systematic error each. That floor was calibrated on Barnard's Star using Gaia, so Barnard's agreement with Gaia is a consistency check, not independent proof. <button class="learn-link" data-learn="uncertainty">More</button></div>`;
      if (Math.abs(f.pm_total) > 1) h += `<p class="small">Parallax (yearly wobble from Earth's orbit) is not fitted; it is included in the Gaia prediction curve. <button class="learn-link" data-learn="parallax">Why?</button></p>`;
    }
    if (d.history?.length) {
      const hist = d.history, cur = { east_arcsec: 0, north_arcsec: 0 };
      h += `<h4>A quarter-century of motion ${L.catalog}</h4><p class="small">Where earlier surveys recorded this object, relative to its SPHEREx position. Catalog positions, matched by propagating back with the Gaia proper motion.</p>`;
      h += plot({ id: "hist", w: 392, h: 230, equal: true, x: { label: "East offset (″)", reverse: true }, y: { label: "North offset (″)" }, series: [
        { type: "line", data: [...hist, cur].map((p) => ({ x: p.east_arcsec, y: p.north_arcsec })), color: "#7a849c", dash: "3 3" },
        { type: "points", data: hist.map((p) => ({ x: p.east_arcsec, y: p.north_arcsec, color: "#7aa2ff", r: 4, title: `${p.catalog} ${fmt(p.year, 1)}` })) },
        { type: "points", data: [{ x: 0, y: 0, color: "#ffb547", r: 5, title: "SPHEREx reference epoch" }] },
        ...hist.map((p) => ({ type: "text", x: p.east_arcsec, y: p.north_arcsec, text: `  ${p.catalog} ${p.year.toFixed(0)}`, color: "#aab3c8" })),
        { type: "text", x: 0, y: 0, text: "  SPHEREx 2025", color: "#ffd28a" }] });
    }
    h += `<p class="btnrow"><button class="learn-link" data-learn="proper-motion">What is proper motion?</button><button class="learn-link" data-learn="registration">How are images aligned?</button></p>`;
    this.body.innerHTML = h; this._wire();
  }

  // ---------------------------------------------------------------- spectrum
  async spectrum() {
    const d = this.d, sid = d.id;
    let h = `<div class="callout"><b>How SPHEREx sees colour.</b> Each exposure records this spot of sky at one wavelength (the filter varies across the detector). Measuring the star in many exposures across six detectors builds a 0.75–5 µm spectrum, point by point. <button class="learn-link" data-learn="spectroscopy">Learn more</button></div>`;
    if (d.core_masked_fraction > 0.5) {
      this.body.innerHTML = h + `<div class="callout bad"><b>No reliable spectrum for this object.</b> Its core saturates the detector, so the flux cannot be measured from the pixels; NADIR shows nothing rather than a biased spectrum. Try an unsaturated neighbour, or Teegarden's Star / van Maanen's Star from the presets. ${L.unknown}</div>`;
      this._wire(); return;
    }
    if (!S.field.spectral_ready) {
      this.body.innerHTML = h + `<p>Spectra need images from all six detectors. For this field that means about ${Object.values(S.field.bands_available).reduce((a, b) => a + Math.min(b, 20), 0) - (S.field.bands_available[S.field.band] || 0)} more cutouts (≈4 MB each from IRSA; cached afterwards).</p><button class="btn primary" id="spec-load">Download spectral images</button><div id="spec-prog" class="small"></div>`;
      document.getElementById("spec-load").onclick = () => this.loadSpectral();
      this._wire(); return;
    }
    this.body.innerHTML = h + `<p class="small">Measuring forced photometry in every image…</p>`;
    try {
      const sp = this.spectra.get(`${S.fid}:${sid}`) || await getJSON(`/api/fields/${S.fid}/sources/${sid}/spectrum`);
      this.spectra.set(`${S.fid}:${sid}`, sp);
      if (this.d?.id !== sid || S.tab !== "spectrum") return;
      this.body.innerHTML = h + spectrumHTML(sp, d); this._wire();
    } catch (e) { this.body.innerHTML = h + `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  async loadSpectral() {
    const btn = document.getElementById("spec-load"); btn.disabled = true;
    const prog = document.getElementById("spec-prog");
    try {
      const r = await postJSON(`/api/fields/${S.fid}/spectral`);
      if (r.job) await pollJob(r.job, { onUpdate: (j) => { prog.innerHTML = `${esc(j.message || j.stage)} · ${j.done}/${j.total || "?"}${j.detail?.cached != null ? ` (${j.detail.cached} from cache)` : ""}`; } });
      S.field.spectral_ready = true; this.spectrum();
    } catch (e) { prog.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; btn.disabled = false; }
  }

  // ------------------------------------------------------------ observations
  observations() {
    const d = this.d, P = d.points || [];
    const byIdx = new Map(S.field.epochs.map((e) => [e.index, e]));
    let h = `<p class="small">Every epoch in which this source was measured. Click a row to compare it (as B) with the reference epoch A. Provenance links show the exact archive request.</p>`;
    h += `<table class="data"><thead><tr><th>UTC</th><th>λ µm</th><th>E ″</th><th>N ″</th><th>flags</th><th></th></tr></thead><tbody>`;
    for (const p of P) {
      const e = byIdx.get(p.obs_index) || {};
      const flags = [p.core_masked && "sat", p.blended && "blend", p.artifact && "artifact", p.used_in_fit === false && "clipped", p.is_reference_epoch && "ref"].filter(Boolean).join(" ");
      h += `<tr class="clickable" data-obs="${p.obs_index}"><td class="num">${esc(dateTime(e.date))}</td><td class="num" style="color:${lambdaColor(p.wavelength_um)}">${fmt(p.wavelength_um, 3)}</td><td class="num">${pm(p.east_arcsec, p.sigma_east_arcsec)}</td><td class="num">${pm(p.north_arcsec, p.sigma_north_arcsec)}</td><td class="small">${flags}</td><td><a href="/api/fields/${S.fid}/epochs/${p.obs_index}/provenance" target="_blank" rel="noopener" title="Provenance of ${esc(e.obs_id)}">prov</a></td></tr>`;
    }
    h += `</tbody></table>`;
    const unused = S.field.epochs.filter((e) => !e.usable);
    if (unused.length) h += `<p class="small">${unused.length} archive image(s) not used: ${unused.map((e) => `${esc(e.obs_id)} (${esc(e.unusable_reason)})`).join("; ")}</p>`;
    this.body.innerHTML = h;
    this.body.querySelectorAll("tr[data-obs]").forEach((tr) => (tr.onclick = (ev) => { if (ev.target.tagName !== "A") emit("setB", +tr.dataset.obs); }));
  }

  // ------------------------------------------------------------------- notes
  notes() {
    const d = this.d, key = `nadir:notes:${d.ra.toFixed(4)},${d.dec.toFixed(4)}`;
    let saved = ""; try { saved = localStorage.getItem(key) || ""; } catch (_) {}
    this.body.innerHTML = `<p class="small">Your hypotheses and observations. Stored only in this browser (localStorage), never sent anywhere.</p>
      <textarea id="note" placeholder="e.g. Moves ~10″/yr north; consistent with Gaia. Check flux in the 2.3 µm CO band…">${esc(saved)}</textarea>
      <div class="btnrow"><button class="btn" id="note-share">Copy link to this investigation</button><button class="btn" id="note-export">Download report (.md)</button><span class="small" id="note-status"></span></div>`;
    const ta = document.getElementById("note"), st = document.getElementById("note-status");
    ta.oninput = () => { try { localStorage.setItem(key, ta.value); st.textContent = "saved locally"; } catch (_) { st.textContent = "browser storage unavailable — notes not saved"; } };
    document.getElementById("note-share").onclick = async () => { try { await navigator.clipboard.writeText(location.href); st.textContent = "link copied"; } catch (_) { st.textContent = location.href; } };
    document.getElementById("note-export").onclick = () => {
      const blob = new Blob([report(d, ta.value)], { type: "text/markdown" }), a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `nadir-${displayName(d).replace(/[^a-z0-9]+/gi, "-")}.md`; a.click(); URL.revokeObjectURL(a.href);
    };
  }

  _wire() {
    this.body.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => this.tab(b.dataset.go)));
    this.body.querySelectorAll("[data-learn]").forEach((b) => (b.onclick = () => emit("learn", b.dataset.learn)));
  }
}

export function displayName(d) {
  const sb = d.catalog?.simbad;
  if (sb) return sb.id.replace(/^NAME /, "").replace(/\s+/g, " ");
  if (d.catalog?.gaia) return `Gaia DR3 ${d.catalog.gaia.source_id}`;
  return `NADIR source #${d.id}`;
}
function gaiaClass(g) {
  const c = [["star", g.p_star], ["galaxy", g.p_galaxy], ["quasar", g.p_quasar]].sort((a, b) => (b[1] || 0) - (a[1] || 0))[0];
  return c[1] != null ? `DSC: ${c[0]} (p=${fmt(c[1], 2)})` : "DSC: unclassified";
}

function verdict(d) {
  const T = TIERS[d.tier] || TIERS.insufficient, f = d.fit;
  const cls = { significant: "ok", systematics_limited: "ok", candidate: "warn", rejected: "bad", stationary: "", insufficient: "" }[d.tier] ?? "";
  let txt = "";
  if (d.tier === "significant") txt = `This source moves consistently across ${d.n_visits} independent visits (${fmt(f.snr, 1)}σ), measured on clean pixels.`;
  else if (d.tier === "systematics_limited") txt = `The motion is real — it is reproduced across ${d.n_visits} visits at ${fmt(f.snr, 1)}σ — but the star saturates the detector, so the accuracy is limited by a systematic floor.`;
  else if (d.tier === "candidate") txt = `Formally significant, but it fails a consistency test, so it is <b>not</b> a detection: ${esc(d.reasons.join("; "))}.`;
  else if (d.tier === "rejected") txt = `${esc(d.reasons[0] || "")}. A blend, a pixel-phase effect or a wavelength-dependent centroid can fake motion between two images; repeat visits expose it.`;
  else if (d.tier === "stationary") txt = `No motion above 5σ (${f ? fmt(f.snr, 1) + "σ" : "n/a"}). Stars like this anchor the reference frame.`;
  else txt = esc((d.reasons || []).join("; ") || "Not enough data.");
  return `<div class="callout ${cls}"><span class="tier ${d.tier}">${T.label}</span><p style="margin:6px 0 0">${txt}</p></div>`;
}

function spectrumHTML(sp, d) {
  const P = sp.points || [];
  if (!P.length) return `<div class="callout bad">No usable photometry: ${sp.rejected.length} measurements rejected (${esc([...new Set(sp.rejected.map((r) => r.reason))].join("; "))}). ${L.unknown}</div>`;
  const visit = (m) => (m < 61000 ? 0 : m < 61200 ? 1 : 2);
  const good = P.filter((p) => p.snr >= 3);
  const ys = good.map((p) => p.flux_mjy).concat((sp.catalog_points || []).map((c) => c.flux_mjy));
  const lo = Math.max(Math.min(...ys) * 0.7, 1e-3), hi = Math.max(...ys) * 1.3;
  const series = [];
  if (sp.blackbody) series.push({ type: "line", data: sp.blackbody.curve.wavelength_um.map((w, i) => ({ x: w, y: sp.blackbody.curve.flux_mjy[i] })), color: "#a98bff", dash: "6 4", title: sp.blackbody.label });
  for (const [i, f] of (S.showFeatures ? sp.reference_features : []).entries()) series.push({ type: "vline", x: f.wavelength_um, label: f.label, color: "#4fd1b5", row: i % 3 });
  series.push({ type: "points", data: P.map((p) => ({ x: p.wavelength_um, y: p.flux_mjy, ey: p.flux_err_mjy, color: VISIT_COLORS[visit(p.mjd)], opacity: p.snr < 3 ? 0.3 : 0.9, title: `${p.band} · ${fmt(p.wavelength_um, 3)} µm · ${fmt(p.flux_mjy, 2)} ± ${fmt(p.flux_err_mjy, 2)} mJy · ${mjdToDate(p.mjd)}` })) });
  series.push({ type: "points", shape: "square", r: 4.5, hollow: true, data: (sp.catalog_points || []).map((c) => ({ x: c.wavelength_um, y: c.flux_mjy, ey: c.flux_err_mjy, color: "#ffffff", title: `${c.catalog} ${c.band}: ${fmt(c.mag, 2)} mag = ${fmt(c.flux_mjy, 2)} mJy` })) });
  let h = `<div class="legend-row"><span><i style="background:#7aa2ff"></i>2025 visit</span><span><i style="background:#ffb547"></i>Mar 2026</span><span><i style="background:#4fd1b5"></i>Aug–Sep 2026</span><span><i style="border:1.5px solid #fff;background:none;border-radius:0"></i>2MASS / AllWISE</span>${sp.blackbody ? '<span><i style="background:#a98bff;border-radius:0;height:2px"></i>blackbody fit</span>' : ""}</div>`;
  h += plot({ id: "spec", w: 392, h: 270, x: { label: "Wavelength (µm)", domain: [0.7, 5.1] }, y: { label: "Flux density (mJy)", domain: [0, hi] }, series });
  h += `<label class="small"><input type="checkbox" id="feat" ${S.showFeatures ? "checked" : ""}> Show where common infrared features occur ${L.illustrative}</label>`;
  h += `<h4>What this spectrum supports</h4><dl class="kv">`;
  h += `<dt>Measurements</dt><dd>${P.length} fluxes in ${sp.bands.join(", ")} ${L.measured}<br><span class="small">forced ePSF photometry at the ${sp.pm_used ? "proper-motion-propagated (" + esc(sp.pm_used) + ")" : "fixed"} position; ${sp.rejected.length} rejected (${sp.n_saturated} saturated)</span></dd>`;
  if (sp.catalog_comparison?.length) h += `<dt>Check vs catalogs</dt><dd>${sp.catalog_comparison.map((c) => `${c.band}: ×${fmt(c.ratio, 2)}`).join(" · ")} ${L.catalog}<br><span class="small">SPHEREx ÷ 2MASS/AllWISE near each band. Agreement within ~20% is expected (broad filters vs R≈40 sampling, different epochs).</span></dd>`;
  if (sp.blackbody) h += `<dt>Colour temperature</dt><dd>${fmt(sp.blackbody.T_color_K, 0)} K (range ${fmt(sp.blackbody.T_lo_K, 0)}–${sp.blackbody.unconstrained_high ? "unbounded" : fmt(sp.blackbody.T_hi_K, 0)} K) ${L.inferred}<br><span class="small">single-blackbody model, χ²ᵣ ${fmt(sp.blackbody.chi2_red, 1)}. Real stars are not blackbodies (molecular bands), so this is not an effective temperature.${sp.gaia_teff ? ` Gaia's model estimate: ${fmt(sp.gaia_teff, 0)} K ${L.catalog}.` : ""}</span></dd>`;
  if (sp.simbad_sp_type) h += `<dt>Spectral type</dt><dd>${esc(sp.simbad_sp_type)} ${L.catalog}</dd>`;
  h += `<dt>Composition</dt><dd>Not determined ${L.unknown}<br><span class="small">Feature markers show where molecules absorb or emit in general; NADIR does not claim to detect them in this source.</span></dd></dl>`;
  return h;
}

function report(d, notes) {
  const f = d.fit, c = d.catalog || {};
  return `# NADIR investigation: ${displayName(d)}\n\nGenerated ${new Date().toISOString()} · ${location.href}\n\n` +
    `- Position (ICRS, SPHEREx reference epoch): ${d.ra.toFixed(6)}, ${d.dec.toFixed(6)}\n- Evidence tier: ${TIERS[d.tier]?.label}\n` +
    (f ? `- Proper motion (measured): E ${pm(f.pm_east, f.pm_east_err)}, N ${pm(f.pm_north, f.pm_north_err)} arcsec/yr (${fmt(f.snr, 1)} sigma; ${f.model})\n` : "") +
    (c.pm_comparison ? `- Gaia DR3 proper motion (catalog): E ${fmt(c.pm_comparison.gaia_pm_east, 3)}, N ${fmt(c.pm_comparison.gaia_pm_north, 3)} arcsec/yr; difference ${fmt(c.pm_comparison.z_total, 1)} sigma\n` : "") +
    `- Epochs: ${d.n_epochs} in ${d.n_visits} visits; saturated ${fmt(100 * d.core_masked_fraction, 0)}%\n- Data: NASA SPHEREx DR1/QR3 via IRSA; field ${S.field.ra}, ${S.field.dec}, band ${S.field.band}\n\n## Notes\n\n${notes || "(none)"}\n`;
}

document.addEventListener("change", (e) => { if (e.target.id === "feat") { S.showFeatures = e.target.checked; emit("refreshTab"); } });
