// Project NADIR frontend entry point: presentation only — all science is server-side.
import { S, TIERS, on, emit } from "./state.js";
import { getJSON, postJSON, pollJob, ApiError } from "./api.js";
import { esc, fmt, dateTime, sexa } from "./fmt.js";
import { SkyView, cursorText } from "./sky.js";
import { Timeline } from "./timeline.js";
import { Inspector, displayName } from "./inspector.js";
import { discoverHTML, learnHTML, helpHTML, LEARN } from "./sheets.js";
import { Explorer, label as sliceLabel, CUBE_WL } from "./explore.js";

const $ = (id) => document.getElementById(id);
const BOOT = new URLSearchParams(location.search); // read once: updateUrl() rewrites the query string
const sky = new SkyView($("sky"));
const timeline = new Timeline($("tl-track"));
const inspector = new Inspector();
let cfg = {}, presets = [], explorer = null, loadCtl = null, blinkTimer = null, playTimer = null, firstLoad = true;

// ------------------------------------------------------------------ intro
function intro() {
  const el = $("intro"); let seen = false;
  try { seen = sessionStorage.getItem("nadir:intro") === "1"; } catch (_) {}
  if (seen || BOOT.has("nointro")) return;
  el.hidden = false;
  const done = () => { el.hidden = true; try { sessionStorage.setItem("nadir:intro", "1"); } catch (_) {} document.removeEventListener("keydown", key, true); };
  // capture phase + stopImmediatePropagation: skipping the intro must not also trigger app shortcuts
  const key = (e) => { if (["Escape", "Enter", " "].includes(e.key)) { e.preventDefault(); e.stopImmediatePropagation(); done(); } };
  $("intro-skip").onclick = done; el.onclick = done; document.addEventListener("keydown", key, true);
  setTimeout(done, 4200);
}

// ---------------------------------------------------------------- loading
const STAGE_TEXT = { discover: "Finding SPHEREx observations", download: "Downloading image cutouts", read: "Reading FITS data", detect: "Detecting & measuring sources",
  register: "Aligning epochs on reference stars", track: "Following sources through time", render: "Preparing comparison frames" };

function showLoader(snap, q) {
  const el = $("loader"); el.hidden = false;
  const stages = snap?.stages || Object.keys(STAGE_TEXT), idx = snap?.stage_index ?? 0;
  const det = snap && snap.total > 0, pct = det ? Math.round(100 * snap.done / snap.total) : 0;
  let li = stages.map((s, i) => `<li class="${i < idx || snap?.status === "done" ? "done" : i === idx ? "active" : ""}"><span class="st">${i < idx ? "✓" : ""}</span>${esc(STAGE_TEXT[s] || s)}<span class="t">${snap?.stage_times?.[s] != null ? snap.stage_times[s].toFixed(1) + "s" : i === idx && det ? `${snap.done}/${snap.total}` : ""}</span></li>`).join("");
  const extra = snap?.stage === "download" && snap.detail?.cached != null ? ` · ${snap.detail.cached} from local cache, ${snap.done - snap.detail.cached} fresh from IRSA` : "";
  el.innerHTML = `<div class="loader-card"><h3>Observing ${esc(q.name || `${fmt(q.ra, 4)}°, ${fmt(q.dec, 4)}°`)}</h3>
    <div class="sub">SPHEREx ${esc(q.band)} · ${q.size}° field · ${snap ? `${snap.elapsed_s.toFixed(1)} s` : "starting"}</div>
    <ol class="stages">${li}</ol><div class="bar ${det ? "" : "indet"}"><i style="width:${pct}%"></i></div>
    <div class="loader-msg">${esc(snap?.message || "Contacting the NADIR server…")}${extra}</div>
    <div class="loader-actions"><button class="btn ghost" id="ld-cancel">Cancel</button></div></div>`;
  $("ld-cancel").onclick = () => { loadCtl?.abort(); hideLoader(); if (!S.field) loadPreset(presets.find((p) => p.demo) || presets[0]); };
}
function hideLoader() { $("loader").hidden = true; }
function showError(msg, q) {
  const el = $("loader"); el.hidden = false;
  el.innerHTML = `<div class="loader-card error"><h3>Could not build this field</h3><div class="sub">${esc(q.name || `${fmt(q.ra, 4)}°, ${fmt(q.dec, 4)}°`)} · ${esc(q.band)}</div>
    <p>${esc(msg)}</p><p class="small">Nothing has been changed${S.field ? "; the previous field is still loaded" : ""}. Archive problems are usually temporary.</p>
    <div class="loader-actions">${S.field ? '<button class="btn ghost" id="er-close">Keep previous field</button>' : ""}<button class="btn" id="er-demo">Open cached Barnard's Star demo</button><button class="btn primary" id="er-retry">Retry</button></div></div>`;
  $("er-retry").onclick = () => loadField(q);
  $("er-demo").onclick = () => loadPreset(presets.find((p) => p.demo) || presets[0]);
  if ($("er-close")) $("er-close").onclick = hideLoader;
}

