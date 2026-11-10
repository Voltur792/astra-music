(() => {
  const channel = new URLSearchParams(location.hash.slice(1)).get("queue-channel");
  if (!channel || parent === window) return;
  window.MUSIC_QUEUE_EMBEDDED = true;
  const pending = new Map();
  let next = 0;
  window.addEventListener("message", event => {
    if (event.source !== parent || event.data?.channel !== channel || event.data.type !== "reply") return;
    const entry = pending.get(event.data.id);
    if (!entry) return;
    pending.delete(event.data.id); clearTimeout(entry.timer);
    if (event.data.error) entry.reject(new Error(event.data.error));
    else entry.resolve(event.data.result);
  });
  window.astra = {callBackend(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++next;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("Очередь не ответила. Откройте её заново.")); }, 95000);
      pending.set(id, {resolve, reject, timer});
      parent.postMessage({channel, type: "call", id, method, params}, "*");
    });
  }};
  window.attachMusicKit?.(window.astra);
  window.closeMusicQueue = () => parent.postMessage({channel, type: "close"}, "*");
})();
