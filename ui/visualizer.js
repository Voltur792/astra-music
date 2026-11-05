const canvas = document.querySelector("#spectrum");
const spectrum = new MusicSpectrum(canvas);
let settings = { mode: "music", widget: true, background: false, style: "waves", intensity: .65 };
let playing = false;
let pending = false;
let frameId;
function parse(value) {
  for (let i = 0; i < 3 && typeof value === "string"; i++) {
    try { value = JSON.parse(value); } catch (_) { return {}; }
  }
  return value || {};
}
function enabled() { return settings.background && settings.mode !== "off" && (settings.mode === "all" || playing); }
async function sync() {
  if (pending || !window.astra?.callBackend) return;
  pending = true;
  try {
    const state = parse(await window.astra.callBackend("music_visualizer_state", {}));
    const previousMode = settings.mode;
    settings = { ...settings, ...state.settings };
    playing = Boolean(state.playing);
    spectrum.setOptions(settings);
    canvas.hidden = !enabled();
    if (previousMode !== settings.mode || !enabled()) spectrum.clear();
    if (enabled()) {
      if (state.bands?.length) spectrum.sample(state.bands);
      else spectrum.clear();
    }
  } catch (_) {
    playing = false;
    spectrum.clear();
    canvas.hidden = true;
  } finally { pending = false; }
}
const timer = setInterval(sync, 80);
void sync();
let lastFrameAt = 0;
function frame(now) {
  if (enabled() && now - lastFrameAt >= 33) { spectrum.draw(now); lastFrameAt = now; }
  frameId = requestAnimationFrame(frame);
}
frameId = requestAnimationFrame(frame);
window.addEventListener("beforeunload", () => {
  clearInterval(timer);
  cancelAnimationFrame(frameId);
});
