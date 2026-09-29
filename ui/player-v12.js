const audio = document.querySelector("#audio");
const player = document.querySelector(".player");
const title = document.querySelector("#title");
const artist = document.querySelector("#artist");
const source = document.querySelector("#source");
const cover = document.querySelector("#cover");
const coverFallback = document.querySelector("#coverFallback");
const artworkGlow = document.querySelector("#artworkGlow");
const notice = document.querySelector("#notice");
const playButton = document.querySelector("#play");
const playGlyph = playButton.querySelector(".glyph");
const stopButton = document.querySelector("#stop");
const previousButton = document.querySelector("#previous");
const nextButton = document.querySelector("#next");
const likeButton = document.querySelector("#like");
const likeGlyph = likeButton.querySelector(".glyph");
const seekSlider = document.querySelector("#seek");
const volumeSlider = document.querySelector("#volume");
const volumeValue = document.querySelector("#volumeValue");
const elapsedLabel = document.querySelector("#elapsed");
const durationLabel = document.querySelector("#duration");
let current = null;
let hls = null;
let audioSourceReady = Promise.resolve(true);
let lastRevision = -1;
let lastCommandRevision = -1;
let busy = false;
let lastBackendRestoreAt = 0;
let backendRestoreError = "";
let lastProgressReportAt = 0;
let pendingSeek = null;
let volumeDragging = false;
let likePending = false;
let queueControlPending = false;
let audioBands = null;
let audioBandsUpdatedAt = 0;
let audioBandsUnsubscribe = null;
let visualFrameId = 0;
let visualLevel = 0;
let lastVisualStyleAt = 0;
let meterContext = null;
let meterAnalyser = null;
let meterData = null;
let lastMeterAttemptAt = 0;

function setPlayerVisible(visible) {
  // Astra reserves this widget's space even when its contents are hidden.
  // Keep the player visible in every playback state.
  player.classList.remove("is-hidden");
}

function subscribeAudioBands() {
  if (audioBandsUnsubscribe || typeof bridge()?.requestAudioData !== "function") return;
  try {
    const unsubscribe = bridge().requestAudioData((data) => {
      audioBands = data?.bands || data?.data || null;
      audioBandsUpdatedAt = Date.now();
    });
    audioBandsUnsubscribe = typeof unsubscribe === "function" ? unsubscribe : () => {};
  } catch (_) {
    // The animation still works gently when Astra does not provide audio bands.
  }
}

function releaseAudioMeter() {
  if (meterContext) void meterContext.close().catch(() => {});
  meterContext = null;
  meterAnalyser = null;
  meterData = null;
}

function ensureLocalAudioMeter() {
  if (meterAnalyser || Date.now() - lastMeterAttemptAt < 1500) return;
  lastMeterAttemptAt = Date.now();
  const capture = audio.captureStream || audio.mozCaptureStream;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (typeof capture !== "function" || !AudioContextClass) return;
  let context = null;
  try {
    const stream = capture.call(audio);
    if (!stream?.getAudioTracks?.().length) return;
    context = new AudioContextClass();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = .7;
    source.connect(analyser);
    meterContext = context;
    meterAnalyser = analyser;
    meterData = new Uint8Array(analyser.frequencyBinCount);
    void context.resume().catch(() => {});
  } catch (_) {
    if (context) void context.close().catch(() => {});
  }
}

function drawVisualFrame() {
  visualFrameId = 0;
  if (!player.classList.contains("is-playing")) return;
  ensureLocalAudioMeter();
  let target = .24;
  let localAudioLevel = 0;
  if (meterAnalyser && meterContext?.state === "running") {
    meterAnalyser.getByteFrequencyData(meterData);
    let sum = 0;
    const count = Math.min(32, meterData.length);
    for (let index = 0; index < count; index += 1) sum += meterData[index];
    localAudioLevel = count ? sum / count / 255 : 0;
  }
  if (localAudioLevel > .025) {
    target = Math.min(1, localAudioLevel * 2.2);
  } else if (audioBands?.length && Date.now() - audioBandsUpdatedAt < 1200) {
    let sum = 0;
    for (const band of audioBands) sum += Math.max(0, Number(band) || 0);
    const average = sum / audioBands.length;
    target = Math.min(1, average > 1 ? average / 100 : average);
  }
  visualLevel += (target - visualLevel) * .12;
  if (Date.now() - lastVisualStyleAt > 100) {
    lastVisualStyleAt = Date.now();
    const duration = Math.max(2.2, 8.2 - visualLevel * 5.2);
    player.style.setProperty("--music-flow-duration", `${duration.toFixed(2)}s`);
    player.style.setProperty("--music-art-duration", `${(duration * 1.2).toFixed(2)}s`);
    player.style.setProperty("--music-glow-opacity", String(.58 + visualLevel * .30));
    player.style.setProperty("--music-glow-secondary", String(.42 + visualLevel * .26));
    player.style.setProperty("--music-art-opacity", String(.58 + visualLevel * .32));
  }
  visualFrameId = requestAnimationFrame(drawVisualFrame);
}

