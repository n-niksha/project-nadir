// The Observatory viewport: registered SPHEREx frames on a TAN (north-up,
// east-left) grid, with pan/zoom, an RA/Dec graticule and source overlays.
//
// Coordinate systems are kept distinct:
//   grid pixel (x, y)    0-based, y up (north), from the server's WCS
//   image space (u, v)   u = x + 0.5, v = ny - (y + 0.5)  (PNG rows run north->south)
//   screen (sx, sy)      sx = ox + k u, sy = oy + k v
//   sky (ra, dec)        via the gnomonic projection below
//
// Rendering modes (display only — measurements never see these pixels):
//   presentation  flagged single pixels are filled from their valid neighbours,
//                 large no-data areas become transparent, a gentle midnight→warm
//                 tone curve is applied, and the image is resampled smoothly.
//   science       the server's sampled pixels exactly, nearest-neighbour, with
//                 flagged pixels in navy and saturated cores in white.
import { S, TIERS, emit } from "./state.js";
import { fmt, sexa, displayName } from "./fmt.js";
import { C, alpha, FONT } from "./tokens.js";

const D2R = Math.PI / 180;
const NOTABLE = new Set(["significant", "systematics_limited", "candidate", "rejected"]);
const REDUCED = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export class Tan {
  constructor(g) { this.ra0 = g.crval[0]; this.dec0 = g.crval[1]; this.crpix = g.crpix; this.cdelt = g.cdelt; this.nx = g.nx; this.ny = g.ny; }
  pixToSky(x, y) {
    const xi = (x + 1 - this.crpix[0]) * this.cdelt[0] * D2R, eta = (y + 1 - this.crpix[1]) * this.cdelt[1] * D2R;
    const d0 = this.dec0 * D2R, rho = Math.hypot(xi, eta), c = Math.atan(rho);
    if (rho === 0) return [this.ra0, this.dec0];
    const dec = Math.asin(Math.cos(c) * Math.sin(d0) + eta * Math.sin(c) * Math.cos(d0) / rho);
    const ra = this.ra0 * D2R + Math.atan2(xi * Math.sin(c), rho * Math.cos(d0) * Math.cos(c) - eta * Math.sin(d0) * Math.sin(c));
    return [((ra / D2R) % 360 + 360) % 360, dec / D2R];
  }
  skyToPix(ra, dec) {
    const a = (ra - this.ra0) * D2R, d = dec * D2R, d0 = this.dec0 * D2R;
    const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a);
    const xi = Math.cos(d) * Math.sin(a) / cosc, eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a)) / cosc;
    return [xi / D2R / this.cdelt[0] + this.crpix[0] - 1, eta / D2R / this.cdelt[1] + this.crpix[1] - 1];
  }
}

// Presentation tone curve: deep midnight blacks rising to a warm off-white.
// Monotonic in every channel, so relative brightness order is preserved.
const LUT = (() => {
  const t = new Uint8ClampedArray(256 * 3);
  for (let v = 0; v < 256; v++) {
    const x = v / 255;
    t[v * 3] = 5 + 250 * Math.pow(x, 1.1);
    t[v * 3 + 1] = 8 + 239 * Math.pow(x, 1.02);
    t[v * 3 + 2] = 17 + 214 * Math.pow(x, 0.9);
  }
  return t;
})();

/** Mitchell–Netravali (B = C = 1/3) cubic kernel: smooth, minimal ringing around stars. */
function mitchell(x) {
  x = Math.abs(x);
  if (x < 1) return (7 * x * x * x - 12 * x * x + 16 / 3) / 6;
  if (x < 2) return (-7 / 3 * x * x * x + 12 * x * x - 20 * x + 32 / 3) / 6;
  return 0;
}

/**
 * Display-only presentation of a server frame (palette PNG: navy = no data,
 * warm white = saturated).  Returns RGBA at `scale`× the native size:
 *  1. isolated flagged pixels are filled from ≥3 valid neighbours (≤3 passes);
 *  2. the grey values are resampled with a bicubic kernel, alpha-weighted so
 *     large no-data areas stay transparent instead of bleeding;
 *  3. a monotonic tone curve maps grey to midnight → warm white.
 * Nothing here is used for measurement; it cannot add resolution.
 */
