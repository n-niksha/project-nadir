// Colour tokens for canvas and SVG drawing, read from css/tokens.css at runtime
// so the stylesheet stays the single source of truth.  Fallbacks keep the
// module importable outside a browser (node tests).
const FALLBACK = {
  "ink-0": "#03050a", "text-1": "#ece6da", "text-2": "#b6bbc8", "text-3": "#8a91a3",
  sun: "#f2b350", "sun-2": "#ffd699", sky: "#8fa8e8", violet: "#ab98ec", teal: "#5ec8b0", coral: "#ef7d6d", cyan: "#6fd3f0",
  "st-significant": "#f2b350", "st-systematic": "#f2b350", "st-candidate": "#ab98ec", "st-rejected": "#ef7d6d",
  "st-stationary": "#5ec8b0", "st-insufficient": "#8a91a3", "st-variable": "#6fd3f0",
  "cat-gaia": "#8fa8e8", "cat-2mass": "#ef9a6d", "cat-wise": "#ab98ec", "cat-simbad": "#ece6da",
  "series-1": "#8fa8e8", "series-2": "#f2b350", "series-3": "#5ec8b0", "series-4": "#ab98ec", "series-5": "#ef8f6d", "series-6": "#6fd3f0",
};
let cache = null;
export function C(name) {
  if (!cache) {
    cache = { ...FALLBACK };
    if (typeof document !== "undefined") {
      const cs = getComputedStyle(document.documentElement);
      for (const key of Object.keys(FALLBACK)) {
        let v = cs.getPropertyValue(`--${key}`).trim();
        if (v.startsWith("var(")) v = cs.getPropertyValue(v.slice(4, -1).trim()).trim();
        if (v) cache[key] = v;
      }
    }
  }
  return cache[name] ?? FALLBACK[name];
}
/** hex colour + alpha -> rgba() */
export function alpha(hex, a) {
  const h = hex.replace("#", ""), n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
export const FONT = { sans: '"IBM Plex Sans", system-ui, sans-serif', mono: '"IBM Plex Mono", ui-monospace, Menlo, monospace' };
export const seriesColor = (i) => C(`series-${(i % 6) + 1}`);
