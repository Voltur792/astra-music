(() => {
  const rows = document.getElementById("hotkeysRows");
  if (!rows) return;
  const status = document.getElementById("hotkeysStatus");
  const cancel = document.getElementById("hotkeysCancel");
  const retry = document.getElementById("hotkeysRetry");
  const actions = [["play_pause", "Воспроизведение / пауза"], ["previous", "Предыдущий трек"], ["next", "Следующий трек"], ["stop", "Стоп"]];
  let state = null, recording = "", session = "", busy = false, disposed = false, cancelRequested = false;
  const controls = new Map();
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
  function render() {
    rows.setAttribute("aria-busy", String(!state || busy));
    for (const [action, row] of controls) {
      row.value.textContent = recording === action ? "Нажмите клавишу…" : state?.labels?.[action] || "Медиаклавиша";
      row.value.dataset.recording = String(recording === action);
      row.change.disabled = !state?.supported || busy;
      row.reset.disabled = !state?.supported || busy || !state?.bindings?.[action];
    }
    cancel.hidden = !recording;
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
      busy = false; recording = ""; session = ""; render();
      controls.get(action)?.change.focus({preventScroll: true});
    }
  }
  async function reset(action) {
    if (busy) return;
    busy = true; render();
    try { state = await call({operation: "reset", action}); report("Возвращена стандартная медиаклавиша."); }
    catch (error) { report(error.message || "Не удалось сбросить сочетание.", true); }
    finally { busy = false; render(); }
  }
  for (const [action, name] of actions) {
    const row = document.createElement("div"); row.className = "hotkey-row";
    const title = document.createElement("span"); title.className = "hotkey-name"; title.textContent = name;
    const value = document.createElement("kbd"); value.className = "hotkey-value"; value.id = "hotkey-" + action;
    const button = document.createElement("button"); button.type = "button"; button.textContent = "Изменить";
    button.setAttribute("aria-label", "Изменить: " + name); button.setAttribute("aria-describedby", value.id);
    const clear = document.createElement("button"); clear.type = "button"; clear.textContent = "Сброс"; clear.setAttribute("aria-label", "Сбросить: " + name);
    row.append(title, value, button, clear); rows.append(row);
    controls.set(action, {value, change: button, reset: clear});
    button.addEventListener("click", () => change(action)); clear.addEventListener("click", () => reset(action));
  }
  function cancelRecording() {
    cancelRequested = true;
    if (session) void call({operation: "cancel", session}).catch(error => report(error.message, true));
  }
  cancel.addEventListener("click", cancelRecording);
  window.addEventListener("music-tab-change", event => {
    if (event.detail !== "hotkeys" && recording) cancelRecording();
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
  window.addEventListener("beforeunload", () => {
    disposed = true; cancelRequested = true; clearInterval(timer);
    if (session) void call({operation: "cancel", session}).catch(() => {});
  });
})();