export function presentFrame(data, w, h, scale = 3) {
  const n = w * h, val = new Float32Array(n), ok = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    const noData = Math.abs(r - 16) <= 3 && Math.abs(g - 20) <= 3 && Math.abs(b - 44) <= 3;
    ok[i] = noData ? 0 : 1; val[i] = r;
  }
  for (let pass = 0; pass < 3; pass++) {
    const fill = [];
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x; if (ok[i]) continue;
      let s = 0, c = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const xx = x + dx, yy = y + dy; if ((!dx && !dy) || xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
        const j = yy * w + xx; if (ok[j]) { s += val[j]; c++; }
      }
      if (c >= 3) fill.push(i, s / c);
    }
    if (!fill.length) break;
    for (let k = 0; k < fill.length; k += 2) { val[fill[k]] = fill[k + 1]; ok[fill[k]] = 1; }
  }
  for (let i = 0; i < n; i++) if (!ok[i]) val[i] = 0;
  // separable bicubic: horizontal pass then vertical, on (value·alpha, alpha)
  const W = w * scale, H = h * scale, taps = (len, L) => {
    const idx = new Int32Array(len * 4), wt = new Float32Array(len * 4);
    for (let o = 0; o < len; o++) {
      const src = (o + 0.5) / scale - 0.5, base = Math.floor(src);
      let sum = 0;
      for (let t = 0; t < 4; t++) { const j = base - 1 + t; idx[o * 4 + t] = Math.max(0, Math.min(L - 1, j)); wt[o * 4 + t] = mitchell(src - j); sum += wt[o * 4 + t]; }
      for (let t = 0; t < 4; t++) wt[o * 4 + t] /= sum;
    }
    return [idx, wt];
  };
  const [xi, xw] = taps(W, w), [yi, yw] = taps(H, h);
  const hv = new Float32Array(W * h), ha = new Float32Array(W * h);
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let x = 0; x < W; x++) {
      let v = 0, a = 0;
      for (let t = 0; t < 4; t++) { const j = row + xi[x * 4 + t], q = xw[x * 4 + t]; a += q * ok[j]; v += q * val[j]; }
      hv[y * W + x] = v; ha[y * W + x] = a;
    }
  }
  const out = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let v = 0, a = 0;
      for (let t = 0; t < 4; t++) { const j = yi[y * 4 + t] * W + x, q = yw[y * 4 + t]; a += q * ha[j]; v += q * hv[j]; }
      const o = (y * W + x) * 4;
      if (a < 0.5) { out[o + 3] = 0; continue; }
      const g = Math.max(0, Math.min(255, Math.round(v / a)));
      out[o] = LUT[g * 3]; out[o + 1] = LUT[g * 3 + 1]; out[o + 2] = LUT[g * 3 + 2]; out[o + 3] = Math.min(255, Math.round(a * 255));
    }
  }
  return { data: out, width: W, height: H };
}

