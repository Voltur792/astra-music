// Private loopback transport for the persistent WebView2 audio process.
window.MUSIC_AUDIO_HOST = location.pathname.endsWith("/player-v12.html");
const nativeRoot = location.pathname.slice(0, location.pathname.lastIndexOf("/"));
window.astra = {
  callBackend: async (method, params = {}) => {
    const response = await fetch(nativeRoot + "/api", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ method, params }) });
    if (!response.ok) throw new Error("Audio host unavailable");
    return response.json();
  }
};
// Only the actual audio page reports health. A visible remote window must not
// hide a stalled audio process from the supervisor.
if (window.MUSIC_AUDIO_HOST) {
  const heartbeat = () => {
    if (window.MUSIC_PLAYER_READY) void window.astra.callBackend("music_audio_host_ping").catch(() => {});
  };
  heartbeat();
  const heartbeatTimer = setInterval(heartbeat, 1000);
  window.addEventListener("beforeunload", () => clearInterval(heartbeatTimer));
  window.addEventListener("load", () => {
    if (window.MUSIC_PLAYER_READY) { sessionStorage.removeItem("music-startup-reloads"); return; }
    const retries = Number(sessionStorage.getItem("music-startup-reloads") || 0);
    if (retries < 2) {
      sessionStorage.setItem("music-startup-reloads", String(retries + 1));
      setTimeout(() => location.reload(), 500);
    }
    // Without a running player script there is no heartbeat, so the supervisor
    // can restart a failed browser instead of accepting a permanently blank page.
  });
}
