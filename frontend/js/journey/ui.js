// NADIR Journeys — the immersive layer: captions, evidence card, controls,
// optional voice, the end actions and Stage B ("Explore it yourself").
// The engine owns time; this module only renders engine events and forwards input.
import { esc } from "../fmt.js";

const $ = (id) => document.getElementById(id);
const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"];
const STAMP_TAG = { measured: '<span class="tag measured">real data</span>', catalog: '<span class="tag cat">catalog</span>', illustrative: '<span class="tag illustrative">illustrative</span>' };
const store = { get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} } };

export class JourneyUI {
  constructor(engine, { onExit, onAction, reduced }) {
    this.e = engine; this.onExit = onExit; this.onAction = onAction; this.reduced = reduced;
    this.root = $("journey"); this.voice = store.get("nadir:journey-voice") === "on" && "speechSynthesis" in window;
    this.synth = "speechSynthesis" in window ? window.speechSynthesis : null;
    this._key = (ev) => this.key(ev);
    engine.on("start", ({ journey, scenes }) => this.build(journey, scenes))
      .on("scene", (s) => this.scene(s))
      .on("caption", ({ text }) => this.caption(text))
      .on("stamp", (st) => this.stamp(st))
      .on("card", (html) => this.card(html))
      .on("tick", ({ t, duration, index }) => this.progress(index, t / duration))
      .on("state", (st) => this.state(st))
      .on("end", () => this.end())
      .on("exit", () => this.close());
    $("jr-play").onclick = () => this.e.toggle();
    $("jr-next").onclick = () => this.e.next();
    $("jr-prev").onclick = () => this.e.prev();
    $("jr-replay").onclick = () => { this.hideEnd(); this.e.replay(); };
    $("jr-exit").onclick = () => this.e.stop("exit");
    $("jr-voice").onclick = () => this.setVoice(!this.voice);
    $("jr-evidence").onclick = () => this.togglePanel();
    this.root.querySelectorAll("[data-jr-action]").forEach((b) => (b.onclick = () => this.onAction(b.dataset.jrAction)));
    if (!this.synth) $("jr-voice").hidden = true;
  }

  // ------------------------------------------------------------- lifecycle
  build(journey, scenes) {
    this.journey = journey; this.scenes = scenes;
    this.root.hidden = false; this.root.classList.toggle("reduced", !!this.reduced);
    this.root.toggleAttribute("data-full", !!journey.full);
    $("jr-name").textContent = journey.title;
    $("jr-sub").textContent = journey.subtitle || "";
    $("jr-progress").innerHTML = scenes.map((s, i) => `<li><button data-scene="${i}" aria-label="Scene ${i + 1}: ${esc(s.title)}"><i></i></button></li>`).join("");
    $("jr-progress").querySelectorAll("[data-scene]").forEach((b) => (b.onclick = () => { this.hideEnd(); this.e.playing = true; this.e.goto(+b.dataset.scene); this.state(this.e.state()); }));
    this.caption(""); this.stamp(null); this.card(null); this.hideEnd();
    this.setVoice(this.voice, true);
    this.panelOpen = !matchMedia("(max-width: 900px)").matches;
    this.root.classList.toggle("panel-closed", !this.panelOpen);
    document.addEventListener("keydown", this._key, true);
    this.state({ playing: true });
    requestAnimationFrame(() => $("jr-play").focus({ preventScroll: true }));
  }
  close() {
    document.removeEventListener("keydown", this._key, true);
    this.synth?.cancel();
    this.root.hidden = true; this.hideEnd();
    this.onExit?.();
  }

