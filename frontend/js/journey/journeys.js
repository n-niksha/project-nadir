// Journey definitions.  Scenes are built by small factories from the real API
// payloads; a "story" only adds tone (titles, an opening line, a literature
// note) — never numbers.  Barnard's Star gets the full five-scene show; any
// other source gets the same scenes where its data supports them.
//
// ctx (provided by main.js) — the only way scenes touch the app:
//   field, d (source detail), subject (field source), anchor (epoch index A), name, reduced
//   frame(i) · loadFrames([i]) · fly(x, y, k, ms) · fit(ms) · kFor(arcsec)
//   layer(patch) · reveal(key, ms) · spectrum(id) · app.{compare, tab, overlays, select, notesKey}
import { esc, fmt, niceDate, displayName } from "../fmt.js";
import { MOVING, visitFrames, referenceStars, registrationSummary, motionFacts, distance, describeType,
  spectralCandidates, spectrumUsable, catalogAgreement, warmth } from "./science.js";
import { TAG, facts, card, frameList, motionChart, spectrumChart, legend, SW, identity, tierBadge } from "./views.js";
import { C } from "../tokens.js";

const n0 = (v) => Math.round(v).toLocaleString("en-US");
const near = (v, step) => (Math.round(v / step) * step).toLocaleString("en-US");
const stampFrame = (e, extra = "") => e ? { tag: "measured", text: `Real SPHEREx exposure · ${niceDate(e.date)} · λ ${fmt(e.wavelength_um, 3)} µm${extra}` } : null;
const epochOf = (ctx, i) => ctx.field.epochs.find((e) => e.index === i);
const saturated = (d) => d.core_masked_fraction > 0.5;

// ------------------------------------------------------------------ scenes
function wonder(st) {
  return {
    id: "wonder", title: st.titles?.wonder || "The still sky",
    enter(ctx) { ctx.frame(ctx.anchor); ctx.fit(0); ctx.layer({ focus: [{ id: ctx.subject.id, label: ctx.name, color: "sun", hidden: true }] }); },
    card(ctx) {
      const d = ctx.d, sb = d.catalog?.simbad, g = d.catalog?.gaia, dist = distance(d), type = describeType(sb?.sp_type);
      return card({ kicker: "Meet the object", title: esc(ctx.name), body: facts([
        { k: "What it is", v: esc(type ? `${type[0].toUpperCase()}${type.slice(1)}` : identity(d)), tag: sb || g ? "catalog" : "unknown", note: sb ? `SIMBAD: ${esc(identity(d))}${sb.sp_type ? ` · spectral type ${esc(sb.sp_type)}` : ""}` : "" },
        dist && { k: "Distance", v: `${fmt(dist.ly, dist.ly < 100 ? 2 : 0)} light-years`, tag: "catalog", note: `1 / Gaia DR3 parallax (${fmt(dist.parallax_mas, 1)} mas)` },
        g?.teff_gspphot > 0 && { k: "Temperature", v: `≈${n0(g.teff_gspphot)} K`, tag: "catalog", note: "Gaia DR3 GSP-Phot model estimate" },
        { k: "In SPHEREx", v: `${d.n_epochs} exposures · ${d.n_visits} visits`, tag: "measured", note: `over ${fmt(d.baseline_days, 0)} days` },
        saturated(d) && { k: "Brightness", v: "Saturates the detector", tag: "measured", note: `core masked in ${fmt(100 * d.core_masked_fraction, 0)}% of exposures` },
        st.literature && { k: "History", v: esc(st.literature.text), tag: "literature", note: esc(st.literature.cite) },
      ]) });
    },
    beats(ctx) {
      const d = ctx.d, dist = distance(d), type = describeType(d.catalog?.simbad?.sp_type);
      const who = `This is ${ctx.name}${type ? `: a ${type}` : ""}${dist ? `${type ? "" : ","} ${fmt(dist.ly, dist.ly < 20 ? 1 : 0)} light-years away` : ""}${dist && dist.ly < 10 ? ", one of the Sun's nearest neighbours" : ""}.`;
      return [
        { at: 0, caption: st.opening?.[0] || "The night sky looks still.", stamp: stampFrame(epochOf(ctx, ctx.anchor)) },
        { at: 3400, caption: st.opening?.[1] || "But nothing in it is truly at rest.", do: (c) => c.fly(c.subject.x, c.subject.y, c.kFor(560), 4400) },
        { at: 7400, caption: who, do: (c) => c.layer({ focus: [{ id: c.subject.id, label: c.name, color: "sun" }] }) },
        saturated(d) ? { at: 11000, caption: "In SPHEREx's infrared eyes it blazes so brightly that its core saturates the detector." } : null,
      ];
    },
    duration: 14000,
    explore: (ctx) => ({ title: "Find it yourself", prompt: `Pan and zoom the real field. ${ctx.name} sits in the amber brackets; everything else is a neighbour SPHEREx measured too.`, actions: [{ label: "Centre on it", run: (c) => c.app.center() }] }),
  };
}

