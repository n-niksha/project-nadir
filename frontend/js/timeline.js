// Time Machine track.  Two honest views of time at once:
//  - a thin overview line with every exposure at its true calendar position;
//  - a broken axis below it: one equal-width segment per visit (exposures in
//    time order), separated by labelled gaps ("196-day gap").  Nothing is
//    interpolated; every tick is one real observation.
// An optional exposure list (#visit-strip) shows each observation as a card.
import { S, emit } from "./state.js";
import { esc, fmt, dateTime, lambdaColor, niceDate, shortRange } from "./fmt.js";
import { visitGroups, wavelengthMatch } from "./playback.js";

export class Timeline {
  constructor(el, strip) {
    this.el = el; this.strip = strip;
    for (const root of [el, strip]) {
      root.addEventListener("click", (event) => this.activate(event));
      root.addEventListener("keydown", (event) => {
        if (["Enter", " "].includes(event.key) && event.target.closest(".tl-epoch, .tl-card")) { event.preventDefault(); this.activate(event); }
        if (["ArrowLeft", "ArrowRight"].includes(event.key) && event.target.closest(".tl-epoch, .tl-card")) {
          event.preventDefault(); event.stopPropagation();
          const sel = event.target.closest(".tl-card") ? ".tl-card" : ".tl-epoch";
          const nodes = [...root.querySelectorAll(sel)], current = nodes.indexOf(event.target.closest(sel));
          nodes[(current + (event.key === "ArrowRight" ? 1 : -1) + nodes.length) % nodes.length]?.focus();
        }
      });
    }
    el.addEventListener("pointerover", (e) => this.tip(e.target.closest(".tl-epoch")));
    el.addEventListener("focusin", (e) => this.tip(e.target.closest(".tl-epoch")));
    el.addEventListener("pointerleave", () => this.tip(null));
    el.addEventListener("focusout", () => this.tip(null));
    new ResizeObserver(() => { if (this.f && Math.abs(this.el.clientWidth - (this.lastW || 0)) > 1) this.render(); }).observe(el);
  }
  setField(field) { this.f = field; this.render(); }

  render() {
    if (!this.f) return;
    const focused = this.el.querySelector(":focus")?.dataset.i;
    const epochs = this.f.epochs, visits = visitGroups(epochs), dated = visits.flat();
    const undated = epochs.filter((e) => !Number.isFinite(e.mjd));
    if (!epochs.length) { this.el.innerHTML = `<p class="small">No observations available.</p>`; return; }
    const width = Math.max(280, this.el.clientWidth || 600), pad = 6;
    this.lastW = this.el.clientWidth;
    const segments = [...visits, ...(undated.length ? [undated] : [])], n = segments.length;
    const gapW = n > 1 ? Math.max(56, Math.min(96, width * .09)) : 0;
    const segW = (width - pad * 2 - gapW * (n - 1)) / n;
    const anchor = this.f.epochs[S.a]?.wavelength_um;
    const start = dated[0]?.mjd, end = dated.at(-1)?.mjd, span = Math.max((end - start) || 1, 1e-6);
    const linear = (mjd) => pad + (mjd - start) / span * (width - pad * 2);
    this.linear = linear;

    let svg = `<svg class="cov" viewBox="0 0 ${width} 82" preserveAspectRatio="none" aria-hidden="true">`;
    // overview: true calendar spacing
    svg += `<line x1="${pad}" x2="${width - pad}" y1="7" y2="7" stroke="var(--line-2)" stroke-width="1"/>`;
    for (const e of dated) svg += `<line x1="${linear(e.mjd)}" x2="${linear(e.mjd)}" y1="4" y2="10" stroke="${e.usable ? "var(--text-3)" : "var(--coral)"}" stroke-width="1"/>`;
    svg += `<circle id="tl-cursor" cy="7" r="4" fill="var(--sun)" cx="-20"/>`;
    let ticks = "";
    segments.forEach((seg, i) => {
      const x0 = pad + i * (segW + gapW), isUndated = seg === undated;
      svg += `<rect x="${x0}" y="23" width="${segW}" height="40" rx="6" fill="rgba(143,168,232,.06)" stroke="var(--line-1)"/>`;
      const roomy = segW > 190;
      const label = isUndated ? (roomy ? `Timestamp unavailable · ${seg.length}` : "Undated") : roomy ? `Visit ${i + 1} · ${shortRange(seg[0].date, seg.at(-1).date)}` : `Visit ${i + 1}`;
      svg += `<text class="vlabel" x="${x0 + 2}" y="78">${esc(label)}</text>`;
      if (!isUndated && segW > 110) svg += `<text x="${x0 + segW - 2}" y="78" text-anchor="end">${seg.length} exp.</text>`;
      if (i && !isUndated) {
        const prev = segments[i - 1].at(-1), gap = seg[0].mjd - prev.mjd, gx = x0 - gapW;
        svg += `<line x1="${gx + 6}" x2="${x0 - 6}" y1="43" y2="43" stroke="var(--line-3)" stroke-dasharray="2 4"/>`;
        svg += `<text class="gap" x="${gx + gapW / 2}" y="38" text-anchor="middle">${fmt(gap, 0)}${gapW >= 84 ? "-day gap" : " d"}</text>`;
      }
      const w = Math.max(6, Math.min(16, segW / seg.length));
      seg.forEach((epoch, j) => {
        const x = x0 + segW * (j + .5) / seg.length;
        const matched = wavelengthMatch(epoch.wavelength_um, anchor);
        const cls = [!epoch.usable && "unusable", S.playbackMode === "motion" && epoch.usable && !matched && "unmatched"].filter(Boolean).join(" ");
        const title = `${epoch.date ? dateTime(epoch.date) + " UTC" : "Undated"} · ${fmt(epoch.wavelength_um, 3)} µm · ${epoch.release}${epoch.usable ? "" : " · unavailable: " + epoch.unusable_reason}`;
        ticks += `<button class="tl-epoch ${cls}" data-i="${epoch.index}" role="option" aria-selected="false" aria-label="${esc(title)}" style="left:${x - w / 2}px;width:${w}px;--epoch-color:${epoch.usable ? lambdaColor(epoch.wavelength_um) : "var(--coral)"}"><span class="ab" hidden></span></button>`;
      });
    });
    svg += `</svg>`;
    this.el.innerHTML = `<div class="coverage">${svg}${ticks}</div><div class="tl-tip" hidden></div>`;
    this.el.setAttribute("role", "listbox"); this.el.setAttribute("aria-label", "Observations in time order");

    if (this.strip) {
      const card = (epoch) => {
        const matched = wavelengthMatch(epoch.wavelength_um, anchor);
        return `<button class="tl-card ${S.playbackMode === "motion" && !matched ? "unmatched" : ""}" data-i="${epoch.index}" aria-selected="false" style="--epoch-color:${epoch.usable ? lambdaColor(epoch.wavelength_um) : "var(--coral)"}"><span>${esc(epoch.date?.slice(5, 16).replace("T", " ") || "Undated")}</span><b>${fmt(epoch.wavelength_um, 3)} µm</b><small>${epoch.usable ? "" : "unavailable · "}<span data-epoch-tag="${epoch.index}"></span></small></button>`;
      };
      this.strip.innerHTML = segments.map((seg, i) => `<section><h4>${seg === undated ? "Timestamp unavailable" : `Visit ${i + 1} · ${seg.length} observations`}</h4><div class="visit-epochs">${seg.map(card).join("")}</div></section>`).join("");
    }
    this.update();
    if (focused != null) this.el.querySelector(`[data-i="${focused}"]`)?.focus({ preventScroll: true });
  }

