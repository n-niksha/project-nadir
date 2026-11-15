// Data selection for journeys.  Pure functions over the API payloads, so the
// choices a journey makes (which frames, which reference stars, which star can
// demonstrate a spectrum) are testable and never invented.
import { epochList, visitGroups } from "../playback.js";

export const MOVING = new Set(["significant", "systematics_limited"]);
export const MOON_ARCSEC = 1865; // mean apparent diameter of the full Moon (≈31.1′)

/** One real, registered, wavelength-matched frame per visit (closest to the anchor's wavelength). */
export function visitFrames(field, anchor) {
  const wl = field?.epochs?.find((e) => e.index === anchor)?.wavelength_um;
  if (!Number.isFinite(wl)) return [];
  const list = epochList(field, anchor, "motion");
  // the server's own visit definition (same as the motion fit), else a 30-day gap rule
  const groups = field.visits?.length
    ? field.visits.map((v) => list.filter((e) => e.mjd >= v.mjd_min - 1e-6 && e.mjd <= v.mjd_max + 1e-6)).filter((g) => g.length)
    : visitGroups(list);
  return groups
    .map((group) => group.slice().sort((a, b) => Math.abs(a.wavelength_um - wl) - Math.abs(b.wavelength_um - wl) || a.mjd - b.mjd)[0])
    .filter(Boolean);
}

/** Nearby, clean, unsaturated stationary stars: the "ruler" shown around the subject. */
export function referenceStars(field, subject, n = 6, radius = 48) {
  return (field?.sources || [])
    .filter((s) => s.id !== subject.id && s.tier === "stationary" && !s.saturated && (s.clean_fraction ?? 0) >= 0.8 && (s.blended_fraction ?? 0) < 0.1)
    .map((s) => ({ s, d: Math.hypot(s.x - subject.x, s.y - subject.y) }))
    .filter((o) => o.d > 5 && o.d <= radius)
    .sort((a, b) => a.d - b.d).slice(0, n).map((o) => o.s);
}

const median = (v) => { const a = v.filter(Number.isFinite).sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] : null; };

/** How well the frames were aligned: median stars used and median residual (measured by the registration). */
export function registrationSummary(field) {
  const reg = (field?.epochs || []).filter((e) => e.usable && e.registration?.ok).map((e) => e.registration);
  return { n_frames: reg.length, n_stars: median(reg.map((r) => r.n_used)), rms_arcsec: median(reg.map((r) => Math.hypot(r.rms_east_arcsec, r.rms_north_arcsec))) };
}

export function compass(pa) {
  return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][Math.round((((pa % 360) + 360) % 360) / 45) % 8];
}

/** Headline numbers for the motion scene, all derived from measured/catalog values in the payload. */
export function motionFacts(d) {
  const f = d?.fit; if (!f) return null;
  const sigma = f.snr > 0 ? f.pm_total / f.snr : null;
  const cmp = d.catalog?.pm_comparison;
  const vp = f.visit_points || [];
  const shift = vp.length >= 2 ? { arcsec: Math.hypot(vp.at(-1).east_arcsec - vp[0].east_arcsec, vp.at(-1).north_arcsec - vp[0].north_arcsec), days: vp.at(-1).mjd - vp[0].mjd } : null;
  return {
    pm: f.pm_total, sigma, snr: f.snr, pa: f.pm_pa_deg, dir: compass(f.pm_pa_deg),
    moonYears: f.pm_total > 0 ? MOON_ARCSEC / f.pm_total : null,
    gaia: cmp ? { pm: Math.hypot(cmp.gaia_pm_east, cmp.gaia_pm_north), z: cmp.z_total, consistent: cmp.consistent } : null,
    shift, visits: vp.length || d.n_visits,
  };
}

/** Distance from a Gaia parallax (catalog). */
export function distance(d) {
  const p = d?.catalog?.gaia?.parallax;
  return p > 0 ? { pc: 1000 / p, ly: 3261.56 / p, parallax_mas: p } : null;
}

/** Plain words for a SIMBAD spectral type ("M4V" → "red dwarf"); null when the type is missing. */
export function describeType(spType) {
  const m = /^([OBAFGKM])\d?(?:\.\d)?\s*(I{1,3}|IV|V)?/.exec((spType || "").trim());
  if (!m) return null;
  const [, cls, lum] = m;
  if (lum === "V") return { M: "red dwarf", K: "orange dwarf", G: "Sun-like yellow dwarf" }[cls] || `${cls}-type main-sequence star`;
  if (lum === "III" || lum === "II" || lum === "I") return `${cls}-type giant star`;
  return `${cls}-type star`;
}

/** Stars that could demonstrate a spectrum honestly: unsaturated, clean, unblended, catalog-identified. */
export function spectralCandidates(field, exclude, n = 5) {
  return (field?.sources || [])
    .filter((s) => s.id !== exclude && s.tier === "stationary" && !s.saturated && (s.clean_fraction ?? 0) >= 0.85 && !(s.blended_fraction > 0) && s.catalog?.gaia)
    .sort((a, b) => (!!b.catalog?.simbad?.sp_type - !!a.catalog?.simbad?.sp_type) || b.flux_ref - a.flux_ref)
    .slice(0, n);
}

/** Is a measured spectrum good enough to narrate?  Mirrors the dossier's own caveats. */
export function spectrumUsable(sp) {
  const bb = sp?.blackbody;
  return !!(sp && sp.n_measured >= 40 && bb && bb.chi2_red <= 3 && !bb.unconstrained_high && !bb.unconstrained_low && (sp.catalog_comparison || []).length >= 2);
}

/** Largest SPHEREx ÷ catalog flux difference, in percent. */
export function catalogAgreement(sp) {
  const r = (sp?.catalog_comparison || []).map((c) => c.ratio).filter(Number.isFinite);
  if (!r.length) return null;
  return { maxPct: Math.max(...r.map((x) => Math.abs(x - 1))) * 100, catalogs: [...new Set(sp.catalog_comparison.map((c) => c.catalog))] };
}

export function warmth(T) { return T >= 6500 ? "hot" : T >= 4800 ? "Sun-like" : "cool"; }
