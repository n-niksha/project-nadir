// NADIR frontend entry point: presentation only — all science is server-side.
import { S, TIERS, on, emit } from "./state.js";
import { getJSON, postJSON, pollJob, ApiError } from "./api.js";
import { esc, fmt, dateTime, sexa, niceDate, lambdaColor, displayName } from "./fmt.js";
import { SkyView, cursorText } from "./sky.js";
import { Timeline } from "./timeline.js";
import { Inspector } from "./inspector.js";
import { discoverHTML, learnHTML, helpHTML } from "./sheets.js";
import { Explorer, label as sliceLabel, CUBE_WL } from "./explore.js";
import { epochList, stepEpoch } from "./playback.js";
import { JourneyEngine } from "./journey/engine.js";
import { JourneyUI, Lessons } from "./journey/ui.js";
import { journeyFor } from "./journey/journeys.js";

const $ = (id) => document.getElementById(id);
const BOOT = new URLSearchParams(location.search); // read once: updateUrl() rewrites the query string
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sky = new SkyView($("sky"));
const timeline = new Timeline($("tl-track"), $("visit-strip"));
const inspector = new Inspector();
let cfg = {}, presets = [], explorer = null, loadCtl = null, blinkTimer = null, playTimer = null, firstLoad = true;
const store = { get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} } };

// ------------------------------------------------------------------ intro
// A ≤3 s, skippable title moment on the first visit of a session. The app
// loads behind it, so it never delays the data.
function intro() {
  const el = $("intro"); let seen = false;
  try { seen = sessionStorage.getItem("nadir:intro") === "1"; } catch (_) {}
  if (!BOOT.has("intro") && (seen || BOOT.has("nointro") || BOOT.has("ra") || REDUCED)) return;
  el.hidden = false;
  const done = () => { el.hidden = true; try { sessionStorage.setItem("nadir:intro", "1"); } catch (_) {} document.removeEventListener("keydown", key, true); };
  // capture phase + stopImmediatePropagation: skipping the intro must not also trigger app shortcuts
  const key = (e) => { e.preventDefault(); e.stopImmediatePropagation(); done(); };
  $("intro-skip").onclick = done; el.onclick = done; document.addEventListener("keydown", key, true);
  setTimeout(done, 3200);
}

// ---------------------------------------------------------------- loading
const STAGE_TEXT = { discover: "Finding SPHEREx observations", download: "Downloading image cutouts", read: "Reading FITS data", detect: "Detecting & measuring sources",
  register: "Aligning epochs on reference stars", track: "Following sources through time", render: "Preparing comparison frames" };
const qName = (q) => q.name || `${fmt(q.ra, 4)}°, ${fmt(q.dec, 4)}°`;

function showLoader(snap, q) {
  const el = $("loader"); el.hidden = false;
  const stages = snap?.stages || Object.keys(STAGE_TEXT), idx = snap?.stage_index ?? 0;
  const det = snap && snap.total > 0, pct = det ? Math.round(100 * snap.done / snap.total) : 0;
  const li = stages.map((s, i) => `<li class="${i < idx || snap?.status === "done" ? "done" : i === idx ? "active" : ""}"><span class="st">${i < idx ? "✓" : ""}</span>${esc(STAGE_TEXT[s] || s)}<span class="t">${snap?.stage_times?.[s] != null ? snap.stage_times[s].toFixed(1) + "s" : i === idx && det ? `${snap.done}/${snap.total}` : ""}</span></li>`).join("");
  const extra = snap?.stage === "download" && snap.detail?.cached != null ? ` · ${snap.detail.cached} from local cache, ${snap.done - snap.detail.cached} fresh from IRSA` : "";
  el.innerHTML = `<div class="loader-card"><div class="lk">Observing</div><h3>${esc(qName(q))}</h3>
    <div class="sub">SPHEREx ${esc(q.band)} · ${q.size}° field · ${snap ? `${snap.elapsed_s.toFixed(1)} s` : "starting"}</div>
    <ol class="stages">${li}</ol><div class="bar ${det ? "" : "indet"}"><i style="width:${pct}%"></i></div>
    <div class="loader-msg">${esc(snap?.message || "Contacting the NADIR server…")}${extra}</div>
    <div class="loader-actions"><button class="btn ghost sm" id="ld-cancel">Cancel</button></div></div>`;
  $("ld-cancel").onclick = () => { loadCtl?.abort(); hideLoader(); if (!S.field) loadPreset(presets.find((p) => p.demo) || presets[0]); };
}
function hideLoader() { $("loader").hidden = true; }
function showError(msg, q) {
  const el = $("loader"); el.hidden = false;
  el.innerHTML = `<div class="loader-card error"><div class="lk">Could not build this field</div><h3>${esc(qName(q))}</h3><div class="sub">${esc(q.band)}</div>
    <p>${esc(msg)}</p><p class="small">Nothing has been changed${S.field ? "; the previous field is still loaded" : ""}. Archive problems are usually temporary.</p>
    <div class="loader-actions">${S.field ? '<button class="btn ghost" id="er-close">Keep previous field</button>' : ""}<button class="btn" id="er-demo">Open cached Barnard's Star demo</button><button class="btn primary" id="er-retry">Retry</button></div></div>`;
  $("er-retry").onclick = () => loadField(q);
  $("er-demo").onclick = openDemo;
  if ($("er-close")) $("er-close").onclick = hideLoader;
}

async function loadConfig() {
  const results = await Promise.all([getJSON("/api/config"), getJSON("/api/presets")]);
  [cfg, presets] = results;
  renderPresets();
}

async function openDemo() {
  try {
    if (!presets.length) await loadConfig();
    const preset = presets.find((p) => p.demo) || presets[0];
    if (!preset) throw new Error("The server returned no demo presets.");
    loadPreset(preset);
  } catch (e) { showStartupError(e); }
}

function showStartupError(error) {
  showError(error.message, { name: "Application startup", band: "D2" });
  $("er-retry").onclick = boot;
  $("data-badge-text").textContent = "NADIR server unavailable";
}

