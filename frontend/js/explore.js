// Explore: the whole SPHEREx sky (DR1 all-sky HiPS cube, 102 wavelengths)
// in Aladin Lite.  Loaded lazily so the Time Machine never depends on it.
import { S } from "./state.js";
import { fmt, esc } from "./fmt.js";

const ALADIN = "https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js";
// Wavelength of each cube frame (µm), from the HiPS properties (data_cube_crval3_arr).
export const CUBE_WL = [0.753, 0.771, 0.790, 0.809, 0.828, 0.848, 0.868, 0.889, 0.910, 0.932, 0.955, 0.978, 1.001, 1.025, 1.050, 1.075, 1.102,
  1.113, 1.139, 1.167, 1.195, 1.224, 1.254, 1.284, 1.315, 1.346, 1.378, 1.412, 1.446, 1.481, 1.517, 1.554, 1.592, 1.631, 1.656, 1.695, 1.735, 1.775,
  1.818, 1.861, 1.905, 1.949, 1.995, 2.041, 2.089, 2.139, 2.189, 2.240, 2.292, 2.345, 2.399, 2.445, 2.506, 2.575, 2.646, 2.719, 2.794, 2.871, 2.951,
  3.033, 3.118, 3.204, 3.293, 3.383, 3.476, 3.570, 3.667, 3.768, 3.833, 3.867, 3.901, 3.935, 3.970, 4.005, 4.040, 4.076, 4.112, 4.148, 4.184, 4.220,
  4.257, 4.294, 4.331, 4.368, 4.405, 4.435, 4.467, 4.500, 4.533, 4.566, 4.600, 4.635, 4.669, 4.704, 4.739, 4.773, 4.808, 4.843, 4.879, 4.915, 4.951, 4.988];
const BLANK = new Set([50, 51]); // dichroic channels, blank in the cube

