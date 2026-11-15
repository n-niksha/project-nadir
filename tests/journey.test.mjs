// NADIR Journeys: engine timing/navigation and the data choices scenes make.
//   node --test tests/journey.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { JourneyEngine } from '../frontend/js/journey/engine.js';
import { visitFrames, referenceStars, motionFacts, describeType, spectralCandidates, spectrumUsable, catalogAgreement, distance } from '../frontend/js/journey/science.js';
import { journeyFor, isBarnard, _test } from '../frontend/js/journey/journeys.js';

// a manual clock: nothing runs unless the test advances it
function engine(opts = {}) {
  const e = new JourneyEngine({ now: () => 0, raf: () => 1, caf: () => {}, ...opts });
  const log = [];
  for (const ev of ['scene', 'caption', 'stamp', 'card', 'end', 'exit', 'scene-failed']) e.on(ev, (d) => log.push([ev, d]));
  return { e, log };
}
const flush = () => new Promise((r) => setTimeout(r, 0));
const ctx = { reset() {} };

test('beats fire in order, only while playing, and scenes advance on their own', async () => {
  const fired = [];
  const J = { scenes: [
    { id: 'a', title: 'A', duration: 1000, beats: () => [{ at: 0, caption: 'a0' }, { at: 500, caption: 'a1', do: () => fired.push('a1') }] },
    { id: 'b', title: 'B', duration: 800, final: true, beats: () => [{ at: 0, caption: 'b0' }, { at: 700, caption: 'b1' }] },
  ] };
  const { e, log } = engine();
  await e.load(J, ctx).start(); await flush();
  assert.equal(e.i, 0);
  assert.deepEqual(log.filter(([k]) => k === 'caption').map(([, d]) => d.text), ['a0']);
  e.pause(); e.advance(600);
  assert.equal(e.t, 0, 'paused clock must not move');
  assert.deepEqual(fired, []);
  e.play(); e.advance(600);
  assert.deepEqual(fired, ['a1']);
  e.advance(500); await flush();
  assert.equal(e.i, 1, 'scene ends -> next scene');
  e.advance(900);
  assert.equal(e.ended, true); assert.equal(e.playing, false);
  assert.ok(log.some(([k]) => k === 'end'));
  e.advance(5000);
  assert.equal(e.i, 1, 'final scene never auto-advances');
});

test('skip / previous / replay never fire stale beats from an abandoned scene', async () => {
  let resolveSlow;
  const fired = [];
  const J = { scenes: [
    { id: 'a', title: 'A', beats: () => [{ at: 0, do: () => fired.push('a') }] },
    { id: 'slow', title: 'Slow', prepare: () => new Promise((r) => { resolveSlow = r; }), beats: () => [{ at: 0, do: () => fired.push('slow') }] },
    { id: 'c', title: 'C', beats: () => [{ at: 0, do: () => fired.push('c') }] },
  ] };
  const { e } = engine();
  await e.load(J, ctx).start();
  const pending = e.next();          // into the slow scene (still preparing)
  await e.next();                    // user skips again before it is ready
  resolveSlow({}); await pending; await flush();
  assert.equal(e.i, 2);
  assert.deepEqual(fired, ['a', 'c'], 'the superseded scene must not run');
  await e.prev(); await flush();
  assert.equal(e.i, 1);
  await e.replay(); await flush();
  assert.equal(e.i, 0);
  e.stop();
  assert.equal(e.active, false);
  await e.goto(2);
  assert.equal(e.i, 0, 'a stopped journey ignores navigation');
});