function passage(st) {
  return {
    id: "time", title: st.titles?.time || "The passage of time",
    async prepare(ctx) {
      const frames = visitFrames(ctx.field, ctx.anchor);
      if (frames.length < 2) throw new Error(`only ${frames.length} visit has wavelength-matched frames`);
      await ctx.loadFrames(frames.map((e) => e.index));
      return { frames, refs: referenceStars(ctx.field, ctx.subject), reg: registrationSummary(ctx.field), m: motionFacts(ctx.d) };
    },
    enter(ctx, x) {
      ctx.frame(x.frames[0].index);
      ctx.fly(ctx.subject.x, ctx.subject.y, ctx.kFor(340), 1400);
      ctx.layer({ focus: [{ id: ctx.subject.id, label: ctx.name, color: "sun" }], refs: { ids: x.refs.map((s) => s.id), hidden: true } });
    },
    card(ctx, x) {
      return card({ kicker: "Real observations", title: `${x.frames.length} visits, ${fmt(x.frames.at(-1).mjd - x.frames[0].mjd, 0)} days`, body:
        frameList(x.frames, x.frames[0].index) + facts([
          { k: "Matched", v: "within 2% in wavelength", tag: "measured", note: "so brightness and blur are comparable between frames" },
          x.reg.n_stars && { k: "Aligned on", v: `≈${x.reg.n_stars} stars per image`, tag: "measured", note: `typical residual ${fmt(x.reg.rms_arcsec, 2)}″ (registration)` },
        ]) + `<p class="jr-note">One observed frame per step. Nothing between visits is interpolated.</p>` });
    },
    beats(ctx, x) {
      const F = x.frames, last = F.at(-1), moving = MOVING.has(ctx.d.tier);
      const show = (e) => (c) => { c.frame(e.index); c.highlightFrame(e.index); };
      const beats = [
        { at: 0, caption: `SPHEREx came back to this patch of sky again and again. These are ${F.length} of its real visits.`, stamp: stampFrame(F[0]) },
        { at: 3400, caption: `${n0(F[1].mjd - F[0].mjd)} days later…`, stamp: stampFrame(F[1]), do: show(F[1]) },
      ];
      if (F.length > 2) beats.push({ at: 6200, caption: `…and ${n0(last.mjd - F[0].mjd)} days after the first.`, stamp: stampFrame(last), do: show(last) });
      const t0 = F.length > 2 ? 9000 : 6200;
      beats.push({ at: t0, caption: "Now flick between the first and last visit. The neighbours in teal hold still.", do: (c) => c.reveal("refs", 900) });
      // blink: alternate the two real frames — beats, not timers, so pause and skip just work
      const period = ctx.reduced ? 1500 : 700;
      for (let t = t0 + 400, k = 0; t < t0 + 7000; t += period, k++) {
        const e = k % 2 ? last : F[0];
        beats.push({ at: t, stamp: stampFrame(e, " · blinking"), do: show(e) });
      }
      const m = x.m;
      beats.push({ at: t0 + 3800, caption: moving && m?.shift ? `${ctx.name} does not. Between these visits it moved ${fmt(m.shift.arcsec, 1)}″ ${m.dir}.` : moving ? `${ctx.name} does not.` : `So does ${ctx.name}: within the errors, it stays put.` });
      beats.push({ at: t0 + 7200, caption: "Only moments SPHEREx actually recorded are shown. Nothing in between is invented.", stamp: stampFrame(last), do: show(last) });
      return beats;
    },
    fallback: (ctx, err) => ({
      card: card({ kicker: "Real observations", title: "Not enough visits", body: `<p>${esc(err?.message || "No comparable frames")}. ${TAG.unknown}</p><p class="jr-note">A change over time needs at least two visits at matching wavelengths. NADIR will not fake one.</p>` }),
      beats: [{ at: 0, caption: "To see change, you need the same sky at two different times." }, { at: 4200, caption: `For ${ctx.name}, SPHEREx has fewer than two comparable visits here, so we cannot show change over time — and won't pretend to.` }],
    }),
    explore: (ctx, x) => x?.frames?.length >= 2 ? { title: "Blink the visits yourself", prompt: "Flick between the first and last visit. Which stars jump? Which hold still? Try the B − A difference view too.",
      actions: [{ label: "Blink first ↔ last visit", run: (c) => c.app.compare(x.frames[0].index, x.frames.at(-1).index, "blink") }, { label: "Difference B − A", run: (c) => c.app.compare(x.frames[0].index, x.frames.at(-1).index, "diff") }] } : null,
  };
}

