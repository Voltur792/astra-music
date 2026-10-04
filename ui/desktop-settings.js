"use strict";
(() => {
  const enabled = document.getElementById("desktopWidgetEnabled");
  const onTop = document.getElementById("desktopWidgetOnTop");
  const reset = document.getElementById("desktopWidgetReset");
  const transparency = document.getElementById("desktopWidgetTransparency");
  const transparencyValue = document.getElementById("desktopWidgetTransparencyValue");
  const status = document.getElementById("desktopWidgetStatus");
  let pending = false, snapshot = null, editingTransparency = false;
  const parse = value => typeof value === "string" ? JSON.parse(value) : value || {};
  const call = async (method, params = {}) => parse(await window.astra.callBackend(method, params));
  function render(result) {
    snapshot = result;
    const settings = result.settings || {};
    enabled.checked = Boolean(settings.enabled);
    onTop.checked = Boolean(settings.on_top);
    if (!editingTransparency) transparency.value = settings.transparency ?? 0;
    transparencyValue.value = transparency.value + "%";
    enabled.disabled = pending || !result.available;
    onTop.disabled = pending || !result.available || !settings.enabled;
    reset.disabled = pending || !result.available;
    transparency.disabled = pending || !result.available;
    status.textContent = result.message || (!result.available ? "Доступно в Windows." : settings.enabled ? "Виджет включён. Перетащите его за название трека." : "Выключен. Позиция и прозрачность сохраняются после перезапуска Astra.");
  }
  async function load() {
    if (pending || editingTransparency || !window.astra?.callBackend) return;
    pending = true;
    try { const result = await call("music_desktop_get"); pending = false; render(result); }
    catch (_) { status.textContent = "Настройки недоступны. Повторяю подключение…"; }
    finally { pending = false; }
  }
  async function save(resetPosition = false) {
    if (pending) return;
    pending = true;
    enabled.disabled = onTop.disabled = reset.disabled = transparency.disabled = true;
    status.textContent = "Сохраняю…";
    try {
      const result = await call("music_desktop_set", {settings: {enabled: enabled.checked, on_top: onTop.checked, transparency: Number(transparency.value)}, reset_position: resetPosition});
      if (result.error) throw new Error(result.error);
      pending = false; render(result);
    } catch (error) {
      pending = false;
      if (snapshot) render(snapshot);
      status.textContent = error.message || "Не удалось сохранить настройки.";
    } finally { pending = false; }
  }
  enabled.addEventListener("change", () => save());
  onTop.addEventListener("change", () => save());
  reset.addEventListener("click", () => save(true));
  transparency.addEventListener("pointerdown", () => { editingTransparency = true; });
  transparency.addEventListener("input", () => { editingTransparency = true; transparencyValue.value = transparency.value + "%"; });
  transparency.addEventListener("change", () => { editingTransparency = false; save(); });
  transparency.addEventListener("pointerup", () => { editingTransparency = false; });
  transparency.addEventListener("pointercancel", () => { editingTransparency = false; });
  transparency.addEventListener("blur", () => { editingTransparency = false; });
  const timer = setInterval(load, 2000); void load();
  window.addEventListener("beforeunload", () => clearInterval(timer));
})();
