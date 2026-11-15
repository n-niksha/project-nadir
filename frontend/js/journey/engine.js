// NADIR Journeys — a small, declarative scene engine.
//
// A journey is a list of scenes.  A scene declares what it needs (prepare),
// how the stage should look when it starts (enter: camera, frame, sky layer),
// a data card, and timed beats (captions + small actions).  The engine owns
// ONE clock: it advances only while playing, it is the only thing that fires
// beats, and every scene change bumps a generation counter so late async work
// from an abandoned scene can never touch the stage.  No DOM in here: the UI
// subscribes to events, which keeps the engine testable in node.
//
// Scene shape (all fields optional except id/title):
//   { id, title, chapter,
//     when(ctx)            -> include this scene for this object?
//     prepare(ctx)         -> Promise<data>; throwing selects `fallback`
//     enter(ctx, data)     -> set camera / frame / overlays for the scene
//     card(ctx, data)      -> HTML for the evidence card (null = none)
//     beats(ctx, data)     -> [{ at: ms, caption?, stamp?, card?, do?(ctx, data) }]
//     fallback(ctx, error) -> { card?, beats } used when prepare fails
//     duration             -> ms (default: last beat + 4.5 s)
//     final                -> stop at the end and offer the exit actions
//     explore              -> Stage B lesson that reuses this scene }

export class JourneyEngine {
  constructor({ now, raf, caf, hold } = {}) {
    this.now = now || (() => performance.now());
    this.raf = raf || ((f) => requestAnimationFrame(f));
    this.caf = caf || ((h) => cancelAnimationFrame(h));
    this.hold = hold || (() => false); // e.g. "the voice is still speaking"
    this.listeners = new Map();
    this.gen = 0; this.active = false; this.playing = false;
    this.i = -1; this.t = 0; this.beats = []; this.fired = 0; this.ready = false; this.ended = false;
    this._h = null; this._last = 0;
  }

  on(name, fn) { (this.listeners.get(name) || this.listeners.set(name, []).get(name)).push(fn); return this; }
  emit(name, detail) { for (const fn of this.listeners.get(name) || []) { try { fn(detail); } catch (e) { console.warn(`[journey] ${name} listener failed`, e); } } }

  /** Load a journey for a context (the host adapter: field, source, sky…). */
  load(journey, ctx) {
    this.stop();
    this.journey = journey; this.ctx = ctx;
    this.scenes = journey.scenes.filter((s) => { try { return !s.when || s.when(ctx); } catch (_) { return false; } });
    this.cache = new Map();
    return this;
  }

  start(index = 0) {
    if (!this.scenes?.length) throw new Error("This journey has no scenes for the selected object.");
    this.active = true; this.playing = true;
    for (const s of this.scenes) this.prepared(s); // warm every scene's data in the background
    this.emit("start", { journey: this.journey, scenes: this.scenes });
    this._loop();
    return this.goto(index);
  }

  /** memoised, never-rejecting prepare */
  prepared(scene) {
    if (!this.cache.has(scene.id)) {
      const p = Promise.resolve().then(() => (scene.prepare ? scene.prepare(this.ctx) : null))
        .then((data) => ({ ok: true, data }), (error) => ({ ok: false, error }));
      this.cache.set(scene.id, p);
    }
    return this.cache.get(scene.id);
  }

  async goto(index) {
    if (!this.active) return;
    const i = Math.max(0, Math.min(this.scenes.length - 1, index));
    const gen = ++this.gen, scene = this.scenes[i];
    this.i = i; this.t = 0; this.fired = 0; this.ready = false; this.ended = false; this.beats = [];
    this.emit("scene", { index: i, count: this.scenes.length, scene, loading: true });
    const res = await this.prepared(scene);
    if (gen !== this.gen || !this.active) return; // superseded while loading
    this.res = res;
    let card = null, beats = [];
    try { this.ctx.reset?.(scene); } catch (e) { console.warn("[journey] reset failed", e); }
    if (res.ok) {
      try { scene.enter?.(this.ctx, res.data); } catch (e) { console.warn(`[journey] ${scene.id}.enter failed`, e); }
      try { card = scene.card ? scene.card(this.ctx, res.data) : null; } catch (e) { console.warn(`[journey] ${scene.id}.card failed`, e); card = null; }
      try { beats = scene.beats ? scene.beats(this.ctx, res.data) : []; } catch (e) { console.warn(`[journey] ${scene.id}.beats failed`, e); beats = null; }
    }
    if (!res.ok || !beats) {
      const fb = scene.fallback?.(this.ctx, res.error) || {};
      card = fb.card ?? null;
      beats = fb.beats || [{ at: 0, caption: `This part of the journey could not be prepared${res.error?.message ? ` (${res.error.message})` : ""}. The observatory is unaffected.` }];
      this.emit("scene-failed", { index: i, scene, error: res.error });
    }
    this.beats = beats.filter(Boolean).slice().sort((a, b) => a.at - b.at);
    this.duration = scene.duration ?? ((this.beats.at(-1)?.at || 0) + 4500);
    this.ready = true;
    this.emit("scene", { index: i, count: this.scenes.length, scene, loading: false, card, ok: res.ok, duration: this.duration });
    this._last = this.now();
    this._fire();
  }

