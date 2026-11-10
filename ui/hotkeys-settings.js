(() => {
  const rows = document.getElementById("hotkeysRows");
  if (!rows) return;
  const loading = document.getElementById("hotkeysLoading");
  const status = document.getElementById("hotkeysStatus");
  const cancel = document.getElementById("hotkeysCancel");
  const retry = document.getElementById("hotkeysRetry");
  const actions = [["play_pause", "Воспроизведение / пауза"], ["previous", "Предыдущий трек"], ["next", "Следующий трек"], ["stop", "Стоп"]];
  let state = null, recording = "", session = "", busy = false, disposed = false, cancelRequested = false;
  const controls = new Map();
  let kit, kitRoot, rowsRequested = false, focusId = "";
  const panel = rows.closest("[role=tabpanel]");
  async function call(params) {
    let result = await window.astra.callBackend("music_hotkeys", params);
    for (let n = 0; n < 4; n++) {
      if (typeof result === "string") result = JSON.parse(result);
      else if (result?.result_json) result = result.result_json;
      else break;
    }
    if (result?.error) throw new Error(result.error);
    return result;
  }
  function report(text, error = false) {
    status.textContent = text;
    status.classList.toggle("bad", error);
  }
  function rowState(action) {
    const capturing = recording === action;
    const disabled = !state?.supported || busy;
    return {capturing, disabled, canReset: !disabled && !!state?.bindings?.[action],
      text: capturing ? "Нажмите клавишу…" : state?.labels?.[action] || "Медиаклавиша"};
  }
  function restoreFocus() {
    if (focusId && !panel?.hidden) document.getElementById(focusId)?.focus({preventScroll: true});
    focusId = "";
  }
  function rowsReady() {
    rows.hidden = false;
    loading.hidden = true;
    rows.setAttribute("aria-busy", String(!state || busy));
    restoreFocus();
  }
  function renderKit() {
    const {h, SettingRow, Button} = kit;
    const items = actions.map(([action, name]) => {
      const {capturing, disabled, canReset, text} = rowState(action);
      const valueId = "hotkey-" + action;
      const control = h("div", {className: "hotkey-kit-actions"},
        h("kbd", {id: valueId, className: "hotkey-value", "data-recording": String(capturing)}, text),
        h(Button, {id: "hotkey-change-" + action, className: "hotkey-kit-button", disabled,
          "aria-label": "Изменить: " + name, "aria-describedby": valueId, onClick: () => change(action)}, "Изменить"),
        h(Button, {id: "hotkey-reset-" + action, className: "hotkey-kit-button", disabled: !canReset,
          "aria-label": "Сбросить: " + name, onClick: () => reset(action)}, "Сброс"));
      return h(SettingRow, {key: action, label: name, control});
    });
    kitRoot.render(h("div", {className: "hotkey-kit-rows", ref: node => { if (node) rowsReady(); }}, ...items));
  }
  function render() {
    rows.setAttribute("aria-busy", String(!loading.hidden || !state || busy));
    cancel.hidden = !recording;
    if (kitRoot) { renderKit(); return; }
    for (const [action, row] of controls) {
      const {capturing, disabled, canReset, text} = rowState(action);
      row.value.textContent = text;
      row.value.dataset.recording = String(capturing);
      row.change.disabled = disabled;
      row.reset.disabled = !canReset;
    }
    restoreFocus();
  }
  async function loadRows() {
    if (rowsRequested) return;
    rowsRequested = true;
    let timeout;
    try {
      if (window.MusicUi) kit = await window.MusicUi.load();
      else if (window.astra?.loadUi) {
        kit = await Promise.race([
          window.astra.loadUi({apiVersion: 1}),
          new Promise(resolve => { timeout = setTimeout(resolve, 12000); }),
        ]);
      }
      if (!disposed && kit?.SettingRow && kit.Button && typeof kit.h === "function" && typeof kit.mount === "function") {
        kitRoot = kit.mount(rows, null);
        render();
        return;
      }
    } catch (_) {
      kitRoot?.unmount();
      kitRoot = undefined;
    } finally {
      clearTimeout(timeout);
    }
    if (disposed) return;
    mountFallback();
    render();
    rowsReady();
  }
  async function change(action) {
    if (busy) return;
    busy = true; recording = action; cancelRequested = false; render();
    report("Нажмите клавишу или сочетание в течение 20 секунд. Esc — отмена.");
    try {
      let result = await call({operation: "start", action});
      session = result.session;
      if (cancelRequested || disposed) result = await call({operation: "cancel", session});
      while (result.pending && !disposed) {
        await new Promise(resolve => setTimeout(resolve, 150));
        result = await call({operation: "poll", session});
      }
      if (result.bindings) { state = result; report("Сочетание сохранено."); }
      else report("Запись отменена. Прежняя клавиша сохранена.");
    } catch (error) {
      report(error.message || "Не удалось записать клавишу. Повторите попытку.", true);
      if (session) void call({operation: "cancel", session}).catch(() => {});
    } finally {
      busy = false; recording = ""; session = "";
      focusId = "hotkey-change-" + action;
      render();
    }
  }
  async function reset(action) {
    if (busy) return;
    busy = true; render();
    try { state = await call({operation: "reset", action}); report("Возвращена стандартная медиаклавиша."); }
    catch (error) { report(error.message || "Не удалось сбросить сочетание.", true); }
    finally { busy = false; render(); }
  }
  function mountFallback() {
    for (const [action, name] of actions) {
      const row = document.createElement("div"); row.className = "hotkey-row";
      const title = document.createElement("span"); title.className = "hotkey-name"; title.textContent = name;
      const value = document.createElement("kbd"); value.className = "hotkey-value"; value.id = "hotkey-" + action;
      const button = document.createElement("button"); button.type = "button"; button.textContent = "Изменить";
      button.id = "hotkey-change-" + action;
      button.setAttribute("aria-label", "Изменить: " + name); button.setAttribute("aria-describedby", value.id);
      const clear = document.createElement("button"); clear.type = "button"; clear.textContent = "Сброс"; clear.setAttribute("aria-label", "Сбросить: " + name);
      clear.id = "hotkey-reset-" + action;
      row.append(title, value, button, clear); rows.append(row);
      controls.set(action, {value, change: button, reset: clear});
      button.addEventListener("click", () => change(action)); clear.addEventListener("click", () => reset(action));
    }
  }
  function cancelRecording() {
    cancelRequested = true;
    if (session) void call({operation: "cancel", session}).catch(error => report(error.message, true));
  }
  cancel.addEventListener("click", cancelRecording);
  window.addEventListener("music-tab-change", event => {
    if (event.detail === "hotkeys") void loadRows();
    else if (recording) cancelRecording();
  });
  async function load() {
    if (!window.astra?.callBackend) return;
    try {
      state = await call({operation: "get"}); retry.hidden = true; clearInterval(timer);
      report(state.supported ? "Стандартные медиаклавиши работают без настройки." : "Запись глобальных сочетаний доступна на Windows.");
    } catch (_) { report("Не удалось загрузить клавиши. Повторите подключение.", true); retry.hidden = false; }
    render();
  }
  retry.addEventListener("click", load);
  const timer = setInterval(load, 2000);
  render(); void load();
  if (!panel?.hidden) void loadRows();
  window.addEventListener("beforeunload", () => {
    disposed = true; cancelRequested = true; clearInterval(timer);
    kitRoot?.unmount();
    if (session) void call({operation: "cancel", session}).catch(() => {});
  });
})();