test('a failing scene shows its honest fallback and the journey continues', async () => {
  const J = { scenes: [
    { id: 'bad', title: 'Bad', prepare: () => { throw new Error('archive offline'); }, beats: () => [{ at: 0, caption: 'never' }],
      fallback: (c, err) => ({ beats: [{ at: 0, caption: `skipped: ${err.message}` }] }) },
    { id: 'throws', title: 'Throws', beats: () => { throw new Error('bug'); } },
    { id: 'ok', title: 'OK', beats: () => [{ at: 0, caption: 'fine' }] },
  ] };
  const { e, log } = engine();
  const warn = console.warn; console.warn = () => {};
  try {
    await e.load(J, ctx).start(); await flush();
    assert.equal(log.filter(([k]) => k === 'caption').at(-1)[1].text, 'skipped: archive offline');
    await e.next(); await flush();
    assert.match(log.filter(([k]) => k === 'caption').at(-1)[1].text, /could not be prepared/);
    await e.next(); await flush();
    assert.equal(log.filter(([k]) => k === 'caption').at(-1)[1].text, 'fine');
    assert.equal(log.filter(([k]) => k === 'scene-failed').length, 2);
  } finally { console.warn = warn; }
});

test('the narrator hold pauses the clock before the next beat, never past it', async () => {
  let speaking = true;
  const J = { scenes: [{ id: 'a', title: 'A', final: true, beats: () => [{ at: 0, caption: 'x' }, { at: 1000, caption: 'y' }] }] };
  const { e, log } = engine({ hold: () => speaking });
  await e.load(J, ctx).start(); await flush();
  e.advance(5000);
  assert.ok(e.t < 1000); assert.equal(log.filter(([k]) => k === 'caption').length, 1);
  speaking = false; e.advance(10);
  assert.equal(log.filter(([k]) => k === 'caption').at(-1)[1].text, 'y');
});

// ------------------------------------------------------------------ data
const ep = (index, mjd, wl, extra = {}) => ({ index, mjd, wavelength_um: wl, usable: true, has_frame: true, date: new Date((mjd - 40587) * 864e5).toISOString(), ...extra });
const field = {
  grid: { pixel_scale_arcsec: 3.075, nx: 100, ny: 100 },
  epochs: [ep(0, 60000, 1.50), ep(1, 60001, 1.51), ep(2, 60002, 1.70), ep(3, 60200, 1.505), ep(4, 60201, 1.49, { usable: false }), ep(5, 60400, 1.52), ep(6, 60410, 1.50, { has_frame: false })],
  visits: [{ visit: 0, mjd_min: 60000, mjd_max: 60002 }, { visit: 1, mjd_min: 60200, mjd_max: 60201 }, { visit: 2, mjd_min: 60400, mjd_max: 60410 }],
  sources: [
    { id: 0, x: 50, y: 50, tier: 'systematics_limited', saturated: true, clean_fraction: 0 },
    { id: 1, x: 60, y: 50, tier: 'stationary', saturated: false, clean_fraction: 1, blended_fraction: 0, flux_ref: 10, catalog: { gaia: {} } },
    { id: 2, x: 52, y: 52, tier: 'stationary', saturated: false, clean_fraction: 1, blended_fraction: 0, flux_ref: 50, catalog: { gaia: {}, simbad: { sp_type: 'A3V' } } },
    { id: 3, x: 70, y: 70, tier: 'stationary', saturated: true, clean_fraction: 1, flux_ref: 99, catalog: { gaia: {} } },
    { id: 4, x: 55, y: 50, tier: 'stationary', saturated: false, clean_fraction: 1, blended_fraction: 0.5, flux_ref: 80, catalog: { gaia: {} } },
    { id: 5, x: 90, y: 90, tier: 'rejected' },
  ],
};

test('visit frames: one real, registered, wavelength-matched frame per server visit', () => {
  const f = visitFrames(field, 0);
  assert.deepEqual(f.map((e) => e.index), [0, 3, 5], 'no unusable, frameless or off-wavelength epochs; one per visit');
  assert.deepEqual(visitFrames({ ...field, epochs: field.epochs.slice(0, 3) }, 0).map((e) => e.index), [0], 'a single visit yields one frame -> the scene falls back');
  assert.deepEqual(visitFrames(field, 99), []);
});