  // ---------------------------------------------------------------- events
  scene({ index, count, scene, loading, card, ok }) {
    $("jr-chapter").textContent = `${ROMAN[index] || index + 1} of ${ROMAN[count - 1] || count}`;
    $("jr-scene").textContent = scene.title;
    this.root.dataset.scene = scene.id;
    this.root.classList.toggle("loading", !!loading);
    this.hideEnd();
    $("jr-progress").querySelectorAll("li").forEach((li, i) => { li.classList.toggle("done", i < index); li.classList.toggle("on", i === index); li.querySelector("i").style.width = i < index ? "100%" : "0%"; });
    if (loading) { this.caption(""); this.card(`<article class="jr-card"><p class="jr-kicker">Preparing</p><p class="jr-note">Reading the cached measurements for this scene…</p></article>`); return; }
    this.card(card); this.root.classList.toggle("failed", ok === false);
    this.flourish(index, scene.title);
  }
  flourish(index, title) {
    if (this.reduced) return;
    const el = $("jr-flourish");
    el.innerHTML = `<span>${ROMAN[index] || index + 1}</span><b>${esc(title)}</b>`;
    el.classList.remove("show"); void el.offsetWidth; el.classList.add("show");
  }
  caption(text) {
    const el = $("jr-caption");
    el.classList.remove("in"); void el.offsetWidth;
    el.textContent = text || ""; if (text) el.classList.add("in");
    if (text && this.voice && this.synth) {
      this.synth.cancel();
      const u = new SpeechSynthesisUtterance(text.replace(/″/g, " arcseconds").replace(/µm/g, "microns").replace(/σ/g, " sigma"));
      u.rate = 0.98; u.pitch = 1; this._spoke = performance.now(); this._est = 1500 + text.split(/\s+/).length * 420;
      this.synth.speak(u);
    }
  }
  /** the engine holds the clock while the voice is still reading (bounded, in case speech stalls) */
  speaking() { return !!(this.voice && this.synth?.speaking && performance.now() - (this._spoke || 0) < (this._est || 0)); }
  stamp(st) {
    const el = $("jr-stamp");
    if (!st) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false; el.innerHTML = `${STAMP_TAG[st.tag] || ""}<span>${esc(st.text)}</span>`;
  }
  card(html) {
    const el = $("jr-panel");
    el.classList.remove("in"); void el.offsetWidth;
    el.innerHTML = html || ""; el.hidden = !html;
    $("jr-evidence").hidden = !html;
    if (html) el.classList.add("in");
    el.querySelectorAll(".plot [data-point-info]").forEach((node) => {
      const show = () => { const out = node.closest(".chart")?.querySelector(".plot-readout"); if (out) out.textContent = node.dataset.pointInfo; };
      node.onpointerenter = show; node.onfocus = show; node.onclick = show;
    });
  }
  highlightFrame(i) { $("jr-panel").querySelectorAll("[data-frame]").forEach((li) => li.classList.toggle("on", +li.dataset.frame === i)); }
  progress(index, f) {
    const li = $("jr-progress").children[index]; if (li) li.querySelector("i").style.width = `${Math.min(100, f * 100).toFixed(1)}%`;
  }
  state(st) {
    const on = !!st.playing;
    $("jr-play").innerHTML = `<svg><use href="#i-${on ? "pause" : "play"}"/></svg>`;
    $("jr-play").setAttribute("aria-label", on ? "Pause journey" : st.ended ? "Replay journey" : "Resume journey");
    this.root.classList.toggle("paused", !on);
    if (this.synth && this.voice) on ? this.synth.resume() : this.synth.pause();
  }
  end() {
    $("jr-end").hidden = false; this.root.classList.add("ended");
    $("jr-progress").querySelectorAll("li").forEach((li) => { li.classList.add("done"); li.querySelector("i").style.width = "100%"; });
    $("jr-end").querySelector("button")?.focus({ preventScroll: true });
  }
  hideEnd() { $("jr-end").hidden = true; this.root.classList.remove("ended"); }
  setVoice(on, quiet = false) {
    this.voice = !!on && !!this.synth;
    store.set("nadir:journey-voice", this.voice ? "on" : "off");
    $("jr-voice").setAttribute("aria-pressed", this.voice);
    $("jr-voice").innerHTML = this.voice ? "Voice on" : "Voice off";
    if (!this.voice) this.synth?.cancel();
    else if (!quiet) this.caption($("jr-caption").textContent);
  }
  togglePanel() {
    this.panelOpen = !this.panelOpen;
    this.root.classList.toggle("panel-closed", !this.panelOpen);
    $("jr-evidence").setAttribute("aria-expanded", this.panelOpen);
  }