export class SkyView {
  constructor(canvas) {
    // CPU-backed: frames must be pixel-identical across redraws (blink A === A),
    // which GPU-composited resampling does not guarantee once pixels are read back.
    this.c = canvas; this.ctx = canvas.getContext("2d", { willReadFrequently: true });
    this.images = new Map(); this.proc = new Map(); this.diff = null; this.k = 1; this.ox = 0; this.oy = 0;
    this.hover = null; this.flashAt = 0;
    this._bind();
    new ResizeObserver(() => this.resize()).observe(canvas);
  }
  setField(f) {
    this.f = f; this.tan = new Tan(f.grid); this.images.clear(); this.proc.clear(); this.diff = null; this.userMoved = false; this.hover = null;
    this.byId = new Map(f.sources.map((s) => [s.id, s]));
    this.resize(); this.fit();
    if (!REDUCED) { this.c.classList.remove("resolving"); void this.c.offsetWidth; this.c.classList.add("resolving"); }
  }
  resize() {
    const r = this.c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!r.width) return;
    this.c.width = Math.round(r.width * dpr); this.c.height = Math.round(r.height * dpr); this.dpr = dpr;
    if (this.f && !this.userMoved) this.fit(); else this.draw();
  }
  fit() {
    if (!this.f) return;
    this.userMoved = false;
    const W = this.c.width / this.dpr, H = this.c.height / this.dpr, g = this.f.grid;
    this.k = Math.min(W / g.nx, H / g.ny) * 0.94; this.ox = (W - g.nx * this.k) / 2; this.oy = (H - g.ny * this.k) / 2;
    this.draw();
  }
  zoomAt(factor, sx, sy) {
    const W = this.c.width / this.dpr, H = this.c.height / this.dpr;
    sx ??= W / 2; sy ??= H / 2;
    this.userMoved = true;
    const nk = Math.max(0.5, Math.min(60, this.k * factor)); const f = nk / this.k;
    this.ox = sx - (sx - this.ox) * f; this.oy = sy - (sy - this.oy) * f; this.k = nk; this.draw();
  }
  centerOn(x, y, k) {
    const W = this.c.width / this.dpr, H = this.c.height / this.dpr;
    if (k) this.k = k;
    this.userMoved = true;
    const [u, v] = this.gridToImg(x, y); this.ox = W / 2 - u * this.k; this.oy = H / 2 - v * this.k; this.draw();
  }
  gridToImg(x, y) { return [x + 0.5, this.f.grid.ny - (y + 0.5)]; }
  gridToScreen(x, y) { const [u, v] = this.gridToImg(x, y); return [this.ox + u * this.k, this.oy + v * this.k]; }
  screenToGrid(sx, sy) { const u = (sx - this.ox) / this.k, v = (sy - this.oy) / this.k; return [u - 0.5, this.f.grid.ny - v - 0.5]; }
  arcsecPerScreenPx() { return this.f.grid.pixel_scale_arcsec / this.k; }

  loadFrame(i) {
    if (this.images.has(i)) return this.images.get(i).ready;
    const img = new Image(); img.decoding = "async";
    const ready = new Promise((res, rej) => { img.onload = () => res(img); img.onerror = () => rej(new Error(`Observation ${i} image could not be loaded. Reopen the field to retry.`)); });
    img.src = `/api/fields/${this.f.fid}/frames/${i}.png`;
    this.images.set(i, { img, ready });
    ready.then(() => this.draw()).catch(() => {});
    return ready;
  }
  async preload(indices) {
    const field = this.f;
    const queue = [...indices];
    await Promise.all(Array.from({ length: 3 }, async () => {
      while (queue.length && this.f === field) {
        try { await this.loadFrame(queue.shift()); } catch (_) {}
      }
    }));
  }
  setDiff(a, b) {
    const key = `${a}-${b}`;
    if (this.diff?.key === key) return this.diff.ready;
    const img = new Image();
    const ready = new Promise((res, rej) => { img.onload = () => res(img); img.onerror = rej; });
    img.src = `/api/fields/${this.f.fid}/diff.png?a=${a}&b=${b}`;
    this.diff = { key, img, ready }; ready.then(() => this.draw()).catch(() => {});
    return ready;
  }
  _img(i) { const e = this.images.get(i); return e && e.img.complete && e.img.naturalWidth ? e.img : null; }
  /** the drawable for frame i in the current rendering mode */
  _frame(i) {
    const img = this._img(i); if (!img) return null;
    if (S.render === "science") return img;
    let p = this.proc.get(i);
    if (!p) {
      const src = document.createElement("canvas"); src.width = img.naturalWidth; src.height = img.naturalHeight;
      const sx = src.getContext("2d", { willReadFrequently: true }); sx.drawImage(img, 0, 0);
      const out = presentFrame(sx.getImageData(0, 0, src.width, src.height).data, src.width, src.height, src.width > 600 ? 2 : 3);
      const cv = document.createElement("canvas"); cv.width = out.width; cv.height = out.height;
      cv.getContext("2d").putImageData(new ImageData(out.data, out.width, out.height), 0, 0);
      p = cv; this.proc.set(i, p);
      if (this.proc.size > 24) this.proc.delete(this.proc.keys().next().value);
    }
    return p;
  }

  // which frame(s) to draw for the current comparison mode
  current() {
    if (S.cmp === "a") return { base: S.a };
    if (S.cmp === "b") return { base: S.b };
    if (S.cmp === "blink") return { base: S.blinkOn ? S.b : S.a };
    if (S.cmp === "overlay") return { base: S.a, top: S.b, alpha: S.opacity };
    if (S.cmp === "play") return { base: S.frame ?? S.a };
    if (S.cmp === "diff") return { diff: true };
    return { base: S.a };
  }

  draw() {
    const ctx = this.ctx, dpr = this.dpr || 1;
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.c.width, this.c.height);
    if (!this.f) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const g = this.f.grid, W = g.nx * this.k, H = g.ny * this.k, sci = S.render === "science";
    ctx.imageSmoothingEnabled = !sci || this.k < 1;
    ctx.imageSmoothingQuality = "high";
    // footprint
    ctx.fillStyle = sci ? "#0a0f1e" : C("ink-0"); ctx.fillRect(this.ox, this.oy, W, H);
    const cur = this.current();
    if (cur.diff) { if (this.diff?.img.complete && this.diff.img.naturalWidth) ctx.drawImage(this.diff.img, this.ox, this.oy, W, H); }
    else {
      const base = this._frame(cur.base);
      if (base) ctx.drawImage(base, this.ox, this.oy, W, H);
      else { ctx.fillStyle = C("text-3"); ctx.font = `13px ${FONT.sans}`; ctx.fillText("Loading observation…", this.ox + 14, this.oy + 24); }
      if (cur.top != null) { const top = this._frame(cur.top); if (top) { ctx.globalAlpha = cur.alpha; ctx.drawImage(top, this.ox, this.oy, W, H); ctx.globalAlpha = 1; } }
    }
    if (sci && this.k >= 14) this.drawPixelGrid();
    ctx.strokeStyle = alpha(C("sky"), .22); ctx.lineWidth = 1; ctx.strokeRect(this.ox + .5, this.oy + .5, W - 1, H - 1);
    this.labelBoxes = [];
    if (S.overlays.grid) this.drawGraticule();
    this.drawCatalogs();
    this.drawSources();
    this.drawHover();
    this.drawSelected();
    this.drawScaleBar();
  }

  drawPixelGrid() {
    const ctx = this.ctx, g = this.f.grid, W = this.c.width / this.dpr, H = this.c.height / this.dpr;
    ctx.save(); ctx.strokeStyle = "rgba(255,255,255,.06)"; ctx.lineWidth = 1; ctx.beginPath();
    const x0 = Math.max(0, Math.floor(-this.ox / this.k)), x1 = Math.min(g.nx, Math.ceil((W - this.ox) / this.k));
    const y0 = Math.max(0, Math.floor(-this.oy / this.k)), y1 = Math.min(g.ny, Math.ceil((H - this.oy) / this.k));
    for (let u = x0; u <= x1; u++) { const x = Math.round(this.ox + u * this.k) + .5; ctx.moveTo(x, this.oy + y0 * this.k); ctx.lineTo(x, this.oy + y1 * this.k); }
    for (let v = y0; v <= y1; v++) { const y = Math.round(this.oy + v * this.k) + .5; ctx.moveTo(this.ox + x0 * this.k, y); ctx.lineTo(this.ox + x1 * this.k, y); }
    ctx.stroke(); ctx.restore();
  }

  drawGraticule() {
    const ctx = this.ctx, t = this.tan, g = this.f.grid;
    const visW = (this.c.width / this.dpr) / this.k * g.pixel_scale_arcsec / 60; // arcmin across the view
    const steps = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60];
    const stepDec = steps.find((s) => Math.min(visW, g.nx * g.pixel_scale_arcsec / 60) / s <= 6) || 60; // arcmin
    const cosd = Math.cos(t.dec0 * D2R), stepRa = stepDec / Math.max(cosd, 0.05);
    const corners = [[0, 0], [g.nx, 0], [0, g.ny], [g.nx, g.ny], [g.nx / 2, 0], [g.nx / 2, g.ny], [0, g.ny / 2], [g.nx, g.ny / 2]].map(([x, y]) => t.pixToSky(x, y));
    let decs = corners.map((c) => c[1]), ras = corners.map((c) => c[0]);
    if (Math.max(...ras) - Math.min(...ras) > 180) ras = ras.map((r) => (r > 180 ? r - 360 : r));
    const d0 = Math.min(...decs), d1 = Math.max(...decs), r0 = Math.min(...ras), r1 = Math.max(...ras);
    const Wv = this.c.width / this.dpr, Hv = this.c.height / this.dpr;
    const left = Math.max(this.ox, 0), bottom = Math.min(this.oy + g.ny * this.k, Hv);
    ctx.save(); ctx.lineWidth = 1; ctx.strokeStyle = alpha(C("sky"), .13); ctx.font = `11px ${FONT.mono}`;
    const sd = stepDec / 60, sr = stepRa / 60, labels = [];
    for (let dec = Math.ceil(d0 / sd) * sd; dec <= d1; dec += sd) {
      ctx.beginPath(); let first = true;
      for (let i = 0; i <= 40; i++) { const ra = r0 + (r1 - r0) * i / 40; const [x, y] = t.skyToPix(ra, dec); const [sx, sy] = this.gridToScreen(x, y); first ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy); first = false; }
      ctx.stroke();
      const [, ly] = this.gridToScreen(0, t.skyToPix(t.ra0, dec)[1]);
      if (ly > this.oy + 14 && ly < bottom - 14 && ly > 90) labels.push([decLabel(dec, sd), left + 6, ly - 4]);
    }
    for (let ra = Math.ceil(r0 / sr) * sr; ra <= r1; ra += sr) {
      ctx.beginPath(); let first = true;
      for (let i = 0; i <= 40; i++) { const dec = d0 + (d1 - d0) * i / 40; const [x, y] = t.skyToPix(ra, dec); const [sx, sy] = this.gridToScreen(x, y); first ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy); first = false; }
      ctx.stroke();
      const [x] = t.skyToPix(ra, t.dec0); const [sx] = this.gridToScreen(x, 0);
      if (sx > left + 60 && sx < Math.min(this.ox + g.nx * this.k, Wv) - 50) labels.push([raLabel(ra, sr), sx + 4, bottom - 8]);
    }
    for (const [text, x, y] of labels) haloText(ctx, text, x, y, alpha(C("text-2"), .75));
    ctx.restore();
  }

  sourceVisible(s) {
    if (!S.tiers.has(s.tier) && !(s.tier === "insufficient" && S.tiers.has("stationary"))) return false;
    const groups = s.catalog?.simbad?.groups || (s.catalog?.gaia ? [gaiaGroup(s.catalog.gaia)] : ["unknown"]);
    return groups.some((gp) => S.classes.has(gp));
  }
  shown(s) { return this.sourceVisible(s) && (NOTABLE.has(s.tier) ? S.overlays.sources : S.overlays.refs); }
  ringRadius(s) { return NOTABLE.has(s.tier) ? Math.min(20, Math.max(9, 1.7 * this.k)) : Math.min(7, Math.max(2.5, 0.9 * this.k)); }

  drawSources() {
    const ctx = this.ctx, f = this.f, ps = f.grid.pixel_scale_arcsec;
    ctx.save();
    // reference stars first (quiet), notable sources on top
    for (const s of f.sources) {
      if (NOTABLE.has(s.tier) || !this.shown(s) || s.id === S.selected) continue;
      const [sx, sy] = this.gridToScreen(s.x, s.y), r = this.ringRadius(s);
      ctx.strokeStyle = alpha(TIERS[s.tier]?.color || C("text-3"), s.tier === "insufficient" ? .3 : .5); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, 2 * Math.PI); ctx.stroke();
    }
    for (const s of f.sources) {
      if (!NOTABLE.has(s.tier) || !this.shown(s)) continue;
      const [sx, sy] = this.gridToScreen(s.x, s.y), T = TIERS[s.tier], r = this.ringRadius(s);
      const dash = s.tier === "systematics_limited" ? [6, 4] : s.tier === "candidate" ? [2, 4] : [];
      if (s.tier === "rejected") {
        const a = r * .5;
        halo(ctx, T.color, 1.8, () => { ctx.moveTo(sx - a, sy - a); ctx.lineTo(sx + a, sy + a); ctx.moveTo(sx + a, sy - a); ctx.lineTo(sx - a, sy + a); });
      } else if (s.id !== S.selected) halo(ctx, T.color, 1.8, () => ctx.arc(sx, sy, r, 0, 2 * Math.PI), dash);
      if (s.variability?.flag) halo(ctx, C("cyan"), 1.6, () => ctx.arc(sx, sy, r + 4, -0.7, 0.7));
      // motion vector: true angular scale, one year of measured motion
      if (S.overlays.tracks && s.fit && s.tier !== "rejected") {
        const dx = -s.fit.pm_east / ps, dy = s.fit.pm_north / ps;
        const [ex, ey] = this.gridToScreen(s.x + dx, s.y + dy);
        if (Math.hypot(ex - sx, ey - sy) > r + 2) {
          const a = Math.atan2(ey - sy, ex - sx);
          arrow(ctx, sx + Math.cos(a) * r, sy + Math.sin(a) * r, ex, ey, T.color);
        }
      }
      if (this.k >= 2.2 && s.tier !== "rejected" && s.id !== S.selected) this.label(displayName(s), sx + r + 6, sy + 4, alpha(C("text-1"), .9), 12);
    }
    ctx.restore();
  }

  drawCatalogs() {
    const L = S.catalogs?.layers; if (!L) return;
    const ctx = this.ctx; ctx.save(); ctx.lineWidth = 1;
    const ps = this.f.grid.pixel_scale_arcsec, research = S.visualPreset === "research";
    if (S.overlays.gaia && L.gaia) {
      const col = alpha(C("cat-gaia"), .8);
      for (const g of L.gaia.items) {
        if (!S.classes.has(g.cls === "uncertain" || g.cls === "unclassified" ? "unknown" : g.cls) && !(g.variable && S.classes.has("variable"))) continue;
        const [sx, sy] = this.gridToScreen(g.x, g.y), r = Math.max(3, Math.min(6, this.k * .9));
        ctx.strokeStyle = col; ctx.beginPath(); ctx.moveTo(sx, sy - r); ctx.lineTo(sx + r, sy); ctx.lineTo(sx, sy + r); ctx.lineTo(sx - r, sy); ctx.closePath(); ctx.stroke();
        if (research && this.k > 7) this.label(`Gaia ${g.id}`, sx + r + 4, sy - 4, col, 11);
        if (g.pmra && Math.hypot(g.pmra, g.pmdec) > 100) { const [ex, ey] = this.gridToScreen(g.x - g.pmra / 1e3 / ps, g.y + g.pmdec / 1e3 / ps); ctx.setLineDash([3, 2]); arrow(ctx, sx, sy, ex, ey, col, false); ctx.setLineDash([]); }
      }
    }
    for (const [key, tok, name] of [["twomass", "cat-2mass", "2MASS"], ["allwise", "cat-wise", "WISE"]]) {
      if (!S.overlays[key] || !L[key]) continue;
      const col = alpha(C(tok), .75), a = Math.max(3, Math.min(5, this.k * .7));
      ctx.strokeStyle = col;
      for (const g of L[key].items) {
        const [sx, sy] = this.gridToScreen(g.x, g.y); ctx.beginPath(); ctx.moveTo(sx - a, sy); ctx.lineTo(sx + a, sy); ctx.moveTo(sx, sy - a); ctx.lineTo(sx, sy + a); ctx.stroke();
        if (research && this.k > 9) this.label(`${name} ${g.id}`, sx + 6, sy + 13, col, 11);
      }
    }
    if (S.overlays.simbad && L.simbad) {
      for (const g of L.simbad.items) {
        if (!g.groups.some((gp) => S.classes.has(gp))) continue;
        const [sx, sy] = this.gridToScreen(g.x, g.y);
        ctx.fillStyle = alpha(C("cat-simbad"), .8); ctx.beginPath(); ctx.arc(sx, sy, 1.6, 0, 7); ctx.fill();
        const named = /^NAME /.test(g.id);
        if (this.k > 4 || (named && this.k > 1)) this.label(g.id.replace(/^NAME /, ""), sx + 6, sy - 6, alpha(C("cat-simbad"), named ? .95 : .7), named ? 12.5 : 11);
      }
    }
    ctx.restore();
  }

  /** text label with collision avoidance against labels drawn this frame */
  label(text, x, y, color, size = 11.5) {
    const ctx = this.ctx; ctx.save(); ctx.font = `500 ${size}px ${FONT.sans}`;
    const width = ctx.measureText(text).width;
    const W = this.c.width / this.dpr, H = this.c.height / this.dpr;
    if (x < 0 || y < 14 || x + width > W - 4 || y > H - 4) { ctx.restore(); return false; }
    if (this.labelBoxes.some((b) => x < b.x + b.w + 6 && x + width + 6 > b.x && Math.abs(y - b.y) < size + 3)) { ctx.restore(); return false; }
    this.labelBoxes.push({ x, y, w: width });
    haloText(ctx, text, x, y, color); ctx.restore(); return true;
  }

  drawHover() {
    const s = this.hover != null ? this.byId?.get(this.hover) : null;
    if (!s || s.id === S.selected) return;
    const ctx = this.ctx, [sx, sy] = this.gridToScreen(s.x, s.y), r = Math.max(this.ringRadius(s) + 3, 10);
    ctx.save(); halo(ctx, alpha(C("text-1"), .9), 1.4, () => ctx.arc(sx, sy, r, 0, 2 * Math.PI), [3, 3]);
    const T = TIERS[s.tier];
    this.label(displayName(s), sx + r + 6, sy - 2, C("text-1"), 12.5);
    this.label(T?.label || "", sx + r + 6, sy + 13, alpha(T?.color || C("text-2"), .95), 11);
    ctx.restore();
  }

  flash() {
    if (REDUCED) return;
    this.flashAt = performance.now();
    if (this._animating) return;
    this._animating = true;
    const step = () => {
      const done = performance.now() - this.flashAt >= 620;
      this.draw();
      if (done) this._animating = false; else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  drawSelected() {
    const d = S.detail; if (!d || !this.f) return;
    const ctx = this.ctx, ps = this.f.grid.pixel_scale_arcsec;
    const [sx, sy] = this.gridToScreen(d.x, d.y);
    ctx.save();
    // per-epoch measured positions at true scale (visible when zoomed in)
    if (S.overlays.tracks && d.points && this.k > 4) {
      for (const p of d.points) {
        const [px, py] = this.gridToScreen(d.x - p.east_arcsec / ps, d.y + p.north_arcsec / ps);
        ctx.fillStyle = p.is_reference_epoch ? "#ffffff" : alpha(C("sun-2"), .85);
        ctx.beginPath(); ctx.arc(px, py, 1.8, 0, 7); ctx.fill();
      }
    }
    // catalog history (2MASS / AllWISE / Gaia) when inside the field
    if (d.history && S.overlays.tracks) {
      ctx.setLineDash([2, 4]); ctx.strokeStyle = alpha(C("sky"), .7); ctx.lineWidth = 1.2;
      let prev = null;
      for (const h of d.history) {
        const [hx, hy] = this.gridToScreen(d.x - h.east_arcsec / ps, d.y + h.north_arcsec / ps);
        if (prev) { ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(hx, hy); ctx.stroke(); }
        ctx.setLineDash([]); ctx.fillStyle = C("sky"); ctx.beginPath(); ctx.arc(hx, hy, 2.6, 0, 7); ctx.fill(); ctx.setLineDash([2, 4]);
        this.label(`${h.catalog} ${h.year.toFixed(0)}`, hx + 7, hy + 4, alpha(C("sky"), .95), 11.5);
        prev = [hx, hy];
      }
      if (prev) { ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(sx, sy); ctx.stroke(); }
      ctx.setLineDash([]);
    }
    // reticle: four brackets, with a brief "lock-on" when newly selected
    const tf = Math.min(1, (performance.now() - this.flashAt) / 600), ease = 1 - Math.pow(1 - tf, 3);
    const r = Math.max(15, 3.6 * Math.min(this.k, 8)) * (REDUCED ? 1 : 1 + (1 - ease) * 1.6);
    const len = Math.max(6, r * .45), col = C("sun");
    if (!REDUCED && tf < 1) { ctx.globalAlpha = (1 - ease) * .5; ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(sx, sy, r * 1.25, 0, 7); ctx.stroke(); ctx.globalAlpha = 1; }
    halo(ctx, col, 2, () => {
      for (const [dx, dy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
        ctx.moveTo(sx + dx * r, sy + dy * (r - len)); ctx.lineTo(sx + dx * r, sy + dy * r); ctx.lineTo(sx + dx * (r - len), sy + dy * r);
      }
    });
    this.label(displayName(d), sx + r + 8, sy - r + 12, C("sun-2"), 13);
    ctx.restore();
  }

  drawScaleBar() {
    const ctx = this.ctx, H = this.c.height / this.dpr, W = this.c.width / this.dpr;
    const aps = this.arcsecPerScreenPx(), target = 100 * aps;
    const nice = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((v) => v >= target * 0.6) || 600;
    const len = nice / aps, x = W - 24 - len, y = H - 72;
    ctx.save(); ctx.lineWidth = 1.5; ctx.lineCap = "round";
    halo(ctx, alpha(C("text-1"), .85), 1.5, () => { ctx.moveTo(x, y); ctx.lineTo(x + len, y); ctx.moveTo(x, y - 4); ctx.lineTo(x, y + 4); ctx.moveTo(x + len, y - 4); ctx.lineTo(x + len, y + 4); });
    ctx.font = `11.5px ${FONT.mono}`; ctx.textAlign = "center";
    haloText(ctx, nice >= 60 ? `${nice / 60}′` : `${nice}″`, x + len / 2, y - 8, C("text-1"));
    // compass: north up, east left (sky convention)
    const cx = x - 34, cy = y + 2;
    halo(ctx, alpha(C("text-1"), .8), 1.4, () => { ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - 22); ctx.moveTo(cx, cy); ctx.lineTo(cx - 22, cy); });
    ctx.font = `600 11px ${FONT.sans}`;
    haloText(ctx, "N", cx, cy - 27, C("text-1")); haloText(ctx, "E", cx - 30, cy + 4, C("text-1"));
    ctx.restore();
  }

  pick(sx, sy) {
    let best = null, bd = 16;
    for (const s of this.f.sources) {
      if (!this.sourceVisible(s)) continue;
      const [px, py] = this.gridToScreen(s.x, s.y), d = Math.hypot(px - sx, py - sy) - (NOTABLE.has(s.tier) ? 4 : 0);
      if (d < bd) { bd = d; best = s; }
    }
    return best;
  }

  _bind() {
    const c = this.c; let drag = null, moved = false;
    c.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, ox: this.ox, oy: this.oy }; moved = false; c.setPointerCapture(e.pointerId); });
    c.addEventListener("pointermove", (e) => {
      const r = c.getBoundingClientRect(), sx = e.clientX - r.left, sy = e.clientY - r.top;
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) { moved = true; this.userMoved = true; c.classList.add("dragging"); }
        this.ox = drag.ox + dx; this.oy = drag.oy + dy;
      }
      if (this.f) {
        const [x, y] = this.screenToGrid(sx, sy), [ra, dec] = this.tan.pixToSky(x, y);
        const hit = drag && moved ? null : this.pick(sx, sy);
        const hid = hit?.id ?? null;
        if (hid !== this.hover || drag) { this.hover = hid; this.draw(); }
        emit("cursor", { ra, dec, inside: x >= 0 && y >= 0 && x < this.f.grid.nx && y < this.f.grid.ny, hit });
        c.style.cursor = hit ? "pointer" : (drag ? "grabbing" : "grab");
      }
    });
    c.addEventListener("pointerleave", () => { if (this.hover != null) { this.hover = null; this.draw(); } });
    c.addEventListener("pointerup", (e) => {
      c.classList.remove("dragging");
      if (drag && !moved && this.f) { const r = c.getBoundingClientRect(); const hit = this.pick(e.clientX - r.left, e.clientY - r.top); if (hit) emit("select", hit.id); }
      drag = null;
    });
    c.addEventListener("wheel", (e) => { e.preventDefault(); const r = c.getBoundingClientRect(); this.zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top); }, { passive: false });
    c.addEventListener("dblclick", () => this.fit());
    c.addEventListener("keydown", (e) => {
      const step = 40;
      if (e.key === "+" || e.key === "=") this.zoomAt(1.25);
      else if (e.key === "-") this.zoomAt(0.8);
      else if (e.key === "0") this.fit();
      else if (e.key === "ArrowUp") { this.oy += step; this.draw(); }
      else if (e.key === "ArrowDown") { this.oy -= step; this.draw(); }
      else if (e.key === "ArrowLeft" && e.shiftKey) { this.ox += step; this.draw(); }
      else if (e.key === "ArrowRight" && e.shiftKey) { this.ox -= step; this.draw(); }
      else return;
      e.preventDefault();
    });
  }
}