  _fire() {
    while (this.fired < this.beats.length && this.beats[this.fired].at <= this.t) {
      const b = this.beats[this.fired++], gen = this.gen;
      if (b.caption != null) this.emit("caption", { text: typeof b.caption === "function" ? b.caption(this.ctx, this.res?.data) : b.caption, tone: b.tone });
      if (b.stamp !== undefined) this.emit("stamp", b.stamp);
      if (b.card !== undefined) { try { this.emit("card", typeof b.card === "function" ? b.card(this.ctx, this.res?.data) : b.card); } catch (e) { console.warn("[journey] card failed", e); } }
      if (b.do) {
        try {
          const r = b.do(this.ctx, this.res?.data, this);
          if (r?.catch) r.catch((e) => { if (gen === this.gen) console.warn("[journey] beat failed", e); });
        } catch (e) { console.warn("[journey] beat failed", e); }
      }
      if (gen !== this.gen) return; // a beat navigated
    }
  }

  /** advance the clock by dt ms (exposed for tests) */
  advance(dt) {
    if (!this.active || !this.ready || !this.playing || this.ended) return;
    const next = this.beats[this.fired];
    // hold before the next beat while the narrator is still talking
    if (next && this.t + dt >= next.at && this.hold()) { this.t = Math.max(this.t, next.at - 1); return; }
    this.t = Math.min(this.t + dt, this.duration);
    this._fire();
    this.emit("tick", { t: this.t, duration: this.duration, index: this.i });
    if (this.t >= this.duration && this.fired >= this.beats.length) {
      if (this.scenes[this.i].final || this.i === this.scenes.length - 1) { this.ended = true; this.playing = false; this.emit("state", this.state()); this.emit("end", {}); }
      else if (!this.hold()) this.goto(this.i + 1);
    }
  }

  _loop() {
    this.caf(this._h);
    const step = () => {
      if (!this.active) return;
      const n = this.now(), dt = Math.min(250, n - this._last); this._last = n; // clamp: a hidden tab must not skip scenes
      this.advance(dt);
      this._h = this.raf(step);
    };
    this._last = this.now();
    this._h = this.raf(step);
  }

  state() { return { active: this.active, playing: this.playing, index: this.i, count: this.scenes?.length || 0, ended: this.ended, t: this.t, duration: this.duration }; }
  play() { if (!this.active) return; if (this.ended) return this.replay(); this.playing = true; this._last = this.now(); this.emit("state", this.state()); }
  pause() { if (!this.active) return; this.playing = false; this.emit("state", this.state()); }
  toggle() { this.playing ? this.pause() : this.play(); }
  next() { if (!this.active) return; if (this.i >= this.scenes.length - 1) { this.t = this.duration; this.fired = this.beats.length; this._fire(); this.ended = true; this.playing = false; this.emit("state", this.state()); this.emit("end", {}); return; } this.playing = true; this.emit("state", this.state()); return this.goto(this.i + 1); }
  prev() { if (!this.active) return; this.playing = true; this.emit("state", this.state()); return this.goto(this.t > 3500 ? this.i : this.i - 1); }
  replay() { if (!this.active) return; this.playing = true; this.emit("state", this.state()); return this.goto(0); }

  /** leave the journey; the UI restores the observatory on "exit" */
  stop(reason = "exit") {
    if (!this.active) return;
    this.active = false; this.playing = false; this.ready = false; this.gen++;
    this.caf(this._h); this._h = null;
    this.emit("exit", { reason, index: this.i });
  }
}