  /** screen region not covered by journey chrome, as fractions + pixels (for camera framing) */
  freeBox() {
    const vp = $("viewport").getBoundingClientRect(), top = this.root.querySelector(".jr-top").getBoundingClientRect(), bot = this.root.querySelector(".jr-bottom").getBoundingClientRect();
    const panel = $("jr-panel"), pr = !panel.hidden && this.panelOpen && getComputedStyle(panel).position === "absolute" ? panel.getBoundingClientRect() : null;
    const side = pr && pr.left > vp.left + vp.width * 0.45 ? pr.left - 16 : vp.right;
    const box = { left: vp.left, right: Math.max(vp.left + 200, side), top: Math.min(top.bottom + 8, vp.top + vp.height * 0.3), bottom: Math.max(bot.top - 8, vp.top + vp.height * 0.5) };
    return { w: box.right - box.left, h: box.bottom - box.top, ax: ((box.left + box.right) / 2 - vp.left) / vp.width, ay: ((box.top + box.bottom) / 2 - vp.top) / vp.height };
  }

  key(ev) {
    if (this.root.hidden) return;
    if (ev.target.closest?.("input, textarea, select")) return;
    const k = ev.key;
    const handled = { " ": () => this.e.toggle(), k: () => this.e.toggle(), K: () => this.e.toggle(), ArrowRight: () => this.e.next(), ArrowLeft: () => this.e.prev(),
      Escape: () => this.e.stop("exit"), v: () => this.setVoice(!this.voice), V: () => this.setVoice(!this.voice) }[k];
    if (!handled) return;
    if (k === " " && ev.target.closest?.("button") && ev.target.id !== "jr-play") return; // let Space press the focused button
    ev.preventDefault(); ev.stopImmediatePropagation();
    if (k === " " && ev.target.id === "jr-play") { this.e.toggle(); return; }
    handled();
  }
}

/** Stage B: the same scenes, as short self-paced lessons on the live observatory. */
export class Lessons {
  constructor(el) { this.el = el; }
  async open(engine, ctx) {
    const items = [];
    for (const s of engine.scenes) {
      const res = await engine.prepared(s);
      let lesson = null;
      try { lesson = s.explore?.(ctx, res.ok ? res.data : null); } catch (e) { console.warn("[journey] lesson failed", s.id, e); }
      if (lesson) items.push({ ...lesson, scene: s });
    }
    this.items = items; this.ctx = ctx; this.i = 0;
    if (!items.length) return;
    this.el.hidden = false; this.render();
  }
  close() { this.el.hidden = true; this.items = null; }
  render() {
    const L = this.items[this.i], ctx = this.ctx, n = this.items.length;
    let saved = ""; if (L.notes) saved = store.get(ctx.app.notesKey) || "";
    this.el.innerHTML = `<header><p class="welcome-kicker">Explore it yourself · ${this.i + 1} of ${n}</p><button class="icon-btn" data-l="close" aria-label="Close lessons"><svg><use href="#i-close"/></svg></button></header>
      <h3>${esc(L.title)}</h3><p>${esc(L.prompt)}</p>
      ${L.actions?.length ? `<div class="lesson-actions">${L.actions.map((a, k) => `<button class="btn ${k ? "" : "primary"} sm" data-run="${k}">${esc(a.label)}</button>`).join("")}</div>` : ""}
      ${L.notes ? `<label class="sr-only" for="lesson-note">Your observation</label><textarea id="lesson-note" rows="3" placeholder="e.g. The teal stars stay put; the amber one jumps north between visits…">${esc(saved)}</textarea><p class="small" id="lesson-status">Saved in this browser only, alongside the Notes tab.</p>` : ""}
      <footer><button class="btn ghost sm" data-l="prev" ${this.i ? "" : "disabled"}>Back</button><span class="lesson-pips">${this.items.map((_, k) => `<i class="${k === this.i ? "on" : ""}"></i>`).join("")}</span><button class="btn sm" data-l="next">${this.i === n - 1 ? "Done" : "Next"}</button></footer>`;
    this.el.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => { try { L.actions[+b.dataset.run].run(ctx); } catch (e) { console.warn(e); } }));
    this.el.querySelector('[data-l="close"]').onclick = () => this.close();
    this.el.querySelector('[data-l="prev"]').onclick = () => { this.i = Math.max(0, this.i - 1); this.render(); };
    this.el.querySelector('[data-l="next"]').onclick = () => { if (this.i >= n - 1) this.close(); else { this.i++; this.render(); } };
    const ta = this.el.querySelector("#lesson-note");
    if (ta) ta.oninput = () => { store.set(ctx.app.notesKey, ta.value); this.el.querySelector("#lesson-status").textContent = "Saved in this browser · also in the Notes tab"; };
  }
}