async function loadField(q, opts = {}) {
  loadCtl?.abort(); // supersede any running request (server job is cancelled too)
  const ctl = new AbortController(); loadCtl = ctl;
  stopTimers(); closePops(); lessons.close();
  $("catalog-status").textContent = "Loading Gaia DR3, SIMBAD, 2MASS, AllWISE…";
  showLoader(null, q);
  try {
    const r = await postJSON("/api/fields", { ra: q.ra, dec: q.dec, size: q.size, band: q.band }, ctl.signal);
    if (!r.ready) await pollJob(r.job, { signal: ctl.signal, onUpdate: (s) => showLoader(s, q) });
    const f = await getJSON(`/api/fields/${r.fid}`, ctl.signal);
    if (ctl.signal.aborted) return;
    S.query = q; S.fid = r.fid; S.field = f; S.catalogs = null; S.detail = null; S.selected = null;
    S.a = opts.a ?? f.best_pair?.a ?? f.reference_index; S.b = opts.b ?? f.best_pair?.b ?? f.epochs.find((e) => e.usable && e.index !== S.a)?.index;
    if (!f.epochs[S.b]?.usable) S.b = f.best_pair?.b;
    S.frame = S.a;
    sky.setField(f); timeline.setField(f);
    await Promise.all([sky.loadFrame(S.a), sky.loadFrame(S.b)]);
    if (ctl.signal.aborted) return;
    sky.preload(epochList(f, S.a, "motion").map((e) => e.index).concat(f.epochs.filter((epoch) => epoch.usable).map((epoch) => epoch.index)));
    renderProvenance(); renderFilters(); renderTargetReadout();
    hideLoader();
    setCmp(opts.mode || (firstLoad ? "a" : S.cmp === "play" ? "a" : S.cmp));
    refreshPair();
    const target = opts.src != null ? f.sources.find((s) => s.id === +opts.src) : nearestTarget(f);
    if (target) await select(target.id, { center: false, keepTab: opts.keepTab === true, quiet: true });
    else inspector.close();
    loadCatalogs(r.fid);
    if (opts.tour === true) startTour();
    else maybeWelcome();
    firstLoad = false;
    updateUrl();
  } catch (e) {
    if (e.name === "AbortError") return;
    showError(e.message, q);
  }
}

function nearestTarget(f) {
  // The source at the requested position, if one was measured there (within 12").
  const t = sky.tan, [x, y] = t.skyToPix(S.query.ra, S.query.dec);
  let best = null, bd = 12 / f.grid.pixel_scale_arcsec;
  for (const s of f.sources) { const d = Math.hypot(s.x - x, s.y - y); if (d < bd) { bd = d; best = s; } }
  return best;
}

async function loadCatalogs(fid) {
  $("catalog-status").textContent = "Loading Gaia DR3, SIMBAD, 2MASS, AllWISE…";
  try {
    const r = await postJSON(`/api/fields/${fid}/catalogs`);
    if (r.job) await pollJob(r.job, { interval: 600 });
    if (fid !== S.fid) return;
    S.catalogs = await getJSON(`/api/fields/${fid}/catalogs`);
    const f = await getJSON(`/api/fields/${fid}`); // cross-matches now include catalog identities
    if (fid !== S.fid) return;
    S.field = f; sky.f = f; sky.byId = new Map(f.sources.map((s) => [s.id, s]));
    const errs = Object.entries(S.catalogs.errors || {});
    const parts = Object.entries(S.catalogs.layers).map(([k, l]) => `${l.catalog?.split(" (")[0] || k}: ${l.items.length}${l.stale ? " (stale cache)" : ""}`);
    renderFilters();
    if (S.selected != null) await select(S.selected, { center: false, keepTab: true, quiet: true });
    sky.draw(); updateHud();
    // set last: tests and users treat this text as "catalogs fully applied"
    $("catalog-status").innerHTML = "Catalog sources in view — " + esc(parts.join(" · ")) + (errs.length ? `<br><span style="color:var(--sun)">Unavailable: ${esc(errs.map(([k]) => k).join(", "))}</span>` : "");
  } catch (e) { $("catalog-status").textContent = `Catalogs unavailable (${e.message}). SPHEREx measurements are unaffected.`; }
}

// ------------------------------------------------------------- comparison
function setPlayIcon(on) {
  $("play").innerHTML = `<svg><use href="#i-${on ? "pause" : "play"}"/></svg>`;
  $("play").classList.toggle("playing", !!on);
  $("play").setAttribute("aria-label", on ? "Pause observations" : "Play observations");
}
function setCmp(mode) {
  if (!S.field) return;
  if (mode === "play" && S.cmp === "play") { togglePlay(); return; }
  S.cmp = mode; stopTimers();
  document.querySelectorAll("[data-cmp]").forEach((b) => { b.classList.toggle("active", b.dataset.cmp === mode); b.setAttribute("aria-pressed", b.dataset.cmp === mode); });
  $("opacity-ctl").hidden = mode !== "overlay";
  if (mode === "blink") blinkTimer = setInterval(() => { S.blinkOn = !S.blinkOn; sky.draw(); timeline.update(); updateHud(); }, Math.max(150, 1000 / Math.max(1, S.fps / 2.5)));
  if (mode === "diff") sky.setDiff(S.a, S.b);
  if (mode === "play") { if (!playList().includes(S.frame)) S.frame = playList()[0]; startPlay(); }
  setPlayIcon(S.playing || mode === "blink");
  sky.draw(); timeline.render(); updateHud(); updateUrl();
}
function stopTimers() { clearInterval(blinkTimer); clearTimeout(playTimer); blinkTimer = playTimer = null; S.playing = false; setPlayIcon(false); }
function playList() {
  return epochList(S.field, S.a, S.playbackMode).map((epoch) => epoch.index);
}
function startPlay() {
  clearTimeout(playTimer);
  const fid = S.fid;
  const token = {}; S.playToken = token;
  if (playList().length < 2) { S.playing = false; toast("Fewer than two comparable observations. Choose another anchor A or “All wavelengths”."); return; }
  S.playing = true; setPlayIcon(true); hideWelcome();
  const advance = async () => {
    if (!S.playing || S.playToken !== token || S.fid !== fid) return;
    const next = stepEpoch(S.field, S.a, S.playbackMode, S.frame, 1);
    try { await sky.loadFrame(next); }
    catch (_) { stopTimers(); toast("Frame unavailable. Reopen the field to retry."); return; }
    if (!S.playing || S.playToken !== token || S.fid !== fid) return;
    S.frame = next; sky.draw(); timeline.update(); updateHud();
    playTimer = setTimeout(advance, 1000 / S.fps);
  };
  playTimer = setTimeout(advance, 1000 / S.fps);
}
function togglePlay() {
  if (S.cmp === "blink") { if (blinkTimer) { clearInterval(blinkTimer); blinkTimer = null; setPlayIcon(false); } else setCmp("blink"); return; }
  if (S.cmp !== "play") { setCmp("play"); return; }
  if (S.playing) { clearTimeout(playTimer); S.playing = false; setPlayIcon(false); } else startPlay();
}