function measuring(st) {
  return {
    id: "measure", title: st.titles?.measure || "Measuring the impossible",
    when: (ctx) => MOVING.has(ctx.d.tier) && !!ctx.d.fit,
    prepare: (ctx) => ({ m: motionFacts(ctx.d), refs: referenceStars(ctx.field, ctx.subject), reg: registrationSummary(ctx.field) }),
    enter(ctx, x) {
      const d = ctx.d, ps = ctx.field.grid.pixel_scale_arcsec, f = d.fit;
      ctx.frame(ctx.anchor);
      ctx.fly(ctx.subject.x, ctx.subject.y + (f.visit_points?.at(-1)?.north_arcsec || 0) / ps / 2, ctx.kFor(95), 1600);
      const vp = f.visit_points?.length ? f.visit_points : (d.points || []);
      ctx.layer({
        focus: [],
        refs: { ids: x.refs.map((s) => s.id) },
        trail: { pts: vp.map((v, i) => ({ x: d.x - v.east_arcsec / ps, y: d.y + v.north_arcsec / ps, r: (v.sigma_north_arcsec || 0) / ps, label: f.visit_points?.length ? `Visit ${i + 1} · ${niceDate(new Date((v.mjd - 40587) * 864e5).toISOString()).replace(/^\d+ /, "")}` : null })), hidden: true },
        fitArrow: { x0: d.x - f.offset_east / ps, y0: d.y + f.offset_north / ps, x1: d.x - (f.offset_east + f.pm_east) / ps, y1: d.y + (f.offset_north + f.pm_north) / ps, label: "1 year of motion (fit)", hidden: true },
        history: d.history?.length ? { pts: d.history.map((h) => ({ x: d.x - h.east_arcsec / ps, y: d.y + h.north_arcsec / ps, label: `${h.catalog} ${h.year.toFixed(0)}` })), to: { x: d.x, y: d.y }, hidden: true } : null,
      });
    },
    card(ctx, x) {
      const d = ctx.d, m = x.m;
      return card({ kicker: "The measurement", title: `${fmt(m.pm, 2)}<small>″/yr</small>`, cls: "hero", body:
        `<p class="jr-hero-sub">± ${fmt(m.sigma, 1)}″/yr (1σ) · ${fmt(m.snr, 1)}σ · toward PA ${fmt(m.pa, 0)}° ${TAG.measured}</p>` +
        motionChart(d) + legend([[SW.dot(C("sun")), "Visit means ±1σ (measured)"], [SW.line(C("sun")), "Fit"], ...(d.gaia_curve ? [[SW.dash(), "Gaia DR3 prediction (catalog)"]] : [])]) +
        facts([
          m.gaia && { k: "Gaia DR3", v: `${fmt(m.gaia.pm, 2)}″/yr`, tag: "catalog", note: `difference ${fmt(m.gaia.z, 1)}σ (${m.gaia.consistent ? "consistent" : "inconsistent"})` },
          { k: "Ruler", v: `≈${x.reg.n_stars} reference stars`, tag: "measured", note: "positions are relative to the field, not absolute" },
        ]) +
        (saturated(d) ? `<div class="callout warn"><b>Why the error is large.</b> The core saturates, so positions come from the star's faint wings, and whole visits can shift by 1–2″. NADIR allows ${fmt(d.fit.visit_points?.[0]?.sigma_north_arcsec ?? 1.2, 1)}″ per visit.${st.calibrated ? " That allowance was calibrated on this very star with Gaia, so agreement here is a consistency check, not independent proof." : ""}</div>` : "") });
    },
    beats(ctx, x) {
      const d = ctx.d, m = x.m, hist = d.history || [];
      const b = [
        { at: 0, caption: `To measure motion this small you need a ruler: about ${x.reg.n_stars} field stars that hold still, used to align every image.`, stamp: stampFrame(epochOf(ctx, ctx.anchor)), do: (c) => c.reveal("refs", 600) },
        { at: 4200, caption: `NADIR located ${ctx.name} in ${d.n_epochs} exposures${d.fit.visit_points ? ` and averaged each of the ${d.fit.visit_points.length} visits` : ""}.`, stamp: { tag: "measured", text: "Amber: measured positions, ±1σ circles" }, do: (c) => c.reveal("trail", 2400) },
        { at: 8400, caption: `Result: ${fmt(m.pm, 1)} ± ${fmt(m.sigma, 1)}″ a year, toward the ${m.dir}.${m.moonYears ? ` At that pace it would cross the full Moon's width in about ${near(m.moonYears, m.moonYears > 100 ? 10 : 1)} years.` : ""}`, do: (c) => c.reveal("fitArrow", 900) },
      ];
      if (m.gaia) b.push({ at: 13200, caption: `Gaia measured ${fmt(m.gaia.pm, 2)}″ a year. ${m.gaia.consistent ? `They agree within ${fmt(m.gaia.z, 1)}σ` : `They differ by ${fmt(m.gaia.z, 1)}σ`}${st.calibrated ? " — but NADIR's error allowance was tuned on this star, so this is a consistency check, not independent proof." : "."}` });
      if (hist.length) {
        const first = hist[0], span = 2025.7 - first.year;
        b.push({ at: m.gaia ? 18200 : 13200, caption: `Older catalogs caught it earlier: ${hist.map((h) => `${h.catalog} in ${h.year.toFixed(0)}`).join(", ")}. ${span >= 24 ? "A quarter-century" : `${n0(span)} years`} of travel.`,
          stamp: { tag: "catalog", text: "Blue: catalog positions from earlier surveys, not SPHEREx" },
          do: (c) => { const ps = c.field.grid.pixel_scale_arcsec; c.layer({ focus: [{ id: c.subject.id, label: c.name, color: "sun", quiet: true }] }); c.fly(d.x - first.east_arcsec / ps / 2, d.y + first.north_arcsec / ps / 2, c.kFor(Math.hypot(first.east_arcsec, first.north_arcsec) * 1.5), 2200); c.reveal("history", 2000); } });
      }
      return b;
    },
    explore: (ctx) => ({ title: "Check the measurement", prompt: "Open the Motion tab. Do the visit averages line up within their error bars? Hover a point for its exact position and uncertainty.", actions: [{ label: "Open the Motion evidence", run: (c) => c.app.tab("motion") }, { label: "Show reference stars", run: (c) => c.app.overlays({ refs: true, sources: true }) }] }),
  };
}

function light(st) {
  return {
    id: "light", title: st.titles?.light || "Reading light",
    async prepare(ctx) {
      if (!saturated(ctx.d)) {
        const sp = await ctx.spectrum(ctx.subject.id);
        if (!spectrumUsable(sp) && !(sp.points || []).length) throw new Error("no usable spectral measurements");
        return { own: true, sp, src: ctx.subject, d: ctx.d };
      }
      let failed = 0;
      for (const s of spectralCandidates(ctx.field, ctx.subject.id)) {
        try { const sp = await ctx.spectrum(s.id); if (spectrumUsable(sp)) return { own: false, sp, src: s, d: await ctx.source(s.id) }; }
        catch (e) { failed++; console.warn("[journey] spectrum candidate failed", s.id, e.message); }
      }
      return { own: false, sp: null, src: null, failed };
    },
    enter(ctx) { ctx.frame(ctx.anchor); ctx.fly(ctx.subject.x, ctx.subject.y, ctx.kFor(420), 1400); ctx.layer({ focus: [{ id: ctx.subject.id, label: ctx.name, color: "sun" }] }); },
    card(ctx, x) {
      if (x.own) return specCard(x, ctx.name, false);
      return card({ kicker: "Reading light", title: "No spectrum for this star", body: facts([
        { k: "Saturated", v: `core masked in ${fmt(100 * ctx.d.core_masked_fraction, 0)}% of ${ctx.d.n_epochs} exposures`, tag: "measured" },
        { k: "Spectrum", v: "Not measured", tag: "unknown", note: "Too bright: the pixels at its centre carry no reliable flux, so NADIR shows nothing rather than a biased spectrum." },
      ]) });
    },
    beats(ctx, x) {
      const b = [{ at: 0, caption: "Light carries more than position. Spread into its colours, it tells of temperature and makeup.", stamp: stampFrame(epochOf(ctx, ctx.anchor)) }];
      if (x.own) {
        const bb = x.sp.blackbody, agree = catalogAgreement(x.sp);
        b.push({ at: 4200, caption: `SPHEREx measured ${ctx.name} at ${x.sp.n_measured} wavelengths between 0.75 and 5 µm.` });
        if (bb && spectrumUsable(x.sp)) b.push({ at: 8600, caption: `The shape matches a ${warmth(bb.T_color_K)} glow: a colour temperature of about ${near(bb.T_color_K, 100)} K. That's a model fit, not a thermometer.` });
        if (agree) b.push({ at: 13400, caption: `Where ${agree.catalogs.join(" and ")} measured it too, the fluxes agree within ${fmt(agree.maxPct, 0)}%.` });
        return b;
      }
      b.push({ at: 4000, caption: `But ${ctx.name} is too bright for SPHEREx. Its core saturates, so NADIR shows no spectrum rather than a wrong one.` });
      if (!x.sp) { b.push({ at: 8800, caption: x.failed ? "The neighbours' spectra could not be read right now, so we leave the light unread — and say so." : "No unsaturated neighbour here has a spectrum clean enough to narrate, so we leave the light unread — and say so." }); return b; }
      const dn = displayName(x.d), sb = x.d.catalog?.simbad, type = sb?.sp_type, bb = x.sp.blackbody, agree = catalogAgreement(x.sp);
      b.push({ at: 8400, caption: `So let's read a calmer neighbour in the same images instead: ${dn}. This is a different star.`,
        stamp: { tag: "measured", text: `Now showing a different star: ${dn}` },
        card: specCard(x, dn, true),
        do: (c) => { c.fly(x.src.x, x.src.y, c.kFor(420), 2000); c.layer({ focus: [{ id: c.subject.id, label: c.name, color: "sun", quiet: true }, { id: x.src.id, label: `Spectrum demo: ${dn}`, color: "violet" }] }); } });
      b.push({ at: 12800, caption: `SPHEREx measured it at ${x.sp.n_measured} wavelengths. Its light fades toward 5 µm: the slope of a ${warmth(bb.T_color_K)} star${type ? `, and SIMBAD lists it as type ${type}` : ""}.` });
      if (agree) b.push({ at: 17200, caption: `Where ${agree.catalogs.join(" and ")} measured it years earlier, the fluxes agree within ${fmt(agree.maxPct, 0)}%. Different instruments, one answer.` });
      return b;
    },
    fallback: (ctx, err) => ({ card: card({ kicker: "Reading light", title: "Spectrum unavailable", body: `<p>${esc(err?.message || "The spectral images could not be read")}. ${TAG.unknown}</p>` }),
      beats: [{ at: 0, caption: "Light carries more than position: spread into colours, it reveals temperature and makeup." }, { at: 4600, caption: "The spectral images for this field could not be read from the local cache, so this chapter is skipped rather than guessed." }] }),
    explore: (ctx, x) => ({ title: "Read the light", prompt: x?.src ? `Open the Spectrum tab for ${x.own ? ctx.name : displayName(x.d)}. Each dot is one exposure at one wavelength — hover to see when it was taken.` : "Open the Spectrum tab: NADIR explains why no spectrum is shown.",
      actions: [{ label: "Open the spectrum", run: (c) => { if (x?.src && !x.own) c.app.select(x.src.id, "spectrum"); else c.app.tab("spectrum"); } }] }),
  };
}

function specCard(x, name, swapped) {
  const sp = x.sp, bb = sp.blackbody, agree = catalogAgreement(sp), sb = x.d?.catalog?.simbad, g = x.d?.catalog?.gaia;
  return card({ kicker: swapped ? "Spectrum demonstration · a different star" : "Reading light", title: esc(name), cls: swapped ? "swapped" : "", body:
    (swapped ? `<p class="jr-note">The previous star is saturated. This unsaturated neighbour is in the same SPHEREx images.</p>` : "") +
    spectrumChart(sp) + legend([[SW.dot(C("sky")), "SPHEREx exposures (measured)"], [SW.square(), "2MASS / WISE (catalog)"], ...(bb ? [[SW.line(C("violet")), "Colour model (inferred)"]] : [])]) +
    facts([
      { k: "Measured", v: `${sp.n_measured} fluxes, ${sp.bands.join(" ")}`, tag: "measured", note: "different wavelengths were observed on different dates" },
      bb && { k: "Colour temp.", v: `≈${near(bb.T_color_K, 100)} K`, tag: "inferred", note: `single-blackbody fit, χ²ᵣ ${fmt(bb.chi2_red, 1)}; not a true surface temperature` },
      g?.teff_gspphot > 0 && { k: "Gaia estimate", v: `≈${near(g.teff_gspphot, 10)} K`, tag: "catalog", note: "GSP-Phot model" },
      sb?.sp_type && { k: "Spectral type", v: esc(sb.sp_type), tag: "catalog", note: "SIMBAD" },
      agree && { k: "vs catalogs", v: `within ${fmt(agree.maxPct, 0)}%`, tag: "catalog", note: (sp.catalog_comparison || []).map((c) => `${c.band} ×${fmt(c.ratio, 2)}`).join(" · ") },
    ]) });
}

function observer(st) {
  return {
    id: "observer", title: st.titles?.observer || "Becoming the observer", final: true,
    prepare: (ctx) => ({ rejected: ctx.field.sources.filter((s) => s.tier === "rejected"), m: motionFacts(ctx.d) }),
    enter(ctx, x) {
      ctx.frame(ctx.anchor); ctx.fit(2200);
      ctx.layer({ focus: [{ id: ctx.subject.id, label: ctx.name, color: "sun" }], rejected: { ids: x.rejected.map((s) => s.id), hidden: true } });
    },
    card(ctx, x) {
      const d = ctx.d, m = x.m;
      const row = (ok, t, tag) => `<li class="${ok ? "ok" : "no"}"><span aria-hidden="true">${ok ? "✓" : "–"}</span><span>${t} ${TAG[tag] || ""}</span></li>`;
      return card({ kicker: "How a claim is checked", title: esc(ctx.name), body: `${tierBadge(d)}<ul class="jr-checks">` +
        row(d.n_visits >= 2, `Repeated across ${d.n_visits} independent visits`, "measured") +
        row(!!m, m ? `Uncertainty stated: ±${fmt(m.sigma, 1)}″/yr` : "No motion fit: uncertainty not applicable", m ? "measured" : "unknown") +
        row(!!m?.gaia, m?.gaia ? `Compared with Gaia DR3 (${fmt(m.gaia.z, 1)}σ)${st.calibrated ? " — not independent here" : ""}` : "No Gaia comparison", "catalog") +
        row(true, `Limits named: ${saturated(d) ? "saturated core, no spectrum" : "see the evidence tab"}`, "measured") +
        row(true, `${x.rejected.length} look-alike${x.rejected.length === 1 ? "" : "s"} rejected in this field`, "measured") +
        `</ul>` });
    },
    beats(ctx, x) {
      const r = x.rejected.length;
      return [
        { at: 0, caption: "Discovery isn't seeing something once. It's checking.", stamp: stampFrame(epochOf(ctx, ctx.anchor)) },
        { at: 3400, caption: r ? `${r === 1 ? "One other star" : `${r} other stars`} seemed to move between two images. The other visits disagreed, so NADIR rejected ${r === 1 ? "it" : "them"}.` : "Every candidate here had to repeat across independent visits before NADIR would call it motion.", do: (c) => c.reveal("rejected", 800) },
        { at: 8000, caption: "Every number here carries its uncertainty, its source and its limits. Now it's your turn." },
      ];
    },
    duration: 11500,
    explore: (ctx) => ({ title: "Record your observation", prompt: "What would you check next? Write it down — your notes stay in this browser and appear in the Notes tab.", notes: true }),
  };
}

// --------------------------------------------------------------- registry
const BARNARD = {
  id: "barnard", title: "The Stars Are Not Still", subtitle: "A journey with Barnard's Star", chapters: true, calibrated: true,
  opening: ["The night sky has always looked still.", "But the universe has never stopped moving."],
  literature: { text: "1916: E. E. Barnard found it creeping across old photographic plates — the largest motion of any known star.", cite: "Barnard 1916, Astronomical Journal 29, 181" },
};

export function isBarnard(d) { return /barnard/i.test(d?.catalog?.simbad?.id || ""); }

/** The journey for a source: Barnard's full show, otherwise the same scenes where data allows. */
export function journeyFor(d) {
  if (isBarnard(d)) {
    const st = BARNARD;
    return { id: st.id, title: st.title, subtitle: st.subtitle, full: true, scenes: [wonder(st), passage(st), measuring(st), light(st), observer(st)] };
  }
  const st = { chapters: true, titles: { wonder: "Meet the object", time: "Across the visits", measure: "The measurement", light: "Its light", observer: "Checking the claim" } };
  return { id: `overview-${d.id}`, title: "A closer look", subtitle: displayName(d), full: false, scenes: [wonder(st), passage(st), measuring(st), light(st), observer(st)] };
}

export const _test = { wonder, passage, measuring, light, observer };