async function loadField(q, opts = {}) {
  loadCtl?.abort(); // supersede any running request (server job is cancelled too)
  const ctl = new AbortController(); loadCtl = ctl;
  stopTimers();
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
    for (const e of f.epochs) if (e.usable) sky.loadFrame(e.index); // preload all registered frames
    renderProvenance(); renderFilters(); renderTargetReadout();
    hideLoader();
    setCmp(opts.mode || (firstLoad ? "blink" : S.cmp === "play" ? "a" : S.cmp));
    refreshPair();
    const target = opts.src != null ? f.sources.find((s) => s.id === +opts.src) : nearestTarget(f);
    if (target) await select(target.id, { center: false });
    else inspector.close();
    loadCatalogs(r.fid);
    if (firstLoad && opts.tour !== false && !BOOT.has("notour")) startTour();
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
    $("catalog-status").innerHTML = esc(parts.join(" · ")) + (errs.length ? `<br><span style="color:var(--amber)">unavailable: ${esc(errs.map(([k]) => k).join(", "))}</span>` : "");
    renderFilters();
    if (S.selected != null) await select(S.selected, { center: false, keepTab: true });
    sky.draw();
  } catch (e) { $("catalog-status").textContent = `Catalogs unavailable (${e.message}). SPHEREx measurements are unaffected.`; }
}

// ------------------------------------------------------------- comparison
function setCmp(mode) {
  if (mode === "play" && S.cmp === "play") { togglePlay(); return; }
  S.cmp = mode; stopTimers();
  document.querySelectorAll("[data-cmp]").forEach((b) => b.classList.toggle("active", b.dataset.cmp === mode));
  $("opacity-ctl").hidden = mode !== "overlay";
  if (mode === "blink") blinkTimer = setInterval(() => { S.blinkOn = !S.blinkOn; sky.draw(); updateHud(); }, Math.max(150, 1000 / Math.max(1, S.fps / 2.5)));
  if (mode === "diff") sky.setDiff(S.a, S.b);
  if (mode === "play") { S.frame = S.a; S.playing = true; startPlay(); }
  $("play").textContent = S.playing || mode === "blink" ? "❚❚" : "▶";
  sky.draw(); timeline.render(); updateHud(); updateUrl();
}
function stopTimers() { clearInterval(blinkTimer); clearInterval(playTimer); blinkTimer = playTimer = null; S.playing = false; $("play").textContent = "▶"; }
function playList() {
  const a = S.field.epochs[S.a]?.wavelength_um;
  return S.field.epochs.filter((e) => e.usable && (!S.matchedOnly || Math.abs(e.wavelength_um / a - 1) <= 0.02)).map((e) => e.index);
}
function startPlay() {
  clearInterval(playTimer); S.playing = true; $("play").textContent = "❚❚";
  playTimer = setInterval(() => { const L = playList(); const i = L.indexOf(S.frame); S.frame = L[(i + 1) % L.length]; sky.draw(); timeline.render(); updateHud(); }, 1000 / S.fps);
}
function togglePlay() {
  if (S.cmp === "blink") { if (blinkTimer) { clearInterval(blinkTimer); blinkTimer = null; $("play").textContent = "▶"; } else setCmp("blink"); return; }
  if (S.cmp !== "play") { setCmp("play"); return; }
  if (S.playing) { clearInterval(playTimer); S.playing = false; $("play").textContent = "▶"; } else startPlay();
}

async function refreshPair() {
  if (S.a == null || S.b == null || S.a === S.b) return;
  if (S.cmp === "diff") sky.setDiff(S.a, S.b);
  try { S.pair = await getJSON(`/api/fields/${S.fid}/pair?a=${S.a}&b=${S.b}`); } catch (_) { S.pair = null; }
  updateHud();
}

