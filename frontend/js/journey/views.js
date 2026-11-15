// Evidence cards and compact charts shared by the cinematic journey (Stage A)
// and "Explore it yourself" (Stage B).  Every value carries a provenance tag.
import { esc, fmt, niceDate, lambdaColor, mjdToDate, OTYPE, displayName } from "../fmt.js";
import { plot } from "../plots.js";
import { C } from "../tokens.js";
import { TIERS } from "../state.js";

export const TAG = {
  measured: '<span class="tag measured">measured</span>', catalog: '<span class="tag cat">catalog</span>',
  inferred: '<span class="tag inferred">inferred</span>', illustrative: '<span class="tag illustrative">illustrative</span>',
  unknown: '<span class="tag unknown">unknown</span>', literature: '<span class="tag lit">literature</span>',
};

/** rows: [{k, v, tag, note}] */
export function facts(rows) {
  return `<dl class="jr-facts">${rows.filter(Boolean).map((r) => `<dt>${esc(r.k)}</dt><dd>${r.v} ${TAG[r.tag] || ""}${r.note ? `<span class="small">${r.note}</span>` : ""}</dd>`).join("")}</dl>`;
}

export function card({ kicker, title, body, cls = "" }) {
  return `<article class="jr-card ${cls}"><p class="jr-kicker">${esc(kicker)}</p>${title ? `<h3>${title}</h3>` : ""}${body}</article>`;
}

export function frameList(frames, active) {
  return `<ol class="jr-frames">${frames.map((e, i) => `<li class="${e.index === active ? "on" : ""}" data-frame="${e.index}"><b>Visit ${i + 1}</b><span>${esc(niceDate(e.date))}</span><span class="lam" style="color:${lambdaColor(e.wavelength_um)}">λ ${fmt(e.wavelength_um, 3)} µm</span></li>`).join("")}</ol>`;
}

/** Path on the sky: measured visit means (or epochs), the fitted motion and Gaia's prediction. */
export function motionChart(d, { w = 320, h = 230, id = "jrpath" } = {}) {
  const f = d.fit; if (!f) return "";
  const vp = f.visit_points || [];
  const pts = vp.length ? vp.map((v, i) => ({ x: v.east_arcsec, y: v.north_arcsec, ex: v.sigma_east_arcsec, ey: v.sigma_north_arcsec, color: C("sun"), r: 4.5, title: `Visit ${i + 1} mean of ${v.n} exposures (${mjdToDate(v.mjd)}): E ${fmt(v.east_arcsec, 2)}″, N ${fmt(v.north_arcsec, 2)}″ ± ${fmt(v.sigma_north_arcsec, 1)}″` }))
    : (d.points || []).map((p) => ({ x: p.east_arcsec, y: p.north_arcsec, ey: p.sigma_north_arcsec, ex: p.sigma_east_arcsec, color: C("sun"), r: 2.4, opacity: .75, title: `${mjdToDate(p.mjd)}: E ${fmt(p.east_arcsec, 2)}″, N ${fmt(p.north_arcsec, 2)}″` }));
  const t0 = f.t0_mjd, ts = (vp.length ? vp : d.points || []).map((p) => (p.mjd - t0) / 365.25);
  const tl = [Math.min(...ts) - 0.05, Math.max(...ts) + 0.05];
  const gaia = (d.gaia_curve || []).map((g) => ({ x: g.east_arcsec, y: g.north_arcsec }));
  return plot({ id, w, h, equal: true, zero: "xy", margin: { l: 44, b: 38 }, x: { label: "East offset (″) · east is left", reverse: true, ticks: 4 }, y: { label: "North offset (″)", ticks: 4 },
    hint: "Hover a point for its values.",
    series: [
      ...(gaia.length ? [{ type: "line", data: gaia, color: C("sky"), dash: "5 4", title: "Gaia DR3 prediction" }] : []),
      { type: "arrow", x0: f.offset_east + f.pm_east * tl[0], y0: f.offset_north + f.pm_north * tl[0], x1: f.offset_east + f.pm_east * tl[1], y1: f.offset_north + f.pm_north * tl[1] },
      { type: "points", data: pts },
    ] });
}

/** Compact measured spectrum with the colour model and catalog photometry. */
export function spectrumChart(sp, { w = 320, h = 220, id = "jrspec" } = {}) {
  const P = sp.points || [];
  const ys = P.flatMap((p) => [p.flux_mjy + p.flux_err_mjy]).concat((sp.catalog_points || []).map((c) => c.flux_mjy)).filter(Number.isFinite);
  const series = [];
  if (sp.blackbody) series.push({ type: "line", data: sp.blackbody.curve.wavelength_um.map((x, i) => ({ x, y: sp.blackbody.curve.flux_mjy[i] })), color: C("violet"), dash: "6 4", width: 1.8 });
  series.push({ type: "points", data: P.filter((p) => !p.flags?.length).map((p) => ({ x: p.wavelength_um, y: p.flux_mjy, ey: p.flux_err_mjy, color: lambdaColor(p.wavelength_um), r: 2.4, opacity: .9, title: `${fmt(p.wavelength_um, 3)} µm · ${fmt(p.flux_mjy, 2)} ± ${fmt(p.flux_err_mjy, 2)} mJy · ${mjdToDate(p.mjd)}` })) });
  series.push({ type: "points", shape: "square", hollow: true, r: 4.5, data: (sp.catalog_points || []).map((c) => ({ x: c.wavelength_um, y: c.flux_mjy, color: C("text-1"), title: `${c.catalog} ${c.band}: ${fmt(c.flux_mjy, 1)} mJy (catalog)` })) });
  return plot({ id, w, h, margin: { l: 48, b: 38 }, x: { label: "Wavelength (µm)", domain: [0.7, 5.1], ticks: 5 }, y: { label: "Flux density (mJy)", domain: [0, Math.max(1, ...ys) * 1.08], ticks: 4 },
    hint: "Each dot is one exposure. Hover for its values.", series });
}

export function legend(items) {
  return `<div class="legend-row">${items.map(([sw, t]) => `<span>${sw}${t}</span>`).join("")}</div>`;
}
export const SW = {
  dot: (c) => `<i style="background:${c}"></i>`, line: (c) => `<i class="ln" style="background:${c}"></i>`, dash: () => '<i class="ln dash"></i>',
  square: () => `<i style="border:1.5px solid ${C("text-1")};border-radius:2px;background:none"></i>`,
};

export function identity(d) {
  const sb = d.catalog?.simbad;
  return sb ? (OTYPE[sb.otype] || sb.otype) : d.catalog?.gaia ? "Gaia DR3 source (no SIMBAD entry)" : "Unidentified: no catalog counterpart";
}

export function tierBadge(d) { return `<span class="tier ${d.tier}">${esc(TIERS[d.tier]?.label || d.tier)}</span>`; }
export { displayName };
