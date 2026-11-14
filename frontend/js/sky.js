// The Time Machine viewport: registered SPHEREx frames on a TAN (north-up,
// east-left) grid, with pan/zoom, an RA/Dec graticule and source overlays.
//
// Coordinate systems are kept distinct:
//   grid pixel (x, y)    0-based, y up (north), from the server's WCS
//   image space (u, v)   u = x + 0.5, v = ny - (y + 0.5)  (PNG rows run north->south)
//   screen (sx, sy)      sx = ox + k u, sy = oy + k v
//   sky (ra, dec)        via the gnomonic projection below
import { S, TIERS, emit } from "./state.js";
import { fmt, sexa } from "./fmt.js";

const D2R = Math.PI / 180;

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

export class SkyView {
  constructor(canvas) {
    this.c = canvas; this.ctx = canvas.getContext("2d");
    this.images = new Map(); this.diff = null; this.k = 1; this.ox = 0; this.oy = 0;
    this.hover = null; this.flash = null;
    this._bind();
    new ResizeObserver(() => this.resize()).observe(canvas);
  }
  setField(f) {
    this.f = f; this.tan = new Tan(f.grid); this.images.clear(); this.diff = null; this.userMoved = false;
    this.byId = new Map(f.sources.map((s) => [s.id, s]));
    this.resize(); this.fit();
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
    this.k = Math.min(W / g.nx, H / g.ny) * 0.96; this.ox = (W - g.nx * this.k) / 2; this.oy = (H - g.ny * this.k) / 2;
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
    const ready = new Promise((res, rej) => { img.onload = () => res(img); img.onerror = rej; });
    img.src = `/api/fields/${this.f.fid}/frames/${i}.png`;
    this.images.set(i, { img, ready });
    ready.then(() => this.draw()).catch(() => {});
    return ready;
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
    ctx.fillStyle = "#020308"; ctx.fillRect(0, 0, this.c.width, this.c.height);
    if (!this.f) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const g = this.f.grid, W = g.nx * this.k, H = g.ny * this.k;
    ctx.imageSmoothingEnabled = this.k < 3;
    const cur = this.current();
    if (cur.diff) { if (this.diff?.img.complete) ctx.drawImage(this.diff.img, this.ox, this.oy, W, H); }
    else {
      const base = this._img(cur.base);
      if (base) ctx.drawImage(base, this.ox, this.oy, W, H);
      else { ctx.fillStyle = "#7a849c"; ctx.font = "12px system-ui"; ctx.fillText("loading frame…", this.ox + 12, this.oy + 20); }
      if (cur.top != null) { const top = this._img(cur.top); if (top) { ctx.globalAlpha = cur.alpha; ctx.drawImage(top, this.ox, this.oy, W, H); ctx.globalAlpha = 1; } }
    }
    ctx.strokeStyle = "rgba(122,162,255,.25)"; ctx.lineWidth = 1; ctx.strokeRect(this.ox, this.oy, W, H);
    if (S.overlays.grid) this.drawGraticule();
    this.drawCatalogs();
    if (S.overlays.sources) this.drawSources();
    this.drawSelected();
    this.drawScaleBar();
  }

