// This iframe is disposable: audio belongs to the persistent native host.
"use strict";
const $ = id => document.getElementById(id);
const player = document.querySelector(".player");
const canvas = document.createElement("canvas");
canvas.className = "music-spectrum"; canvas.setAttribute("aria-hidden", "true");
player.prepend(canvas);
const spectrum = new MusicSpectrum(canvas);
let state = {}, pending = false, draggingVolume = false, draggingSeek = false;
let visualize = false, lastFrame = 0, frameId;
const parse = value => typeof value === "string" ? JSON.parse(value) : value || {};
const call = async (method, params = {}) => parse(await window.astra.callBackend(method, params));
const time = value => { const n = Math.max(0, Math.floor(Number(value) || 0)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`; };
function notice(text, bad = false) { $("notice").textContent = text; $("notice").classList.toggle("bad", bad); }
function render() {
  const selected = Boolean(state.track_id), playing = state.status === "playing";
  player.classList.toggle("is-playing", playing);
  player.classList.toggle("is-starting", ["play_requested", "attempting"].includes(state.status));
  $("title").textContent = state.title || "Трек не выбран";
  $("artist").textContent = state.artist || "Музыкальный плеер Astra";
  $("source").textContent = state.source_title || (state.service === "vk" ? "VK Музыка" : state.service === "yandex" ? "Яндекс Музыка" : "");
  if ($("cover").getAttribute("src") !== (state.cover_url || "")) {
    $("cover").hidden = !state.cover_url; $("coverFallback").hidden = Boolean(state.cover_url);
    if (state.cover_url) $("cover").src = state.cover_url; else $("cover").removeAttribute("src");
  }
  $("play").disabled = !selected; $("stop").disabled = !selected;
  $("play").querySelector(".glyph").textContent = playing ? "Ⅱ" : "▶";
  $("play").setAttribute("aria-label", playing ? "Пауза" : "Воспроизвести");
  $("previous").disabled = !selected || Number(state.queue_index) <= 0;
  $("next").disabled = !selected || (state.source !== "radio" && Number(state.queue_index) >= Number(state.queue_count) - 1);
  $("like").disabled = !selected || state.service === "vk" || state.liked;
  $("like").querySelector(".glyph").textContent = state.liked ? "♥" : "♡";
  $("like").classList.toggle("liked", Boolean(state.liked));
  if (!draggingVolume) $("volume").value = Math.round(Number(state.volume ?? .8) * 10);
  $("volumeValue").value = $("volume").value;
  $("seek").disabled = !selected || !state.duration_seconds;
  $("seek").max = Math.max(1, Number(state.duration_seconds) || 1);
  if (!draggingSeek) $("seek").value = state.position_seconds || 0;
  $("elapsed").textContent = time(state.position_seconds); $("duration").textContent = time(state.duration_seconds);
  if (state.status === "failed") {
    const message = state.playback_error === "EdgeNotFound" ? "Не найден Microsoft Edge для аудиоплеера."
      : state.playback_error === "AudioHostUnavailable" ? "Не удалось запустить аудиопроцесс. Нажмите ▶ для повтора."
      : "Не удалось загрузить поток. Нажмите ▶ для повтора.";
    notice(message, true);
  }
  else if (state.status === "blocked") notice("Не удалось запустить постоянный плеер", true);
  else notice("");
}
async function sync() {
  if (pending || !window.astra?.callBackend) return;
  pending = true;
  try {
    const [next, visual] = await Promise.all([call("music_playback_status"), call("music_visualizer_state")]);
    state = next; render();
    const settings = visual.settings || {};
    visualize = Boolean(settings.widget && settings.mode !== "off" && (settings.mode === "all" || visual.playing));
    canvas.hidden = !visualize; spectrum.setOptions(settings);
    if (visualize && visual.bands?.length) spectrum.sample(visual.bands); else spectrum.clear();
    if (visual.audio_error) notice(visual.audio_error, true);
  } catch (_) { notice("Плеер недоступен. Проверьте подключение плагина.", true); }
  finally { pending = false; }
}
async function control(action, value = 0) {
  try { const result = await call("music_playback_control", {action, value}); if (result.error) notice(result.error, true); else await sync(); }
  catch (_) { notice("Не удалось выполнить команду", true); }
}
$("play").addEventListener("click", () => control(state.status === "playing" ? "pause" : "play"));
$("stop").addEventListener("click", async () => { await call("music_playback_stop"); await sync(); });
for (const id of ["previous", "next", "like"]) $(id).addEventListener("click", () => control(id));
$("volume").addEventListener("pointerdown", () => { draggingVolume = true; });
$("volume").addEventListener("input", () => { $("volumeValue").value = $("volume").value; });
$("volume").addEventListener("change", () => { draggingVolume = false; control("volume", Number($("volume").value)); });
$("seek").addEventListener("input", () => { draggingSeek = true; $("elapsed").textContent = time($("seek").value); });
$("seek").addEventListener("change", () => { draggingSeek = false; control("seek", Number($("seek").value)); });
$("cover").addEventListener("error", () => { $("cover").hidden = true; $("coverFallback").hidden = false; });
function frame(now) { if (visualize && now - lastFrame > 33) { spectrum.draw(now); lastFrame = now; } frameId = requestAnimationFrame(frame); }
const timer = setInterval(sync, 80); void sync(); frameId = requestAnimationFrame(frame);
window.addEventListener("beforeunload", () => { clearInterval(timer); cancelAnimationFrame(frameId); });