async function refreshPair() {
  if (S.a == null || S.b == null || S.a === S.b) return;
  if (S.cmp === "diff") sky.setDiff(S.a, S.b);
  const fid = S.fid, first = S.a, second = S.b;
  try { const pair = await getJSON(`/api/fields/${fid}/pair?a=${first}&b=${second}`); if (S.fid !== fid || S.a !== first || S.b !== second) return; S.pair = pair; }
  catch (_) { if (S.fid !== fid || S.a !== first || S.b !== second) return; S.pair = null; }
  updateHud();
}

function updateHud() {
  const f = S.field; if (!f) return;
  const ep = (i) => f.epochs[i];
  const lab = (i, tag) => { const e = ep(i); return e ? `<b>${tag}</b> ${esc(niceDate(e.date))} · ${esc(e.date?.slice(11, 16) || "")} UTC · λ ${fmt(e.wavelength_um, 3)} µm` : ""; };
  const cur = sky.current();
  let h;
  if (cur.diff) h = `<span class="diffkey"><b>B − A</b> difference <span class="sw-o">● brighter in B</span><span class="sw-b">● brighter in A</span> qualitative</span>`;
  else if (cur.top != null) h = `${lab(S.a, "A")} &nbsp;+&nbsp; ${lab(S.b, "B")} at ${Math.round(S.opacity * 100)}%`;
  else if (S.cmp === "play") h = `<b>Timelapse</b> · real exposures only, aligned on field stars`;
  else h = lab(cur.base, cur.base === S.a ? "A" : cur.base === S.b ? "B" : "Epoch");
  $("hud-frame").innerHTML = h;

  const list = playList(), shown = cur.base ?? S.a, current = ep(shown), pos = list.indexOf(shown), previous = ep(list[pos - 1]);
  $("playback-note").textContent = S.playbackMode === "motion"
    ? `Motion: ${list.length} registered frames within 2% of A's wavelength at the field center. Elsewhere wavelength may differ. One observed frame per step; gaps are not interpolated. Frames are scaled to the reference epoch's stellar flux.`
    : `Spectral exploration: ${list.length} registered frames, changing wavelength. Brightness changes may be spectral, not temporal. One observed frame per step.`;
  if (S.cmp === "play" && !list.includes(shown)) $("playback-note").textContent += " This manually selected exposure is outside the playback wavelength filter.";
  if (cur.diff) {
    const a = ep(S.a), b = ep(S.b);
    $("epoch-readout").innerHTML = a && b ? `<span class="now-date">B − A</span><span class="now-time">${esc(a.date?.slice(0, 10))} → ${esc(b.date?.slice(0, 10))}</span>` : "";
  } else if (current) {
    const lc = lambdaColor(current.wavelength_um);
    $("epoch-readout").innerHTML = `<span class="now-date">${esc(niceDate(current.date))}</span><span class="now-time">${esc(current.date?.slice(11, 16) || "")} UTC</span>`
      + `<span class="lam-chip" style="color:${lc};border-color:${lc}55" title="Observed wavelength at the field center">λ ${fmt(current.wavelength_um, 3)} µm</span>`
      + `<span class="now-count">${pos >= 0 ? `Frame <b>${pos + 1}</b> of ${list.length}` : "Outside playback filter"}${previous ? ` · ${fmt(current.mjd - previous.mjd, current.mjd - previous.mjd < 2 ? 2 : 0)} d after previous` : ""}</span>`;
  } else $("epoch-readout").innerHTML = "";
  setPlayIcon(S.playing || !!blinkTimer);
  const notes = S.pair?.notes || [];
  const a = ep(S.a), b = ep(S.b);
  $("tl-info").innerHTML = a && b ? `A ${esc(a.date?.slice(0, 10))} → B ${esc(b.date?.slice(0, 10))} · <b>${fmt((b.mjd - a.mjd), 0)} days</b> · Δλ/λ ${fmt(100 * Math.abs(b.wavelength_um / a.wavelength_um - 1), 2)}%` +
    (notes.length ? ` · <span class="warnnote" title="${esc(notes.join(" "))}">⚠ ${esc(notes[0].split(":")[0])}</span>` : "") : "";
  $("hud-legend").innerHTML = legendHTML();
}
function legendHTML() {
  if (S.visualPreset === "clean" || !S.field) return "";
  const c = S.field.tier_counts || {};
  const ring = (col, dash) => `<svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="none" stroke="${col}" stroke-width="1.8" ${dash ? `stroke-dasharray="${dash}"` : ""}/></svg>`;
  const rows = [];
  if (S.overlays.sources) {
    if (c.significant) rows.push([ring(TIERS.significant.color), "Moving"]);
    if (c.systematics_limited) rows.push([ring(TIERS.systematics_limited.color, "3 2"), "Moving · limited precision"]);
    if (c.candidate) rows.push([ring(TIERS.candidate.color, "1.5 2"), "Candidate"]);
    if (c.rejected) rows.push([`<svg width="14" height="14"><path d="M4 4l6 6M10 4l-6 6" stroke="${TIERS.rejected.color}" stroke-width="1.8" stroke-linecap="round"/></svg>`, "Rejected"]);
  }
  if (S.overlays.refs) rows.push([ring(TIERS.stationary.color), "Reference star"]);
  if (S.overlays.tracks && (c.significant || c.systematics_limited)) rows.push([`<svg width="18" height="10"><path d="M1 5h14M11 2l4 3-4 3" fill="none" stroke="${TIERS.significant.color}" stroke-width="1.6"/></svg>`, "1 yr of motion"]);
  return rows.map(([i, t]) => `<span class="lg">${i}${t}</span>`).join("");
}

// ------------------------------------------------------------- selection
async function select(id, { center = true, keepTab = false, quiet = false } = {}) {
  if (id == null) { S.selected = null; S.detail = null; inspector.close(); sky.draw(); updateUrl(); return; }
  const fresh = S.selected !== id;
  S.selected = id;
  const fid = S.fid;
  try {
    const d = await getJSON(`/api/fields/${S.fid}/sources/${id}`);
    if (S.selected !== id || S.fid !== fid) return;
    S.detail = d;
    if (!keepTab && S.tab === "notes") S.tab = "overview";
    inspector.open(d);
    if (center && sky.k < 3) sky.centerOn(d.x, d.y, Math.max(sky.k, 3)); else sky.draw();
    if (fresh && !quiet) sky.flash();
  } catch (e) { toast(e.message); }
  updateUrl();
}