test('reference stars are clean, unsaturated, unblended neighbours; spectral demos are catalog stars', () => {
  assert.deepEqual(referenceStars(field, field.sources[0]).map((s) => s.id), [1], 'id 2 is too close to separate, 3 saturated, 4 blended');
  assert.deepEqual(spectralCandidates(field, 0).map((s) => s.id), [2, 1], 'SIMBAD-typed stars first, then brightness');
});

test('spectrum gate mirrors the dossier caveats; agreement is the worst band', () => {
  const sp = { n_measured: 150, blackbody: { chi2_red: 2.4 }, catalog_comparison: [{ ratio: 0.93, catalog: '2MASS' }, { ratio: 1.02, catalog: 'AllWISE' }] };
  assert.equal(spectrumUsable(sp), true);
  assert.equal(spectrumUsable({ ...sp, blackbody: { chi2_red: 7.3 } }), false, 'poor continuum fit is not narrated');
  assert.equal(spectrumUsable({ ...sp, blackbody: null }), false);
  assert.equal(Math.round(catalogAgreement(sp).maxPct), 7);
});

test('motion facts come straight from the fit and catalog comparison', () => {
  const d = { fit: { pm_total: 10.1, snr: 5.88, pm_pa_deg: 357, visit_points: [{ mjd: 60913, east_arcsec: -0.27, north_arcsec: 0.27 }, { mjd: 61275, east_arcsec: -1.07, north_arcsec: 10.3 }] },
    catalog: { pm_comparison: { gaia_pm_east: -0.80, gaia_pm_north: 10.36, z_total: 0.17, consistent: true }, gaia: { parallax: 546.98 } } };
  const m = motionFacts(d);
  assert.equal(m.dir, 'north');
  assert.ok(Math.abs(m.sigma - 1.718) < 0.01);
  assert.ok(Math.abs(m.gaia.pm - 10.39) < 0.01);
  assert.ok(Math.abs(m.shift.arcsec - 10.06) < 0.01);
  assert.ok(Math.abs(distance(d).ly - 5.963) < 0.01);
  assert.equal(motionFacts({}), null);
  assert.equal(describeType('M4V'), 'red dwarf');
  assert.equal(describeType('A3V'), 'A-type main-sequence star');
  assert.equal(describeType(''), null);
});

test('journey registry: Barnard gets the full show; others only scenes their data supports', () => {
  const barnard = { id: 0, tier: 'systematics_limited', fit: {}, catalog: { simbad: { id: "NAME Barnard's star" } } };
  const j = journeyFor(barnard);
  assert.equal(isBarnard(barnard), true);
  assert.equal(j.title, 'The Stars Are Not Still');
  assert.deepEqual(j.scenes.map((s) => s.id), ['wonder', 'time', 'measure', 'light', 'observer']);
  const still = { id: 1, tier: 'stationary', catalog: {} };
  const e = new JourneyEngine({ now: () => 0, raf: () => 1, caf: () => {} }).load(journeyFor(still), { d: still });
  assert.deepEqual(e.scenes.map((s) => s.id), ['wonder', 'time', 'light', 'observer'], 'no motion scene without a measured motion');
  assert.match(journeyFor(still).title, /A closer look/);
});

test('missing observations: the time scene refuses to fake change', async () => {
  const scene = _test.passage({});
  const ctx1 = { field: { ...field, epochs: field.epochs.slice(0, 3) }, anchor: 0, subject: field.sources[0], d: { tier: 'stationary' }, name: 'X', loadFrames: async () => {} };
  await assert.rejects(() => scene.prepare(ctx1), /only 1 visit/);
  const fb = scene.fallback(ctx1, new Error('only 1 visit has wavelength-matched frames'));
  assert.match(fb.beats.map((b) => b.caption).join(' '), /won't pretend/);
});