function updateHud() {
  const f = S.field; if (!f) return;
  const ep = (i) => f.epochs[i];
  const lab = (i, tag) => { const e = ep(i); return e ? `<b>${tag}</b> ${esc(dateTime(e.date))} UTC · λ ${fmt(e.wavelength_um, 3)} µm · ${esc(e.release)}` : ""; };
  let h;
  const cur = sky.current();
  if (cur.diff) h = `<b>B − A</b> difference · orange = brighter in B, blue = brighter in A`;
  else if (cur.top != null) h = `${lab(S.a, "A")}<br>${lab(S.b, "B")} at ${Math.round(S.opacity * 100)}% opacity`;
  else h = lab(cur.base, cur.base === S.a ? "A" : cur.base === S.b ? "B" : `Epoch`);
  if (S.cmp === "play") h += `<br><span class="small">Frames are aligned on field stars and scaled to the reference epoch's stellar flux; differing wavelengths change relative brightness.</span>`;
  $("hud-frame").innerHTML = h;
  const notes = S.pair?.notes || [];
  const a = ep(S.a), b = ep(S.b);
  $("tl-info").innerHTML = a && b ? `A ${esc(a.date?.slice(0, 10))} → B ${esc(b.date?.slice(0, 10))} · <b>${fmt((b.mjd - a.mjd), 0)} days</b> · Δλ/λ ${fmt(100 * Math.abs(b.wavelength_um / a.wavelength_um - 1), 2)}%` +
    (notes.length ? ` · <span style="color:var(--amber)" title="${esc(notes.join(" "))}">⚠ ${esc(notes[0].split(":")[0])}</span>` : "") : "";
  $("hud-legend").innerHTML = legendHTML();
}
function legendHTML() {
  const ring = (c, dash) => `<svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="none" stroke="${c}" stroke-width="1.6" ${dash ? `stroke-dasharray="${dash}"` : ""}/></svg>`;
  const rows = [[ring("#ffb547"), "Significant motion"], [ring("#ffb547", "3 2"), "Motion, systematics-limited"], [ring("#a98bff", "1.5 2"), "Low-confidence candidate"],
    [`<svg width="14" height="14"><path d="M3 3l8 8M11 3l-8 8" stroke="#ff6f6f" stroke-width="1.6"/></svg>`, "Rejected (not reproduced)"], [ring("#4fd1b5"), "Stationary reference"]];
  return rows.map(([i, t]) => `<div class="lg">${i}${t}</div>`).join("") + `<div class="lg small">Arrows: 1 year of measured motion, true scale<br>White core = saturated pixels · navy = flagged/no data</div>`;
}

// ------------------------------------------------------------- selection
async function select(id, { center = true, keepTab = false } = {}) {
  if (id == null) { S.selected = null; S.detail = null; inspector.close(); sky.draw(); updateUrl(); return; }
  S.selected = id;
  try {
    const d = await getJSON(`/api/fields/${S.fid}/sources/${id}`);
    if (S.selected !== id) return;
    S.detail = d;
    if (!keepTab && S.tab === "notes") S.tab = "overview";
    inspector.open(d);
    if (center && sky.k < 3) sky.centerOn(d.x, d.y, Math.max(sky.k, 3)); else sky.draw();
  } catch (e) { toast(e.message); }
  updateUrl();
}

