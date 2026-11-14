# NADIR design system

**NADIR — An Atlas of the Unseen.** The sky is the hero; everything else is
quiet glass around it. Every number says where it came from.

Tokens live in `frontend/css/tokens.css`. Components live in
`frontend/css/nadir.css`. Canvas and SVG code read colour tokens at runtime
through `frontend/js/tokens.js` (`C("sun")`, `alpha()`, `seriesColor()`), so
the stylesheet is the single source of truth. Do not hard-code hex values in
new code; add a token.

## Identity

| Asset | File | Use |
|---|---|---|
| Mark | `frontend/img/nadir-mark.svg` (also inline `#i-mark` symbol) | Top bar, intro, anywhere ≥ 20 px |
| Favicon | `frontend/img/favicon.svg` | Browser tab (rounded tile, simplified mark) |
| Wordmark | `frontend/img/nadir-wordmark.svg` | Slides, README, social cards |

The mark is the celestial sphere seen from inside: an aperture ring open at
its lowest point, the horizon plane, the zenith–nadir axis, and an amber point
of light at the nadir — the point directly beneath the observer, where the
unseen begins. At 16 px it reduces to ring + amber dot.

Wordmark: `NADIR` in IBM Plex Sans Medium, tracking `0.42em`; tagline in
Instrument Serif italic.

## Colour

| Role | Token | Notes |
|---|---|---|
| Night | `--ink-0 … --ink-4` | Near-black → midnight; panels step up one ink at a time |
| Text | `--text-1/2/3` | Warm off-white; `--text-3` is the minimum for any readable text (≥ 4.8:1) |
| Signature | `--sun` `--sun-2` | NADIR amber. Reserved for *the one thing to look at*: play, selection, the headline measurement |
| Secondary science | `--sky` `--violet` | Catalog data, predictions, models |
| Semantic | `--teal` `--coral` `--cyan` | OK/reference, rejected/error, variability |

Evidence tiers (`--st-*`) and provenance (`--ev-*`) are aliases, so overlay
rings, badges, legends and charts always agree:

| Tier | Colour | Sky marker |
|---|---|---|
| Significant motion | sun | solid ring + motion arrow |
| Systematics-limited | sun | dashed ring + arrow |
| Candidate | violet | dotted ring |
| Rejected | coral | × |
| Stationary reference | teal, 50% | small thin ring (Research or *Reference stars* layer only) |

Catalog overlays: Gaia = blue ◇, 2MASS = orange +, AllWISE = violet +,
SIMBAD = off-white dot + name. Every marker is drawn over a dark halo so it
reads on any background and never fills the star it marks.

## Type

| Role | Family | Size |
|---|---|---|
| Display: object names, page titles, dates in the Time Machine | Instrument Serif | 24–48 px |
| UI and body | IBM Plex Sans 400/500/600 | 13–16 px |
| Measurements, coordinates, timestamps | IBM Plex Mono (tabular) | 12–36 px |
| Labels / kickers | Plex Sans, uppercase, `0.12em` tracking | 11–12 px |

**Minimum size is 12 px** for anything a user must read (11 px only for
uppercase kickers and kbd glyphs). Fonts are self-hosted (`frontend/fonts`,
OFL) so the demo works offline.

## Space, shape, motion

4 px base (`--s-1 … --s-7`). Radii `6 / 10 / 14 / pill`. Controls are 34 px
(30 px small), 44 px for the primary play button and tabs. Motion uses
`--ease` with 120 / 220 / 480 ms durations. All animation is skipped under
`prefers-reduced-motion`, and no animation ever blocks interaction.

## Components

- **Buttons**: `.btn` (default), `.primary` (navigational commit),
  `.accent` (amber — at most one per view), `.ghost`, `.sm`; `.icon-btn`,
  `.icon-btn.glass` over imagery; `.link-btn` / `.learn-link`.
- **Segmented control** `.seg` — mutually exclusive modes; the active button
  uses `aria-pressed="true"` or `.active`.
- **Popover** `.popover` inside `.pop-anchor` — one open at a time, closes on
  outside click / Esc; used for targets, layers and data provenance.
- **Badges**: `.tier.<tier>` (evidence), `.tag.<measured|cat|inferred|illustrative|unknown>`
  (provenance of a single value), `.chip` (filters), `.pillstat` (facts).
- **Callout** `.callout(.ok|.warn|.bad)` — verdicts and caveats.
- **Key–value** `.kv` — label column + value with a `.small` qualifier line.
- **Hero result** `.hero-result` — one headline number, its uncertainty, and fact pills.
- **Chart** `plot()` in `js/plots.js` → `<figure class="chart">` with a live
  `.plot-readout`; every point has a 16 px invisible hit target, is focusable,
  and announces its exact values.
- **Loader / error card** `.loader-card` — staged progress, always with a way out.

## Layout

```
┌ top bar: mark · views · search (/) · Guided demo ─────────────────────────┐
│ ┌ sky (full bleed) ──────────────────────────────────┐ ┌ dossier ───────┐ │
│ │ field ▾ (targets)            Clean|Discovery|Research│ │ name, tier     │ │
│ │ frame caption                         Layers ▾      │ │ hero result     │ │
│ │                                                     │ │ explain ▾       │ │
│ │ legend · Real SPHEREx data ▾      scale/compass  ±⤢ │ │ tabs            │ │
│ └─────────────────────────────────────────────────────┘ │                 │ │
│ ┌ Time Machine: ‹ ▶ › date · λ · frame n/N   λ-filter speed ⌄ ┐ │         │ │
│ │ visit segments with real gaps (+ true-scale overview line)  │ │         │ │
│ │ Timelapse | A B Blink Overlay B−A    A→B · days · Δλ/λ   Set A/B │       │ │
│ └─────────────────────────────────────────────────────────────┘ └────────┘ │
```

Progressive disclosure: essential controls are always visible (search,
target, zoom, play/step, date, mode); intermediate ones sit one click away
(layers, λ filter, compare modes, dossier tabs); advanced ones are inside
`<details>` (coordinates/detector, evidence/class filters, measurement
ledgers, research explanations, every-exposure list).

Breakpoints: ≤1360 drop tagline · ≤1240 narrower dossier · ≤1100 icon-only
Layers · ≤1024 single column (sky, Time Machine, dossier stacked) · ≤640
mobile (HUD reflows, popovers become bottom sheets).

## Rendering contract (display only)

`presentFrame()` in `js/sky.js` relies on the server's frame PNG palette
(`nadir/field.py: GRAY_PALETTE`): index 254 → navy `(16,20,44)` = no data,
255 → `(255,246,228)` = saturated core. If that palette changes, update
`presentFrame()` and its unit test. Presentation mode fills isolated flagged
pixels from ≥3 valid neighbours, resamples with a Mitchell–Netravali bicubic
kernel (3×) weighted by validity so large gaps stay transparent, and applies a
monotonic midnight→warm tone curve. *Scientific pixels* draws the server
frame nearest-neighbour with pixel boundaries at high zoom. Neither touches
measurement.

## Voice

Curious, clear, credible; occasionally poetic, never pretentious. One
memorable line per moment at most ("The universe is never still."). Prefer
"moves 10.1″ a year" to "exhibits proper motion". Never call a candidate a
discovery. When something can't be measured, say so plainly and say why.
