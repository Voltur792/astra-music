const audio = document.querySelector("#audio");
const title = document.querySelector("#title");
const artist = document.querySelector("#artist");
const source = document.querySelector("#source");
const cover = document.querySelector("#cover");
const coverFallback = document.querySelector("#coverFallback");
const notice = document.querySelector("#notice");
const playButton = document.querySelector("#play");
const stopButton = document.querySelector("#stop");
const previousButton = document.querySelector("#previous");
const nextButton = document.querySelector("#next");
const likeButton = document.querySelector("#like");
const seekSlider = document.querySelector("#seek");
const volumeSlider = document.querySelector("#volume");
const volumeValue = document.querySelector("#volumeValue");
const elapsedLabel = document.querySelector("#elapsed");
const durationLabel = document.querySelector("#duration");
let current = null;
let lastRevision = -1;
let lastCommandRevision = -1;
let busy = false;
let restoreAttemptedFor = "";
let lastBackendRestoreAt = 0;
let lastProgressReportAt = 0;
let pendingSeek = null;
let volumeDragging = false;
let likePending = false;
let queueControlPending = false;

function bridge() {
  return window.astra || window.__astraBridge || null;
}

function call(method, params = {}) {
  const api = bridge();
  const fn = api && (api.callBackend || api.call);
  return fn ? fn.call(api, method, params) : Promise.reject(new Error("bridge not ready"));
}

function parse(value) {
  for (let depth = 0; depth < 3; depth += 1) {
    if (typeof value === "string") {
      try { value = JSON.parse(value); } catch (_) { return {}; }
      continue;
    }
    if (value && typeof value === "object" && typeof value.result_json === "string") {
      value = value.result_json;
      continue;
    }
    return value || {};
  }
  return value || {};
}

