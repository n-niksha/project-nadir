// Observation timeline: x = time (each visit gets its own segment, because
// visits are months apart but exposures within a visit are minutes-to-days
// apart), y = wavelength at the target.  This makes SPHEREx's linear-variable-
// filter stepping visible: each visit climbs through wavelength.
import { S, emit } from "./state.js";
import { esc, fmt, dateTime, lambdaColor } from "./fmt.js";

export class Timeline {
  constructor(el) { this.el = el; this.el.addEventListener("click", (e) => this._click(e)); this.el.addEventListener("keydown", (e) => this._key(e)); }
  setField(f) { this.f = f; this.render(); }

  layout() {
    const f = this.f, eps = f.epochs.filter((e) => e.usable);
    const visits = new Map();
    for (const e of eps) {
      const v = f.visits.findIndex((vv) => e.mjd >= vv.mjd_min - 1e-6 && e.mjd <= vv.mjd_max + 1e-6);
      if (!visits.has(v)) visits.set(v, []);
      visits.get(v).push(e);
    }
    return { eps, visits: [...visits.entries()].sort((a, b) => a[0] - b[0]) };
  }

  render() {
    const f = this.f; if (!f) return;
    const r = this.el.getBoundingClientRect(), W = Math.max(320, r.width), H = Math.max(60, r.height);
    const { eps, visits } = this.layout();
    const m = { l: 44, r: 10, t: 6, b: 18 }, gap = 26;
    const wl = eps.map((e) => e.wavelength_um), lo = Math.min(...wl), hi = Math.max(...wl);
    const total = visits.reduce((a, [, list]) => a + Math.max(list.length, 4), 0);
    const pw = W - m.l - m.r - gap * (visits.length - 1), unit = pw / total;
    const Y = (w) => m.t + (H - m.t - m.b) * (1 - (w - lo) / Math.max(hi - lo, 1e-6));
    let x = m.l, svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`;
    svg += `<text x="4" y="${Y(hi) + 4}" fill="#7a849c" font-size="10">${fmt(hi, 2)}µm</text><text x="4" y="${Y(lo)}" fill="#7a849c" font-size="10">${fmt(lo, 2)}µm</text>`;
    this.pos = new Map();
    const matchedTo = S.a != null ? f.epochs[S.a]?.wavelength_um : null;
    for (const [, list] of visits) {
      const w = Math.max(list.length, 4) * unit;
      svg += `<rect x="${x - 4}" y="${m.t - 2}" width="${w + 8}" height="${H - m.b - m.t + 4}" rx="6" fill="rgba(122,162,255,.035)" stroke="rgba(122,162,255,.12)"/>`;
      const d0 = list[0].date?.slice(0, 10), d1 = list[list.length - 1].date?.slice(0, 10);
      svg += `<text x="${x}" y="${H - 4}" fill="#aab3c8" font-size="10.5">${esc(d0 === d1 ? d0 : `${d0} → ${d1.slice(5)}`)} · ${list.length}</text>`;
      list.forEach((e, k) => {
        const cx = x + (k + 0.5) * w / list.length, cy = Y(e.wavelength_um);
        this.pos.set(e.index, [cx, cy]);
        const isA = e.index === S.a, isB = e.index === S.b, isF = S.cmp === "play" && e.index === S.frame;
        const dim = S.matchedOnly && matchedTo && Math.abs(e.wavelength_um / matchedTo - 1) > 0.02 && !isA;
        const reg = e.registration || {}, rms = reg.rms_native_px;
        const title = `${e.obs_id} · ${dateTime(e.date)} UTC · λ ${fmt(e.wavelength_um, 3)} µm · ${e.release}` + (rms != null ? ` · registration ${fmt(rms, 3)} px` : "") + (e.is_reference ? " · reference epoch" : "");
        svg += `<g class="tl-epoch" data-i="${e.index}" tabindex="0" role="option" aria-selected="${isA || isB}" aria-label="${esc(title)}"><title>${esc(title)}</title>`;
        svg += `<rect class="hit" x="${cx - Math.max(3, w / list.length / 2)}" y="${m.t}" width="${Math.max(6, w / list.length)}" height="${H - m.t - m.b}" fill="transparent"/>`;
        svg += `<circle cx="${cx}" cy="${cy}" r="${isA || isB || isF ? 5 : 3.2}" fill="${lambdaColor(e.wavelength_um, lo * 0.97, hi * 1.03)}" opacity="${dim ? 0.18 : 0.95}" ${isF ? 'stroke="#fff" stroke-width="2"' : ""}/>`;
        if (isA || isB) svg += `<text x="${cx}" y="${cy - 9}" text-anchor="middle" font-size="11" font-weight="700" fill="${isA ? "#7aa2ff" : "#ffb547"}">${isA ? "A" : "B"}</text>`;
        svg += `</g>`;
      });
      x += w + gap;
    }
    svg += "</svg>";
    this.el.innerHTML = svg;
  }

  _click(e) {
    const g = e.target.closest(".tl-epoch"); if (!g) return;
    const i = +g.dataset.i;
    if (S.cmp === "play") emit("frame", i);
    else emit(e.shiftKey || e.altKey ? "setA" : "setB", i);
  }
  _key(e) {
    const g = e.target.closest(".tl-epoch"); if (!g) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); emit(e.shiftKey ? "setA" : "setB", +g.dataset.i); }
  }
}