  drawGraticule() {
    const ctx = this.ctx, t = this.tan, g = this.f.grid;
    const fovArcmin = g.nx * g.pixel_scale_arcsec / 60 / Math.max(1, this.k / ((this.c.width / this.dpr) / g.nx));
    const steps = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60];
    const stepDec = steps.find((s) => fovArcmin / s <= 7) || 60; // arcmin
    const cosd = Math.cos(t.dec0 * D2R), stepRa = stepDec / Math.max(cosd, 0.05); // arcmin of RA
    const corners = [[0, 0], [g.nx, 0], [0, g.ny], [g.nx, g.ny], [g.nx / 2, 0], [g.nx / 2, g.ny], [0, g.ny / 2], [g.nx, g.ny / 2]].map(([x, y]) => t.pixToSky(x, y));
    let decs = corners.map((c) => c[1]), ras = corners.map((c) => c[0]);
    if (Math.max(...ras) - Math.min(...ras) > 180) ras = ras.map((r) => (r > 180 ? r - 360 : r));
    const d0 = Math.min(...decs), d1 = Math.max(...decs), r0 = Math.min(...ras), r1 = Math.max(...ras);
    ctx.save(); ctx.lineWidth = 1; ctx.strokeStyle = "rgba(122,162,255,.22)"; ctx.fillStyle = "rgba(170,179,200,.75)"; ctx.font = "10.5px ui-monospace, Menlo, monospace";
    const sd = stepDec / 60, sr = stepRa / 60;
    for (let dec = Math.ceil(d0 / sd) * sd; dec <= d1; dec += sd) {
      ctx.beginPath(); let first = true, lab = null;
      for (let i = 0; i <= 40; i++) { const ra = r0 + (r1 - r0) * i / 40; const [x, y] = t.skyToPix(ra, dec); const [sx, sy] = this.gridToScreen(x, y); first ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy); first = false; if (x >= 0 && x <= g.nx && !lab) lab = [sx, sy]; }
      ctx.stroke();
      const [lx, ly] = this.gridToScreen(0, t.skyToPix(t.ra0, dec)[1]);
      if (ly > this.oy + 8 && ly < this.oy + g.ny * this.k - 4) ctx.fillText(decLabel(dec, sd), Math.max(lx + 4, 4), ly - 3);
    }
    for (let ra = Math.ceil(r0 / sr) * sr; ra <= r1; ra += sr) {
      ctx.beginPath(); let first = true;
      for (let i = 0; i <= 40; i++) { const dec = d0 + (d1 - d0) * i / 40; const [x, y] = t.skyToPix(ra, dec); const [sx, sy] = this.gridToScreen(x, y); first ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy); first = false; }
      ctx.stroke();
      const [x] = t.skyToPix(ra, t.dec0); const [sx, sy] = this.gridToScreen(x, 0);
      if (sx > this.ox + 20 && sx < this.ox + g.nx * this.k - 20) ctx.fillText(raLabel(ra, sr), sx + 3, Math.min(sy - 4, this.c.height / this.dpr - 6));
    }
    ctx.restore();
  }

  sourceVisible(s) {
    if (!S.tiers.has(s.tier)) return false;
    const groups = s.catalog?.simbad?.groups || (s.catalog?.gaia ? [gaiaGroup(s.catalog.gaia)] : ["unknown"]);
    return groups.some((gp) => S.classes.has(gp));
  }

  drawSources() {
    const ctx = this.ctx, f = this.f, ps = f.grid.pixel_scale_arcsec;
    ctx.save();
    for (const s of f.sources) {
      if (!this.sourceVisible(s)) continue;
      const [sx, sy] = this.gridToScreen(s.x, s.y), T = TIERS[s.tier] || TIERS.insufficient;
      const big = s.tier !== "stationary" && s.tier !== "insufficient";
      const r = big ? Math.max(9, 2.2 * this.k) : Math.max(4, 1.3 * this.k);
      ctx.strokeStyle = T.color; ctx.globalAlpha = big ? 1 : 0.55; ctx.lineWidth = big ? 1.8 : 1;
      ctx.setLineDash(s.tier === "systematics_limited" ? [5, 3] : s.tier === "candidate" ? [2, 3] : []);
      ctx.beginPath();
      if (s.tier === "rejected") { ctx.moveTo(sx - r * .7, sy - r * .7); ctx.lineTo(sx + r * .7, sy + r * .7); ctx.moveTo(sx + r * .7, sy - r * .7); ctx.lineTo(sx - r * .7, sy + r * .7); }
      else ctx.arc(sx, sy, r, 0, 2 * Math.PI);
      ctx.stroke();
      if (s.variability?.flag) { ctx.setLineDash([]); ctx.strokeStyle = "#5fd8ff"; ctx.beginPath(); ctx.arc(sx, sy, r + 3, -0.6, 0.6); ctx.stroke(); }
      // motion vector: true angular scale, the motion over one year
      if (S.overlays.tracks && s.fit && (s.tier === "significant" || s.tier === "systematics_limited" || s.tier === "candidate")) {
        const dx = -s.fit.pm_east / ps, dy = s.fit.pm_north / ps;
        const [ex, ey] = this.gridToScreen(s.x + dx, s.y + dy);
        ctx.setLineDash([]); ctx.globalAlpha = .9; ctx.strokeStyle = T.color; ctx.lineWidth = 1.5;
        arrow(ctx, sx, sy, ex, ey);
      }
    }
    ctx.restore();
  }

  drawCatalogs() {
    const L = S.catalogs?.layers; if (!L) return;
    const ctx = this.ctx; ctx.save(); ctx.lineWidth = 1;
    const ps = this.f.grid.pixel_scale_arcsec;
    if (S.overlays.gaia && L.gaia) {
      ctx.strokeStyle = "rgba(122,162,255,.85)";
      for (const g of L.gaia.items) {
        if (!S.classes.has(g.cls === "uncertain" || g.cls === "unclassified" ? "unknown" : g.cls) && !(g.variable && S.classes.has("variable"))) continue;
        const [sx, sy] = this.gridToScreen(g.x, g.y), r = 3.5;
        ctx.beginPath(); ctx.moveTo(sx, sy - r); ctx.lineTo(sx + r, sy); ctx.lineTo(sx, sy + r); ctx.lineTo(sx - r, sy); ctx.closePath(); ctx.stroke();
        if (g.pmra && Math.hypot(g.pmra, g.pmdec) > 100) { const [ex, ey] = this.gridToScreen(g.x - g.pmra / 1e3 / ps, g.y + g.pmdec / 1e3 / ps); ctx.setLineDash([3, 2]); arrow(ctx, sx, sy, ex, ey); ctx.setLineDash([]); }
      }
    }
    for (const [key, col] of [["twomass", "rgba(255,138,101,.8)"], ["allwise", "rgba(169,139,255,.8)"]]) {
      if (!S.overlays[key] || !L[key]) continue;
      ctx.strokeStyle = col;
      for (const g of L[key].items) { const [sx, sy] = this.gridToScreen(g.x, g.y); ctx.beginPath(); ctx.moveTo(sx - 4, sy); ctx.lineTo(sx + 4, sy); ctx.moveTo(sx, sy - 4); ctx.lineTo(sx, sy + 4); ctx.stroke(); }
    }
    if (S.overlays.simbad && L.simbad) {
      ctx.fillStyle = "rgba(228,232,242,.85)"; ctx.font = "11px system-ui"; ctx.strokeStyle = "rgba(228,232,242,.6)";
      for (const g of L.simbad.items) {
        if (!g.groups.some((gp) => S.classes.has(gp))) continue;
        const [sx, sy] = this.gridToScreen(g.x, g.y);
        ctx.beginPath(); ctx.arc(sx, sy, 2, 0, 7); ctx.stroke();
        if (this.k > 2.2 || /NAME/.test(g.id)) ctx.fillText(g.id.replace(/^NAME /, ""), sx + 5, sy - 4);
      }
    }
    ctx.restore();
  }

  drawSelected() {
    const d = S.detail; if (!d || !this.f) return;
    const ctx = this.ctx, ps = this.f.grid.pixel_scale_arcsec;
    const [sx, sy] = this.gridToScreen(d.x, d.y);
    ctx.save(); ctx.strokeStyle = "#ffd28a"; ctx.lineWidth = 1.5;
    const r = Math.max(14, 3.5 * this.k);
    ctx.beginPath(); for (const a of [0, 90, 180, 270]) { const t = a * D2R; ctx.moveTo(sx + Math.cos(t) * r, sy + Math.sin(t) * r); ctx.lineTo(sx + Math.cos(t) * (r + 7), sy + Math.sin(t) * (r + 7)); } ctx.stroke();
    // per-epoch measured positions at true scale (visible when zoomed in)
    if (S.overlays.tracks && d.points && this.k > 4) {
      for (const p of d.points) {
        const [px, py] = this.gridToScreen(d.x - p.east_arcsec / ps, d.y + p.north_arcsec / ps);
        ctx.fillStyle = p.is_reference_epoch ? "#ffffff" : "rgba(255,210,138,.85)";
        ctx.beginPath(); ctx.arc(px, py, 1.8, 0, 7); ctx.fill();
      }
    }
    // catalog history (2MASS / AllWISE / Gaia) when inside the field
    if (d.history && S.overlays.tracks) {
      ctx.font = "10.5px system-ui"; ctx.fillStyle = "#aab3c8"; ctx.strokeStyle = "rgba(170,179,200,.6)"; ctx.setLineDash([2, 3]);
      let prev = null;
      for (const h of d.history) {
        const [hx, hy] = this.gridToScreen(d.x - h.east_arcsec / ps, d.y + h.north_arcsec / ps);
        if (prev) { ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(hx, hy); ctx.stroke(); }
        ctx.beginPath(); ctx.arc(hx, hy, 3, 0, 7); ctx.stroke(); ctx.fillText(`${h.catalog} ${h.year.toFixed(0)}`, hx + 5, hy + 3); prev = [hx, hy];
      }
      if (prev) { ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(sx, sy); ctx.stroke(); }
    }
    ctx.restore();
  }

  drawScaleBar() {
    const ctx = this.ctx, H = this.c.height / this.dpr, W = this.c.width / this.dpr;
    const aps = this.arcsecPerScreenPx(), target = 110 * aps;
    const nice = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((v) => v >= target * 0.6) || 600;
    const len = nice / aps, x = W - 24 - len, y = H - 52;
    ctx.save(); ctx.strokeStyle = "#e4e8f2"; ctx.fillStyle = "#e4e8f2"; ctx.lineWidth = 1.5; ctx.font = "11px ui-monospace, Menlo, monospace";
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + len, y); ctx.moveTo(x, y - 4); ctx.lineTo(x, y + 4); ctx.moveTo(x + len, y - 4); ctx.lineTo(x + len, y + 4); ctx.stroke();
    ctx.fillText(nice >= 60 ? `${nice / 60}′` : `${nice}″`, x + len / 2 - 8, y - 7);
    // compass: north up, east left (sky convention)
    const cx = 34, cy = H - 70; ctx.strokeStyle = "rgba(228,232,242,.8)";
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - 24); ctx.moveTo(cx, cy); ctx.lineTo(cx - 24, cy); ctx.stroke();
    ctx.fillText("N", cx - 4, cy - 28); ctx.fillText("E", cx - 36, cy + 4);
    ctx.restore();
  }

  pick(sx, sy) {
    let best = null, bd = 14;
    for (const s of this.f.sources) {
      if (!S.overlays.sources || !this.sourceVisible(s)) continue;
      const [px, py] = this.gridToScreen(s.x, s.y), d = Math.hypot(px - sx, py - sy);
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
        this.ox = drag.ox + dx; this.oy = drag.oy + dy; this.draw();
      }
      if (this.f) {
        const [x, y] = this.screenToGrid(sx, sy), [ra, dec] = this.tan.pixToSky(x, y);
        const hit = this.pick(sx, sy);
        emit("cursor", { ra, dec, inside: x >= 0 && y >= 0 && x < this.f.grid.nx && y < this.f.grid.ny, hit });
        c.style.cursor = hit ? "pointer" : (drag ? "grabbing" : "grab");
      }
    });
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