  update() {
    if (!this.f) return;
    const current = S.cmp === "play" ? S.frame : S.cmp === "b" ? S.b : S.cmp === "blink" && S.blinkOn ? S.b : S.a;
    this.el.querySelectorAll(".tl-epoch").forEach((node) => {
      const index = +node.dataset.i, sel = index === current;
      node.setAttribute("aria-selected", sel);
      const ab = node.querySelector(".ab"), tag = index === S.a ? "A" : index === S.b ? "B" : "";
      ab.hidden = !tag || (sel && S.cmp === "play"); ab.textContent = tag; ab.className = `ab ${tag.toLowerCase()}`;
    });
    this.strip?.querySelectorAll(".tl-card").forEach((node) => {
      const index = +node.dataset.i;
      node.setAttribute("aria-selected", index === current);
      node.querySelector("[data-epoch-tag]").textContent = [index === S.a && "A", index === S.b && "B", index === current && "viewing"].filter(Boolean).join(" · ");
    });
    const cursor = this.el.querySelector("#tl-cursor"), epoch = this.f.epochs[current];
    if (cursor && Number.isFinite(epoch?.mjd)) cursor.setAttribute("cx", this.linear(epoch.mjd));
    const strip = this.strip, selected = strip?.querySelector(".tl-card[aria-selected=true]");
    if (S.cmp === "play" && strip && selected && strip.offsetParent) {
      const bounds = strip.getBoundingClientRect(), item = selected.getBoundingClientRect();
      if (item.left < bounds.left || item.right > bounds.right) strip.scrollLeft += item.left - bounds.left - strip.clientWidth / 2 + item.width / 2;
    }
  }

  tip(node) {
    const tip = this.el.querySelector(".tl-tip"); if (!tip) return;
    if (!node) { tip.hidden = true; return; }
    const e = this.f.epochs[+node.dataset.i]; if (!e) return;
    const x = node.offsetLeft + node.offsetWidth / 2;
    tip.innerHTML = `<b>${esc(niceDate(e.date))}</b> ${e.date ? `<span>${esc(e.date.slice(11, 16))} UTC</span> · ` : ""}<span style="color:${lambdaColor(e.wavelength_um)}">${fmt(e.wavelength_um, 3)} µm</span>${e.usable ? "" : ` · <span style="color:var(--coral)">${esc(e.unusable_reason || "unavailable")}</span>`}`;
    tip.hidden = false;
    const half = tip.offsetWidth / 2, W = this.el.clientWidth;
    tip.style.left = `${Math.max(half, Math.min(W - half, x))}px`;
    tip.style.top = "8px"; tip.style.bottom = "auto"; tip.style.transform = "translate(-50%, -100%)";
  }

  activate(event) {
    const node = event.target.closest(".tl-epoch, .tl-card"); if (!node) return;
    const index = +node.dataset.i;
    if (!this.f.epochs[index].usable) { emit("epochUnavailable", this.f.epochs[index]); return; }
    emit(event.shiftKey ? "setA" : event.altKey ? "setB" : "frame", index);
  }
}
