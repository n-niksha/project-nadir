// Application state + a tiny event bus.  Views subscribe to named events.
export const S = {
  query: null,          // {ra, dec, size, band}
  field: null,          // field summary from /api/fields/{fid}
  fid: null,
  a: null, b: null,     // observation indices being compared
  frame: null,          // observation index shown in "animate" mode
  cmp: "a",             // a | b | blink | overlay | diff | play
  playing: false,
  fps: 4,
  opacity: 0.5,
  matchedOnly: true,
  playbackMode: "motion",
  visualPreset: "discovery",
  render: "presentation", // presentation (smoothed, display-only) | science (sampled pixels + masks)
  explanationLevel: "explore",
  selected: null,       // source id
  detail: null,         // /sources/{id}
  pair: null,           // /pair?a&b
  catalogs: null,       // overlay layers
  overlays: { sources: true, refs: false, tracks: true, grid: true, gaia: false, simbad: true, twomass: false, allwise: false },
  tiers: new Set(["significant", "systematics_limited", "candidate", "rejected", "stationary"]),
  classes: new Set(["star", "galaxy", "quasar", "variable", "other", "unknown"]),
  view: "time",
  tab: "overview",
};
const bus = new EventTarget();
export const on = (name, fn) => bus.addEventListener(name, (e) => fn(e.detail));
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));

export const TIERS = {
  // colours mirror --st-* in css/tokens.css
  significant: { label: "Significant motion", color: "#f2b350", short: "Motion confirmed across visits" },
  systematics_limited: { label: "Motion · systematics-limited", color: "#f2b350", short: "Real motion, accuracy limited by saturation" },
  candidate: { label: "Low-confidence candidate", color: "#ab98ec", short: "Formally significant but fails a consistency test" },
  rejected: { label: "Rejected", color: "#ef7d6d", short: "A 2-image detection the other visits did not reproduce" },
  stationary: { label: "No significant motion", color: "#5ec8b0", short: "Consistent with a fixed position (reference star)" },
  insufficient: { label: "Insufficient evidence", color: "#8a91a3", short: "Too few visits to say anything" },
};