// ----------------------------------------------------------------- panels
const pmOf = (p) => p.role?.match(/(\d+(?:\.\d+)?)"\/yr/)?.[1];
export function presetCards(list, activeId) {
  return list.map((p) => `<button class="preset ${p.id === activeId ? "active" : ""}" role="listitem" data-id="${p.id}"><b>${esc(p.name)}</b>${pmOf(p) ? `<span class="pm">${pmOf(p)}″/yr</span>` : "<span></span>"}<span class="role">${esc(p.role)}</span>${p.cached ? '<span class="cached">Cached locally · opens instantly</span>' : ""}</button>`).join("");
}
function renderPresets() {
  $("presets").innerHTML = presetCards(presets, S.presetId);
  $("presets").querySelectorAll(".preset").forEach((b) => (b.onclick = () => loadPreset(presets.find((p) => p.id === b.dataset.id))));
}
function loadPreset(p) {
  $("ra").value = p.ra; $("dec").value = p.dec; $("band").value = p.band; $("size").value = String(p.size);
  S.presetId = p.id;
  document.querySelectorAll(".preset").forEach((b) => b.classList.toggle("active", b.dataset.id === p.id));
  showView("time");
  loadField({ ra: p.ra, dec: p.dec, size: p.size, band: p.band, name: p.name });
}
function renderTargetReadout() {
  const q = S.query; $("target-readout").textContent = q ? sexa(q.ra, q.dec) : "";
  $("ra").value = q.ra; $("dec").value = q.dec; $("band").value = q.band; $("size").value = String(q.size);
  const preset = presets.find((p) => Math.abs(p.ra - q.ra) < 1e-4 && Math.abs(p.dec - q.dec) < 1e-4);
  S.presetId = preset?.id;
  document.querySelectorAll(".preset").forEach((b) => b.classList.toggle("active", b.dataset.id === S.presetId));
  $("target-name").textContent = q.name || preset?.name || sexa(q.ra, q.dec);
  const usable = S.field.epochs.filter((e) => e.usable).length;
  $("target-meta").textContent = `${q.band} · ${q.size}° field · ${usable} observations`;
}
function renderProvenance() {
  const f = S.field, p = f.provenance, q = p.query || {};
  const badge = q.stale ? `<span class="badge stale">● Stale cache: IRSA unreachable</span>` : q.cached ? `<span class="badge cached">● Archive query from local cache</span>` : `<span class="badge fresh">● Fresh from IRSA</span>`;
  const usable = f.epochs.filter((e) => e.usable).length, rel = p.releases.join(" + ").toUpperCase();
  $("data-badge-text").innerHTML = `<b>Real NASA SPHEREx data</b><span class="badge-extra"> · ${usable} observations · ${esc(rel)}</span>`;
  $("provenance-panel").innerHTML = `<h3 class="pop-title">Data in view</h3>${badge}
    <div class="stat-grid"><div class="stat"><b>${usable}<span class="small"> / ${f.epochs.length}</span></b><span>usable ${esc(f.band)} images</span></div>
    <div class="stat"><b>${esc(rel)}</b><span>SPHEREx releases</span></div>
    <div class="stat"><b>${p.n_from_cache}</b><span>from local cache</span></div>
    <div class="stat"><b>${p.n_downloaded}</b><span>downloaded now</span></div></div>
    <div class="src small">Archive query ${esc(q.retrieved_utc || "")} · built in ${Object.values(f.timings).reduce((a, b) => a + b, 0).toFixed(1)} s · reference epoch ${esc(f.epochs[f.reference_index]?.date?.slice(0, 10) || "")}</div>
    ${f.failures.length ? `<div class="src small" style="color:var(--sun)">${f.failures.length} image(s) failed to download</div>` : ""}
    <p class="small">Images: NASA/IPAC Infrared Science Archive (IRSA), Level-2 spectral images. Every cutout keeps its URL, retrieval time and SHA-256.</p>
    <button class="link-btn" data-view-link="help">Provenance &amp; data sources →</button>`;
  $("provenance-panel").querySelector("[data-view-link]").onclick = () => { closePops(); showView("help"); };
}
function renderFilters() {
  const c = S.field.tier_counts;
  $("tier-filter").innerHTML = Object.entries(TIERS).filter(([k]) => k !== "insufficient").map(([k, t]) => `<button class="chip" data-tier="${k}" aria-pressed="${S.tiers.has(k)}"><span class="dot" style="background:${t.color}"></span>${t.label.split(" ·")[0]}<span class="n">${c[k] || 0}</span></button>`).join("");
  const cls = ["star", "galaxy", "quasar", "variable", "unknown"];
  $("class-filter").innerHTML = cls.map((k) => `<button class="chip" data-cls="${k}" aria-pressed="${S.classes.has(k)}" title="Catalog classification (SIMBAD type, else Gaia DR3 DSC)">${k}</button>`).join("") + `<p class="hint">Classes come from SIMBAD / Gaia DR3 <span class="tag cat">catalog</span>${S.catalogs ? "" : " (loading)"}.</p>`;
  $("tier-filter").querySelectorAll(".chip").forEach((b) => (b.onclick = () => { const k = b.dataset.tier; S.tiers.has(k) ? S.tiers.delete(k) : S.tiers.add(k); b.setAttribute("aria-pressed", S.tiers.has(k)); sky.draw(); }));
  $("class-filter").querySelectorAll(".chip").forEach((b) => (b.onclick = () => { const k = b.dataset.cls; S.classes.has(k) ? S.classes.delete(k) : S.classes.add(k); if (k === "unknown") { S.classes.has("unknown") ? S.classes.add("other") : S.classes.delete("other"); } b.setAttribute("aria-pressed", S.classes.has(k)); sky.draw(); }));
}

// ---------------------------------------------------------------- popovers
const POPS = [["target-btn", "target-pop"], ["layers-btn", "layers-pop"], ["data-badge", "provenance-panel"]];
function closePops(except) {
  for (const [b, p] of POPS) if (p !== except) { $(p).hidden = true; $(b).setAttribute("aria-expanded", "false"); }
}
for (const [b, p] of POPS) {
  $(b).onclick = (e) => {
    e.stopPropagation();
    const open = $(p).hidden; closePops(p); hideWelcome(true);
    $(p).hidden = !open; $(b).setAttribute("aria-expanded", open);
    if (open) $(p).querySelector("button, input, select")?.focus({ preventScroll: true });
  };
  $(p).addEventListener("click", (e) => e.stopPropagation());
}
document.addEventListener("click", () => closePops());

// ------------------------------------------------------------------ welcome
function maybeWelcome() {
  if (store.get("nadir:welcome") === "dismissed" || BOOT.has("notour") || !S.field || S.view !== "time") return;
  const moving = S.field.sources.find((s) => s.tier === "significant" || s.tier === "systematics_limited");
  const days = Math.round(S.field.epochs.filter((e) => e.usable).reduce((m, e) => Math.max(m, e.mjd || 0), 0) - S.field.epochs.filter((e) => e.usable).reduce((m, e) => Math.min(m, e.mjd || Infinity), Infinity));
  $("welcome-text").textContent = moving
    ? `These are ${S.field.epochs.filter((e) => e.usable).length} actual SPHEREx infrared images of one patch of sky, spanning ${days} days and aligned on the stars. Press play and watch ${displayName(moving)} move.`
    : `These are actual SPHEREx infrared images of one patch of sky, aligned on the stars. Press play to step through every observation.`;
  $("welcome").hidden = false;
}
function hideWelcome(remember = false) { $("welcome").hidden = true; if (remember) store.set("nadir:welcome", "dismissed"); }
$("welcome-play").onclick = () => { hideWelcome(true); setCmp("play"); };
$("welcome-tour").onclick = () => { hideWelcome(true); startTour(); };
$("welcome-dismiss").onclick = () => hideWelcome(true);
// any real interaction with the sky retires the welcome card for good
$("sky").addEventListener("pointerdown", () => { if (!$("welcome").hidden) hideWelcome(true); });
$("sky").addEventListener("wheel", () => { if (!$("welcome").hidden) hideWelcome(true); }, { passive: true });

// ------------------------------------------------------------------ views
function sheet(html) { $("sheet").innerHTML = `<div class="sheet-inner">${html}</div>`; $("sheet").scrollTop = 0; }
function showView(v) {
  S.view = v; closePops();
  document.querySelectorAll("[data-view]").forEach((b) => { b.classList.toggle("active", b.dataset.view === v); b.setAttribute("aria-pressed", b.dataset.view === v); });
  $("viewport").hidden = v !== "time"; $("explore").hidden = v !== "explore"; $("sheet").hidden = !["discover", "learn", "help"].includes(v);
  $("timeline").hidden = v !== "time";
  $("stage").style.gridTemplateRows = v === "time" ? "" : "minmax(0, 1fr)";
  if (v === "explore") { explorer ??= new Explorer(cfg, presets, (p) => loadPreset(p)); explorer.show(); }
  if (v !== "time") stopTimers();
  if (v === "discover") {
    sheet(discoverHTML());
    $("sheet").querySelectorAll("[data-sid]").forEach((button) => (button.onclick = () => { showView("time"); select(+button.dataset.sid); }));
    $("sheet").querySelectorAll("[data-discovery-tier]").forEach((button) => (button.onclick = () => {
      const tier = button.dataset.discoveryTier; S.tiers.has(tier) ? S.tiers.delete(tier) : S.tiers.add(tier); renderFilters(); showView("discover");
    }));
  }
  if (v === "learn") renderLearn();
  if (v === "help") { sheet(helpHTML(cfg)); $("sheet").querySelectorAll("[data-tour]").forEach((b) => (b.onclick = () => { showView("time"); startTour(); })); }
  if (v === "time") requestAnimationFrame(() => { sky.resize(); timeline.render(); });
  updateUrl();
}
function renderLearn(focus) {
  sheet(learnHTML(focus));
  const r = $("wl-demo");
  if (r) {
    const upd = () => { const w = CUBE_WL[+r.value]; const band = w < 1.1 ? "D1" : w < 1.63 ? "D2" : w < 2.42 ? "D3" : w < 3.83 ? "D4" : w < 4.42 ? "D5" : "D6"; $("wl-out").textContent = `${sliceLabel(+r.value)} · detector ${band}`; $("wl-out").style.color = lambdaColor(w); };
    r.oninput = upd; upd();
    $("wl-explore").onclick = () => { $("slice").value = r.value; showView("explore"); explorer?.setSlice(+r.value); };
  }
  $("sheet").querySelectorAll("[data-learn-go]").forEach((b) => (b.onclick = () => { showView("time"); if (b.dataset.learnGo === "tour") startTour(); }));
  if (focus) $(`learn-${focus}`)?.scrollIntoView({ block: "center" });
}

// ------------------------------------------------------------------- tour
const TOUR = [
  { h: "Two moments, one patch of sky", t: () => `These are two real SPHEREx infrared images of the same field, taken ${fmt((S.field.epochs[S.b]?.mjd - S.field.epochs[S.a]?.mjd) / 30.44, 0)} months apart, blinking. Every image is aligned on the field stars to a fraction of an arcsecond.`, act: () => { setCmp("blink"); sky.fit(); } },
  { h: "One star refuses to stay put", t: () => { const s = S.field.sources.find((x) => x.tier === "systematics_limited" || x.tier === "significant"); return s ? `Watch the star in the amber brackets: it jumps while its ~${S.field.sources.length - 1} neighbours stay put. That is ${displayName(s)}, moving ${fmt(s.fit?.pm_total, 1)}″ per year. The arrow shows one year of its measured motion at true scale.` : "In this field no source shows confirmed motion — the normal, honest outcome for most of the sky."; }, act: () => { const s = S.field.sources.find((x) => x.tier === "systematics_limited" || x.tier === "significant"); if (s) { select(s.id, { center: false }); sky.centerOn(s.x, s.y, Math.max(sky.k, 3.2)); } } },
  { h: "The evidence, not just the answer", t: () => { const d = S.detail; return `The Motion tab plots all ${d?.n_epochs ?? "the"} measurements across ${d?.n_visits ?? "several"} visits, NADIR's fit (amber) and Gaia's independent prediction (blue dashes). ${d?.tier === "systematics_limited" ? "Note the honest label: this star saturates the detector, so the result is systematics-limited." : "The evidence tier says how far the measurement can be trusted."}`; }, act: () => inspector.tab("motion") },
  { h: "Rigor over excitement", t: () => { const r = S.field.sources.filter((x) => x.tier === "rejected"); return r.length ? `${r.length} other source${r.length > 1 ? "s" : ""} passed the classic two-image test (${r.map((x) => fmt(x.pair?.significance, 1) + "σ").join(", ")}), but the other visits do not reproduce ${r.length > 1 ? "them" : "it"}. NADIR rejects ${r.length > 1 ? "them" : "it"} (coral ×) instead of announcing a discovery.` : "Every candidate must be reproduced across independent visits before NADIR calls it motion; in this field no two-image detection was contradicted."; }, act: () => { const s = S.field.sources.find((x) => x.tier === "rejected"); if (s) { select(s.id, { center: false }); sky.centerOn(s.x, s.y, Math.max(sky.k, 3.2)); } } },
  { h: "Everything is traceable", t: "The Observations tab lists each image, its wavelength and the exact NASA archive request behind it. Open “Sky atlas” to explore the whole SPHEREx sky at 102 wavelengths.", act: () => { const s = S.field.sources.find((x) => x.tier === "systematics_limited" || x.tier === "significant"); if (s) select(s.id, { center: false, keepTab: true }).then(() => inspector.tab("obs")); else inspector.tab("obs"); } },
];
let tourStep = -1;
function startTour() { if (!S.field) { toast("The demo is still loading."); return; } hideWelcome(true); showView("time"); tourStep = -1; nextTour(); }
function nextTour() {
  tourStep++;
  const el = $("tour");
  if (tourStep >= TOUR.length) { el.hidden = true; return; }
  const s = TOUR[tourStep]; s.act?.();
  const text = typeof s.t === "function" ? s.t() : s.t;
  el.hidden = false;
  el.innerHTML = `<div class="step">GUIDED DEMO <span class="pips">${TOUR.map((_, i) => `<i class="${i <= tourStep ? "on" : ""}"></i>`).join("")}</span> ${tourStep + 1} / ${TOUR.length}</div><h4>${esc(s.h)}</h4><p>${esc(text)}</p><div class="row"><button class="btn ghost sm" id="tour-end">End tour</button><button class="btn accent sm" id="tour-next">${tourStep === TOUR.length - 1 ? "Finish" : "Next"}</button></div>`;
  $("tour-next").onclick = nextTour; $("tour-end").onclick = () => (el.hidden = true);
  $("tour-next").focus({ preventScroll: true });
}

// -------------------------------------------------------------- URL state
function updateUrl() {
  if (!S.query) return;
  const p = new URLSearchParams({ ra: S.query.ra, dec: S.query.dec, size: S.query.size, band: S.query.band });
  if (S.a != null) p.set("a", S.a); if (S.b != null) p.set("b", S.b);
  p.set("mode", S.cmp); if (S.selected != null) p.set("src", S.selected); if (S.view !== "time") p.set("view", S.view);
  if (S.selected != null && S.tab !== "overview") p.set("tab", S.tab);
  if (S.render !== "presentation") p.set("render", S.render);
  history.replaceState(null, "", "?" + p.toString());
}
function parseCoords(text) {
  const m = text.trim().match(/^([+-]?\d+(?:\.\d+)?)[\s,]+([+-]?\d+(?:\.\d+)?)$/);
  return m ? { ra: +m[1], dec: +m[2] } : null;
}
function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 4500); }

