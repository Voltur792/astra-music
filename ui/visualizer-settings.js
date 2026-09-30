(function () {
  "use strict";
  const panel = document.getElementById("visualizerSettings");
  if (!panel) return;
  const styles = [["spectrum", "Спектр"], ["waves", "Неоновые волны"], ["liquid", "Жидкий свет"], ["ripple", "Круги на воде"], ["ribbon", "Цветные ленты"], ["orbit", "Орбита"], ["particles", "Частицы"], ["mesh", "Неоновая сетка"], ["binary", "Цифровые волны"], ["terrain", "Золотой ландшафт"], ["silk", "Цветной шёлк"], ["splash", "Всплеск"], ["rain", "Радужный дождь"], ["contour", "Слои цвета"]];
  const defaults = { mode: "music", widget: true, background: false, style: "waves", intensity: .65 };
  let settings = { ...defaults }, loaded = false, saving = false, dirty = false, saveTimer;
  const status = document.getElementById("visualizerSaveStatus");
  const widget = document.getElementById("visualizerWidget");
  const background = document.getElementById("visualizerBackground");
  const intensity = document.getElementById("visualizerIntensity");
  const output = document.getElementById("visualizerIntensityValue");
  const styleGroup = document.getElementById("visualizerStyles");
  const previews = [];
  function report(text, error = false) { status.textContent = text; status.classList.toggle("bad", error); }
  function render() {
    panel.querySelectorAll("[data-visualizer-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.visualizerMode === settings.mode)));
    styleGroup.querySelectorAll("[data-visualizer-style]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.visualizerStyle === settings.style)));
    widget.checked = settings.widget; background.checked = settings.background;
    intensity.value = settings.intensity; output.value = Math.round(settings.intensity * 100) + "%";
    previews.forEach(preview => preview.renderer.setOptions({ style: preview.style, intensity: settings.intensity }));
  }
  function parse(value) { return typeof value === "string" ? JSON.parse(value) : value; }
  async function save() {
    if (!loaded || saving || !dirty) return;
    saving = true; dirty = false;
    const snapshot = { ...settings };
    report("Сохранение…");
    try {
      const result = parse(await window.astra.callBackend("music_visualizer_set", { settings: snapshot }));
      if (result?.error || result?.ok === false) throw new Error(result.error || result.message || "Настройки не сохранены");
      report(dirty ? "Сохранение…" : "Сохранено");
    } catch (error) { dirty = true; report("Не удалось сохранить. Повторить", true); }
    finally { saving = false; }
    // Queue newer edits; failed requests wait for explicit retry or the next edit.
    if (dirty && status.textContent === "Сохранение…") save();
  }
  function change(key, value) {
    settings[key] = value; dirty = true; render();
    clearTimeout(saveTimer); saveTimer = setTimeout(save, key === "intensity" ? 180 : 0);
  }
  panel.querySelectorAll("[data-visualizer-mode]").forEach(button => button.addEventListener("click", () => change("mode", button.dataset.visualizerMode)));
  widget.addEventListener("change", () => change("widget", widget.checked));
  background.addEventListener("change", () => change("background", background.checked));
  intensity.addEventListener("input", () => change("intensity", Number(intensity.value)));
  status.addEventListener("click", () => { if (!loaded) initialize(); else save(); });
  styles.forEach(([id, label]) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "visualizer-style";
    button.dataset.visualizerStyle = id; button.setAttribute("aria-pressed", "false");
    const canvas = document.createElement("canvas"); canvas.setAttribute("aria-hidden", "true");
    const text = document.createElement("span"); text.textContent = label;
    button.append(canvas, text); styleGroup.append(button);
    const renderer = new window.MusicSpectrum(canvas); renderer.setOptions({ style: id, intensity: settings.intensity });
    previews.push({ renderer, style: id }); button.addEventListener("click", () => change("style", id));
  });
  // Arrow navigation keeps both custom button groups convenient from the keyboard.
  panel.querySelectorAll("[data-visualizer-group]").forEach(group => group.addEventListener("keydown", event => {
    if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const buttons = [...group.querySelectorAll("button")], index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const target = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length;
    buttons[target].focus();
  }));
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  let frameId, lastPreview = 0;
  function preview(now) {
    if (!document.hidden && now - lastPreview > (media.matches ? 1000 : 50)) {
      const rect = panel.getBoundingClientRect();
      if (rect.bottom > 0 && rect.top < innerHeight) {
        // Synthetic samples are restricted to these labelled settings previews.
        const bands = Array.from({ length: 48 }, (_, i) => .12 + Math.pow((1 + Math.sin(i * .32 + (media.matches ? 1 : now * .001))) / 2, 3) * .7);
        previews.forEach(item => { item.renderer.sample(bands); item.renderer.draw(now); });
      }
      lastPreview = now;
    }
    frameId = requestAnimationFrame(preview);
  }
  async function initialize() {
    report("Загрузка настроек…"); panel.setAttribute("aria-busy", "true");
    const fieldset = panel.querySelector("fieldset"); fieldset.disabled = true;
    try {
      const start = performance.now();
      while (!window.astra?.callBackend) {
        if (performance.now() - start > 8000) throw new Error("Bridge unavailable");
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      const result = parse(await window.astra.callBackend("music_visualizer_get", {}));
      if (!result || result.error) throw new Error("Settings unavailable");
      settings = { ...defaults, ...result };
      if (!["off", "all", "music"].includes(settings.mode)) settings.mode = defaults.mode;
      if (!styles.some(([id]) => id === settings.style)) settings.style = defaults.style;
      settings.widget = settings.widget !== false; settings.background = settings.background === true;
      settings.intensity = Math.max(.2, Math.min(1, Number(settings.intensity) || .65));
      loaded = true; render(); report("Сохранено"); fieldset.disabled = false;
    } catch (_) { report("Настройки недоступны. Повторить", true); }
    finally { panel.setAttribute("aria-busy", "false"); }
  }
  render(); initialize(); frameId = requestAnimationFrame(preview);
  window.addEventListener("beforeunload", () => { clearTimeout(saveTimer); cancelAnimationFrame(frameId); });
})();
