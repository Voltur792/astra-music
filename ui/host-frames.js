/* HTML surfaces stay sandboxed even when Astra loads contribution entries as JS. */
(() => {
  const mounts = new WeakMap();
  function backendResult(value) {
    for (let n = 0; n < 6; n++) {
      if (typeof value === "string") {
        try { value = JSON.parse(value); } catch (_) { break; }
      } else if (value && typeof value === "object" && typeof value.result_json === "string") value = value.result_json;
      else break;
    }
    return value;
  }
  function mount(root, bridge, path, surface) {
    mounts.get(root)?.stop();
    const channel = crypto.randomUUID();
    const host = document.createElement("div");
    host.style.cssText = "width:100%;height:100%;min-height:0;display:flex;flex:1;background:transparent";
    const frame = document.createElement("iframe");
    frame.title = surface === "page" ? "Astra Music" : "Цветомузыка Astra Music";
    frame.setAttribute("sandbox", "allow-scripts allow-forms");
    frame.style.cssText = "display:block;border:0;width:100%;height:100%;min-height:0;flex:1;background:transparent";
    frame.src = "http://astra-plugin.localhost/music-controller/" + path + "#music-frame=" + channel;
    host.append(frame);
    let wallpaper = null, previousPosition = null;
    function restorePosition() {
      if (previousPosition && wallpaper) {
        if (previousPosition.value) wallpaper.style.setProperty("position", previousPosition.value, previousPosition.priority);
        else wallpaper.style.removeProperty("position");
      }
      previousPosition = null;
    }
    function placeBackground() {
      // Paint inside the existing wallpaper layer: an opaque wallpaper hides
      // a negative global layer, while raising it above the app covers controls.
      const candidates = [];
      for (const element of document.querySelectorAll("body *")) {
        if (element === host || element === root || host.contains(element) || root.contains(element)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.left > 4 || rect.top > 4 || rect.right < innerWidth - 4 || rect.bottom < innerHeight - 4) continue;
        if (element.querySelector("button,input,textarea,iframe,[role=dialog]")) continue;
        const css = getComputedStyle(element);
        if (css.visibility === "hidden" || css.display === "none") continue;
        if (css.backgroundImage !== "none") candidates.push(element);
        else if (["IMG", "VIDEO", "CANVAS"].includes(element.tagName) && element.parentElement &&
          !element.parentElement.querySelector("button,input,textarea,iframe,[role=dialog]")) candidates.push(element.parentElement);
      }
      const target = candidates.find(element => getComputedStyle(element).backgroundImage.includes("url(")) || candidates[0] || root;
      if (host.parentElement === target) return;
      restorePosition(); wallpaper = target;
      if (target !== root && getComputedStyle(target).position === "static") {
        previousPosition = {value: target.style.getPropertyValue("position"), priority: target.style.getPropertyPriority("position")};
        target.style.setProperty("position", "relative");
      }
      host.style.position = target === root ? "fixed" : "absolute";
      host.style.inset = "0"; host.style.zIndex = "1"; host.style.pointerEvents = "none";
      target.append(host);
      // Log only the placement kind, never wallpaper URLs or other host data.
      void bridge.callBackend("music_visualizer_layer_report", {placement: target === root ? "slot" : "wallpaper"}).catch(() => {});
    }
    if (surface === "visualizer") placeBackground(); else root.append(host);
    let stopped = false;
    const post = data => frame.contentWindow?.postMessage({channel, ...data}, "*");
    let kitActive = false;
    const themeAttributes = ["data-theme", "data-palette", "data-accent", "data-material", "data-glass", "data-fluent"];
    function uiEnvironment(variables = bridge.getCssVariables?.() || {}) {
      const html = document.documentElement;
      const attributes = Object.fromEntries(themeAttributes.map(name => [name, html.getAttribute(name)]));
      attributes["data-theme"] ||= bridge.getTheme?.() || "dark";
      return {attributes, variables, language: html.lang || "ru"};
    }
    function theme() {
      const variables = bridge.getCssVariables?.() || {};
      if (kitActive) post({type: "astra-bridge-event", event: "uiEnvironmentChange", payload: uiEnvironment(variables)});
      post({type: "theme", theme: bridge.getTheme?.() || document.documentElement.dataset.theme || "dark",
        variables});
    }
    async function receive(event) {
      const message = event.data;
      if (stopped || event.source !== frame.contentWindow || !message) return;
      // The stock UI loader needs a read-only environment snapshot. Backend
      // calls still require the private Music channel and its allowlist below.
      if (surface === "page" && message.type === "astra-bridge-request" &&
          message.method === "getUiEnvironment" && /^req_\d+$/.test(message.id)) {
        kitActive = true;
        post({type: "astra-bridge-response", id: message.id, result: uiEnvironment()});
        return;
      }
      if (message.channel !== channel) return;
      if (message.type === "ready") { theme(); return; }
      if (message.type !== "request") return;
      try {
        let result;
        if (message.operation === "backend" && /^music_[a-z0-9_]+$/.test(message.method || "")) {
          if (surface === "visualizer" && message.method !== "music_visualizer_state") return;
          result = backendResult(await bridge.callBackend(message.method, message.params || {}));
        } else if (surface === "page" && ["getData", "setData"].includes(message.operation) && message.key === "music-controller-ui-v1") {
          result = message.operation === "getData" ? await bridge.getData?.(message.key)
            : await bridge.setData?.(message.key, message.value);
        } else return;
        if (!stopped) post({type: "reply", id: message.id, result: result ?? null});
      } catch (error) { if (!stopped) post({type: "reply", id: message.id, error: String(error.message || error)}); }
    }
    const untheme = bridge.onThemeChange?.(theme);
    const environmentObserver = new MutationObserver(() => { if (kitActive) theme(); });
    if (surface === "page") environmentObserver.observe(document.documentElement, {attributes: true, attributeFilter: [...themeAttributes, "lang", "style"]});
    window.addEventListener("message", receive);
    const observer = new MutationObserver(() => {
      if (!root.isConnected) stop();
      else if (!host.isConnected) { if (surface === "visualizer") placeBackground(); else stop(); }
    });
    observer.observe(document.documentElement, {childList: true, subtree: true});
    function stop() {
      if (stopped) return;
      stopped = true; observer.disconnect(); untheme?.(); window.removeEventListener("message", receive);
      environmentObserver.disconnect();
      restorePosition();
      host.remove();
    }
    mounts.set(root, {stop});
    return {stop};
  }
  window.MusicHostFrames = {mount};
})();