// ---------------------------------------------------------------- events
on("select", (id) => { if (!journey.active) select(id); });
on("journey", (id) => startJourney(id));
on("setA", (i) => { if (!S.field.epochs[i]?.usable) return; S.a = i; if (S.a === S.b) S.b = S.field.epochs.find(epoch => epoch.usable && epoch.index !== i)?.index; afterPairChange(); });
on("setB", (i) => { if (!S.field.epochs[i]?.usable || i === S.a) return; S.b = i; if (!["b", "blink", "overlay", "diff"].includes(S.cmp)) S.cmp = "b"; afterPairChange(); setCmp(S.cmp); });
on("frame", (i) => { if (!S.field?.epochs[i]?.usable) return; stopTimers(); S.cmp = "play"; S.frame = i; document.querySelectorAll("[data-cmp]").forEach(button => { button.classList.toggle("active", button.dataset.cmp === "play"); button.setAttribute("aria-pressed", button.dataset.cmp === "play"); }); $("opacity-ctl").hidden = true; sky.loadFrame(i); sky.draw(); timeline.update(); updateHud(); updateUrl(); });
on("epochUnavailable", (epoch) => toast(`Observation unavailable: ${epoch.unusable_reason || "missing timestamp or registration"}. See Observations for provenance.`));
on("cursor", ({ ra, dec, inside, hit }) => { $("hud-cursor").textContent = inside ? cursorText(ra, dec) + (hit ? `  ·  ${displayName(hit)}` : "") : ""; });
on("learn", (k) => { showView("learn"); renderLearn(k); });
on("urlchange", updateUrl);
on("refreshTab", () => inspector.tab(S.tab));
function afterPairChange() { timeline.render(); refreshPair(); sky.draw(); updateHud(); updateUrl(); }