function arrow(ctx, x0, y0, x1, y1) {
  const a = Math.atan2(y1 - y0, x1 - x0), h = 6;
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  if (Math.hypot(x1 - x0, y1 - y0) < 4) return;
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x1 - h * Math.cos(a - .45), y1 - h * Math.sin(a - .45)); ctx.moveTo(x1, y1); ctx.lineTo(x1 - h * Math.cos(a + .45), y1 - h * Math.sin(a + .45)); ctx.stroke();
}
function decLabel(dec, step) { const s = dec < 0 ? "−" : "+", d = Math.abs(dec) + 1e-9, dd = Math.floor(d), mm = (d - dd) * 60; return step < 1 / 60 ? `${s}${dd}°${mm.toFixed(1)}′` : `${s}${dd}°${Math.round(mm).toString().padStart(2, "0")}′`; }
function raLabel(ra, step) { ra = (ra + 360) % 360; const h = ra / 15, hh = Math.floor(h + 1e-9), mm = (h - hh) * 60, m = Math.floor(mm + 1e-6), ss = (mm - m) * 60; return step * 4 < 1 ? `${hh}h${m}m${ss.toFixed(0)}s` : `${hh}h${String(Math.round(mm)).padStart(2, "0")}m`; }
export function gaiaGroup(g) {
  if (g.variable) return "variable";
  const c = [["star", g.p_star], ["galaxy", g.p_galaxy], ["quasar", g.p_quasar]].sort((a, b) => (b[1] || 0) - (a[1] || 0))[0];
  return (c[1] || 0) >= 0.5 ? c[0] : "unknown";
}
export function cursorText(ra, dec) { return `${sexa(ra, dec)}  ·  ${fmt(ra, 5)}°, ${fmt(dec, 5)}°`; }