function setVisualPlaying(playing) {
  player.classList.toggle("is-playing", playing);
  if (playing) {
    player.classList.remove("is-starting");
    subscribeAudioBands();
    if (!visualFrameId) visualFrameId = requestAnimationFrame(drawVisualFrame);
  } else {
    if (visualFrameId) cancelAnimationFrame(visualFrameId);
    visualFrameId = 0;
    visualLevel = 0;
    player.style.removeProperty("--music-glow-opacity");
    player.style.removeProperty("--music-glow-secondary");
    player.style.removeProperty("--music-art-opacity");
  }
}

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
    player.classList.remove("has-artwork");
    artworkGlow.style.backgroundImage = "";
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
  player.classList.remove("has-artwork");
  artworkGlow.style.backgroundImage = "";
  cover.hidden = true;
  coverFallback.hidden = false;
});

cover.addEventListener("load", () => {
  if (!cover.naturalWidth) return;
  artworkGlow.style.backgroundImage = `url(${JSON.stringify(cover.currentSrc || cover.src)})`;
  player.classList.add("has-artwork");
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
  likeButton.disabled = !current || current.service === "vk" || Boolean(state.liked) || likePending;
  likeGlyph.textContent = state.liked ? "♥" : "♡";
  likeButton.classList.toggle("liked", Boolean(state.liked));
  likeButton.setAttribute("aria-label", state.liked ? "Трек в понравившихся" : "Добавить в понравившиеся");
  likeButton.title = state.liked ? "Трек в понравившихся" : "Добавить в понравившиеся";
  if (!volumeDragging) volumeSlider.value = String(Math.round(Math.max(0, Math.min(1, Number(state.volume ?? 0.8))) * 10));
  volumeValue.value = volumeSlider.value;
  source.textContent = state.source === "radio"
    ? (state.service === "vk" ? "VK Музыка · " : "Яндекс Музыка · ") + (state.source_title || "Радио")
    : state.source === "playlist"
      ? `Плейлист · ${state.source_title || (state.service === "vk" ? "VK Музыка" : "Яндекс Музыка")} · ${index + 1}/${count}`
      : state.service === "vk" ? "VK Музыка" : "Яндекс Музыка";
}

function destroyHls() {
  if (!hls) return;
  hls.destroy();
  hls = null;
}

function loadAudioSource(url) {
  releaseAudioMeter();
  destroyHls();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  if (!/\.m3u8(?:[?#]|$)/i.test(url)) {
    audio.src = url;
    audio.load();
    return Promise.resolve(true);
  }

  if (window.Hls && Hls.isSupported()) {
    return new Promise((resolve, reject) => {
      const instance = new Hls();
      hls = instance;
      let settled = false;
      instance.on(Hls.Events.MEDIA_ATTACHED, () => instance.loadSource(url));
      instance.on(Hls.Events.MANIFEST_PARSED, () => {
        if (settled) return;
        settled = true;
        resolve(true);
      });
      instance.on(Hls.Events.ERROR, (_event, data) => {
        if (!data?.fatal) return;
        const detail = String(data.details || data.type || "HlsError");
        if (!settled) {
          settled = true;
          reject(new Error("VK-поток не загрузился: " + detail));
        }
        if (hls === instance) destroyHls();
        setNotice("Не удалось загрузить аудиопоток", true);
        void report("failed", detail.slice(0, 80));
      });
      instance.attachMedia(audio);
    });
  }

  if (audio.canPlayType("application/vnd.apple.mpegurl")) {
    audio.src = url;
    audio.load();
    return Promise.resolve(true);
  }
  return Promise.reject(new Error("В Astra не удалось включить поддержку HLS/M3U8."));
}

function clearAudioSource() {
  releaseAudioMeter();
  destroyHls();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  audioSourceReady = Promise.resolve(true);
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
  setPlayerVisible(true);
  player.classList.add("is-starting");
  playButton.disabled = true;
  setNotice("Запускаю…");
  try {
    if (!(await audioSourceReady)) throw new Error("Аудиопоток не готов к воспроизведению.");
    const attempt = audio.play();
    const attemptReport = report("attempting");
    await attempt;
    await attemptReport;
    playGlyph.textContent = "Ⅱ";
    playButton.setAttribute("aria-label", "Пауза");
    setNotice("");
    setVisualPlaying(true);
    await report("playing");
  } catch (error) {
    const blocked = error?.name === "NotAllowedError";
    await report(blocked ? "blocked" : "failed", error?.name || "PlaybackError");
    setNotice(blocked ? "Автозапуск запрещён — нажмите ▶" : "Не удалось запустить аудио", true);
    player.classList.remove("is-starting");
    setVisualPlaying(false);
    playGlyph.textContent = "▶";
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
    setPlayerVisible(false);
    setVisualPlaying(false);
    title.textContent = "Подключаю плеер Astra…";
    return;
  }
  busy = true;
  try {
    let state = parse(await call("music_playback_state"));
    const restoreDelay = backendRestoreError ? 60000 : 5000;
    if (!state.url && Date.now() - lastBackendRestoreAt > restoreDelay) {
      lastBackendRestoreAt = Date.now();
      try {
        const restored = parse(await call("music_playback_restore"));
        if (restored.ok && (restored.stream_url || restored.url)) state = restored;
        else if (restored.ok) state = parse(await call("music_playback_state"));
        backendRestoreError = restored.error
          ? `Не удалось восстановить трек: ${String(restored.error).slice(0, 180)}`
          : "";
        if (backendRestoreError) lastBackendRestoreAt = Date.now();
      } catch (_) {
        backendRestoreError = "Не удалось восстановить трек. Выберите его снова во вкладке «Музыка».";
        lastBackendRestoreAt = Date.now();
      }
    }
    const playableUrl = state.stream_url || state.url;
    if (!playableUrl) {
      setPlayerVisible(false);
      setVisualPlaying(false);
      player.classList.remove("is-starting");
      if (current) {
        clearAudioSource();
        current = null;
      }
      title.textContent = "Трек не выбран";
      artist.textContent = "Музыкальный плеер Astra";
      source.textContent = "";
      updateCover("");
      playButton.disabled = true;
      stopButton.disabled = true;
      previousButton.disabled = true;
      nextButton.disabled = true;
      likeButton.disabled = true;
      likeGlyph.textContent = "♡";
      likeButton.classList.remove("liked");
      seekSlider.disabled = true;
      setNotice(backendRestoreError, Boolean(backendRestoreError));
      return;
    }
    backendRestoreError = "";
    const status = String(state.status || "");
    const shouldShow = ["play_requested", "attempting", "playing", "blocked"].includes(status);
    setPlayerVisible(shouldShow);
    // The audio element is the source of truth for the glow. A delayed backend
    // status must not turn the animation off while the song is audibly playing.
    setVisualPlaying(!audio.paused && !audio.ended && Boolean(audio.currentSrc));
    player.classList.toggle("is-starting", shouldShow && ["play_requested", "attempting"].includes(status));

    const changedTrack = Number(state.revision) !== lastRevision;
    if (changedTrack) {
      current = { ...state, url: playableUrl };
      lastRevision = Number(state.revision);
      lastCommandRevision = Number(state.command_revision ?? -1);
      audioSourceReady = loadAudioSource(current.url).catch((error) => {
        setNotice(error?.message || "Не удалось загрузить аудиопоток", true);
        void report("failed", error?.name || "HlsError");
        return false;
      });
      audio.volume = Math.max(0, Math.min(1, Number(state.volume ?? 0.8)));
      pendingSeek = Number(state.position_seconds || 0) > 0 ? Number(state.position_seconds) : null;
      title.textContent = current.title || "Без названия";
      artist.textContent = current.artist || (current.service === "vk" ? "VK Музыка" : "Яндекс Музыка");
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
          clearAudioSource();
          current = null;
        }
      }
    }
    if (state.status === "blocked" && audio.paused) setNotice("Автозапуск запрещён — нажмите ▶", true);
    updateProgress();
  } catch (error) {
    console.warn("Astra Music: home player sync failed", error);
    setPlayerVisible(false);
    setVisualPlaying(false);
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
    setPlayerVisible(false);
    void report("paused");
  }
});

stopButton.addEventListener("click", async () => {
  setPlayerVisible(false);
  clearAudioSource();
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
  ensureLocalAudioMeter();
  setPlayerVisible(true);
  setVisualPlaying(true);
  playGlyph.textContent = "Ⅱ";
  playButton.setAttribute("aria-label", "Пауза");
  setNotice("");
  void report("playing");
});
audio.addEventListener("pause", () => {
  setVisualPlaying(false);
  if (current && !audio.ended) {
    playGlyph.textContent = "▶";
    playButton.setAttribute("aria-label", "Воспроизвести");
  }
});
audio.addEventListener("ended", () => {
  setVisualPlaying(false);
  playGlyph.textContent = "▶";
  playButton.setAttribute("aria-label", "Воспроизвести");
  if (current && current.source !== "track") {
    void control("ended", audio.currentTime, { duration_seconds: audio.duration });
  } else {
    setPlayerVisible(false);
    void report("paused");
  }
});
audio.addEventListener("error", () => {
  setVisualPlaying(false);
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
window.addEventListener("beforeunload", () => {
  if (visualFrameId) cancelAnimationFrame(visualFrameId);
  audioBandsUnsubscribe?.();
  releaseAudioMeter();
});
