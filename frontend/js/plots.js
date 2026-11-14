// Small dependency-free SVG plotting for scientific panels.
import { esc } from "./fmt.js";

export function niceTicks(lo, hi, n = 5) {
  if (!(hi > lo)) { hi = lo + 1; }
  const span = hi - lo, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

function extent(vals, pad = 0.08) {
  const f = vals.filter((v) => Number.isFinite(v));
  if (!f.length) return [0, 1];
  let lo = Math.min(...f), hi = Math.max(...f);
  if (hi === lo) { lo -= 1; hi += 1; }
  const p = (hi - lo) * pad;
  return [lo - p, hi + p];
}

/* spec: {w, h, x:{label, domain?, reverse?, fmt?}, y:{...}, equal?, series:[...]}
   series types: points {data:[{x,y,ex,ey,color,r,title,hollow}]}, line {data:[{x,y}], color, dash, width},
                 vline {x, color, label, dash}, hline {y, color}, text {x,y,text,color,anchor}, arrow {x0,y0,x1,y1,color} */
export function plot(spec) {
  const W = spec.w || 400, H = spec.h || 220, m = { l: 46, r: 12, t: 12, b: 34, ...(spec.margin || {}) };
  const all = (k) => spec.series.flatMap((s) => {
    if (s.type === "points") return s.data.flatMap((d) => [d[k] - (d["e" + k] || 0), d[k] + (d["e" + k] || 0)]);
    if (s.type === "line") return s.data.map((d) => d[k]);
    if (s.type === "arrow") return [s[k + "0"], s[k + "1"]];
    if (s.type === "vline" && k === "x") return [s.x];
    if (s.type === "hline" && k === "y") return [s.y];
    return [];
  });
  let [x0, x1] = spec.x.domain || extent(all("x"));
  let [y0, y1] = spec.y.domain || extent(all("y"));
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  if (spec.equal) { // same angular scale on both axes
    const sx = (x1 - x0) / pw, sy = (y1 - y0) / ph, s = Math.max(sx, sy);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    x0 = cx - s * pw / 2; x1 = cx + s * pw / 2; y0 = cy - s * ph / 2; y1 = cy + s * ph / 2;
  }
  const X = (v) => m.l + (spec.x.reverse ? (x1 - v) : (v - x0)) / (x1 - x0) * pw;
  const Y = (v) => m.t + (y1 - v) / (y1 - y0) * ph;
  const fx = spec.x.fmt || ((v) => +v.toPrecision(4)), fy = spec.y.fmt || ((v) => +v.toPrecision(4));
  let s = `<svg class="plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.title || spec.y.label + " vs " + spec.x.label)}">`;
  s += `<defs><clipPath id="c${spec.id || "p"}"><rect x="${m.l}" y="${m.t}" width="${pw}" height="${ph}"/></clipPath>
        <marker id="ah${spec.id || "p"}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#ffb547"/></marker></defs>`;
  for (const t of niceTicks(x0, x1, spec.x.ticks || 5)) s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${m.t}" y2="${m.t + ph}"/><text x="${X(t)}" y="${H - m.b + 14}" text-anchor="middle">${esc(fx(t))}</text>`;
  for (const t of niceTicks(y0, y1, spec.y.ticks || 5)) s += `<line class="grid" x1="${m.l}" x2="${m.l + pw}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 6}" y="${Y(t) + 3.5}" text-anchor="end">${esc(fy(t))}</text>`;
  s += `<rect class="ax" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" fill="none"/>`;
  s += `<text x="${m.l + pw / 2}" y="${H - 4}" text-anchor="middle">${esc(spec.x.label)}</text>`;
  s += `<text transform="translate(11 ${m.t + ph / 2}) rotate(-90)" text-anchor="middle">${esc(spec.y.label)}</text>`;
  s += `<g clip-path="url(#c${spec.id || "p"})">`;
  for (const se of spec.series) {
    if (se.type === "line" && se.data.length) {
      const d = se.data.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
      s += `<path d="${d}" fill="none" stroke="${se.color || "#aab3c8"}" stroke-width="${se.width || 1.5}" ${se.dash ? `stroke-dasharray="${se.dash}"` : ""} opacity="${se.opacity ?? 1}"><title>${esc(se.title || "")}</title></path>`;
    } else if (se.type === "vline") {
      s += `<line x1="${X(se.x)}" x2="${X(se.x)}" y1="${m.t}" y2="${m.t + ph}" stroke="${se.color || "#4fd1b5"}" stroke-dasharray="${se.dash || "2 3"}" opacity=".7"/>`;
      if (se.label) s += `<text x="${X(se.x) + 2}" y="${m.t + 10 + (se.row || 0) * 11}" style="fill:${se.color || "#4fd1b5"}">${esc(se.label)}</text>`;
    } else if (se.type === "hline") {
      s += `<line x1="${m.l}" x2="${m.l + pw}" y1="${Y(se.y)}" y2="${Y(se.y)}" stroke="${se.color || "#7a849c"}" stroke-dasharray="${se.dash || "3 3"}"/>`;
    } else if (se.type === "arrow") {
      s += `<line x1="${X(se.x0)}" y1="${Y(se.y0)}" x2="${X(se.x1)}" y2="${Y(se.y1)}" stroke="${se.color || "#ffb547"}" stroke-width="1.6" marker-end="url(#ah${spec.id || "p"})"/>`;
    } else if (se.type === "text") {
      s += `<text x="${X(se.x)}" y="${Y(se.y)}" text-anchor="${se.anchor || "start"}" style="fill:${se.color || "#aab3c8"}">${esc(se.text)}</text>`;
    } else if (se.type === "points") {
      for (const d of se.data) {
        if (!Number.isFinite(d.x) || !Number.isFinite(d.y)) continue;
        const c = d.color || se.color || "#7aa2ff", r = d.r || se.r || 2.6;
        let g = `<g opacity="${d.opacity ?? se.opacity ?? 1}" ${d.title ? `tabindex="0" role="img" aria-label="${esc(d.title)}" data-point-info="${esc(d.title)}"` : ''}>${d.title ? `<title>${esc(d.title)}</title>` : ""}`;
        if (d.ex) g += `<line x1="${X(d.x - d.ex)}" x2="${X(d.x + d.ex)}" y1="${Y(d.y)}" y2="${Y(d.y)}" stroke="${c}" stroke-width="1" opacity=".55"/>`;
        if (d.ey) g += `<line x1="${X(d.x)}" x2="${X(d.x)}" y1="${Y(d.y - d.ey)}" y2="${Y(d.y + d.ey)}" stroke="${c}" stroke-width="1" opacity=".55"/>`;
        const shape = d.shape || se.shape;
        if (shape === "square") g += `<rect x="${X(d.x) - r}" y="${Y(d.y) - r}" width="${2 * r}" height="${2 * r}" fill="${d.hollow || se.hollow ? "none" : c}" stroke="${c}" stroke-width="1.3"/>`;
        else if (shape === "diamond") g += `<path d="M${X(d.x)},${Y(d.y) - r * 1.3} l${r * 1.3},${r * 1.3} l${-r * 1.3},${r * 1.3} l${-r * 1.3},${-r * 1.3}z" fill="${d.hollow || se.hollow ? "none" : c}" stroke="${c}" stroke-width="1.3"/>`;
        else g += `<circle cx="${X(d.x)}" cy="${Y(d.y)}" r="${r}" fill="${d.hollow || se.hollow ? "none" : c}" stroke="${c}" stroke-width="1.2"/>`;
        s += g + "</g>";
      }
    }
  }
  s += "</g></svg>";
  return s;
}