// ----------------------------------------------------------------- panels
function renderPresets() {
  $("presets").innerHTML = presets.map((p) => `<button class="preset" role="listitem" data-id="${p.id}"><b>${esc(p.name)}</b><span>${esc(p.role)}</span>${p.cached ? '<span class="cached">● cached locally — loads offline</span>' : ""}</button>`).join("");
  $("presets").querySelectorAll(".preset").forEach((b) => (b.onclick = () => loadPreset(presets.find((p) => p.id === b.dataset.id))));
}
function loadPreset(p) {
  $("ra").value = p.ra; $("dec").value = p.dec; $("band").value = p.band; $("size").value = String(p.size);
  document.querySelectorAll(".preset").forEach((b) => b.classList.toggle("active", b.dataset.id === p.id));
  showView("time");
  loadField({ ra: p.ra, dec: p.dec, size: p.size, band: p.band, name: p.name });
}
function renderTargetReadout() {
  const q = S.query; $("target-readout").textContent = q ? sexa(q.ra, q.dec) : "";
  $("ra").value = q.ra; $("dec").value = q.dec; $("band").value = q.band; $("size").value = String(q.size);
}
function renderProvenance() {
  const f = S.field, p = f.provenance, q = p.query || {};
  const badge = q.stale ? `<span class="badge stale">● stale cache: IRSA unreachable</span>` : q.cached ? `<span class="badge cached">● archive query from local cache</span>` : `<span class="badge fresh">● fresh from IRSA</span>`;
  const usable = f.epochs.filter((e) => e.usable).length;
  $("provenance-panel").innerHTML = `<h2>Data in view</h2>${badge}
    <div class="src">${usable} of ${f.epochs.length} SPHEREx ${esc(f.band)} images usable · ${esc(p.releases.join(" + ").toUpperCase())}</div>
    <div class="src small">${p.n_downloaded} downloaded now, ${p.n_from_cache} from cache · query ${esc(q.retrieved_utc || "")}</div>
    <div class="src small">Built in ${Object.values(f.timings).reduce((a, b) => a + b, 0).toFixed(1)} s · reference epoch ${esc(f.epochs[f.reference_index]?.date?.slice(0, 10) || "")}</div>
    ${f.failures.length ? `<div class="src small" style="color:var(--amber)">${f.failures.length} image(s) failed to download</div>` : ""}
    <button class="learn-link" data-view-link="help">Provenance &amp; data sources</button>`;
  $("provenance-panel").querySelector("[data-view-link]").onclick = () => showView("help");
}
function renderFilters() {
  const c = S.field.tier_counts;
  $("tier-filter").innerHTML = Object.entries(TIERS).filter(([k]) => k !== "insufficient").map(([k, t]) => `<button class="chip" data-tier="${k}" aria-pressed="${S.tiers.has(k)}"><span class="dot" style="background:${t.color}"></span>${t.label.split(" ·")[0]}<span class="n">${c[k] || 0}</span></button>`).join("");
  const cls = ["star", "galaxy", "quasar", "variable", "unknown"];
  $("class-filter").innerHTML = cls.map((k) => `<button class="chip" data-cls="${k}" aria-pressed="${S.classes.has(k)}" title="Catalog classification (SIMBAD type, else Gaia DR3 DSC)">${k}</button>`).join("") + `<p class="hint">Classes come from SIMBAD / Gaia DR3 <span class="tag cat">catalog</span>${S.catalogs ? "" : " (loading)"}.</p>`;
  $("tier-filter").querySelectorAll(".chip").forEach((b) => (b.onclick = () => { const k = b.dataset.tier; S.tiers.has(k) ? S.tiers.delete(k) : S.tiers.add(k); b.setAttribute("aria-pressed", S.tiers.has(k)); sky.draw(); }));
  $("class-filter").querySelectorAll(".chip").forEach((b) => (b.onclick = () => { const k = b.dataset.cls; S.classes.has(k) ? S.classes.delete(k) : S.classes.add(k); if (k === "unknown") { S.classes.has("unknown") ? S.classes.add("other") : S.classes.delete("other"); } b.setAttribute("aria-pressed", S.classes.has(k)); sky.draw(); }));
}

// ------------------------------------------------------------------ views
function showView(v) {
  S.view = v;
  document.querySelectorAll("[data-view]").forEach((b) => { b.classList.toggle("active", b.dataset.view === v); b.setAttribute("aria-pressed", b.dataset.view === v); });
  $("viewport").hidden = v !== "time"; $("explore").hidden = v !== "explore"; $("sheet").hidden = !["discover", "learn", "help"].includes(v);
  $("timeline").hidden = !(v === "time");
  $("stage").style.gridTemplateRows = v === "time" ? "" : "1fr";
  if (v === "explore") { explorer ??= new Explorer(cfg, presets); explorer.show(); }
  if (v === "discover") { $("sheet").innerHTML = discoverHTML(); $("sheet").querySelectorAll("tr[data-sid]").forEach((tr) => (tr.onclick = () => { showView("time"); select(+tr.dataset.sid); })); }
  if (v === "learn") renderLearn();
  if (v === "help") $("sheet").innerHTML = helpHTML(cfg);
  if (v === "time") requestAnimationFrame(() => { sky.resize(); timeline.render(); });
  updateUrl();
}
function renderLearn(focus) {
  $("sheet").innerHTML = learnHTML(focus);
  const r = $("wl-demo");
  if (r) {
    const upd = () => { const w = CUBE_WL[+r.value]; const band = w < 1.1 ? "D1" : w < 1.63 ? "D2" : w < 2.42 ? "D3" : w < 3.83 ? "D4" : w < 4.42 ? "D5" : "D6"; $("wl-out").textContent = `${sliceLabel(+r.value)} · detector ${band}`; };
    r.oninput = upd; upd();
    $("wl-explore").onclick = () => { $("slice").value = r.value; showView("explore"); explorer?.setSlice(+r.value); };
  }
  if (focus) $(`learn-${focus}`)?.scrollIntoView({ block: "center" });
}