document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
document.querySelectorAll("[data-cmp]").forEach((b) => (b.onclick = () => { hideWelcome(); setCmp(b.dataset.cmp); }));
$("play").onclick = () => { hideWelcome(true); togglePlay(); };
$("speed").oninput = (e) => { S.fps = +e.target.value; $("speed-out").textContent = `${S.fps}/s`; if (S.cmp === "play" && S.playing) startPlay(); if (S.cmp === "blink") setCmp("blink"); };
$("opacity").oninput = (e) => { S.opacity = +e.target.value / 100; sky.draw(); updateHud(); };
$("playback-mode").onchange = (event) => { S.playbackMode = event.target.value; S.matchedOnly = S.playbackMode === "motion"; if (S.playing) startPlay(); timeline.render(); updateHud(); };
function stepFrame(direction) { const next = stepEpoch(S.field, S.a, S.playbackMode, sky.current().base ?? S.frame, direction); if (next != null) emit("frame", next); }
$("step-prev").onclick = () => stepFrame(-1); $("step-next").onclick = () => stepFrame(1);
$("frame-a").onclick = () => emit("setA", sky.current().base ?? S.frame);
$("frame-b").onclick = () => emit("setB", sky.current().base ?? S.frame);
$("dock-expand").onclick = () => { const open = $("dock-detail").hidden; $("dock-detail").hidden = !open; $("dock-expand").setAttribute("aria-expanded", open); if (open) timeline.update(); };
$("start-tour").onclick = () => startTour();