export class Explorer {
  constructor(cfg, presets, onOpen) {
    this.cfg = cfg; this.presets = presets; this.onOpen = onOpen; this.picked = null; this.survey = "spherex";
    this.renderPanel();
    document.querySelectorAll("[data-survey]").forEach((b) => (b.onclick = () => this.setSurvey(b.dataset.survey)));
  }
  renderPanel() {
    const pm = (p) => p.role?.match(/(\d+(?:\.\d+)?)"\/yr/)?.[1];
    const panel = document.getElementById("atlas-panel");
    panel.innerHTML = `<p class="eyebrow" style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--sun);margin:0">Sky atlas</p><h2>The whole infrared sky</h2>
      <p>This is SPHEREx's DR1 all-sky map — a co-added mosaic, not a single observation. Drag to explore, pick a wavelength below, or start from a verified target.</p>
      <div class="presets">${this.presets.map((p) => `<button class="preset" data-atlas="${p.id}"><b>${esc(p.name)}</b>${pm(p) ? `<span class="pm">${pm(p)}″/yr</span>` : "<span></span>"}<span class="role">${esc(p.role)}</span></button>`).join("")}</div>
      <p class="small">Choosing a target opens its individual, dated SPHEREx observations in the Observatory. Amber circles on the atlas mark these targets.</p>`;
    panel.querySelectorAll("[data-atlas]").forEach((b) => {
      const p = this.presets.find((x) => x.id === b.dataset.atlas);
      b.onclick = () => this.onOpen?.(p);
      b.onmouseenter = () => this.aladin?.gotoRaDec(p.ra, p.dec);
    });
  }
  setSurvey(name) {
    document.querySelectorAll("[data-survey]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.survey === name));
    this.survey = name;
    const hint = document.getElementById("explore-hint"), slice = document.getElementById("slice");
    slice.disabled = name !== "spherex";
    if (!this.aladin) return;
    try {
      if (name === "2mass") {
        this.context ??= this.aladin.newImageSurvey("CDS/P/2MASS/color");
        this.aladin.setBaseImageLayer(this.context);
        hint.textContent = "Context only: 2MASS J/H/Ks colour composite (1997–2001, CDS HiPS). Not SPHEREx data; never used for NADIR measurements.";
      } else {
        this.aladin.setBaseImageLayer(this.hips);
        this.setSlice(+slice.value);
        hint.textContent = "SPHEREx DR1 all-sky cube (IRSA HiPS) · 102 wavelengths · click anywhere to choose a target, then Observe.";
      }
    } catch (error) { hint.textContent = "This imagery layer could not be loaded."; console.error("[NADIR] survey", error); }
  }
  async show() {
    document.getElementById("slice-out").textContent = label(+document.getElementById("slice").value);
    if (this.aladin) { this.goto(); return; }
    if (this.loading) return this.loading;
    const hint = document.getElementById("explore-hint");
    hint.textContent = "Loading Aladin Lite and the SPHEREx all-sky cube…";
    this.loading = (async () => {
      try {
        const { default: A } = await import(ALADIN);
        await A.init;
        this.A = A;
        this.hips = A.imageHiPS(this.cfg.hips_url, { name: "SPHEREx DR1 cube", imgFormat: "png",
          successCallback: () => this.setSlice(+document.getElementById("slice").value) });
        await this.hips.query;
        this.aladin = A.aladin("#aladin", { survey: this.hips, log: false, fov: 1.2, target: `${S.query?.ra ?? 269.446} ${S.query?.dec ?? 4.768}`, cooFrame: "ICRSd", projection: "TAN",
          showCooGrid: true, showReticle: true, showLayersControl: false, showGotoControl: false, showFrame: false, showFullscreenControl: false,
          showSimbadPointerControl: false, showShareControl: false, showContextMenu: false, showProjectionControl: false, showZoomControl: true });
        const cat = A.catalog({ name: "NADIR presets", sourceSize: 16, color: "#f2b350", shape: "circle", onClick: "showPopup" });
        this.aladin.addCatalog(cat);
        cat.addSources(this.presets.map((p) => A.marker(p.ra, p.dec, { popupTitle: p.name, popupDesc: p.role })));
        this.fieldLayer = A.graphicOverlay({ color: "#8fa8e8", lineWidth: 1.5 }); this.aladin.addOverlay(this.fieldLayer);
        this.pickLayer = A.graphicOverlay({ color: "#f2b350", lineWidth: 2 }); this.aladin.addOverlay(this.pickLayer);
        this.aladin.on("click", (e) => {
          const ra = e?.ra ?? e?.data?.ra, dec = e?.dec ?? e?.data?.dec;
          if (Number.isFinite(ra) && Number.isFinite(dec) && !e?.isDragging) this.pick(ra, dec);
        });
        try { this.aladin.setCooGrid({ color: "#8fa8e8", opacity: 0.35, labelSize: 12, enabled: true }); } catch (_) {}
        this.goto();
        hint.textContent = "SPHEREx DR1 all-sky cube (IRSA HiPS) · 102 wavelengths · click anywhere to choose a target, then Observe.";
      } catch (err) {
        hint.textContent = "The all-sky viewer could not load (needs internet access to cdn.jsdelivr.net and IRSA, and WebGL). The Time Machine still works.";
        console.error("[NADIR] Aladin failed", err); this.loading = null;
      }
    })();
    return this.loading;
  }
  goto() {
    if (!this.aladin || !S.query) return;
    this.aladin.gotoRaDec(S.query.ra, S.query.dec);
    this.fieldLayer?.removeAll?.();
    const h = S.query.size / 2, c = Math.cos(S.query.dec * Math.PI / 180);
    this.fieldLayer?.add(this.A.polygon([[S.query.ra - h / c, S.query.dec - h], [S.query.ra + h / c, S.query.dec - h], [S.query.ra + h / c, S.query.dec + h], [S.query.ra - h / c, S.query.dec + h]]));
  }
  setSlice(i) {
    document.getElementById("slice-out").textContent = label(i);
    if (!this.hips?.added) return;
    try {
      const minFrequency = 299792458 / this.hips.emMax;
      const maxFrequency = 299792458 / this.hips.emMin;
      const frequency = minFrequency + (i + 0.5) / this.hips.cubeDepth * (maxFrequency - minFrequency);
      this.hips.setFrequency({ value: frequency, unit: "Hz" });
    } catch (error) {
      document.getElementById("slice-out").textContent = "Channel selection unavailable";
      document.getElementById("explore-hint").textContent = "The atlas channel could not be selected. Do not interpret its wavelength; use the Time Machine for individual observations.";
      console.error("[NADIR] atlas channel", error);
    }
  }
  pick(ra, dec) {
    this.picked = { ra, dec };
    this.pickLayer?.removeAll?.(); this.pickLayer?.add(this.A.circle(ra, dec, 0.05));
    const b = document.getElementById("explore-go"); b.disabled = false; b.textContent = `Observe ${fmt(ra, 4)}°, ${fmt(dec, 4)}°`;
  }
}
export function label(i) { return `${CUBE_WL[i].toFixed(3)} µm${BLANK.has(i) ? " (blank)" : ""}`; }
