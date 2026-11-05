(() => {
  const $ = id => document.getElementById(id);
  const buttons = [...document.querySelectorAll("[data-widget-theme]")];
  const inputs = [$("widgetThemeBackground"), $("widgetThemeAccent")];
  let settings = {mode: "provider", background: "#18181b", accent: "#ffcc00"}, pending = false, loaded = false;
  const call = async (method, params = {}) => {
    let result = await window.astra.callBackend(method, params);
    for (let n = 0; n < 3; n++) {
      if (typeof result === "string") result = JSON.parse(result);
      else if (result?.result_json) result = result.result_json;
      else break;
    }
    if (result?.error) throw new Error(result.error);
    return result;
  };
  function render() {
    for (const button of buttons) {
      button.disabled = pending || !loaded;
      button.setAttribute("aria-pressed", String(settings.mode === button.dataset.widgetTheme));
    }
    for (const input of inputs) input.disabled = pending || !loaded || settings.mode !== "custom";
    $("widgetThemeBackground").value = settings.background;
    $("widgetThemeAccent").value = settings.accent;
    window.MusicTheme.apply("yandex", settings, $("widgetThemePreview"));
  }
  async function save(update) {
    if (pending || !loaded) return;
    const previous = settings;
    settings = {...settings, ...update}; pending = true; render();
    $("widgetThemeStatus").textContent = "Сохраняю…";
    try {
      settings = await call("music_widget_theme_set", {settings});
      $("widgetThemeStatus").textContent = "Сохранено. Цвета применяются к виджетам и очереди.";
    } catch (error) {
      settings = previous;
      $("widgetThemeStatus").textContent = error.message || "Не удалось сохранить цвета. Повторите попытку.";
    } finally { pending = false; render(); }
  }
  for (const button of buttons) button.addEventListener("click", () => save({mode: button.dataset.widgetTheme}));
  for (const input of inputs) {
    input.addEventListener("input", () => window.MusicTheme.apply("yandex", {...settings,
      background: inputs[0].value, accent: inputs[1].value}, $("widgetThemePreview")));
    input.addEventListener("change", () => save({background: inputs[0].value, accent: inputs[1].value}));
  }
  async function load() {
    if (loaded || pending || !window.astra?.callBackend) return;
    pending = true;
    try {
      settings = await call("music_widget_theme_get"); loaded = true;
      $("widgetThemeStatus").textContent = "Цвет текста подбирается автоматически для читаемости.";
      clearInterval(timer);
    } catch (_) { $("widgetThemeStatus").textContent = "Настройки недоступны. Повторяю подключение…"; }
    finally { pending = false; render(); }
  }
  const timer = setInterval(load, 2000); render(); void load();
  window.addEventListener("beforeunload", () => clearInterval(timer));
})();
