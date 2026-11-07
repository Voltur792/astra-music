// Windows palette and screen picker share the normal input/change save path.
(() => {
  let active = null;
  const nativePalette = /Windows/i.test(navigator.userAgent);
  const targets = ["widgetThemeBackground", "widgetThemeAccent", "visualizerColor1", "visualizerColor2"];
  function parse(result) {
    for (let n = 0; n < 4; n++) {
      if (typeof result === "string") result = JSON.parse(result);
      else if (result?.result_json) result = result.result_json;
      else break;
    }
    if (result?.error) throw new Error(result.error);
    return result;
  }
  for (const id of targets) {
    const input = document.getElementById(id);
    if (!input) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "screen-color-button";
    button.textContent = "Пипетка";
    button.setAttribute("aria-label", "Выбрать с экрана: " + (input.labels?.[0]?.textContent.trim() || "цвет"));
    const status = document.createElement("span");
    status.className = "hint screen-color-status";
    status.setAttribute("role", "status");
    // Keep an action button outside a label, so it cannot also open the native dialog.
    const anchor = input.closest("label") || input;
    const field = document.createElement("div");
    field.className = "screen-color-field";
    const controls = document.createElement("div");
    controls.className = "screen-color-controls";
    anchor.before(field);
    controls.append(anchor, button);
    field.append(controls, status);
    const updateDisabled = () => { button.disabled = input.disabled || Boolean(input.closest("fieldset")?.disabled) || Boolean(active); };
    const observer = new MutationObserver(updateDisabled);
    observer.observe(input, {attributes: true, attributeFilter: ["disabled"]});
    const fieldset = input.closest("fieldset");
    if (fieldset) observer.observe(fieldset, {attributes: true, attributeFilter: ["disabled"]});
    async function choose(mode) {
      if (active || button.disabled) return;
      active = {};
      document.querySelectorAll(".screen-color-button").forEach(item => { item.disabled = true; });
      status.textContent = mode === "palette" ? "Выберите цвет в палитре. Esc — отмена." : "Выберите точку на экране. Esc — отмена.";
      let session = "";
      try {
        let result = parse(await window.astra.callBackend("music_screen_color", {action: "start", mode, initial: input.value}));
        while (result?.pending) {
          session = result.session; active.session = session;
          await new Promise(resolve => setTimeout(resolve, 250));
          result = parse(await window.astra.callBackend("music_screen_color", {action: "poll", session}));
        }
        if (/^#[0-9a-f]{6}$/i.test(result?.color || "") && !input.disabled && !fieldset?.disabled) {
          input.value = result.color;
          input.dispatchEvent(new Event("input", {bubbles: true}));
          input.dispatchEvent(new Event("change", {bubbles: true}));
          status.textContent = "Цвет выбран.";
        } else status.textContent = "Выбор отменён.";
      } catch (error) {
        status.textContent = error.message || "Не удалось выбрать цвет.";
        if (session) void window.astra.callBackend("music_screen_color", {action: "cancel", session}).catch(() => {});
      } finally {
        active = null;
        document.dispatchEvent(new Event("music-color-picker-finished"));
      }
    }
    button.addEventListener("click", () => choose("screen"));
    input.addEventListener("click", event => {
      if (!nativePalette) return;
      event.preventDefault();
      void choose("palette");
    });
    input.addEventListener("keydown", event => {
      if (!nativePalette) return;
      if (!["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      void choose("palette");
    });
    document.addEventListener("music-color-picker-finished", updateDisabled);
    updateDisabled();
    window.addEventListener("beforeunload", () => observer.disconnect());
  }
  window.addEventListener("beforeunload", () => {
    if (active?.session) void window.astra.callBackend("music_screen_color", {action: "cancel", session: active.session}).catch(() => {});
  });
})();