// ------------------------------------------------------------------- tour
const TOUR = [
  { t: "These are two real SPHEREx infrared images of the same patch of sky, taken about 13 months apart, blinking. Every star is aligned on the others to a fraction of an arcsecond.", act: () => setCmp("blink") },
  { t: "Watch the bright star in the yellow brackets: it jumps north while ~140 neighbours stay put. That is Barnard's Star, moving 10.4″ per year. The arrow shows one year of its measured motion at true scale.", act: () => { const s = S.field.sources.find((x) => x.tier === "systematics_limited" || x.tier === "significant"); if (s) { select(s.id, { center: false }); sky.centerOn(s.x, s.y, Math.max(sky.k, 3.2)); } } },
  { t: () => { const d = S.detail; return `The Motion tab shows all ${d?.n_epochs ?? "the"} measurements across ${d?.n_visits ?? "several"} visits, the fitted motion (amber) and Gaia's prediction (blue dashed). Note the honest label: ${d?.tier === "systematics_limited" ? "this star saturates the detector, so the result is systematics-limited" : "the evidence tier says how far the measurement can be trusted"}.`; }, act: () => inspector.tab("motion") },
  { t: () => { const r = S.field.sources.filter((x) => x.tier === "rejected"); return r.length ? `Rigor: ${r.length} other source${r.length > 1 ? "s" : ""} passed the classic two-image test (${r.map((x) => fmt(x.pair?.significance, 1) + "σ").join(", ")}), but the other visits do not reproduce ${r.length > 1 ? "them" : "it"}. NADIR rejects ${r.length > 1 ? "them" : "it"} (red ×) instead of announcing a discovery.` : "Rigor: every candidate must be reproduced across independent visits before NADIR calls it motion; in this field no two-image detection was contradicted."; }, act: () => { const s = S.field.sources.find((x) => x.tier === "rejected"); if (s) { select(s.id, { center: false }); sky.centerOn(s.x, s.y, Math.max(sky.k, 3.2)); } } },
  { t: "Everything is traceable: the Observations tab lists each image, its wavelength and a provenance record of the exact NASA archive request. Explore the whole SPHEREx sky at 102 wavelengths from “Explore sky”.", act: () => inspector.tab("obs") },
];
let tourStep = -1;
function startTour() { tourStep = -1; nextTour(); }
function nextTour() {
  tourStep++;
  const el = $("tour");
  if (tourStep >= TOUR.length) { el.hidden = true; return; }
  const s = TOUR[tourStep]; s.act?.();
  const text = typeof s.t === "function" ? s.t() : s.t;
  el.hidden = false;
  el.innerHTML = `<div class="step">GUIDED DEMO · ${tourStep + 1} / ${TOUR.length}</div><p>${esc(text)}</p><div class="row"><button class="btn ghost" id="tour-end">Skip tour</button><button class="btn primary" id="tour-next">${tourStep === TOUR.length - 1 ? "Finish" : "Next →"}</button></div>`;
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
  history.replaceState(null, "", "?" + p.toString());
}
function parseCoords(text) {
  const m = text.trim().match(/^([+-]?\d+(?:\.\d+)?)[\s,]+([+-]?\d+(?:\.\d+)?)$/);
  return m ? { ra: +m[1], dec: +m[2] } : null;
}
function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 4500); }

// ---------------------------------------------------------------- events
on("select", (id) => select(id));
on("setA", (i) => { if (!S.field.epochs[i]?.usable) return; S.a = i; if (S.a === S.b) S.b = S.field.best_pair?.b; afterPairChange(); });
on("setB", (i) => { if (!S.field.epochs[i]?.usable || i === S.a) return; S.b = i; if (!["b", "blink", "overlay", "diff"].includes(S.cmp)) S.cmp = "b"; afterPairChange(); setCmp(S.cmp); });
on("frame", (i) => { S.frame = i; clearInterval(playTimer); S.playing = false; $("play").textContent = "▶"; sky.draw(); timeline.render(); updateHud(); });
on("cursor", ({ ra, dec, inside, hit }) => { $("hud-cursor").textContent = inside ? cursorText(ra, dec) + (hit ? `  ·  ${displayName(hit)}` : "") : ""; });
on("learn", (k) => { showView("learn"); renderLearn(k); });
on("urlchange", updateUrl);
on("refreshTab", () => inspector.tab(S.tab));
function afterPairChange() { timeline.render(); refreshPair(); sky.draw(); updateHud(); updateUrl(); }

