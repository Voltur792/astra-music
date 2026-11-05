/* Runs in Astra's host document with its explicit dom_access permission. */
(() => {
  "use strict";
  const root = document.currentScript?.parentElement;
  const bridge = window.__astraPluginBridge?.["music-controller"];
  if (!root || !bridge) return;
  window.__musicQueueController?.stop();
  const base = "http://astra-plugin.localhost/music-controller/";
  let popup = null, anchor = null, channel = null;
  function close() { popup?.remove(); popup = null; anchor = null; channel = null; }
  function position() {
    if (!popup) return;
    if (!anchor?.isConnected || !root.isConnected) { close(); return; }
    const r = anchor.getBoundingClientRect(), margin = 12;
    const below = innerHeight - r.bottom - margin - 6, above = r.top - margin - 6;
    const down = below >= 160 || below >= above;
    const height = Math.max(100, Math.min(420, down ? below : above));
    const width = Math.min(Math.max(280, r.width), innerWidth - margin * 2);
    Object.assign(popup.style, {
      width: width + "px", height: height + "px",
      left: Math.max(margin, Math.min(r.left, innerWidth - width - margin)) + "px",
      top: Math.max(margin, down ? r.bottom + 6 : r.top - height - 6) + "px",
    });
  }
  function widgetFor(source) {
    return [...document.querySelectorAll("iframe")].find(frame => {
      if (frame.contentWindow !== source) return false;
      try {
        const url = new URL(frame.src);
        return url.origin === "http://astra-plugin.localhost" &&
          ["/music-controller/player-remote.html", "/music-controller/player-v12.html"].includes(url.pathname);
      } catch (_) { return false; }
    });
  }
  async function receive(event) {
    const message = event.data;
    if (message?.type === "astra-music:queue-toggle") {
      const widget = widgetFor(event.source);
      if (!widget) return;
      if (popup && anchor === widget) close();
      else {
        close(); anchor = widget; channel = crypto.randomUUID();
        popup = document.createElement("iframe");
        popup.title = "Очередь воспроизведения Astra Music";
        popup.setAttribute("sandbox", "allow-scripts allow-forms");
        Object.assign(popup.style, {position: "fixed", zIndex: "2147483000", border: "0", borderRadius: "10px", boxShadow: "0 10px 28px #0006"});
        popup.src = base + "queue-window.html#queue-channel=" + channel;
        document.body.append(popup); position();
      }
      event.source.postMessage({type: "astra-music:queue-ack", id: message.id, opened: Boolean(popup)}, "*");
      return;
    }
    if (!popup || event.source !== popup.contentWindow || message?.channel !== channel) return;
    if (message.type === "close") { close(); return; }
    if (message.type !== "call" || !["music_queue", "music_queue_play"].includes(message.method)) return;
    const destination = event.source, replyChannel = channel;
    try {
      const result = await bridge.callBackend(message.method, message.params || {});
      if (destination === popup?.contentWindow) destination.postMessage({channel: replyChannel, type: "reply", id: message.id, result}, "*");
    } catch (error) {
      if (destination === popup?.contentWindow) destination.postMessage({channel: replyChannel, type: "reply", id: message.id, error: String(error.message || error)}, "*");
    }
  }
  const outside = event => { if (popup && event.target !== popup && event.target !== anchor) close(); };
  const escape = event => { if (event.key === "Escape") close(); };
  const observer = new MutationObserver(() => { if (!root.isConnected || anchor && !anchor.isConnected) close(); });
  observer.observe(document.body, {childList: true, subtree: true});
  window.addEventListener("message", receive);
  window.addEventListener("resize", position);
  window.addEventListener("scroll", position, true);
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("keydown", escape);
  const controller = {stop() {
    close(); observer.disconnect();
    window.removeEventListener("message", receive); window.removeEventListener("resize", position);
    window.removeEventListener("scroll", position, true); document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", escape);
  }};
  window.__musicQueueController = controller;
  window.__astraPluginCleanup ||= {};
  window.__astraPluginCleanup["music-controller"] = () => queueMicrotask(() => { if (!root.isConnected) controller.stop(); });
})();