const RENDER_HINT = {
  presentation: "Display only: isolated flagged pixels are filled from neighbours, a tone curve is applied and pixels are smoothly resampled. This does not add resolution; measurements always use the original pixels.",
  science: "The sampled pixels exactly as registered (3.1″ grid, nearest-neighbour). Navy = flagged / no data, white core = saturated. Pixel boundaries appear when zoomed in.",
};
function setRender(mode) {
  S.render = mode;
  document.querySelectorAll("[data-render]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.render === mode));
  $("render-hint").textContent = RENDER_HINT[mode];
  sky.draw(); updateUrl();
}
document.querySelectorAll("[data-render]").forEach((b) => (b.onclick = () => setRender(b.dataset.render)));

const PRESET_OVERLAYS = {
  clean: { sources: false, refs: false, tracks: false, grid: false, simbad: false, gaia: false, twomass: false, allwise: false },
  discovery: { sources: true, refs: false, tracks: true, grid: true, simbad: true, gaia: false, twomass: false, allwise: false },
  research: { sources: true, refs: true, tracks: true, grid: true, simbad: true, gaia: true, twomass: true, allwise: true },
};
function setVisual(preset) {
  S.visualPreset = preset;
  Object.assign(S.overlays, PRESET_OVERLAYS[preset]);
  document.querySelectorAll("[data-ov]").forEach(input => { input.checked = S.overlays[input.dataset.ov]; });
  document.querySelectorAll("[data-visual]").forEach(button => button.setAttribute("aria-pressed", button.dataset.visual === preset));
  sky.draw(); updateHud();
}
document.querySelectorAll("[data-visual]").forEach(button => { button.onclick = () => setVisual(button.dataset.visual); });
$("zoom-in").onclick = () => { hideWelcome(true); sky.zoomAt(1.4); }; $("zoom-out").onclick = () => { hideWelcome(true); sky.zoomAt(1 / 1.4); }; $("zoom-fit").onclick = () => sky.fit();
document.querySelectorAll("[data-ov]").forEach((cb) => (cb.onchange = () => { S.overlays[cb.dataset.ov] = cb.checked; S.visualPreset = "custom"; document.querySelectorAll("[data-visual]").forEach(button => button.setAttribute("aria-pressed", "false")); sky.draw(); updateHud(); }));
$("coord-form").onsubmit = (e) => {
  e.preventDefault();
  const ra = parseFloat($("ra").value), dec = parseFloat($("dec").value);
  if (!(ra >= 0 && ra < 360) || !(dec >= -90 && dec <= 90)) return toast("RA must be in [0, 360) and Dec in [−90, 90] decimal degrees.");
  showView("time"); loadField({ ra, dec, size: +$("size").value, band: $("band").value });
};
$("quick-search").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("q").value.trim(); if (!text) return;
  $("q").blur();
  const c = parseCoords(text);
  if (c) { if (c.ra < 0 || c.ra >= 360 || Math.abs(c.dec) > 90) return toast("Coordinates out of range: RA 0–360°, Dec −90–90°."); showView("time"); return loadField({ ...c, size: +$("size").value, band: $("band").value }); }
  const pre = presets.find((p) => p.name.toLowerCase().includes(text.toLowerCase()));
  if (pre) return loadPreset(pre);
  toast(`Resolving “${text}” with SIMBAD…`);
  try {
    const r = await getJSON(`/api/resolve?name=${encodeURIComponent(text)}&epoch=2026.0`);
    toast(`${r.name.replace(/^NAME /, "")}: ${fmt(r.ra, 4)}°, ${fmt(r.dec, 4)}°${r.epoch !== 2000 ? " (moved from J2000 with its proper motion)" : ""}`);
    showView("time"); loadField({ ra: +r.ra.toFixed(5), dec: +r.dec.toFixed(5), size: +$("size").value, band: $("band").value, name: r.name.replace(/^NAME /, "") });
  } catch (err) { toast(err instanceof ApiError ? err.message : "Name resolution failed"); }
};
$("slice").oninput = (e) => explorer?.setSlice(+e.target.value);
$("explore-go").onclick = () => { const p = explorer?.picked; if (!p) return; showView("time"); loadField({ ra: +p.ra.toFixed(5), dec: +p.dec.toFixed(5), size: +$("size").value, band: $("band").value }); };
document.addEventListener("keydown", (e) => {
  if (journey.active) return; // the journey owns the keyboard while it plays
  if (e.key === "Escape" && POPS.some(([, p]) => !$(p).hidden)) { closePops(); return; }
  if (e.target.closest("input, textarea, select")) { if (e.key === "Escape") e.target.blur(); return; }
  if (e.key === "/") { e.preventDefault(); $("q").focus(); return; }
  if (!S.field || S.view !== "time" || !$("intro").hidden || e.metaKey || e.ctrlKey) return;
  const usable = S.field.epochs.filter((x) => x.usable).map((x) => x.index);
  if (e.key === " ") { if (e.target.closest("button")) return; e.preventDefault(); setCmp(S.cmp === "blink" ? "a" : "blink"); }
  else if (e.key === "d" || e.key === "D") setCmp("diff");
  else if (e.key === "p" || e.key === "P") setCmp("play");
  else if (e.key === "a" || e.key === "A") setCmp("a");
  else if (e.key === "b" || e.key === "B") setCmp("b");
  else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && !e.shiftKey && document.activeElement !== $("sky") && !e.target.closest(".tabs, .tl-track, .visit-strip")) {
    const list = S.cmp === "play" ? playList() : usable.filter((i) => i !== S.a), cur = S.cmp === "play" ? S.frame : S.b;
    const k = list.indexOf(cur), n = list[(k + (e.key === "ArrowRight" ? 1 : -1) + list.length) % list.length];
    S.cmp === "play" ? emit("frame", n) : emit("setB", n);
  } else if (e.key === "Escape") { $("tour").hidden = true; select(null); }
});

// ------------------------------------------------------------------- boot
async function boot() {
  try { await loadConfig(); }
  catch (e) { showStartupError(e); return; }
  const q = BOOT;
  if (q.get("render") === "science") setRender("science"); else setRender("presentation");
  if (q.has("ra") && q.has("dec")) {
    const query = { ra: +q.get("ra"), dec: +q.get("dec"), size: +(q.get("size") || 0.2), band: q.get("band") || "D2" };
    if (["overview", "motion", "spectrum", "obs", "notes"].includes(q.get("tab"))) S.tab = q.get("tab");
    firstLoad = q.get("tour") === "1";
    await loadField(query, { a: q.has("a") ? +q.get("a") : undefined, b: q.has("b") ? +q.get("b") : undefined, mode: q.get("mode") || undefined, src: q.get("src") ?? undefined, tour: q.get("tour") === "1", keepTab: q.has("tab") });
    if (q.get("view") && q.get("view") !== "time") showView(q.get("view"));
  } else loadPreset(presets.find((p) => p.demo) || presets[0]);
}
// --------------------------------------------------------------- journeys
// NADIR Journeys play over the observatory and put everything back on exit.
// Scenes reach the app only through this context object (see journey/journeys.js).
const journey = new JourneyEngine({ hold: () => journeyUI.speaking() });
const journeyUI = new JourneyUI(journey, { reduced: REDUCED, onExit: () => leaveJourney(), onAction: (a) => journeyAction(a) });
const lessons = new Lessons($("lessons"));
let journeySnap = null, journeyCtx = null, journeyThen = null;
const spectralReady = new Map(); // fid -> Promise (cached-only spectral images, never a fresh download)

function ensureSpectral(fid) {
  if (S.field?.fid === fid && S.field.spectral_ready) return Promise.resolve();
  if (!spectralReady.has(fid)) {
    const p = (async () => {
      const r = await postJSON(`/api/fields/${fid}/spectral?cached_only=true`);
      if (r.job) await pollJob(r.job, { interval: 500 });
      if (S.field?.fid === fid) S.field.spectral_ready = true;
    })();
    p.catch(() => spectralReady.delete(fid));
    spectralReady.set(fid, p);
  }
  return spectralReady.get(fid);
}

