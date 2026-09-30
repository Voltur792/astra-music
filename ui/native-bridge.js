// Private loopback transport for the persistent WebView2 audio process.
window.MUSIC_AUDIO_HOST = true;
const nativeRoot = location.pathname.slice(0, location.pathname.lastIndexOf("/"));
window.astra = {
  callBackend: async (method, params = {}) => {
    const response = await fetch(nativeRoot + "/api", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ method, params }) });
    if (!response.ok) throw new Error("Audio host unavailable");
    return response.json();
  }
};
