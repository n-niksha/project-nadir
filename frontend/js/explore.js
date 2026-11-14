// Explore: the whole SPHEREx sky (DR1 all-sky HiPS cube, 102 wavelengths)
// in Aladin Lite.  Loaded lazily so the Time Machine never depends on it.
import { S, emit } from "./state.js";
import { fmt } from "./fmt.js";

const ALADIN = "https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js";
// Wavelength of each cube frame (µm), from the HiPS properties (data_cube_crval3_arr).
export const CUBE_WL = [0.753, 0.771, 0.790, 0.809, 0.828, 0.848, 0.868, 0.889, 0.910, 0.932, 0.955, 0.978, 1.001, 1.025, 1.050, 1.075, 1.102,
  1.113, 1.139, 1.167, 1.195, 1.224, 1.254, 1.284, 1.315, 1.346, 1.378, 1.412, 1.446, 1.481, 1.517, 1.554, 1.592, 1.631, 1.656, 1.695, 1.735, 1.775,
  1.818, 1.861, 1.905, 1.949, 1.995, 2.041, 2.089, 2.139, 2.189, 2.240, 2.292, 2.345, 2.399, 2.445, 2.506, 2.575, 2.646, 2.719, 2.794, 2.871, 2.951,
  3.033, 3.118, 3.204, 3.293, 3.383, 3.476, 3.570, 3.667, 3.768, 3.833, 3.867, 3.901, 3.935, 3.970, 4.005, 4.040, 4.076, 4.112, 4.148, 4.184, 4.220,
  4.257, 4.294, 4.331, 4.368, 4.405, 4.435, 4.467, 4.500, 4.533, 4.566, 4.600, 4.635, 4.669, 4.704, 4.739, 4.773, 4.808, 4.843, 4.879, 4.915, 4.951, 4.988];
const BLANK = new Set([50, 51]); // dichroic channels, blank in the cube

export class Explorer {
  constructor(cfg, presets) { this.cfg = cfg; this.presets = presets; this.picked = null; }
  async show() {
    document.getElementById("slice-out").textContent = label(+document.getElementById("slice").value);
    if (this.aladin) { this.goto(); return; }
    if (this.loading) return this.loading;
    const hint = document.getElementById("explore-hint");
    hint.textContent = "Loading Aladin Lite and the SPHEREx all-sky cube…";
    this.loading = (async () => {
      try {
        await loadScript(ALADIN);
        await window.A.init;
        const A = window.A;
        this.aladin = A.aladin("#aladin", { fov: 1.2, target: `${S.query?.ra ?? 269.446} ${S.query?.dec ?? 4.768}`, cooFrame: "ICRSd", projection: "TAN",
          showCooGrid: true, showReticle: true, showLayersControl: false, showGotoControl: false, showFrame: true, showFullscreenControl: false,
          showSimbadPointerControl: false, showShareControl: false, showContextMenu: false, showProjectionControl: false, showZoomControl: true });
        this.hips = this.aladin.newImageSurvey(this.cfg.hips_url, { name: "SPHEREx DR1 cube", imgFormat: "png" });
        this.aladin.setBaseImageLayer(this.hips);
        this.setSlice(+document.getElementById("slice").value);
        const cat = A.catalog({ name: "NADIR presets", sourceSize: 14, color: "#ffb547", shape: "circle", onClick: "showPopup" });
        this.aladin.addCatalog(cat);
        cat.addSources(this.presets.map((p) => A.marker(p.ra, p.dec, { popupTitle: p.name, popupDesc: p.role })));
        this.fieldLayer = A.graphicOverlay({ color: "#7aa2ff", lineWidth: 1.5 }); this.aladin.addOverlay(this.fieldLayer);
        this.pickLayer = A.graphicOverlay({ color: "#ffb547", lineWidth: 2 }); this.aladin.addOverlay(this.pickLayer);
        this.aladin.on("click", (e) => {
          const ra = e?.ra ?? e?.data?.ra, dec = e?.dec ?? e?.data?.dec;
          if (Number.isFinite(ra) && Number.isFinite(dec) && !e?.isDragging) this.pick(ra, dec);
        });
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
    this.fieldLayer?.add(window.A.polygon([[S.query.ra - h / c, S.query.dec - h], [S.query.ra + h / c, S.query.dec - h], [S.query.ra + h / c, S.query.dec + h], [S.query.ra - h / c, S.query.dec + h]]));
  }
  setSlice(i) {
    document.getElementById("slice-out").textContent = label(i);
    try { this.hips?.setSliceNumber?.(i); } catch (e) { console.warn("[NADIR] slice", e); }
  }
  pick(ra, dec) {
    this.picked = { ra, dec };
    this.pickLayer?.removeAll?.(); this.pickLayer?.add(window.A.circle(ra, dec, 0.05));
    const b = document.getElementById("explore-go"); b.disabled = false; b.textContent = `Observe ${fmt(ra, 4)}°, ${fmt(dec, 4)}°`;
  }
}
export function label(i) { return `${CUBE_WL[i].toFixed(3)} µm${BLANK.has(i) ? " (blank)" : ""}`; }
function loadScript(src) {
  return new Promise((res, rej) => { if (window.A) return res(); const s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = () => rej(new Error("script load failed")); document.head.appendChild(s); });
}
