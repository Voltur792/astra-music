// Queue requests run only while the list is visible.
(() => {
  const $ = id => document.getElementById(id);
  const popup = document.body.classList.contains("queue-window");
  const panel = $("queuePanel"), toggle = $("queueToggle"), list = $("queueList");
  let snapshot, loading = false, selecting = false, generation = 0;
  const call = async (method, params = {}) => {
    const api = window.astra || window.__astraBridge;
    let result = await (api.callBackend || api.call).call(api, method, params);
    for (let n = 0; n < 3; n++) {
      if (typeof result === "string") result = JSON.parse(result);
      else if (result?.result_json) result = result.result_json;
      else break;
    }
    if (result?.error || result?.success === false) throw new Error(result.error || result.message);
    return result;
  };
  if (!popup) {
    const openInAstra = () => {
      if (parent === window) return Promise.resolve(false);
      return new Promise(resolve => {
        const id = crypto.randomUUID();
        const reply = event => {
          if (event.source !== parent || event.data?.type !== "astra-music:queue-ack" || event.data.id !== id) return;
          clearTimeout(timer); window.removeEventListener("message", reply); resolve(true);
        };
        const timer = setTimeout(() => { window.removeEventListener("message", reply); resolve(false); }, 700);
        window.addEventListener("message", reply);
        parent.postMessage({type: "astra-music:queue-toggle", id}, "*");
      });
    };
    let opening = false;
    toggle.addEventListener("click", async () => {
      if (opening) return;
      opening = true; toggle.disabled = true;
      try {
        if (await openInAstra()) return;
        const rect = document.querySelector(".player").getBoundingClientRect();
        const anchor = window.pywebview?.api?.queue_anchor
          ? await window.pywebview.api.queue_anchor()
          : {x: window.screenX + rect.left, y: window.screenY + Math.max(0, window.outerHeight - window.innerHeight) + rect.top, height: rect.height};
        await call("music_queue_window", anchor);
      } catch (error) {
        const notice = $("notice");
        notice.textContent = error.message || "Не удалось открыть очередь. Повторите попытку.";
        notice.classList.add("bad");
      } finally { opening = false; toggle.disabled = false; }
    });
    return;
  }
  function close() {
    generation++; loading = false;
    if (window.closeMusicQueue) window.closeMusicQueue();
    else if (window.pywebview?.api?.close_queue) void window.pywebview.api.close_queue();
    else window.close();
  }
  function pageButton(label, offset) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "queue-page"; button.textContent = label;
    button.addEventListener("click", () => load(offset)); list.append(button);
  }
  async function load(offset, focus = false) {
    if (panel.hidden || loading || selecting) return;
    const ticket = ++generation;
    loading = true;
    if (!snapshot) $("queueMessage").textContent = "Загружаю очередь…";
    try {
      const data = await call("music_queue", offset === undefined ? {} : {offset});
      if (ticket !== generation || panel.hidden) return;
      window.MusicTheme?.apply(data.service, data.widget_theme);
      const same = snapshot && JSON.stringify(snapshot) === JSON.stringify(data);
      snapshot = data;
      if (!same) {
        list.replaceChildren();
        if (data.offset > 0) pageButton("Предыдущие треки", Math.max(0, data.offset - 100));
        for (const item of data.items) {
          const button = document.createElement("button");
          button.type = "button"; button.className = "queue-track";
          button.dataset.index = item.index;
          if (item.index === data.current_index) button.setAttribute("aria-current", "true");
          const number = document.createElement("span"); number.className = "queue-number";
          number.textContent = item.index === data.current_index ? "▶" : String(item.index + 1);
          const text = document.createElement("span"); text.className = "queue-track-text";
          const title = document.createElement("strong"); title.textContent = item.title || "Без названия";
          const artist = document.createElement("small"); artist.textContent = item.artist || "Неизвестный исполнитель";
          text.append(title, artist); button.append(number, text);
          button.title = `${title.textContent} — ${artist.textContent}`;
          button.addEventListener("click", () => select(item.index, data.revision));
          list.append(button);
        }
        if (data.offset + data.items.length < data.total) pageButton("Следующие треки", data.offset + data.items.length);
        list.scrollTop = 0;
      }
      $("queueTitle").textContent = data.title;
      $("queueMessage").textContent = data.message || (data.total ? `${data.current_index + 1} из ${data.total} · Нажмите на трек` : "Очередь пуста. Выберите плейлист во вкладке «Музыка».");
      if (focus) (list.querySelector('[aria-current="true"]') || list.querySelector("button") || $("queueClose")).focus();
    } catch (error) {
      if (ticket === generation) $("queueMessage").textContent = error.message || "Не удалось загрузить очередь. Закройте и откройте её снова.";
    } finally { if (ticket === generation) loading = false; }
  }
  async function select(index, revision) {
    if (selecting) return;
    selecting = true;
    for (const button of list.querySelectorAll("button")) button.disabled = true;
    $("queueMessage").textContent = "Переключаю трек…";
    try {
      await call("music_queue_play", {index, revision});
      selecting = false;
      await load(snapshot?.offset);
    } catch (error) { $("queueMessage").textContent = error.message || "Не удалось включить трек. Повторите попытку."; }
    finally {
      selecting = false;
      for (const button of list.querySelectorAll("button")) button.disabled = false;
    }
  }
  panel.hidden = false;
  void load(undefined, true);
  $("queueClose").addEventListener("click", close);
  panel.addEventListener("keydown", event => { if (event.key === "Escape") { event.preventDefault(); close(); } });
  const timer = setInterval(() => { if (!panel.hidden) void load(snapshot?.offset); }, 2000);
  window.addEventListener("beforeunload", () => { generation++; clearInterval(timer); });
})();
