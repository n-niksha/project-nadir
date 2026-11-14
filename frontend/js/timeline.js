import { S, emit } from "./state.js";
import { esc, fmt, dateTime, lambdaColor } from "./fmt.js";
import { visitGroups, wavelengthMatch } from "./playback.js";

export class Timeline {
  constructor(el) {
    this.el = el;
    el.addEventListener("click", (event) => this.activate(event));
    el.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) { event.preventDefault(); this.activate(event); }
      if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.preventDefault(); event.stopPropagation();
        const nodes = [...el.querySelectorAll(".tl-epoch")], current = nodes.indexOf(event.target.closest(".tl-epoch"));
        nodes[(current + (event.key === "ArrowRight" ? 1 : -1) + nodes.length) % nodes.length]?.focus();
      }
    });
  }
  setField(field) { this.f = field; this.render(); }
  render() {
    if (!this.f) return;
    const focused = this.el.querySelector(":focus")?.dataset.i;
    const epochs = this.f.epochs, visits = visitGroups(epochs), dated = visits.flat();
    if (!dated.length) { this.el.textContent = "No timestamped observations available."; return; }
    const width = Math.max(320, this.el.clientWidth), height = 74, margin = 28;
    const start = dated[0].mjd, end = dated.at(-1).mjd;
    const position = (mjd) => margin + (mjd - start) / Math.max(end - start, 1e-6) * (width - margin * 2);
    let overview = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Coverage on a linear time axis; empty intervals contain no observations"><line x1="${margin}" x2="${width - margin}" y1="27" y2="27" stroke="#26314a"/>`;
    visits.forEach((visit, index) => {
      const left = position(visit[0].mjd), right = position(visit.at(-1).mjd);
      overview += `<rect x="${left}" y="15" width="${Math.max(2, right-left)}" height="24" fill="#7aa2ff" opacity=".14"/>`;
      for (const epoch of visit) overview += `<line x1="${position(epoch.mjd)}" x2="${position(epoch.mjd)}" y1="20" y2="34" stroke="${epoch.usable ? '#7aa2ff' : '#ff6f6f'}" opacity=".6"/>`;
      overview += `<text data-visit="${index}" tabindex="0" role="button" aria-label="Jump to visit ${index+1}" x="${(left+right)/2}" y="51" text-anchor="middle" fill="#aab3c8" font-size="10" style="cursor:pointer">Visit ${index+1}</text>`;
      if (index) {
        const previous = visits[index-1].at(-1), gap = visit[0].mjd-previous.mjd;
        overview += `<text x="${(position(previous.mjd)+left)/2}" y="24" text-anchor="middle" fill="#aab3c8" font-size="10">${fmt(gap, 1)} d gap</text>`;
      }
    });
    overview += `<line id="tl-cursor" y1="9" y2="40" stroke="#ffb547" stroke-width="2"/><text x="${margin}" y="70" fill="#aab3c8" font-size="10">${esc(dated[0].date?.slice(0,10))}</text><text x="${width-margin}" y="70" text-anchor="end" fill="#aab3c8" font-size="10">${esc(dated.at(-1).date?.slice(0,10))}</text></svg>`;
    this.position = position;
    const item = (epoch) => {
      const matched = wavelengthMatch(epoch.wavelength_um, this.f.epochs[S.a]?.wavelength_um);
      const title = `${dateTime(epoch.date)} UTC · ${fmt(epoch.wavelength_um,3)} µm · ${epoch.release} · ${epoch.usable ? 'registered' : epoch.unusable_reason}`;
      return `<button class="tl-epoch ${S.playbackMode === 'motion' && !matched ? 'unmatched' : ''}" data-i="${epoch.index}" role="option" aria-selected="false" title="${esc(title)}" aria-label="${esc(title)}" style="--epoch-color:${epoch.usable ? lambdaColor(epoch.wavelength_um) : '#ff6f6f'}"><span>${esc(epoch.date?.slice(5,16).replace('T',' ') || 'Undated')}</span><b>${fmt(epoch.wavelength_um,3)} µm</b><small>${epoch.usable ? '' : 'unavailable · '}<span data-epoch-tag="${epoch.index}"></span></small></button>`;
    };
    this.el.innerHTML = `<div class="coverage">${overview}</div><div class="visit-strip">${visits.map((visit,index) => `<section><h4>Visit ${index+1} · ${visit.length} observations</h4><div class="visit-epochs">${visit.map(item).join('')}</div></section>`).join('')}${epochs.some(epoch => !Number.isFinite(epoch.mjd)) ? `<section><h4>Timestamp unavailable</h4><div class="visit-epochs">${epochs.filter(epoch => !Number.isFinite(epoch.mjd)).map(item).join('')}</div></section>` : ''}</div>`;
    this.update();
    if (focused != null) this.el.querySelector(`[data-i="${focused}"]`)?.focus({preventScroll:true});
  }
  update() {
    const current = S.cmp === "play" ? S.frame : S.cmp === "b" ? S.b : S.a;
    this.el.querySelectorAll(".tl-epoch").forEach((node) => {
      const index = +node.dataset.i;
      node.setAttribute("aria-selected", index === current);
      node.querySelector("[data-epoch-tag]").textContent = [index === S.a && 'A', index === S.b && 'B', index === current && 'viewing'].filter(Boolean).join(' · ');
    });
    const cursor = this.el.querySelector("#tl-cursor"), epoch = this.f?.epochs[current];
    if (cursor && Number.isFinite(epoch?.mjd)) { cursor.setAttribute('x1',this.position(epoch.mjd)); cursor.setAttribute('x2',this.position(epoch.mjd)); }
    const strip = this.el.querySelector('.visit-strip'), selected = this.el.querySelector('.tl-epoch[aria-selected=true]');
    if (S.cmp === 'play' && strip && selected) {
      const bounds = strip.getBoundingClientRect(), item = selected.getBoundingClientRect();
      if (item.left < bounds.left || item.right > bounds.right) strip.scrollLeft += item.left-bounds.left-strip.clientWidth/2+item.width/2;
    }
  }
  activate(event) {
    const visit = event.target.closest('[data-visit]');
    if (visit) { this.el.querySelectorAll('.visit-strip section')[+visit.dataset.visit]?.scrollIntoView({block:'nearest',inline:'start'}); return; }
    const node = event.target.closest(".tl-epoch"); if (!node) return;
    const index = +node.dataset.i;
    if (!this.f.epochs[index].usable) { emit("epochUnavailable", this.f.epochs[index]); return; }
    emit(event.shiftKey ? "setA" : event.altKey ? "setB" : "frame", index);
  }
}