/** stroke a path with a dark under-stroke so markers read on any background */
function halo(ctx, color, width, path, dash = []) {
  ctx.save(); ctx.setLineDash(dash); ctx.lineCap = "round"; ctx.lineJoin = "round";
  ctx.beginPath(); path(); ctx.strokeStyle = "rgba(2,3,8,.7)"; ctx.lineWidth = width + 2.4; ctx.stroke();
  ctx.beginPath(); path(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
  ctx.restore();
}
function haloText(ctx, text, x, y, color) {
  ctx.save(); ctx.lineJoin = "round"; ctx.strokeStyle = "rgba(2,3,8,.85)"; ctx.lineWidth = 3.5; ctx.strokeText(text, x, y);
  ctx.fillStyle = color; ctx.fillText(text, x, y); ctx.restore();
}
function arrow(ctx, x0, y0, x1, y1, color, withHalo = true) {
  const a = Math.atan2(y1 - y0, x1 - x0), h = 7;
  const path = () => {
    ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
    if (Math.hypot(x1 - x0, y1 - y0) < 4) return;
    ctx.moveTo(x1 - h * Math.cos(a - .42), y1 - h * Math.sin(a - .42)); ctx.lineTo(x1, y1); ctx.lineTo(x1 - h * Math.cos(a + .42), y1 - h * Math.sin(a + .42));
  };
  if (withHalo) halo(ctx, color, 1.7, path);
  else { ctx.save(); ctx.strokeStyle = color; ctx.beginPath(); path(); ctx.stroke(); ctx.restore(); }
}
function decLabel(dec, step) { const s = dec < 0 ? "−" : "+", d = Math.abs(dec) + 1e-9, dd = Math.floor(d), mm = (d - dd) * 60; return step < 1 / 60 ? `${s}${dd}°${mm.toFixed(1)}′` : `${s}${dd}°${Math.round(mm).toString().padStart(2, "0")}′`; }
function raLabel(ra, step) { ra = (ra + 360) % 360; const h = ra / 15, hh = Math.floor(h + 1e-9), mm = (h - hh) * 60, m = Math.floor(mm + 1e-6), ss = (mm - m) * 60; return step * 4 < 1 ? `${hh}h${m}m${ss.toFixed(0)}s` : `${hh}h${String(Math.round(mm)).padStart(2, "0")}m`; }
export function gaiaGroup(g) {
  if (g.variable) return "variable";
  const c = [["star", g.p_star], ["galaxy", g.p_galaxy], ["quasar", g.p_quasar]].sort((a, b) => (b[1] || 0) - (a[1] || 0))[0];
  return (c[1] || 0) >= 0.5 ? c[0] : "unknown";
}
export function cursorText(ra, dec) { return `${sexa(ra, dec)}  ·  ${fmt(ra, 5)}°, ${fmt(dec, 5)}°`; }