document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
document.querySelectorAll("[data-cmp]").forEach((b) => (b.onclick = () => setCmp(b.dataset.cmp)));
$("play").onclick = togglePlay;
$("speed").oninput = (e) => { S.fps = +e.target.value; $("speed-out").textContent = `${S.fps}/s`; if (S.cmp === "play" && S.playing) startPlay(); if (S.cmp === "blink") setCmp("blink"); };
$("opacity").oninput = (e) => { S.opacity = +e.target.value / 100; sky.draw(); updateHud(); };
$("matched-only").onchange = (e) => { S.matchedOnly = e.target.checked; timeline.render(); };
$("zoom-in").onclick = () => sky.zoomAt(1.4); $("zoom-out").onclick = () => sky.zoomAt(1 / 1.4); $("zoom-fit").onclick = () => sky.fit();
document.querySelectorAll("[data-ov]").forEach((cb) => (cb.onchange = () => { S.overlays[cb.dataset.ov] = cb.checked; sky.draw(); }));
$("coord-form").onsubmit = (e) => {
  e.preventDefault();
  const ra = parseFloat($("ra").value), dec = parseFloat($("dec").value);
  if (!(ra >= 0 && ra < 360) || !(dec >= -90 && dec <= 90)) return toast("RA must be in [0, 360) and Dec in [−90, 90] decimal degrees.");
  showView("time"); loadField({ ra, dec, size: +$("size").value, band: $("band").value });
};
$("quick-search").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("q").value.trim(); if (!text) return;
  const c = parseCoords(text);
  if (c) { if (c.ra < 0 || c.ra >= 360 || Math.abs(c.dec) > 90) return toast("Coordinates out of range."); showView("time"); return loadField({ ...c, size: +$("size").value, band: $("band").value }); }
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
  if (e.target.closest("input, textarea, select") || !S.field || S.view !== "time" || !$("intro").hidden) return;
  const usable = S.field.epochs.filter((x) => x.usable).map((x) => x.index);
  if (e.key === " ") { e.preventDefault(); setCmp(S.cmp === "blink" ? "a" : "blink"); }
  else if (e.key === "d" || e.key === "D") setCmp("diff");
  else if (e.key === "p" || e.key === "P") setCmp("play");
  else if (e.key === "a" || e.key === "A") setCmp("a");
  else if (e.key === "b" || e.key === "B") setCmp("b");
  else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && !e.shiftKey && document.activeElement !== $("sky")) {
    const list = S.cmp === "play" ? playList() : usable.filter((i) => i !== S.a), cur = S.cmp === "play" ? S.frame : S.b;
    const k = list.indexOf(cur), n = list[(k + (e.key === "ArrowRight" ? 1 : -1) + list.length) % list.length];
    S.cmp === "play" ? emit("frame", n) : emit("setB", n);
  } else if (e.key === "Escape") { $("tour").hidden = true; select(null); }
});
window.addEventListener("resize", () => timeline.render());

// ------------------------------------------------------------------- boot
(async function boot() {
  intro();
  try { [cfg, presets] = await Promise.all([getJSON("/api/config"), getJSON("/api/presets")]); }
  catch (e) { showError(e.message, { ra: 0, dec: 0, band: "D2", size: 0.2 }); return; }
  renderPresets();
  const q = BOOT;
  if (q.has("ra") && q.has("dec")) {
    const query = { ra: +q.get("ra"), dec: +q.get("dec"), size: +(q.get("size") || 0.2), band: q.get("band") || "D2" };
    if (q.get("tab")) S.tab = q.get("tab");
    firstLoad = q.get("tour") === "1";
    await loadField(query, { a: q.has("a") ? +q.get("a") : undefined, b: q.has("b") ? +q.get("b") : undefined, mode: q.get("mode") || undefined, src: q.get("src") ?? undefined, tour: q.get("tour") === "1" });
    if (q.get("view") && q.get("view") !== "time") showView(q.get("view"));
  } else loadPreset(presets.find((p) => p.demo) || presets[0]);
})();