function makeJourneyCtx(d, subject) {
  const fid = S.fid, f = S.field, notesKey = `nadir:notes:${d.ra.toFixed(4)},${d.dec.toFixed(4)}`;
  const live = () => journey.active && S.fid === fid;
  return {
    field: f, d, subject, anchor: S.a, name: displayName(d), reduced: REDUCED,
    frame(i) { if (!live() || !f.epochs[i]?.usable) return; S.cmp = "play"; S.frame = i; sky.loadFrame(i).catch(() => {}); sky.draw(); },
    loadFrames: (ix) => Promise.all(ix.map((i) => sky.loadFrame(i))),
    highlightFrame: (i) => journeyUI.highlightFrame(i),
    fly(x, y, k, ms) { if (!live()) return; const b = journeyUI.freeBox(); sky.flyTo(x, y, k, ms, b.ax, b.ay); },
    fit(ms) { if (!live()) return; const b = journeyUI.freeBox(), g = f.grid; sky.flyTo((g.nx - 1) / 2, (g.ny - 1) / 2, Math.min(b.w / g.nx, b.h / g.ny) * 0.96, ms, b.ax, b.ay); },
    kFor(arcsec) { const b = journeyUI.freeBox(); return sky.kForSpan(arcsec, Math.min(b.w, b.h)); },
    layer(patch) { if (patch === undefined) return sky.journey; if (!live()) return; sky.journey = { ...sky.journey, ...patch }; sky.draw(); },
    reveal(key, ms) {
      const o = sky.journey?.[key]; if (!o || !live()) return;
      const now = performance.now(), dur = REDUCED ? 0 : ms;
      sky.journey = { ...sky.journey, [key]: { ...o, hidden: false, t0: now, ms: dur } };
      sky.animateUntil(now + dur + 40);
    },
    reset() { if (live()) sky.journey = {}; },
    async spectrum(id) { await ensureSpectral(fid); return getJSON(`/api/fields/${fid}/sources/${id}/spectrum`); },
    source: (id) => getJSON(`/api/fields/${fid}/sources/${id}`),
    // Stage B: the same instruments, driven on the live observatory
    app: {
      notesKey,
      center: () => { sky.centerOn(subject.x, subject.y, Math.max(sky.k, 4)); },
      compare: (a, b, mode) => { if (S.fid !== fid) return; S.a = a; S.b = b; afterPairChange(); setCmp(mode); },
      tab: (t) => { if (S.selected !== subject.id) { S.tab = t; select(subject.id, { center: false, keepTab: true }); } else inspector.tab(t); revealInspector(); },
      overlays: (patch) => { Object.assign(S.overlays, patch); S.visualPreset = "custom"; document.querySelectorAll("[data-ov]").forEach((i) => { i.checked = S.overlays[i.dataset.ov]; }); document.querySelectorAll("[data-visual]").forEach((b) => b.setAttribute("aria-pressed", "false")); sky.draw(); updateHud(); },
      select: (id, tab) => { if (tab) S.tab = tab; select(id, { keepTab: true }).then(revealInspector); },
    },
  };
}
function revealInspector() { if (matchMedia("(max-width: 1024px)").matches) $("inspector").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" }); }

async function startJourney(id = S.selected) {
  if (!S.field || id == null || journey.active) return;
  const subject = S.field.sources.find((s) => s.id === id);
  if (!subject) return;
  let d = S.detail?.id === id ? S.detail : null;
  try { d ??= await getJSON(`/api/fields/${S.fid}/sources/${id}`); } catch (e) { toast(`Journey unavailable: ${e.message}`); return; }
  stopTimers(); closePops(); hideWelcome(); $("tour").hidden = true; lessons.close();
  journeySnap = { fid: S.fid, selected: S.selected, tab: S.tab, cmp: S.cmp, a: S.a, b: S.b, frame: S.frame, overlays: { ...S.overlays }, visual: S.visualPreset,
    view: { k: sky.k, ox: sky.ox, oy: sky.oy, userMoved: sky.userMoved }, focus: document.activeElement, scrollY: window.scrollY };
  Object.assign(S.overlays, PRESET_OVERLAYS.clean);
  window.scrollTo(0, 0); // stacked (tablet/mobile) layouts scroll; the show is full-screen
  document.body.classList.add("journey-on");
  sky.journey = {}; sky.resize();
  journeyCtx = makeJourneyCtx(d, subject); journeyThen = null;
  try { journey.load(journeyFor(d), journeyCtx).start(); }
  catch (e) { toast(e.message); journey.stop("error"); }
}

/** Restore the observatory exactly as it was (selection, tab, mode, overlays, camera). */
function leaveJourney() {
  const snap = journeySnap; journeySnap = null;
  document.body.classList.remove("journey-on");
  sky.journey = null; sky._fly = null;
  if (snap && S.fid === snap.fid) {
    Object.assign(S.overlays, snap.overlays); S.visualPreset = snap.visual;
    S.a = snap.a; S.b = snap.b; S.frame = snap.frame; S.tab = snap.tab;
    sky.resize();
    Object.assign(sky, snap.view); if (!snap.view.userMoved) sky.fit();
    if (snap.cmp === "play") { // restore the paused timelapse frame without starting playback
      S.cmp = "play"; $("opacity-ctl").hidden = true;
      document.querySelectorAll("[data-cmp]").forEach((btn) => { btn.classList.toggle("active", btn.dataset.cmp === "play"); btn.setAttribute("aria-pressed", btn.dataset.cmp === "play"); });
    } else { S.cmp = null; setCmp(snap.cmp); }
    if (S.selected !== snap.selected) select(snap.selected, { center: false, keepTab: true, quiet: true });
    else if (S.detail) inspector.tab(S.tab);
    timeline.render(); sky.draw(); updateHud(); updateUrl();
    window.scrollTo(0, snap.scrollY || 0);
  } else sky.resize();
  const then = journeyThen; journeyThen = null;
  if (then) then(); else (snap?.focus?.isConnected ? snap.focus : $("sky")).focus?.({ preventScroll: true });
}

function journeyAction(action) {
  const ctx = journeyCtx;
  if (action === "explore") journeyThen = () => { lessons.open(journey, ctx); requestAnimationFrame(() => $("lessons").querySelector("button.primary, button")?.focus({ preventScroll: true })); };
  else if (action === "evidence") journeyThen = () => { ctx.app.tab(ctx.d.fit && ctx.d.tier !== "stationary" ? "motion" : "overview"); requestAnimationFrame(() => document.querySelector(".tabs .active")?.focus({ preventScroll: true })); };
  journey.stop(action);
}

window.NADIR = { S, sky, timeline, journey, startJourney }; // debugging / scripted demos
intro();
boot();
