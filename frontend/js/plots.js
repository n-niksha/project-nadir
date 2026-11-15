// Small dependency-free SVG plotting for scientific panels.
// Charts scale with their container (viewBox), use token colours, give every
// data point a generous invisible hit area, and (by default) a readout line
// below the chart that shows the exact values of the hovered/focused point.
import { esc } from "./fmt.js";
import { C } from "./tokens.js";

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

const minus = (v) => String(v).replace("-", "−");

/* spec: {id, w, h, x:{label, domain?, reverse?, fmt?, ticks?}, y:{...}, equal?, zero?, readout?, hint?, series:[...]}
   series types: points {data:[{x,y,ex,ey,color,r,title,hollow,shape,opacity}]}, line {data:[{x,y}], color, dash, width},
                 band {data:[{x,lo,hi}], color}, vline {x, color, label, dash}, hline {y, color},
                 text {x,y,text,color,anchor}, arrow {x0,y0,x1,y1,color} */
export function plot(spec) {
  const W = spec.w || 400, H = spec.h || 220, m = { l: 50, r: 14, t: 14, b: 40, ...(spec.margin || {}) };
  const id = spec.id || "p";
  const all = (k) => spec.series.flatMap((s) => {
    if (s.type === "points") return s.data.flatMap((d) => [d[k] - (d["e" + k] || 0), d[k] + (d["e" + k] || 0)]);
    if (s.type === "line") return s.data.map((d) => d[k]);
    if (s.type === "band") return k === "x" ? s.data.map((d) => d.x) : s.data.flatMap((d) => [d.lo, d.hi]);
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
  const fx = spec.x.fmt || ((v) => minus(+v.toPrecision(4))), fy = spec.y.fmt || ((v) => minus(+v.toPrecision(4)));
  let s = `<svg class="plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.title || spec.y.label + " versus " + spec.x.label)}">`;
  s += `<defs><clipPath id="c${id}"><rect x="${m.l}" y="${m.t}" width="${pw}" height="${ph}"/></clipPath>
        <marker id="ah${id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${C("sun")}"/></marker></defs>`;
  for (const t of niceTicks(x0, x1, spec.x.ticks || 5)) s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${m.t}" y2="${m.t + ph}"/><text class="tick" x="${X(t)}" y="${H - m.b + 16}" text-anchor="middle">${esc(fx(t))}</text>`;
  for (const t of niceTicks(y0, y1, spec.y.ticks || 5)) s += `<line class="grid" x1="${m.l}" x2="${m.l + pw}" y1="${Y(t)}" y2="${Y(t)}"/><text class="tick" x="${m.l - 7}" y="${Y(t) + 4}" text-anchor="end">${esc(fy(t))}</text>`;
  if (spec.zero !== false) {
    if (y0 < 0 && y1 > 0) s += `<line class="zero" x1="${m.l}" x2="${m.l + pw}" y1="${Y(0)}" y2="${Y(0)}"/>`;
    if (spec.zero === "xy" && x0 < 0 && x1 > 0) s += `<line class="zero" x1="${X(0)}" x2="${X(0)}" y1="${m.t}" y2="${m.t + ph}"/>`;
  }
  s += `<rect class="ax" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" fill="none"/>`;
  s += `<text class="axlabel" x="${m.l + pw / 2}" y="${H - 6}" text-anchor="middle">${esc(spec.x.label)}</text>`;
  s += `<text class="axlabel" transform="translate(13 ${m.t + ph / 2}) rotate(-90)" text-anchor="middle">${esc(spec.y.label)}</text>`;
  s += `<g clip-path="url(#c${id})">`;
  for (const se of spec.series) {
    if (se.type === "band" && se.data.length) {
      const top = se.data.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.hi).toFixed(1)}`).join("");
      const bot = se.data.slice().reverse().map((p) => `L${X(p.x).toFixed(1)},${Y(p.lo).toFixed(1)}`).join("");
      s += `<path d="${top}${bot}Z" fill="${se.color || C("sun")}" opacity="${se.opacity ?? .14}"/>`;
    } else if (se.type === "line" && se.data.length) {
      const d = se.data.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
      s += `<path d="${d}" fill="none" stroke="${se.color || C("text-2")}" stroke-width="${se.width || 1.6}" stroke-linecap="round" ${se.dash ? `stroke-dasharray="${se.dash}"` : ""} opacity="${se.opacity ?? 1}"><title>${esc(se.title || "")}</title></path>`;
    } else if (se.type === "vline") {
      s += `<line x1="${X(se.x)}" x2="${X(se.x)}" y1="${m.t}" y2="${m.t + ph}" stroke="${se.color || C("teal")}" stroke-dasharray="${se.dash || "2 3"}" opacity=".7"/>`;
      if (se.label) s += `<text transform="translate(${X(se.x) - 3} ${m.t + 4}) rotate(-90)" text-anchor="end" style="fill:${se.color || C("teal")};font-size:10.5px">${esc(se.label)}</text>`;
    } else if (se.type === "hline") {
      s += `<line x1="${m.l}" x2="${m.l + pw}" y1="${Y(se.y)}" y2="${Y(se.y)}" stroke="${se.color || C("text-3")}" stroke-dasharray="${se.dash || "3 3"}"/>`;
    } else if (se.type === "arrow") {
      s += `<line x1="${X(se.x0)}" y1="${Y(se.y0)}" x2="${X(se.x1)}" y2="${Y(se.y1)}" stroke="${se.color || C("sun")}" stroke-width="2" marker-end="url(#ah${id})"/>`;
    } else if (se.type === "text") {
      s += `<text x="${X(se.x)}" y="${Y(se.y)}" text-anchor="${se.anchor || "start"}" style="fill:${se.color || C("text-2")}">${esc(se.text)}</text>`;
    }
  }
  // points last so they sit above lines; error bars drawn under markers
  for (const se of spec.series) {
    if (se.type !== "points") continue;
    for (const d of se.data) {
      if (!Number.isFinite(d.x) || !Number.isFinite(d.y)) continue;
      const c = d.color || se.color || C("sky"), r = d.r || se.r || 3;
      const cx = X(d.x), cy = Y(d.y);
      let g = `<g opacity="${d.opacity ?? se.opacity ?? 1}" ${d.title ? `tabindex="0" role="img" aria-label="${esc(d.title)}" data-point-info="${esc(d.title)}"` : ""}>${d.title ? `<title>${esc(d.title)}</title>` : ""}`;
      if (d.ex) g += `<line x1="${X(d.x - d.ex)}" x2="${X(d.x + d.ex)}" y1="${cy}" y2="${cy}" stroke="${c}" stroke-width="1" opacity=".5"/>`;
      if (d.ey) g += `<line x1="${cx}" x2="${cx}" y1="${Y(d.y - d.ey)}" y2="${Y(d.y + d.ey)}" stroke="${c}" stroke-width="1.1" opacity=".6"/>`;
      const shape = d.shape || se.shape, fill = d.hollow || se.hollow ? "#03050a" : c;
      if (shape === "square") g += `<rect x="${cx - r}" y="${cy - r}" width="${2 * r}" height="${2 * r}" rx="1" fill="${fill}" stroke="${c}" stroke-width="1.4"/>`;
      else if (shape === "diamond") g += `<path d="M${cx},${cy - r * 1.3} l${r * 1.3},${r * 1.3} l${-r * 1.3},${r * 1.3} l${-r * 1.3},${-r * 1.3}z" fill="${fill}" stroke="${c}" stroke-width="1.4"/>`;
      else g += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${d.hollow || se.hollow ? c : "#03050a"}" stroke-width="${d.hollow || se.hollow ? 1.4 : .8}"/>`;
      if (d.title) g += `<circle cx="${cx}" cy="${cy}" r="${Math.max(8, r + 5)}" fill="transparent"/>`;
      s += g + "</g>";
    }
  }
  s += "</g></svg>";
  if (spec.readout === false) return s;
  return `<figure class="chart">${s}<figcaption class="plot-readout" aria-live="polite">${esc(spec.hint || "Hover or focus a point for its exact values.")}</figcaption></figure>`;
}
