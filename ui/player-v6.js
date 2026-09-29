const audio = document.querySelector("#audio");
const title = document.querySelector("#title");
const artist = document.querySelector("#artist");
const playButton = document.querySelector("#play");
const stopButton = document.querySelector("#stop");
let current = null;
let lastRevision = -1;
let busy = false;
let restoreAttemptedFor = "";
let lastBackendRestoreAt = 0;

const UI_STATE_KEY = "music-controller-ui-v1";

function bridge() {
  return window.astra || window.__astraBridge || null;
}

function call(method, params = {}) {
  const api = bridge();
  const fn = api && (api.callBackend || api.call);
  return fn ? fn.call(api, method, params) : Promise.reject(new Error("bridge not ready"));
}

function parse(value) {
  if (typeof value === "string") {
    try { return JSON.parse(value); } catch (_) { return {}; }
  }
  return value || {};
}

async function report(status, error = "") {
  if (!current) return;
  try {
    await call("music_playback_report", {
      revision: current.revision, status, error,
    });
  } catch (err) {
    console.warn("Astra Music: playback status could not be reported", err);
  }
}

async function sync() {
  if (busy) return;
  if (!bridge()) {
    title.textContent = "Подключаю плеер Astra…";
    return;
  }
  busy = true;
  try {
    let state = parse(await call("music_playback_state"));
    if (!state.url && Date.now() - lastBackendRestoreAt > 5000) {
      lastBackendRestoreAt = Date.now();
      const restored = parse(await call("music_playback_restore"));
      if (restored.ok) state = parse(await call("music_playback_state"));
    }
    const dataApi = bridge();
    if (!state.url && typeof dataApi?.getData === "function") {
      const saved = parse(await dataApi.getData(UI_STATE_KEY));
      const track = saved.selectedTrack;
      const trackId = String(track?.track_id || "");
      if (track?.service === "yandex" && /^\d+$/.test(trackId) && restoreAttemptedFor !== trackId) {
        restoreAttemptedFor = trackId;
        title.textContent = "Восстанавливаю выбранный трек…";
        artist.textContent = track.title || "Яндекс Музыка";
        const prepared = parse(await call("music_yandex_start", {
          track_id: trackId,
          title: track.title || "",
          artist: track.artist || "",
        }));
        if (prepared.error) throw new Error(prepared.error);
        state = parse(await call("music_playback_state"));
      }
    }
    // Yandex direct links are short lived; refresh before the one-minute URL expires.
    if (state.url && audio.paused && state.track_id && state.expires_at
        && (Number(state.expires_at) * 1000 - Date.now()) < 20000) {
      const prepared = parse(await call("music_yandex_start", {
        track_id: String(state.track_id),
        title: state.title || "",
        artist: state.artist || "",
      }));
      if (prepared.error) throw new Error(prepared.error);
      state = parse(await call("music_playback_state"));
    }
    if (state.revision === lastRevision) return;
    lastRevision = state.revision;
    current = state.url ? state : null;
    if (!current) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      title.textContent = "Трек не выбран";
      artist.textContent = "Яндекс Музыка";
      playButton.disabled = true;
      stopButton.disabled = true;
      return;
    }
    audio.pause();
    audio.src = current.url;
    audio.load();
    title.textContent = current.title || "Без названия";
    artist.textContent = current.artist || "Яндекс Музыка";
    playButton.disabled = false;
    stopButton.disabled = false;
    playButton.textContent = "▶";
    playButton.setAttribute("aria-label", "Воспроизвести");
  } catch (error) {
    console.warn("Astra Music: home player sync failed", error);
    title.textContent = "Не удалось подготовить плеер";
    artist.textContent = error?.message || error?.name || "Проверьте подключение плагина";
    playButton.disabled = true;
    stopButton.disabled = true;
  } finally {
    busy = false;
  }
}

playButton.addEventListener("click", async () => {
  if (!current) return;
  if (!audio.paused) {
    audio.pause();
    void report("paused");
    return;
  }
  // Start synchronously from this click so Chromium grants the user gesture
  // to the persistent status-bar iframe's audio element.
  const attempt = audio.play();
  void report("attempting");
  playButton.disabled = true;
  try {
    await attempt;
    playButton.textContent = "Ⅱ";
    playButton.setAttribute("aria-label", "Пауза");
    await report("playing");
  } catch (error) {
    await report(error?.name === "NotAllowedError" ? "blocked" : "failed", error?.name || "PlaybackError");
    title.textContent = error?.name === "NotAllowedError"
      ? "Astra заблокировала запуск — нажмите ▶ ещё раз"
      : "Ошибка запуска: " + (error?.name || "PlaybackError");
  } finally {
    playButton.disabled = false;
  }
});

audio.addEventListener("pause", () => {
  if (current && !audio.ended) {
    playButton.textContent = "▶";
    playButton.setAttribute("aria-label", "Воспроизвести");
  }
});

audio.addEventListener("playing", () => {
  playButton.textContent = "Ⅱ";
  playButton.setAttribute("aria-label", "Пауза");
});

stopButton.addEventListener("click", async () => {
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  current = null;
  lastRevision = -1;
  try { await call("music_playback_stop"); } catch (_) {}
  await sync();
});

audio.addEventListener("error", () => {
  void report("failed", audio.error?.code ? "MediaError" + audio.error.code : "MediaError");
  title.textContent = "Не удалось загрузить поток";
});

setInterval(sync, 600);
sync();
