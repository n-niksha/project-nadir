// Object Investigation — the dossier.  Every quantity carries an evidence label:
// Measured (NADIR, from SPHEREx pixels), Catalog (external survey),
// Inferred (explicit model), Illustrative (education), Unknown.
import { S, TIERS, emit } from "./state.js";
import { getJSON, postJSON, pollJob } from "./api.js";
import { esc, fmt, pm, sexa, dateTime, mjdToDate, VISIT_COLORS, OTYPE, lambdaColor, displayName } from "./fmt.js";
import { plot } from "./plots.js";
import { visitGroups } from "./playback.js";
import { C } from "./tokens.js";

export { displayName };

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
    document.querySelectorAll("[data-explain]").forEach(button => { button.onclick = () => { S.explanationLevel = button.dataset.explain; this.explain(); }; });
  }
  open(d) {
    const fresh = this.d?.id !== d.id;
    this.d = d; this.el.hidden = false;
    if (fresh) this.el.scrollTop = 0;
    document.getElementById("insp-title").textContent = displayName(d);
    document.getElementById("insp-kicker").textContent = `NADIR source #${d.id}${d.catalog?.simbad?.sp_type ? ` · spectral type ${d.catalog.simbad.sp_type}` : ""}`;
    const classification = d.catalog?.simbad ? (OTYPE[d.catalog.simbad.otype] || d.catalog.simbad.otype) : d.catalog?.gaia ? gaiaClass(d.catalog.gaia) : "Classification unknown";
    document.getElementById("insp-class").innerHTML = `<span class="tier ${d.tier}">${esc(TIERS[d.tier]?.label || d.tier)}</span><span>${esc(classification)}</span>${d.catalog ? L.catalog : L.unknown}`;
    document.getElementById("investigation-summary").innerHTML = hero(d);
    this.explain();
    this.tab(S.tab || "overview");
  }
  close() { this.el.hidden = true; this.d = null; }
  explain() {
    const sections = this.d?.explanation?.[S.explanationLevel] || [];
    document.querySelectorAll("[data-explain]").forEach(button => button.setAttribute("aria-pressed", button.dataset.explain === S.explanationLevel));
    document.getElementById("explanation-body").innerHTML = sections.map(section => `<h4>${esc(section.title)} ${L[section.evidence] || L.unknown}</h4><p>${esc(section.text)}</p>`).join("") || '<p>No structured interpretation is available.</p>';
  }
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
    const d = this.d, c = d.catalog || {}, g = c.gaia, sb = c.simbad, f = d.fit;
    let h = verdict(d);
    h += `<h4>What SPHEREx measured</h4><dl class="kv">`;
    if (f) {
      h += `<dt>Proper motion</dt><dd><span class="num">${fmt(f.pm_total, 2)}″/yr</span> toward PA ${fmt(f.pm_pa_deg, 0)}° ${L.measured}<span class="small num">μE ${pm(f.pm_east, f.pm_east_err)} · μN ${pm(f.pm_north, f.pm_north_err)} ″/yr · ${fmt(f.snr, 1)}σ</span></dd>`;
      if (c.pm_comparison) { const p = c.pm_comparison; h += `<dt>vs Gaia DR3</dt><dd>${p.consistent ? "Consistent" : "<b>Inconsistent</b>"} · difference ${fmt(p.z_total, 1)}σ ${L.catalog}<span class="small num">Gaia μE ${fmt(p.gaia_pm_east, 3)} · μN ${fmt(p.gaia_pm_north, 3)} ″/yr</span></dd>`; }
    } else h += `<dt>Proper motion</dt><dd>Not measurable ${L.unknown}<span class="small">${esc((d.reasons || []).join("; "))}</span></dd>`;
    const v = d.variability;
    if (v?.var_z != null) h += `<dt>Brightness</dt><dd>${v.flag ? "<b>Changes relative to the field</b>" : "Stable relative to the field"} ${L.measured}<span class="small">var_z ${fmt(v.var_z, 1)} from ${v.n_pairs} matched-wavelength pairs · largest change ${fmt(v.max_abs_mag_change, 2)} mag. var_z ≈ 1 for constant stars; a test statistic, not a probability.</span></dd>`;
    else h += `<dt>Brightness</dt><dd>${esc(v?.note || "Not enough matched-wavelength pairs")} ${L.unknown}</dd>`;
    h += `<dt>Data quality</dt><dd>${d.n_epochs} epochs · ${d.n_visits} visits · ${fmt(d.baseline_days, 0)} days<span class="small">clean ${fmt(100 * d.clean_fraction, 0)}% · saturated ${fmt(100 * d.core_masked_fraction, 0)}% · blended ${fmt(100 * d.blended_fraction, 0)}%</span></dd></dl>`;
    h += `<h4>Identity</h4><dl class="kv">`;
    if (sb) h += `<dt>SIMBAD</dt><dd>${esc(sb.id.replace(/^NAME /, ""))} ${L.catalog}<span class="small">${esc(OTYPE[sb.otype] || sb.otype)}${sb.sp_type ? " · spectral type " + esc(sb.sp_type) : ""}</span></dd>`;
    if (g) h += `<dt>Gaia DR3</dt><dd><span class="num">${esc(g.source_id)}</span> ${L.catalog}<span class="small">G = ${fmt(g.gmag, 2)}${g.bp_rp != null ? ` · BP−RP ${fmt(g.bp_rp, 2)}` : ""} · ${gaiaClass(g)}${g.variable ? " · flagged variable" : ""}${g.n_within_6arcsec > 1 ? ` · <b>${g.n_within_6arcsec} Gaia sources within 6″ (blend risk)</b>` : ""}</span></dd>`;
    if (!sb && !g) h += `<dt>Catalogs</dt><dd>No Gaia DR3 (G&lt;18) or SIMBAD counterpart within 6″ ${L.unknown}</dd>`;
    h += `<dt>Position</dt><dd><span class="num">${sexa(d.ra, d.dec)}</span> ${L.measured}<span class="small num">${fmt(d.ra, 6)}°, ${fmt(d.dec, 6)}° (ICRS, ref. epoch)</span></dd>`;
    if (g?.parallax > 0) h += `<dt>Distance</dt><dd>${fmt(1000 / g.parallax, g.parallax > 100 ? 2 : 0)} pc · ${fmt(3.2616 * 1000 / g.parallax, 1)} light-years ${L.catalog}<span class="small">1/parallax from Gaia DR3 (${fmt(g.parallax, 2)} mas)</span></dd>`;
    if (g?.teff_gspphot) h += `<dt>Temperature</dt><dd>${fmt(g.teff_gspphot, 0)} K ${L.catalog}<span class="small">Gaia GSP-Phot model estimate</span></dd>`;
    h += `</dl>`;
    if (d.pair) h += `<h4>Two-image test</h4><p class="small">Displacement ${fmt(Math.hypot(d.pair.d_east_arcsec, d.pair.d_north_arcsec), 2)}″ at ${fmt(d.pair.significance, 1)}σ between the best-matched pair of epochs. ${d.tier === "rejected" ? "<b>The other visits do not confirm it.</b>" : ""}</p>`;
    h += `<details class="evidence"><summary>Evidence behind this interpretation</summary><p>${esc(d.explanation?.summary || "Insufficient evidence")}</p><ul>${(d.reasons || []).map(reason => `<li>${esc(reason)}</li>`).join("")}</ul><p>${d.n_epochs} source positions across ${d.n_visits} visits; ${fmt(d.baseline_days, 1)} days. Masked cores: ${fmt(d.core_masked_fraction * 100, 0)}%; blended: ${fmt(d.blended_fraction * 100, 0)}%.</p><p>Reference stars define relative alignment. Gaia is a comparison catalog. The masked-core systematic floor was calibrated on Barnard; agreement there is not independent validation.</p><button class="btn sm" data-go="obs">Inspect supporting observations</button></details>`;
    h += `<div class="btnrow"><button class="btn" data-go="motion">See the motion</button><button class="btn" data-go="spectrum">Spectrum</button><button class="learn-link" data-learn="evidence">How to read these labels</button></div>`;
    this.body.innerHTML = h;
    this._wire();
  }

  // ------------------------------------------------------------------ motion
  motion() {
    const d = this.d, f = d.fit, P = d.points || [];
    let h = "";
    if (!P.length) { this.body.innerHTML = h + "<p>No measurements.</p>"; return; }
    const ref = P.find((p) => p.is_reference_epoch) || P[0], t0 = ref.mjd;
    const yr = (m) => (m - t0) / 365.25;
    const vc = (v) => VISIT_COLORS[v % VISIT_COLORS.length];
    const pts = (ax) => P.map((p) => ({ x: yr(p.mjd), y: p[ax + "_arcsec"], ey: p["sigma_" + ax + "_arcsec"], color: vc(p.visit), opacity: p.used_in_fit === false ? 0.3 : 0.85, r: 2.6,
      title: `${mjdToDate(p.mjd)} · λ ${fmt(p.wavelength_um, 3)} µm · ${ax === "east" ? "E" : "N"} ${fmt(p[ax + "_arcsec"], 2)} ± ${fmt(p["sigma_" + ax + "_arcsec"], 2)}″${p.core_masked ? " · saturated" : ""}${p.blended ? " · blended" : ""}${p.used_in_fit === false ? " · clipped" : ""}` }));
    const ty = P.map((p) => yr(p.mjd)), tl = [Math.min(...ty) - 0.05, Math.max(...ty) + 0.05];
    const fitLine = (ax) => f ? [{ x: tl[0], y: f["offset_" + ax] + f["pm_" + ax] * tl[0] }, { x: tl[1], y: f["offset_" + ax] + f["pm_" + ax] * tl[1] }] : [];
    const gaia = (ax) => (d.gaia_curve || []).map((g) => ({ x: yr(g.mjd), y: g[ax + "_arcsec"] }));
    const visitPts = (ax) => (f?.visit_points || []).map((v) => ({ x: yr(v.mjd), y: v[ax + "_arcsec"], ey: v["sigma_" + ax + "_arcsec"], color: C("text-1"), r: 4.5, shape: "square", hollow: true,
      title: `Visit ${v.visit + 1} mean (${v.n} epochs) · ${ax === "east" ? "E" : "N"} ${fmt(v[ax + "_arcsec"], 2)} ± ${fmt(v["sigma_" + ax + "_arcsec"], 2)}″ (stat ⊕ systematic)` }));
    const visits = [...new Set(P.map((p) => p.visit))];
    h += `<div class="legend-row">${visits.map((v) => `<span><i style="background:${vc(v)}"></i>Visit ${v + 1} · ${mjdToDate(Math.min(...P.filter((p) => p.visit === v).map((p) => p.mjd))).slice(0, 7)}</span>`).join("")}</div>`;
    h += `<div class="legend-row"><span><i class="ln" style="background:${C("sun")}"></i>NADIR fit (measured)</span>${d.gaia_curve ? `<span><i class="ln dash"></i>Gaia DR3 prediction (catalog)</span>` : ""}${f?.visit_points ? `<span><i style="border:1.5px solid ${C("text-1")};border-radius:2px;background:none"></i>Visit mean</span>` : ""}</div>`;
    // sky-plane path (east to the left, as on the sky)
    h += `<h4>Path on the sky ${L.measured}</h4>`;
    h += plot({ id: "path", w: 380, h: 300, equal: true, zero: "xy", x: { label: "East offset (″) · east is left", reverse: true }, y: { label: "North offset (″)" },
      hint: "Each dot is one exposure's measured position relative to the reference epoch. Hover for details.",
      series: [
        { type: "line", data: gaia("east").map((g, i) => ({ x: g.y, y: gaia("north")[i].y })), color: C("sky"), dash: "5 4", title: "Gaia DR3 prediction (proper motion + parallax)" },
        { type: "points", data: P.map((p) => ({ x: p.east_arcsec, y: p.north_arcsec, ex: p.sigma_east_arcsec, ey: p.sigma_north_arcsec, color: vc(p.visit), opacity: .8, r: 2.6, title: `${mjdToDate(p.mjd)} · E ${fmt(p.east_arcsec, 2)}″, N ${fmt(p.north_arcsec, 2)}″ (±${fmt(p.sigma_east_arcsec, 2)}, ±${fmt(p.sigma_north_arcsec, 2)})` })) },
        ...(f && f.snr >= 5 ? [{ type: "arrow", x0: f.offset_east + f.pm_east * tl[0], y0: f.offset_north + f.pm_north * tl[0], x1: f.offset_east + f.pm_east * tl[1], y1: f.offset_north + f.pm_north * tl[1] }] : []),
      ] });
    const tplot = (ax, id) => plot({ id, w: 380, h: 190, x: { label: "Years since reference epoch" }, y: { label: `${ax === "east" ? "East" : "North"} offset (″)` },
      series: [{ type: "line", data: gaia(ax), color: C("sky"), dash: "5 4" }, { type: "line", data: fitLine(ax), color: C("sun"), width: 2 },
        { type: "points", data: pts(ax) }, { type: "points", data: visitPts(ax) }] });
    h += `<h4>Position versus time ${L.measured}</h4>${tplot("north", "tn")}${tplot("east", "te")}`;
    if (f) {
      h += `<h4>Numbers</h4><dl class="kv"><dt>NADIR motion</dt><dd class="num">μE ${pm(f.pm_east, f.pm_east_err)}<br>μN ${pm(f.pm_north, f.pm_north_err)} ″/yr ${L.measured}</dd>`;
      h += `<dt>Fit quality</dt><dd class="num">χ²ᵣ E ${fmt(f.chi2_red_east, 2)} · N ${fmt(f.chi2_red_north, 2)}<span class="small">${f.n_used} ${f.visit_points ? "visits" : "epochs"} used</span></dd>`;
      h += `<dt>Model</dt><dd class="small" style="padding-top:9px">${esc(f.model)}</dd>`;
      const c = d.catalog?.pm_comparison;
      if (c) h += `<dt>Gaia DR3</dt><dd class="num">μE ${fmt(c.gaia_pm_east, 3)} · μN ${fmt(c.gaia_pm_north, 3)} ″/yr ${L.catalog}<span class="small">difference ${fmt(c.z_total, 1)}σ (${c.consistent ? "consistent" : "inconsistent"})</span></dd>`;
      h += `</dl>`;
      h += `<div class="callout ok"><b>Reading the uncertainty.</b> Error bars are ±1σ. The motion is ${fmt(f.snr, 1)} times larger than its combined uncertainty (${fmt(f.snr, 1)}σ). ${f.snr >= 5 ? "Above NADIR's 5σ threshold." : "Below NADIR's 5σ threshold, so it is not claimed as motion."}</div>`;
      if (d.core_masked_fraction > 0.5) h += `<div class="callout warn"><b>Why the error bars are large.</b> This star saturates SPHEREx's detector, so its position comes from the PSF wings. Positions within one visit agree to ~0.5″, but whole visits can be offset by 1–2″ (the detector is rotated between visits). NADIR therefore fits visit averages with a 1.2″ systematic error each. That floor was calibrated on Barnard's Star using Gaia, so Barnard's agreement with Gaia is a consistency check, not independent proof. <button class="learn-link" data-learn="uncertainty">More</button></div>`;
      if (Math.abs(f.pm_total) > 1) h += `<p class="small">Parallax (yearly wobble from Earth's orbit) is not fitted; it is included in the Gaia prediction curve. <button class="learn-link" data-learn="parallax">Why?</button></p>`;
    }
    if (d.history?.length) {
      const hist = d.history, cur = { east_arcsec: 0, north_arcsec: 0 };
      h += `<h4>A quarter-century of motion ${L.catalog}</h4><p class="small">Where earlier surveys recorded this object, relative to its SPHEREx position. Catalog positions, matched by propagating back with the Gaia proper motion.</p>`;
      h += plot({ id: "hist", w: 380, h: 260, equal: true, x: { label: "East offset (″)", reverse: true }, y: { label: "North offset (″)" }, series: [
        { type: "line", data: [...hist, cur].map((p) => ({ x: p.east_arcsec, y: p.north_arcsec })), color: C("text-3"), dash: "3 3" },
        { type: "points", data: hist.map((p) => ({ x: p.east_arcsec, y: p.north_arcsec, color: C("sky"), r: 4, title: `${p.catalog} ${fmt(p.year, 1)} · E ${fmt(p.east_arcsec, 1)}″, N ${fmt(p.north_arcsec, 1)}″` })) },
        { type: "points", data: [{ x: 0, y: 0, color: C("sun"), r: 5, title: "SPHEREx reference epoch" }] },
        ...hist.map((p) => ({ type: "text", x: p.east_arcsec, y: p.north_arcsec, text: `  ${p.catalog} ${p.year.toFixed(0)}`, color: C("text-2") })),
        { type: "text", x: 0, y: 0, text: "  SPHEREx 2025", color: C("sun-2") }] });
    }
    h += `<p class="btnrow"><button class="learn-link" data-learn="proper-motion">What is proper motion?</button><button class="learn-link" data-learn="registration">How are images aligned?</button></p>`;
    this.body.innerHTML = h; this._wire();
  }

  // ---------------------------------------------------------------- spectrum
  async spectrum() {
    const d = this.d, sid = d.id, fid = S.fid;
    let h = `<div class="callout"><b>How SPHEREx sees colour.</b> Each exposure records this spot of sky at one wavelength (the filter varies across the detector). Measuring the star in many exposures across six detectors builds a 0.75–5 µm spectrum, point by point. <button class="learn-link" data-learn="spectroscopy">Learn more</button></div>`;
    if (d.core_masked_fraction > 0.5) {
      this.body.innerHTML = h + `<div class="callout bad"><b>No reliable spectrum for this object.</b> Its core is masked in most observations, so reliable flux cannot be measured from these pixels; saturation or other quality masks can cause this. NADIR shows nothing rather than a biased spectrum. Try an unsaturated neighbour or van Maanen's Star from the target list. ${L.unknown}</div>`;
      this._wire(); return;
    }
    if (!S.field.spectral_ready) {
      const additional = Object.entries(S.field.bands_available).filter(([band]) => band !== S.field.band).reduce((count, [, total]) => count + Math.min(total, 20), 0);
      this.body.innerHTML = h + `<p>Spectra use all six detectors: up to ${additional} additional cutouts. Start with observations already cached on this machine — nothing is downloaded. Missing images can be requested afterwards. Measurements combine different dates.</p><button class="btn primary" id="spec-load">Load cached spectral observations</button><div id="spec-prog" class="small" style="margin-top:8px"></div>`;
      document.getElementById("spec-load").onclick = () => this.loadSpectral();
      this._wire(); return;
    }
    this.body.innerHTML = h + `<p class="small">Measuring forced photometry in every image…</p>`;
    try {
      const sp = this.spectra.get(`${fid}:${sid}`) || await getJSON(`/api/fields/${fid}/sources/${sid}/spectrum`);
      this.spectra.set(`${fid}:${sid}`, sp);
      if (S.fid !== fid || this.d?.id !== sid || S.tab !== "spectrum") return;
      this.body.innerHTML = h + spectrumHTML(sp, d); this._wire();
    } catch (e) { if (S.fid === fid && this.d?.id === sid && S.tab === "spectrum") this.body.innerHTML = h + `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  async loadSpectral(cachedOnly = true) {
    const fid = S.fid, sid = this.d.id;
    const btn = document.getElementById("spec-load"); btn.disabled = true;
    const prog = document.getElementById("spec-prog");
    try {
      const r = await postJSON(`/api/fields/${fid}/spectral?cached_only=${cachedOnly}`);
      if (r.job) await pollJob(r.job, { onUpdate: (j) => { prog.innerHTML = `${esc(j.message || j.stage)} · ${j.done}/${j.total || "?"}${j.detail?.cached != null ? ` (${j.detail.cached} from cache)` : ""}`; } });
      if (S.fid !== fid) return;
      this.spectra.clear();
      S.field.spectral_ready = true; if (this.d?.id === sid && S.tab === "spectrum") this.spectrum();
    } catch (e) { prog.innerHTML = `<span style="color:var(--coral)">${esc(e.message)}</span>`; btn.disabled = false; }
  }

  // ------------------------------------------------------------ observations
  observations() {
    const d = this.d, P = d.points || [];
    const byIdx = new Map(S.field.epochs.map((e) => [e.index, e]));
    let h = `<p class="small">Every exposure in which this source was measured. Select a row to compare it (as B) with reference epoch A. “prov” opens the exact archive request, timestamp and checksum.</p>`;
    h += `<table class="data obs-table"><thead><tr><th>UTC</th><th>λ µm</th><th>Offset ″ (E / N)</th><th>Flags</th></tr></thead><tbody>`;
    for (const p of P) {
      const e = byIdx.get(p.obs_index) || {};
      const flags = [p.core_masked && ["sat", "saturated"], p.blended && ["", "blend"], p.artifact && ["", "artifact"], p.used_in_fit === false && ["", "clipped"], p.is_reference_epoch && ["ref", "ref"]].filter(Boolean).map(([c, t]) => `<span class="flag ${c}">${t}</span>`).join("");
      h += `<tr class="clickable" data-obs="${p.obs_index}" tabindex="0"><td class="num">${esc(e.date?.slice(0, 10) || "—")}<br><span class="small">${esc(e.date?.slice(11, 16) || "")}</span></td><td class="num" style="color:${lambdaColor(p.wavelength_um)}">${fmt(p.wavelength_um, 3)}</td><td class="num">${pm(p.east_arcsec, p.sigma_east_arcsec)}<br>${pm(p.north_arcsec, p.sigma_north_arcsec)}</td><td>${flags}<a class="prov" href="/api/fields/${S.fid}/epochs/${p.obs_index}/provenance" target="_blank" rel="noopener" title="Provenance of ${esc(e.obs_id)}">prov</a></td></tr>`;
    }
    h += `</tbody></table>`;
    const unused = S.field.epochs.filter((e) => !e.usable);
    if (unused.length) h += `<p class="small">${unused.length} archive image(s) not used: ${unused.map((e) => `${esc(e.obs_id)} (${esc(e.unusable_reason)})`).join("; ")}</p>`;
    this.body.innerHTML = h;
    this.body.querySelectorAll("tr[data-obs]").forEach((tr) => {
      tr.onclick = (ev) => { if (ev.target.tagName !== "A") emit("setB", +tr.dataset.obs); };
      tr.onkeydown = (ev) => { if (ev.key === "Enter") emit("setB", +tr.dataset.obs); };
    });
  }

  // ------------------------------------------------------------------- notes
  notes() {
    const d = this.d, key = `nadir:notes:${d.ra.toFixed(4)},${d.dec.toFixed(4)}`;
    let saved = ""; try { saved = localStorage.getItem(key) || ""; } catch (_) {}
    this.body.innerHTML = `<p class="small">Your field notebook for this source: hypotheses, things to check. Stored only in this browser, never sent anywhere.</p>
      <textarea id="note" placeholder="e.g. Moves ~10″/yr north; consistent with Gaia. Check flux in the 2.3 µm CO band…">${esc(saved)}</textarea>
      <div class="btnrow"><button class="btn" id="note-share">Copy link to this investigation</button><button class="btn" id="note-export">Download report (.md)</button><span class="small" id="note-status"></span></div>`;
    const ta = document.getElementById("note"), st = document.getElementById("note-status");
    ta.oninput = () => { try { localStorage.setItem(key, ta.value); st.textContent = "Saved in this browser"; } catch (_) { st.textContent = "Browser storage unavailable — notes not saved"; } };
    document.getElementById("note-share").onclick = async () => { try { await navigator.clipboard.writeText(location.href); st.textContent = "Link copied"; } catch (_) { st.textContent = location.href; } };
    document.getElementById("note-export").onclick = () => {
      const blob = new Blob([report(d, ta.value)], { type: "text/markdown" }), a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `nadir-${displayName(d).replace(/[^a-z0-9]+/gi, "-")}.md`; a.click(); URL.revokeObjectURL(a.href);
    };
  }

  _wire() {
    this.body.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => this.tab(b.dataset.go)));
    this.body.querySelectorAll("[data-learn]").forEach((b) => (b.onclick = () => emit("learn", b.dataset.learn)));
    const features = document.getElementById("feat");
    const load = document.getElementById("spec-load");
    if (load) load.onclick = () => this.loadSpectral(load.dataset.network !== "true");
    if (features) features.onchange = () => { S.showFeatures = features.checked; this.spectrum(); };
    this.body.querySelectorAll(".plot [data-point-info]").forEach(node => {
      const inspect = () => {
        const output = node.closest(".chart")?.querySelector(".plot-readout") || document.getElementById("spec-readout");
        if (output) output.textContent = node.dataset.pointInfo;
      };
      node.onpointerenter = inspect; node.onfocus = inspect; node.onclick = inspect;
    });
  }
}

function gaiaClass(g) {
  const c = [["star", g.p_star], ["galaxy", g.p_galaxy], ["quasar", g.p_quasar]].sort((a, b) => (b[1] || 0) - (a[1] || 0))[0];
  return c[1] != null ? `Gaia DSC: ${c[0]} (p=${fmt(c[1], 2)})` : "Gaia DSC: unclassified";
}

/** The headline result: one number, its certainty and a plain sentence. */
function hero(d) {
  const f = d.fit, T = TIERS[d.tier] || TIERS.insufficient, cmp = d.catalog?.pm_comparison;
  const pills = [`${d.n_epochs} epochs`, `${d.n_visits} visits`, `${fmt(d.baseline_days, 0)}-day baseline`].map((t) => `<span class="pillstat">${t}</span>`);
  if (cmp) pills.push(`<span class="pillstat ${cmp.consistent ? "ok" : "warn"}">${cmp.consistent ? "Agrees with Gaia" : "Differs from Gaia"} · ${fmt(cmp.z_total, 1)}σ</span>`);
  if (d.core_masked_fraction > 0.5) pills.push(`<span class="pillstat warn">Saturated core</span>`);
  const value = f && d.tier !== "rejected"
    ? `<div class="hero-value">${fmt(f.pm_total, 2)}<small>″/yr</small></div><div class="hero-side">±${fmt(Math.hypot(f.pm_east_err, f.pm_north_err) / Math.SQRT2, 1)} per axis<br>${fmt(f.snr, 1)}σ · PA ${fmt(f.pm_pa_deg, 0)}°</div>`
    : `<div class="hero-value muted">${d.tier === "rejected" ? "Motion not confirmed" : d.tier === "stationary" ? "No significant motion" : "Motion undetermined"}</div><div class="hero-side">${f ? `${fmt(f.pm_total, 2)}″/yr<br>${fmt(f.snr, 1)}σ` : ""}</div>`;
  return `<div class="hero-result ${f && d.tier !== "rejected" && d.tier !== "stationary" ? "" : "quiet"}">
    <div class="hero-label">Proper motion ${f ? L.measured : L.unknown}</div>
    ${value}<div class="hero-meta">${pills.join("")}</div></div>
    <p class="summary-line">${esc(plainSummary(d))}</p>`;
}

function plainSummary(d) {
  const f = d.fit, yrs = 1 / Math.max(f?.pm_total || 1e-9, 1e-9) * 6.15;
  switch (d.tier) {
    case "significant": return `This object drifts ${compass(f.pm_pa_deg)} across the sky by ${fmt(f.pm_total, 1)}″ a year — one SPHEREx pixel every ${fmt(yrs * 12, 0)} months — and every visit agrees.`;
    case "systematics_limited": return `This star visibly moves ${compass(f.pm_pa_deg)} across the field between visits: ${fmt(f.pm_total, 1)}″ per year. Its core saturates the detector, which limits the precision — and NADIR says so.`;
    case "candidate": return "Something looks like motion, but it fails a consistency check. Treat it as a lead to investigate, not a detection.";
    case "rejected": return "Two images suggested motion, but the other visits do not reproduce it — a reminder of why NADIR checks every visit.";
    case "stationary": return "This source holds still relative to its neighbours. Stars like this are the ruler NADIR measures motion against.";
    default: return "There is not enough data yet to say whether this object moves.";
  }
}

function compass(pa) {
  return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][Math.round((((pa % 360) + 360) % 360) / 45) % 8];
}

function verdict(d) {
  const T = TIERS[d.tier] || TIERS.insufficient, f = d.fit;
  const cls = { significant: "ok", systematics_limited: "ok", candidate: "warn", rejected: "bad", stationary: "", insufficient: "" }[d.tier] ?? "";
  let txt = "";
  if (d.tier === "significant") txt = `This source moves consistently across ${d.n_visits} independent visits (${fmt(f.snr, 1)}σ), measured on clean pixels.`;
  else if (d.tier === "systematics_limited") txt = `Motion is supported across ${d.n_visits} visits at ${fmt(f.snr, 1)}σ, but masked or saturated cores limit accuracy. The systematic floor is experimental.`;
  else if (d.tier === "candidate") txt = `Formally significant, but it fails a consistency test, so it is <b>not</b> a detection: ${esc(d.reasons.join("; "))}.`;
  else if (d.tier === "rejected") txt = `${esc(d.reasons[0] || "")}. A blend, a pixel-phase effect or a wavelength-dependent centroid can fake motion between two images; repeat visits expose it.`;
  else if (d.tier === "stationary") txt = `No motion above 5σ (${f ? fmt(f.snr, 1) + "σ" : "n/a"}). Stars like this anchor the reference frame.`;
  else txt = esc((d.reasons || []).join("; ") || "Not enough data.");
  return `<div class="callout ${cls}"><span class="tier ${d.tier}">${T.label}</span><p>${txt}</p></div>`;
}

function spectrumHTML(sp, d) {
  const P = sp.points || [];
  if (!P.length) return `<div class="callout bad">No usable photometry: ${sp.rejected.length} measurements rejected (${esc([...new Set(sp.rejected.map((r) => r.reason))].join("; "))}). ${L.unknown}</div>`;
  const visits = visitGroups(P);
  const visit = (mjd) => Math.max(0, visits.findIndex(group => mjd >= group[0].mjd && mjd <= group.at(-1).mjd));
  const ys = P.flatMap(point => [point.flux_mjy - point.flux_err_mjy, point.flux_mjy + point.flux_err_mjy]).concat((sp.catalog_points || []).map(point => point.flux_mjy)).filter(Number.isFinite);
  const lo = Math.min(0, ...ys), hi = Math.max(1, ...ys) * 1.1;
  const series = [];
  if (sp.blackbody) series.push({ type: "line", data: sp.blackbody.curve.wavelength_um.map((w, i) => ({ x: w, y: sp.blackbody.curve.flux_mjy[i] })), color: C("violet"), dash: "6 4", width: 1.8, title: sp.blackbody.label });
  for (const [i, f] of (S.showFeatures ? sp.reference_features : []).entries()) series.push({ type: "vline", x: f.wavelength_um, label: f.label, color: C("teal"), row: i % 3 });
  series.push({ type: "points", data: P.map((p) => ({ x: p.wavelength_um, y: p.flux_mjy, ex: (p.bandwidth_um || 0) / 2, ey: p.flux_err_mjy, color: VISIT_COLORS[visit(p.mjd) % VISIT_COLORS.length], hollow: !!p.flags?.length, shape: p.flags?.length ? "diamond" : "circle", opacity: p.snr < 3 ? 0.45 : 0.92, r: 3, title: `${fmt(p.wavelength_um, 3)} µm · ${fmt(p.flux_mjy, 2)} ± ${fmt(p.flux_err_mjy, 2)} mJy · ${p.band} · ${mjdToDate(p.mjd)} · ${p.method} · flags: ${p.flags?.join(", ") || "none"} · ${p.obs_id}` })) });
  series.push({ type: "points", shape: "square", r: 5, hollow: true, data: (sp.catalog_points || []).map((c) => ({ x: c.wavelength_um, y: c.flux_mjy, ey: c.flux_err_mjy, color: C("text-1"), title: `${c.catalog} ${c.band}: ${fmt(c.mag, 2)} mag = ${fmt(c.flux_mjy, 2)} mJy (catalog)` })) });
  let h = `<div class="legend-row">${visits.map((group, index) => `<span><i style="background:${VISIT_COLORS[index % VISIT_COLORS.length]}"></i>${mjdToDate(group[0].mjd)} → ${mjdToDate(group.at(-1).mjd)}</span>`).join("")}</div>`;
  h += `<div class="legend-row"><span><i style="border:1.5px solid ${C("text-1")};border-radius:2px;background:none"></i>2MASS / AllWISE (catalog)</span><span>◇ flagged point</span>${sp.blackbody ? `<span><i class="ln" style="background:${C("violet")}"></i>Colour model (inferred)</span>` : ""}</div>`;
  h += `<figure class="chart">${plot({ id: "spec", w: 400, h: 300, margin: { l: 56 }, readout: false, x: { label: "Observed wavelength (µm)", domain: [0.7, 5.1] }, y: { label: "Flux density Fν (mJy)", domain: [lo, hi] }, series })}`;
  h += `<figcaption class="spec-readout" id="spec-readout" aria-live="polite">Hover, focus or tap a point for its epoch, flux, uncertainty, method and flags. Vertical bars: ±1σ. Horizontal bars: channel half-bandwidth, not wavelength error.</figcaption></figure>`;
  h += `<label class="small" style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="feat" ${S.showFeatures ? "checked" : ""}> Show where common infrared features occur ${L.illustrative}</label>`;
  h += `<details class="evidence"><summary>Explain this spectrum</summary><p>${esc(sp.explanation?.explore || "")}</p><h4>Research assumptions</h4><p>${esc(sp.explanation?.research || "")}</p></details>`;
  if (sp.missing_images) h += `<div class="callout warn">${sp.missing_images} planned images are not available in this cached spectrum. Coverage is incomplete.<br><button class="btn sm" id="spec-load" data-network="true">Fetch missing images from IRSA</button><div id="spec-prog"></div></div>`;
  if (S.showFeatures) h += `<p class="small">Illustrative rest-frame regions; not redshift-adjusted and not detections. A dip alone does not identify a molecule.</p><ul class="small">${sp.reference_features.map(feature => `<li>${fmt(feature.wavelength_um, 3)} µm · ${esc(feature.label)}: ${esc(feature.kind)}</li>`).join("")}</ul>`;
  h += `<h4>What this spectrum supports</h4><dl class="kv">`;
  h += `<dt>Measurements</dt><dd>${P.length} fluxes in ${sp.bands.join(", ")} ${L.measured}<span class="small">${esc([...new Set(P.map(point => point.method))].join(", "))} at the ${sp.pm_used ? "proper-motion-propagated (" + esc(sp.pm_used) + ")" : "fixed"} position; ${sp.rejected.length} rejected (${sp.n_saturated} masked/saturated). Aperture measurements are not aperture-corrected.</span></dd>`;
  if (sp.catalog_comparison?.length) h += `<dt>vs catalogs</dt><dd>${sp.catalog_comparison.map((c) => `${c.band}: ×${fmt(c.ratio, 2)}`).join(" · ")} ${L.catalog}<span class="small">SPHEREx ÷ catalog flux near each band. Different epochs, broad filters, blending and aperture losses limit this comparison; no fixed agreement tolerance is claimed.</span></dd>`;
  if (sp.blackbody) h += `<dt>Colour temperature</dt><dd>${fmt(sp.blackbody.T_color_K, 0)} K ${L.inferred}<span class="small">${sp.blackbody.interval_grid_limited ? `Interval unresolved on the ≈${fmt(sp.blackbody.grid_step_K, 0)} K model grid` : `Range ${fmt(sp.blackbody.T_lo_K, 0)}–${sp.blackbody.unconstrained_high ? "unbounded" : fmt(sp.blackbody.T_hi_K, 0)} K`}. Single-blackbody model, χ²ᵣ ${fmt(sp.blackbody.chi2_red, 1)}. Real stars are not blackbodies (molecular bands), so this is not an effective temperature.${sp.gaia_teff ? ` Gaia's model estimate: ${fmt(sp.gaia_teff, 0)} K (catalog).` : ""}</span></dd>`;
  if (sp.simbad_sp_type) h += `<dt>Spectral type</dt><dd>${esc(sp.simbad_sp_type)} ${L.catalog}</dd>`;
  if (!sp.blackbody) h += `<dt>Colour model</dt><dd>Not supported by enough clean S/N ≥ 5 measurements ${L.unknown}</dd>`;
  else h += `<dt>Fit diagnostics</dt><dd>${sp.blackbody.n_points} fitted; ${sp.blackbody.excluded_points} excluded.<span class="small">${sp.blackbody.chi2_red > 5 ? "Poor continuum fit: do not interpret temperature as a physical property." : "Conditional on a single-temperature continuum."} ${sp.blackbody.unconstrained_low ? "Lower interval reaches the model grid boundary." : ""}</span></dd>`;
  h += `<dt>Composition</dt><dd>Not determined ${L.unknown}<span class="small">Feature markers show where molecules absorb or emit in general; NADIR does not claim to detect them in this source.</span></dd></dl>`;
  h += `<details class="evidence"><summary>Measurement ledger &amp; provenance (${P.length} accepted, ${sp.rejected.length} rejected)</summary><table class="data"><thead><tr><th>Epoch / λ</th><th>Flux ± σ mJy / reason</th><th>Flags / archive</th></tr></thead><tbody>${[...P, ...sp.rejected].map(point => `<tr><td class="num">${mjdToDate(point.mjd)}<br>${fmt(point.wavelength_um, 3)} µm</td><td class="num">${point.ok ? `${fmt(point.flux_mjy, 3)} ± ${fmt(point.flux_err_mjy, 3)}` : esc(point.reason)}<br>${esc(point.method || "")}</td><td class="small">${esc(point.flags?.join(", ") || "none")}<br>${point.source_url?.startsWith("https://irsa.ipac.caltech.edu/") ? `<a href="${esc(point.source_url)}" target="_blank" rel="noopener" title="${esc(point.response_sha256 || "")}">IRSA FITS</a>` : "provenance unavailable"}</td></tr>`).join("")}</tbody></table></details>`;
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
