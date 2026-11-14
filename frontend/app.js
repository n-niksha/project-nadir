// Project NADIR frontend: presentation only. All science happens server-side.
"use strict";
const $ = (id) => document.getElementById(id);
const ZOOM = 3; // screen pixels per grid pixel
const COLORS = { candidate_motion: "#ff5a4f", candidate_motion_low_confidence: "#ffb020",
                 no_significant_motion: "#9aa3b5", reference: "#3fbf7f" };
const state = { query: null, search: null, result: null, images: {}, mode: "a", blinkOn: false,
                timer: null, selected: null };

function setStatus(msg, kind = "") { const s = $("status"); s.textContent = msg; s.className = kind; }
const fmt = (v, n = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(n);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, params) {
  const url = path + "?" + new URLSearchParams(params);
  let resp;
  try { resp = await fetch(url); } catch (e) { throw new Error("Cannot reach the NADIR server: " + e.message); }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { const j = await resp.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

// ---------- search ----------
$("preset").onclick = () => {
  $("name").value = "Barnard's Star"; $("ra").value = "269.44627"; $("dec").value = "4.76824";
  $("band").value = "D2"; $("size").value = "0.2";
  setStatus("Preset: Gaia DR3 position of Barnard's Star propagated to epoch 2026.0 (it has moved ~4.6′ since J2000).");
};

$("resolve").onclick = async () => {
  const name = $("name").value.trim();
  if (!name) return setStatus("Type an object name first.", "err");
  setStatus("Resolving with SIMBAD…", "busy");
  try {
    const r = await api("/api/resolve", { name, epoch: 2026.0 });
    $("ra").value = r.ra.toFixed(5); $("dec").value = r.dec.toFixed(5);
    const moved = r.epoch !== 2000 ? ` Position propagated from J2000 to ${r.epoch} using SIMBAD proper motion (${fmt(r.pmra_masyr, 0)}, ${fmt(r.pmdec_masyr, 0)} mas/yr).` : " (J2000 position; no proper motion applied.)";
    setStatus(`${r.name}: RA ${r.ra.toFixed(5)}, Dec ${r.dec.toFixed(5)}.` + moved);
  } catch (e) { setStatus(e.message, "err"); }
};

$("search-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const ra = parseFloat($("ra").value), dec = parseFloat($("dec").value), size = parseFloat($("size").value);
  if (!(ra >= 0 && ra < 360) || !(dec >= -90 && dec <= 90)) return setStatus("Enter RA in [0, 360) and Dec in [−90, 90] decimal degrees.", "err");
  if (!(size >= 0.02 && size <= 0.5)) return setStatus("Size must be between 0.02 and 0.5 degrees.", "err");
  state.query = { ra, dec, size, band: $("band").value };
  $("search").disabled = true; $("main").hidden = true; $("pairbar").hidden = true; stopBlink();
  setStatus("Querying IRSA and downloading cutouts — the first search of a position takes about a minute; repeats are served from the local cache.", "busy");
  try {
    const s = await api("/api/search", state.query);
    state.search = s;
    populate(s);
  } catch (e) { setStatus(e.message, "err"); }
  finally { $("search").disabled = false; }
};

function obsLabel(o) {
  return `[${o.index}] ${(o.date || "no date").slice(0, 16).replace("T", " ")} · ${fmt(o.wavelength_um, 3)} µm · ${o.release || "?"}`;
}

function populate(s) {
  if (s.n_loaded === 0) {
    return setStatus(s.n_in_archive === 0
      ? "SPHEREx has no observations covering this position in this band (DR1 + QR3)."
      : `All ${s.n_in_archive} cutout downloads failed (${s.failures[0]?.error || "unknown error"}).`, "err");
  }
  for (const id of ["obs-a", "obs-b"]) {
    $(id).innerHTML = s.observations.map((o) =>
      `<option value="${o.index}" ${o.usable ? "" : "disabled"}>${esc(obsLabel(o))}${o.usable ? "" : " — " + esc(o.unusable_reason)}</option>`).join("");
  }
  $("pairs").innerHTML = s.suggested_pairs.length
    ? s.suggested_pairs.map((p, i) => `<option value="${i}">${p.dt_days.toFixed(0)} d apart · Δλ/λ ${(100 * p.rel_dlambda).toFixed(2)}% · [${p.a}] vs [${p.b}]</option>`).join("")
    : "<option>none</option>";
  $("pairbar").hidden = false;
  $("search-info").textContent = `${s.n_in_archive} images in archive · ${s.n_usable} usable` + (s.failures.length ? ` · ${s.failures.length} failed downloads` : "");
  if (s.suggested_pairs.length) {
    usePair(0);
    setStatus(s.pair_note || "Observations loaded. Comparing the best-matched pair…", s.pair_note ? "err" : "busy");
    runAnalyze();
  } else {
    setStatus(s.pair_note || "No comparable pair found.", "err");
  }
}

function usePair(i) { const p = state.search.suggested_pairs[i]; if (p) { $("obs-a").value = p.a; $("obs-b").value = p.b; } }
$("pairs").onchange = () => { usePair(+$("pairs").value); runAnalyze(); };
$("analyze").onclick = runAnalyze;
$("obs-a").onchange = $("obs-b").onchange = runAnalyze;

// ---------- analysis ----------
async function runAnalyze() {
  const a = +$("obs-a").value, b = +$("obs-b").value;
  if (a === b) return setStatus("Choose two different observations.", "err");
  $("analyze").disabled = true; stopBlink();
  setStatus("Detecting sources, registering and differencing…", "busy");
  try {
    const r = await api("/api/analyze", { ...state.query, a, b });
    state.result = r; state.selected = null;
    await loadImages(r.images);
    $("main").hidden = false; $("track-panel").hidden = true;
    renderPanels(r);
    if (r.target_id !== null && r.target_id !== undefined) selectSource(r.sources.find((s) => s.id === r.target_id));
    else $("source").textContent = "Click a source in the image.";
    if (new URLSearchParams(location.search).get("track") && state.selected && !state.autoTracked) { state.autoTracked = true; runTrack(state.selected); }
    setMode(state.mode === "blink" ? "blink" : state.mode);
    setStatus(`Compared ${r.a.date?.slice(0, 10)} with ${r.b.date?.slice(0, 10)} (${fmt(r.dt_days, 1)} days apart).`);
  } catch (e) { setStatus(e.message, "err"); }
  finally { $("analyze").disabled = false; }
}

function loadImages(images) {
  const names = ["a", "b", "b_wcs", "diff"];
  return Promise.all(names.map((n) => new Promise((res, rej) => {
    const im = new Image(); im.onload = () => { state.images[n] = im; res(); }; im.onerror = rej; im.src = images[n];
  })));
}

function currentFrame() {
  if (state.mode === "blink") return state.blinkOn ? "b" : "a";
  return state.mode;
}

function draw() {
  const r = state.result; if (!r) return;
  const frame = currentFrame();
  const key = frame === "b" && !$("registered").checked ? "b_wcs" : frame;
  const im = state.images[key]; if (!im) return;
  const cv = $("canvas"), nx = r.grid.nx, ny = r.grid.ny;
  cv.width = nx * ZOOM; cv.height = ny * ZOOM;
  const ctx = cv.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(im, 0, 0, cv.width, cv.height);
  const lab = { a: `A · ${r.a.date?.slice(0, 19)} UTC · ${fmt(r.a.wavelength_um, 3)} µm`,
                b: `B · ${r.b.date?.slice(0, 19)} UTC · ${fmt(r.b.wavelength_um, 3)} µm`,
                diff: `B − A difference · asinh, ±${fmt(r.images.stretch.diff_range, 1)} MJy/sr · orange = brighter in B` }[frame];
  $("frame-label").textContent = lab;
  if (!$("overlay").checked) return;
  const toC = (x, y) => [(x + 0.5) * ZOOM, (ny - 1 - y + 0.5) * ZOOM];
  ctx.lineWidth = 1.5;
  for (const s of r.sources) {
    if (s.status === "reference" && !$("show-refs").checked) continue;
    const useB = frame === "b";
    const [cx, cy] = toC(useB ? s.x + movedDx(s) : s.x, useB ? s.y + movedDy(s) : s.y);
    ctx.strokeStyle = COLORS[s.status] || "#fff";
    ctx.beginPath(); ctx.arc(cx, cy, s.status === "reference" ? 5 : 8, 0, 2 * Math.PI); ctx.stroke();
    if (s.status.startsWith("candidate") && frame !== "b") { // displacement vector A -> B
      const [bx, by] = toC(s.x + movedDx(s), s.y + movedDy(s));
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(bx, by); ctx.stroke();
    }
  }
  ctx.strokeStyle = "#b58cff";
  const lone = frame === "a" ? r.unmatched_a : frame === "b" ? r.unmatched_b : r.unmatched_a.concat(r.unmatched_b);
  for (const u of lone) {
    const [cx, cy] = toC(u.grid_x, u.grid_y);
    ctx.beginPath(); ctx.moveTo(cx - 5, cy); ctx.lineTo(cx + 5, cy); ctx.moveTo(cx, cy - 5); ctx.lineTo(cx, cy + 5); ctx.stroke();
  }
  if (state.selected) {
    const s = state.selected, useB = frame === "b";
    const [cx, cy] = toC(useB ? s.x + movedDx(s) : s.x, useB ? s.y + movedDy(s) : s.y);
    ctx.strokeStyle = "#ffee58"; ctx.lineWidth = 2; ctx.strokeRect(cx - 13, cy - 13, 26, 26);
  }
}
// displacement in grid pixels (east is -x)
const movedDx = (s) => -s.d_east_arcsec / state.result.grid.pixel_scale_arcsec;
const movedDy = (s) => s.d_north_arcsec / state.result.grid.pixel_scale_arcsec;

function stopBlink() { if (state.timer) clearInterval(state.timer); state.timer = null; }
function setMode(mode) {
  state.mode = mode; stopBlink();
  document.querySelectorAll("#modes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  if (mode === "blink") { state.blinkOn = false; state.timer = setInterval(() => { state.blinkOn = !state.blinkOn; draw(); }, +$("rate").value); }
  draw();
}
document.querySelectorAll("#modes button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$("rate").oninput = () => { $("rate-val").textContent = $("rate").value; if (state.mode === "blink") setMode("blink"); };
for (const id of ["registered", "overlay", "show-refs"]) $(id).onchange = draw;

$("canvas").onclick = (ev) => {
  const r = state.result; if (!r) return;
  const rect = $("canvas").getBoundingClientRect();
  const gx = (ev.clientX - rect.left) / ZOOM - 0.5, gy = r.grid.ny - 1 - ((ev.clientY - rect.top) / ZOOM - 0.5);
  let best = null, bd = 9; // within 9 grid px
  for (const s of r.sources) { const d = Math.hypot(s.x - gx, s.y - gy); if (d < bd) { bd = d; best = s; } }
  let lone = null;
  for (const [u, ep] of r.unmatched_a.map((u) => [u, "A"]).concat(r.unmatched_b.map((u) => [u, "B"]))) {
    const d = Math.hypot(u.grid_x - gx, u.grid_y - gy); if (d < bd) { bd = d; lone = { u, ep }; best = null; }
  }
  if (lone) showUnmatched(lone.u, lone.ep); else if (best) selectSource(best);
};

// ---------- panels ----------
function renderPanels(r) {
  const v = r.verdict;
  let h = `<p><b>${esc(v.headline)}</b></p><p>${esc(v.target)}</p>`;
  if (v.candidates.length) {
    h += `<div class="small">Candidates (click to inspect):</div>` + v.candidates.map((id) => {
      const s = r.sources.find((x) => x.id === id);
      return `<div><a href="#" data-sid="${id}">#${id}</a> ${fmt(s.total_arcsec)}″ · ${fmt(s.significance, 1)}σ <span class="tag ${s.status}">${s.status.replaceAll("_", " ")}</span></div>`;
    }).join("");
  }
  $("verdict").innerHTML = h;
  $("verdict").querySelectorAll("a[data-sid]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); selectSource(r.sources.find((x) => x.id === +a.dataset.sid)); }));

  const g = r.registration, x = r.xcorr_check;
  let reg = `<p>${esc(r.message)}</p>`;
  if (r.ok) {
    reg += `<table>
      <tr><th>Model</th><td>${g.model}, ${g.n_used} of ${g.n_matched_clean} clean matched stars${g.converged ? "" : " <span class='warn'>(clipping did not converge)</span>"}</td></tr>
      <tr><th>B→A shift</th><td>E ${fmt(g.shift_east_arcsec)}″, N ${fmt(g.shift_north_arcsec)}″ · rot ${fmt(g.rotation_deg, 4)}° · scale ${fmt(g.scale, 5)}</td></tr>
      <tr><th>Per-source scatter</th><td>E ${fmt(g.rms_east_arcsec)}″, N ${fmt(g.rms_north_arcsec)}″ (${fmt(g.rms_native_px, 3)} native px)</td></tr>
      <tr><th>Cross-corr. check</th><td>${x ? `E ${fmt(x.shift_east_arcsec)}″, N ${fmt(x.shift_north_arcsec)}″ (independent, whole image)` : "not available"}</td></tr>
      <tr><th>Detections</th><td>A ${r.n_detected[0]}, B ${r.n_detected[1]}; matched ${r.sources.length}; one-epoch only ${r.unmatched_a.length}/${r.unmatched_b.length}</td></tr>
      <tr><th>Gaia DR3</th><td>${r.gaia.available ? `${r.gaia.n_matched} sources matched (${r.gaia.n_catalog} stars G&lt;17 in field)` : "unavailable" + (r.gaia.error ? ": " + esc(r.gaia.error) : "")}</td></tr>
      <tr><th>Difference</th><td>B×${fmt(r.difference.flux_scale, 3)} − A; robust σ ${fmt(r.difference.robust_sigma, 3)} MJy/sr</td></tr>
    </table>`;
  }
  const obsRow = (o, n) => `<tr><th>${n}</th><td>${esc(o.obs_id)} · det ${o.detector} · ${esc(o.date)} UTC (MJD ${fmt(o.mjd, 4)}) · ${fmt(o.wavelength_um, 4)} µm · ${fmt(o.exptime_s, 0)} s · ${esc(o.release)} v${esc(o.pipeline_version)} · PA ${fmt(o.detector_pa_deg, 0)}°
     <br><a href="${esc(o.source_url)}" target="_blank" rel="noopener">archive cutout URL</a> · <a href="/api/provenance?${new URLSearchParams({ ...state.query, index: o.index })}" target="_blank">provenance</a>${o.issues.length ? `<br><span class="warn">${esc(o.issues.join("; "))}</span>` : ""}</td></tr>`;
  reg += `<table>${obsRow(r.a, "A")}${obsRow(r.b, "B")}</table>`;
  if (r.rel_dlambda !== null) reg += `<p class="small">Wavelength mismatch Δλ/λ = ${(100 * r.rel_dlambda).toFixed(2)}% (SPHEREx resolution element ≈ 2.4% in this band).</p>`;
  $("reg").innerHTML = reg;
  $("image-note").textContent = `Images are bilinearly reprojected to a ${fmt(r.grid.pixel_scale_arcsec, 3)}″/px tangent-plane grid (native pixels are 6.15″); asinh stretch shared by A and B. White = saturated, dark blue = no valid data. Positions are measured on native pixels, not on these resampled images.`;
}

function selectSource(s) {
  if (!s) return;
  state.selected = s; draw();
  const r = state.result, c = s.catalog, p = c && c.predicted;
  const ep = (e, o) => `<tr><th>${o === r.a ? "A" : "B"} · ${esc(o.date?.slice(0, 19))}</th><td>RA ${e.ra.toFixed(6)}°, Dec ${e.dec.toFixed(6)}° · px (${fmt(e.native_x)}, ${fmt(e.native_y)}) ± (${fmt(e.x_err_px, 3)}, ${fmt(e.y_err_px, 3)}) · S/N ${fmt(e.snr, 0)} · ${e.method}${e.saturated ? " · <span class='warn'>saturated</span>" : ""}${e.blended ? " · <span class='warn'>blended</span>" : ""}${e.warn_flags.length ? " · flags: " + esc(e.warn_flags.join(", ")) : ""}</td></tr>`;
  let h = `<p>#${s.id} <span class="tag ${s.status}">${s.status.replaceAll("_", " ")}</span></p>
    <table>${ep(s.a, r.a)}${ep(s.b, r.b)}
    <tr><th>Displacement A→B</th><td>E ${fmt(s.d_east_arcsec)} ± ${fmt(s.sigma_east_arcsec)}″, N ${fmt(s.d_north_arcsec)} ± ${fmt(s.sigma_north_arcsec)}″ · total ${fmt(s.total_arcsec)}″ · ${fmt(s.significance, 1)}σ</td></tr>
    <tr><th>Gaia DR3</th><td>${c ? `${c.source_id} · G ${fmt(c.gmag, 1)} · ${fmt(c.separation_arcsec, 1)}″ away · PM (${fmt(c.pmra_masyr, 1)}, ${fmt(c.pmdec_masyr, 1)}) mas/yr${p ? ` → predicted E ${fmt(p.east_arcsec)}″, N ${fmt(p.north_arcsec)}″` : ""}` : "no counterpart with G<17 within 6″"}</td></tr></table>
    <p>${esc(describe(s))}</p>
    ${s.reasons.length ? `<p class="warn">Caveats: ${esc(s.reasons.join("; "))}</p>` : ""}
    <button id="track-btn">Track across all epochs</button> <span class="small">uses every usable ${state.query.band} image (~20 s first time)</span>`;
  $("source").innerHTML = h;
  $("track-btn").onclick = () => runTrack(s);
}

function describe(s) {
  if (s.status === "reference") return "Stationary within errors; used as a registration reference star.";
  if (s.status === "no_significant_motion") return "Displacement is below the 5σ detection threshold: no reliable motion.";
  if (s.status === "candidate_motion") return "Significant displacement with clean measurements in both epochs.";
  return "Displacement is formally significant but the measurement is low-confidence (see caveats).";
}

function showUnmatched(u, ep) {
  state.selected = null; draw();
  $("source").innerHTML = `<p><span class="tag unmatched">detected in epoch ${ep} only</span></p>
    <p>RA ${u.ra.toFixed(6)}°, Dec ${u.dec.toFixed(6)}° · S/N ${fmt(u.snr, 0)}${u.warn_flags.length ? " · flags: " + esc(u.warn_flags.join(", ")) : ""}</p>
    <p>No counterpart within 3 native pixels in the other epoch, so <b>no displacement can be measured</b>. Possible causes: noise peak near the detection threshold, cosmic-ray or detector artifact, a blend resolved differently, a variable/transient source, or motion larger than the match radius.</p>`;
}

async function runTrack(s) {
  const r = state.result;
  $("track-btn").disabled = true; setStatus("Measuring the source in every epoch…", "busy");
  try {
    const t = await api("/api/track", { ...state.query, ref: r.a.index, sra: s.ra, sdec: s.dec });
    $("track-panel").hidden = false; renderTrack(t);
    setStatus(`Track complete: ${t.points.length} epochs measured.`);
  } catch (e) { setStatus(e.message, "err"); }
  finally { const b = $("track-btn"); if (b) b.disabled = false; }
}

function renderTrack(t) {
  const f = t.fit, c = t.catalog;
  let h = "";
  if (f) {
    h += `<p><b>Linear fit</b> over ${fmt(f.baseline_days, 0)} d, ${f.n_epochs} epochs: μ<sub>E</sub> = ${fmt(f.east.pm_arcsec_per_yr)} ± ${fmt(f.east.pm_err_arcsec_per_yr)} ″/yr, μ<sub>N</sub> = ${fmt(f.north.pm_arcsec_per_yr)} ± ${fmt(f.north.pm_err_arcsec_per_yr)} ″/yr <span class="small">(errors scaled by √χ²<sub>red</sub> when &gt; 1; parallax not modelled)</span></p>`;
  } else h += `<p class="warn">${esc(t.fit_note || "No fit.")}</p>`;
  if (c) h += `<p>Gaia DR3 ${c.source_id}: μ<sub>E</sub> = ${fmt(c.pm_east_arcsec_per_yr, 3)}, μ<sub>N</sub> = ${fmt(c.pm_north_arcsec_per_yr, 3)} ″/yr, parallax ${fmt(c.parallax_mas, 1)} mas <span class="small">(dashed lines: catalog prediction incl. parallax; not used in the measurement)</span></p>`;
  h += `<div class="scroll"><table><tr><th>UTC</th><th>λ µm</th><th>E ″</th><th>N ″</th><th></th></tr>` +
    t.points.map((p) => `<tr><td>${esc(p.date?.slice(0, 19))}</td><td>${fmt(p.wavelength_um, 3)}</td><td>${fmt(p.east_arcsec)} ± ${fmt(p.sigma_east_arcsec)}</td><td>${fmt(p.north_arcsec)} ± ${fmt(p.sigma_north_arcsec)}</td><td>${p.saturated ? "sat" : ""}${p.is_reference_epoch ? " ref" : ""}</td></tr>`).join("") + `</table></div>`;
  const sk = t.skipped.concat(t.skipped_unusable || []);
  if (sk.length) h += `<p class="small">${sk.length} epoch(s) not measured: ${esc(sk.map((x) => x.obs_id + " (" + x.reason + ")").join("; "))}</p>`;
  $("track").innerHTML = h;
  plotTrack(t);
}

function plotTrack(t) {
  const cv = $("track-plot"), ctx = cv.getContext("2d"), W = cv.width, H = cv.height, m = { l: 44, r: 10, t: 14, b: 26 };
  ctx.clearRect(0, 0, W, H);
  const pts = t.points; if (!pts.length) return;
  const xs = pts.map((p) => p.mjd), vals = [];
  for (const p of pts) { vals.push(p.east_arcsec - p.sigma_east_arcsec, p.east_arcsec + p.sigma_east_arcsec, p.north_arcsec - p.sigma_north_arcsec, p.north_arcsec + p.sigma_north_arcsec);
    if (p.predicted_east_arcsec != null) vals.push(p.predicted_east_arcsec, p.predicted_north_arcsec); }
  const x0 = Math.min(...xs) - 5, x1 = Math.max(...xs) + 5, y0 = Math.min(...vals) - 0.5, y1 = Math.max(...vals) + 0.5;
  const X = (x) => m.l + (x - x0) / (x1 - x0) * (W - m.l - m.r), Y = (y) => H - m.b - (y - y0) / (y1 - y0) * (H - m.t - m.b);
  ctx.strokeStyle = "#2a3040"; ctx.fillStyle = "#8b94a7"; ctx.font = "11px system-ui"; ctx.lineWidth = 1;
  ctx.strokeRect(m.l, m.t, W - m.l - m.r, H - m.t - m.b);
  const step = Math.max(1, Math.ceil((y1 - y0) / 6));
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) { ctx.beginPath(); ctx.moveTo(m.l, Y(y)); ctx.lineTo(W - m.r, Y(y)); ctx.stroke(); ctx.fillText(y + "″", 6, Y(y) + 4); }
  ctx.fillText(`MJD ${x0.toFixed(0)}`, m.l, H - 8); ctx.fillText(`${x1.toFixed(0)}`, W - 44, H - 8);
  const series = [["east", "#6cb4ff"], ["north", "#ff8a65"]];
  for (const [ax, col] of series) {
    ctx.strokeStyle = col; ctx.fillStyle = col;
    for (const p of pts) {
      const x = X(p.mjd), v = p[ax + "_arcsec"], s = p["sigma_" + ax + "_arcsec"];
      ctx.beginPath(); ctx.moveTo(x, Y(v - s)); ctx.lineTo(x, Y(v + s)); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, Y(v), 2.2, 0, 2 * Math.PI); ctx.fill();
    }
    const pred = pts.filter((p) => p["predicted_" + ax + "_arcsec"] != null);
    if (pred.length) { ctx.setLineDash([4, 3]); ctx.beginPath(); pred.forEach((p, i) => { const a = [X(p.mjd), Y(p["predicted_" + ax + "_arcsec"])]; i ? ctx.lineTo(...a) : ctx.moveTo(...a); }); ctx.stroke(); ctx.setLineDash([]); }
  }
  ctx.fillStyle = "#6cb4ff"; ctx.fillText("● East", m.l + 6, m.t + 12); ctx.fillStyle = "#ff8a65"; ctx.fillText("● North", m.l + 56, m.t + 12);
}

// Shareable links: /?ra=..&dec=..&band=D2&size=0.2&mode=blink runs the search on load.
(function init() {
  const q = new URLSearchParams(location.search);
  if (q.has("ra") && q.has("dec")) {
    $("ra").value = q.get("ra"); $("dec").value = q.get("dec");
    if (q.get("band")) $("band").value = q.get("band");
    if (q.get("size")) $("size").value = q.get("size");
    if (["a", "b", "blink", "diff"].includes(q.get("mode"))) state.mode = q.get("mode");
    $("search-form").requestSubmit();
  } else $("preset").click();
})();