function formatTime(value) {
  const seconds = Number.isFinite(Number(value)) ? Math.max(0, Math.floor(Number(value))) : 0;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function setNotice(text = "", bad = false) {
  notice.textContent = text;
  notice.classList.toggle("bad", bad);
}

function updateCover(url, alt = "Обложка трека") {
  cover.alt = alt;
  if (!url) {
    cover.removeAttribute("src");
    cover.hidden = true;
    coverFallback.hidden = false;
    return;
  }
  if (cover.src !== url) {
    cover.hidden = false;
    coverFallback.hidden = true;
    cover.src = url;
  }
}

cover.addEventListener("error", () => {
  cover.hidden = true;
  coverFallback.hidden = false;
});

function updateProgress() {
  const duration = Number.isFinite(audio.duration) ? audio.duration : Number(current?.duration_seconds || 0);
  const position = Number.isFinite(audio.currentTime) ? audio.currentTime : Number(current?.position_seconds || 0);
  if (duration > 0) {
    seekSlider.max = String(duration);
    if (document.activeElement !== seekSlider) seekSlider.value = String(Math.min(position, duration));
  } else {
    seekSlider.max = "0";
    seekSlider.value = "0";
  }
  seekSlider.disabled = !current || duration <= 0;
  elapsedLabel.textContent = formatTime(position);
  durationLabel.textContent = formatTime(duration);
}

function updateTrackControls(state) {
  const count = Number(state.queue_count || 0);
  const index = Number(state.queue_index ?? -1);
  const radio = state.source === "radio";
  playButton.disabled = !current;
  stopButton.disabled = !current;
  previousButton.disabled = !current || index <= 0;
  nextButton.disabled = !current || (!radio && (count < 2 || index >= count - 1));
  nextButton.disabled = nextButton.disabled || queueControlPending;
  previousButton.disabled = previousButton.disabled || queueControlPending;
  likeButton.disabled = !current || Boolean(state.liked) || likePending;
  likeButton.textContent = state.liked ? "♥" : "♡";
  likeButton.classList.toggle("liked", Boolean(state.liked));
  likeButton.setAttribute("aria-label", state.liked ? "Трек в понравившихся" : "Добавить в понравившиеся");
  likeButton.title = state.liked ? "Трек в понравившихся" : "Добавить в понравившиеся";
  if (!volumeDragging) volumeSlider.value = String(Math.round(Math.max(0, Math.min(1, Number(state.volume ?? 0.8))) * 10));
  volumeValue.value = volumeSlider.value;
  source.textContent = state.source === "radio"
    ? "Яндекс Музыка · " + (state.source_title || "Моя волна")
    : state.source === "playlist"
      ? `Плейлист · ${state.source_title || "Яндекс Музыка"} · ${index + 1}/${count}`
      : "Яндекс Музыка";
}

async function report(status, error = "") {
  if (!current) return;
  try {
    await call("music_playback_report", {
      revision: current.revision,
      status,
      error,
      position_seconds: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
      duration_seconds: Number.isFinite(audio.duration) ? audio.duration : 0,
      volume: audio.volume,
    });
  } catch (err) {
    console.warn("Astra Music: playback status could not be reported", err);
  }
}

async function startPlayback() {
  if (!current) return;
  const attempt = audio.play();
  const attemptReport = report("attempting");
  playButton.disabled = true;
  setNotice("Запускаю…");
  try {
    await attempt;
    await attemptReport;
    playButton.textContent = "Ⅱ";
    playButton.setAttribute("aria-label", "Пауза");
    setNotice("");
    await report("playing");
  } catch (error) {
    await attemptReport;
    const blocked = error?.name === "NotAllowedError";
    await report(blocked ? "blocked" : "failed", error?.name || "PlaybackError");
    setNotice(blocked ? "Автозапуск запрещён — нажмите ▶" : "Не удалось запустить аудио", true);
    playButton.textContent = "▶";
    playButton.setAttribute("aria-label", "Воспроизвести");
  } finally {
    playButton.disabled = !current;
  }
}

function applySeek(target) {
  if (!current || !Number.isFinite(audio.duration) || audio.duration <= 0) {
    pendingSeek = target;
    return;
  }
  audio.currentTime = Math.max(0, Math.min(Number(target) || 0, audio.duration));
  pendingSeek = null;
  updateProgress();
  void report("progress");
}

async function control(action, value = 0, extras = {}) {
  const queueAction = ["next", "previous", "ended"].includes(action);
  if (queueAction && queueControlPending) return null;
  if (queueAction) {
    queueControlPending = true;
    updateTrackControls(current || {});
  }
  if (action === "like") {
    likePending = true;
    updateTrackControls(current || {});
  }
  try {
    const result = parse(await call("music_playback_control", { action, value, ...extras }));
    if (result.error) throw new Error(result.error);
    if (result.message) setNotice(result.message);
    if (action === "like" && result.success && current) {
      current = { ...current, liked: Boolean(result.liked) };
      updateTrackControls(current);
    }
    await sync();
    return result;
  } catch (error) {
    setNotice(error?.message || "Не удалось выполнить команду", true);
    return null;
  } finally {
    if (queueAction) queueControlPending = false;
    if (action === "like") likePending = false;
    if (queueAction || action === "like") updateTrackControls(current || {});
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
      if (restored.ok && (restored.stream_url || restored.url)) state = restored;
      else if (restored.ok) state = parse(await call("music_playback_state"));
    }
    const playableUrl = state.stream_url || state.url;
    if (!playableUrl) {
      if (current) {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
        current = null;
      }
      title.textContent = "Трек не выбран";
      artist.textContent = "Яндекс Музыка";
      source.textContent = "";
      updateCover("");
      playButton.disabled = true;
      stopButton.disabled = true;
      previousButton.disabled = true;
      nextButton.disabled = true;
      likeButton.disabled = true;
      likeButton.textContent = "♡";
      likeButton.classList.remove("liked");
      seekSlider.disabled = true;
      setNotice("");
      return;
    }

    const changedTrack = Number(state.revision) !== lastRevision;
    if (changedTrack) {
      current = { ...state, url: playableUrl };
      lastRevision = Number(state.revision);
      lastCommandRevision = Number(state.command_revision ?? -1);
      audio.pause();
      audio.src = current.url;
      audio.volume = Math.max(0, Math.min(1, Number(state.volume ?? 0.8)));
      audio.load();
      pendingSeek = Number(state.position_seconds || 0) > 0 ? Number(state.position_seconds) : null;
      title.textContent = current.title || "Без названия";
      artist.textContent = current.artist || "Яндекс Музыка";
      updateCover(current.cover_url || "", `Обложка: ${current.title || "трек"}`);
      setNotice("");
      updateTrackControls(state);
      if (state.command === "play") void startPlayback();
    } else {
      current = { ...state, url: playableUrl };
      updateTrackControls(state);
      const commandRevision = Number(state.command_revision ?? -1);
      if (commandRevision !== lastCommandRevision) {
        lastCommandRevision = commandRevision;
        if (state.command === "play") void startPlayback();
        else if (state.command === "pause") {
          audio.pause();
          void report("paused");
        } else if (state.command === "seek") applySeek(state.seek_to_seconds);
        else if (state.command === "volume") audio.volume = Math.max(0, Math.min(1, Number(state.volume ?? 0.8)));
        else if (state.command === "stop") {
          audio.pause();
          audio.removeAttribute("src");
          audio.load();
          current = null;
        }
      }
    }
    if (state.status === "blocked" && audio.paused) setNotice("Автозапуск запрещён — нажмите ▶", true);
    updateProgress();
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

playButton.addEventListener("click", () => {
  if (!current) return;
  if (audio.paused) void startPlayback();
  else {
    audio.pause();
    void report("paused");
  }
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

previousButton.addEventListener("click", () => void control("previous"));
nextButton.addEventListener("click", () => void control("next"));
likeButton.addEventListener("click", () => void control("like"));

seekSlider.addEventListener("input", () => {
  elapsedLabel.textContent = formatTime(seekSlider.value);
});
seekSlider.addEventListener("change", () => {
  const seconds = Number(seekSlider.value);
  if (Number.isFinite(audio.duration) && audio.duration > 0) audio.currentTime = seconds;
  void control("seek", seconds);
});

volumeSlider.addEventListener("pointerdown", () => { volumeDragging = true; });
volumeSlider.addEventListener("pointerup", () => { volumeDragging = false; });
volumeSlider.addEventListener("input", () => {
  volumeValue.value = volumeSlider.value;
  audio.volume = Math.max(0, Math.min(10, Number(volumeSlider.value))) / 10;
});
volumeSlider.addEventListener("change", () => {
  volumeDragging = false;
  void control("volume", Number(volumeSlider.value));
});

audio.addEventListener("loadedmetadata", () => {
  if (pendingSeek !== null) applySeek(pendingSeek);
  updateProgress();
});
audio.addEventListener("timeupdate", updateProgress);
audio.addEventListener("playing", () => {
  playButton.textContent = "Ⅱ";
  playButton.setAttribute("aria-label", "Пауза");
  setNotice("");
  void report("playing");
});
audio.addEventListener("pause", () => {
  if (current && !audio.ended) {
    playButton.textContent = "▶";
    playButton.setAttribute("aria-label", "Воспроизвести");
  }
});
audio.addEventListener("ended", () => {
  playButton.textContent = "▶";
  playButton.setAttribute("aria-label", "Воспроизвести");
  if (current && current.source !== "track") {
    void control("ended", audio.currentTime, { duration_seconds: audio.duration });
  }
});
audio.addEventListener("error", () => {
  void report("failed", audio.error?.code ? "MediaError" + audio.error.code : "MediaError");
  setNotice("Не удалось загрузить поток", true);
});

setInterval(() => {
  updateProgress();
  if (current && !audio.paused && Date.now() - lastProgressReportAt > 1500) {
    lastProgressReportAt = Date.now();
    void report("progress");
  }
}, 250);
setInterval(sync, 600);
sync();
