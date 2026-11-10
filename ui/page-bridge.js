(() => {
  const channel = new URLSearchParams(location.hash.slice(1)).get("music-frame");
  if (!channel || parent === window) {
    const script = document.createElement("script");
    script.src = location.hostname === "127.0.0.1" ? "native-bridge.js"
      : "http://astra-plugin.localhost/bridge/astra-bridge.js";
    document.head.append(script);
    return;
  }
  let next = 0;
  const pending = new Map(), themeListeners = new Set();
  const applyColorScheme = () => document.body?.style.setProperty("color-scheme",
    document.documentElement.dataset.theme === "light" ? "light" : "dark");
  document.addEventListener("DOMContentLoaded", applyColorScheme, {once: true});
  function request(operation, params) {
    return new Promise((resolve, reject) => {
      const id = ++next;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("Музыка не ответила вовремя.")); }, 95000);
      pending.set(id, {resolve, reject, timer});
      parent.postMessage({channel, type: "request", id, operation, ...params}, "*");
    });
  }
  window.addEventListener("message", event => {
    const data = event.data;
    if (event.source !== parent || data?.channel !== channel) return;
    if (data.type === "theme") {
      document.documentElement.dataset.theme = data.theme;
      applyColorScheme();
      for (const [key, value] of Object.entries(data.variables || {}))
        if (key.startsWith("--") && typeof value === "string") document.documentElement.style.setProperty(key, value);
      for (const listener of themeListeners) listener(data.theme);
    } else if (data.type === "reply") {
      const entry = pending.get(data.id);
      if (!entry) return;
      pending.delete(data.id); clearTimeout(entry.timer);
      if (data.error) entry.reject(new Error(data.error)); else entry.resolve(data.result);
    }
  });
  const musicBridge = {
    callBackend: (method, params = {}) => request("backend", {method, params}),
    getData: key => request("getData", {key}),
    setData: (key, value) => request("setData", {key, value}),
    onThemeChange: listener => { themeListeners.add(listener); return () => themeListeners.delete(listener); },
  };
  if (location.pathname.endsWith("/index.html") && window.attachMusicKit) {
    window.attachMusicKit(musicBridge);
    const loadUi = musicBridge.loadUi;
    musicBridge.loadUi = async options => {
      const kit = await loadUi(options);
      applyColorScheme();
      return kit;
    };
  }
  window.astra = musicBridge;
  parent.postMessage({channel, type: "ready"}, "*");
})();
