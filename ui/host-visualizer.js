(() => {
  const root = document.currentScript?.parentElement;
  const bridge = window.__astraPluginBridge?.["music-controller"];
  if (!root || !bridge) return;
  window.__musicHostFramesReadyV5 ||= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "http://astra-plugin.localhost/music-controller/host-frames.js?v=5";
    script.onload = resolve; script.onerror = reject; document.head.append(script);
  });
  window.__musicHostFramesReadyV5.then(() => {
    if (root.isConnected) window.MusicHostFrames.mount(root, bridge, "visualizer.html", "visualizer");
  }).catch(() => {});
})();
