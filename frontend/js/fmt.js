export const fmt = (v, n = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(n);
export const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const pm = (v, e, n = 2) => `${fmt(v, n)} ± ${fmt(e, n)}`;
export function sexa(ra, dec) {
  const h = ((ra / 15) % 24 + 24) % 24, hh = Math.floor(h), mm = Math.floor((h - hh) * 60), ss = ((h - hh) * 60 - mm) * 60;
  const s = dec < 0 ? "−" : "+", d = Math.abs(dec), dd = Math.floor(d), dm = Math.floor((d - dd) * 60), ds = ((d - dd) * 60 - dm) * 60;
  return `${String(hh).padStart(2, "0")}h${String(mm).padStart(2, "0")}m${ss.toFixed(1).padStart(4, "0")}s ${s}${String(dd).padStart(2, "0")}°${String(dm).padStart(2, "0")}′${ds.toFixed(0).padStart(2, "0")}″`;
}
export const date = (iso) => (iso || "").slice(0, 10);
export const dateTime = (iso) => (iso || "").slice(0, 16).replace("T", " ");
export function mjdToDate(mjd) { return new Date((mjd - 40587) * 86400000).toISOString().slice(0, 10); }
// SPHEREx wavelength (0.75–5 µm) -> colour: violet/blue (short) through teal to amber/red (long).
export function lambdaColor(wl, lo = 0.75, hi = 5.0) {
  const t = Math.max(0, Math.min(1, (Math.log(wl) - Math.log(lo)) / (Math.log(hi) - Math.log(lo))));
  const stops = [[122, 120, 255], [95, 170, 255], [79, 209, 181], [255, 210, 120], [255, 140, 90]];
  const p = t * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(p)), f = p - i;
  const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
export const VISIT_COLORS = ["#7aa2ff", "#ffb547", "#4fd1b5", "#a98bff", "#ff8a65", "#5fd8ff"];
export const OTYPE = {
  "*": "Star", "PM*": "High proper-motion star", "HPM*": "High proper-motion star", "BY*": "BY Draconis variable (spotted red dwarf)",
  "Fl*": "Flare star", "WD*": "White dwarf", "low-mass*": "Low-mass star", "BD*": "Brown dwarf", "**": "Double star", "SB*": "Spectroscopic binary",
  "EB*": "Eclipsing binary", "V*": "Variable star", "RR*": "RR Lyrae variable", "Mi*": "Mira variable", "LP*": "Long-period variable",
  "G": "Galaxy", "QSO": "Quasar", "AGN": "Active galaxy", "Y*O": "Young stellar object", "TT*": "T Tauri star", "PN": "Planetary nebula",
  "HII": "HII region", "C*": "Carbon star", "RG*": "Red giant", "Ro*": "Rotating variable", "Ir*": "Irregular variable", "IR": "Infrared source",
  "X": "X-ray source", "Rad": "Radio source", "ClG": "Galaxy cluster", "GiC": "Galaxy in cluster", "Pl": "Exoplanet",
};
