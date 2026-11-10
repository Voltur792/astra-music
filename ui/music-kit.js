(() => {
  const toggles = new Map();
  let request;
  function load() {
    return request ||= (async () => {
      if (!window.astra?.loadUi || location.hostname === "127.0.0.1") return null;
      let timeout;
      try {
        return await Promise.race([
          window.astra.loadUi({apiVersion: 1}),
          new Promise(resolve => { timeout = setTimeout(() => resolve(null), 12000); }),
        ]);
      } catch (_) { return null; }
      finally { clearTimeout(timeout); }
    })();
  }

  function syncToggles() {
    for (const sync of toggles.values()) sync();
  }

  function mountToggle(label, kit) {
    const input = label.querySelector('input[type="checkbox"]');
    const text = label.lastElementChild?.textContent.trim();
    if (!input || !text) return;
    const target = document.createElement("div");
    label.after(target);
    const view = kit.mount(target, null);
    const {h, Toggle} = kit;
    let restoreFocus = document.activeElement === input;
    let last;
    function sync() {
      const checked = input.checked, disabled = input.matches(":disabled");
      if (last?.checked === checked && last.disabled === disabled) return;
      last = {checked, disabled};
      view.render(h("div", {ref: node => {
        if (!node || !restoreFocus) return;
        restoreFocus = false;
        node.querySelector("button")?.focus();
      }}, h(Toggle, {checked, disabled, label: text, onChange: () => { input.click(); sync(); }})));
    }
    toggles.set(input, sync);
    sync();
    label.hidden = true;
  }

  // Keep the original form and playback elements. The Kit stylesheet supplies
  // their skin while the browser still owns validation, focus and range events.
  const fields = 'input:not([type]), input[type="search"], input[type="text"], input[type="password"], input[type="url"]';
  const skinTargets = `button, ${fields}, textarea, .card`;
  function skin(element) {
    if (element.closest(".astra-ui-root")) return;
    if (element.matches("button")) {
      if (element.matches(".visualizer-style, .trigger, .queue-track")) return;
      if (element.closest(".player")) {
        if (element.classList.contains("queue-page")) element.classList.add("ui-btn", "ui-btn--sm", "ui-btn--secondary");
        else element.classList.add("ui-icon-btn", "ui-icon-btn--sm", "ui-icon-btn--ghost");
      } else {
        const primary = element.matches('.primary, [aria-selected="true"], [aria-pressed="true"]');
        element.classList.add("ui-btn", "ui-btn--sm");
        element.classList.toggle("ui-btn--primary", primary);
        element.classList.toggle("ui-btn--secondary", !primary);
      }
    } else if (element.matches(fields) && !element.classList.contains("ui-input__field")) {
      const wrapper = document.createElement("span");
      wrapper.className = "ui-input ui-input--md music-kit-input";
      element.classList.add("ui-input__field");
      element.before(wrapper);
      wrapper.append(element);
    } else if (element.matches("textarea")) element.classList.add("ui-textarea");
    else if (element.matches(".card")) element.classList.add("ui-card", "ui-card--surface", "ui-card--pad-md");
  }

  function skinTree(root) {
    if (!(root instanceof Element) || root.closest(".astra-ui-root")) return;
    if (root.matches(skinTargets)) skin(root);
    root.querySelectorAll(skinTargets).forEach(skin);
  }

  function enhance(kit) {
    document.documentElement.classList.add("music-kit");
    skinTree(document.body);
    if (kit.Toggle) document.querySelectorAll(".visualizer-switch").forEach(label => mountToggle(label, kit));
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === "childList") record.addedNodes.forEach(skinTree);
        else if (record.attributeName === "disabled") {
          if (record.target.matches("fieldset")) syncToggles();
          else toggles.get(record.target)?.();
        } else if (record.target.matches("button")) skin(record.target);
      }
    });
    observer.observe(document.body, {subtree: true, childList: true, attributes: true,
      attributeFilter: ["aria-pressed", "aria-selected", "disabled"]});
    window.addEventListener("pagehide", () => observer.disconnect(), {once: true});
  }

  window.MusicUi = {load, syncToggles};
  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const kit = await load();
      if (kit?.mount && kit.h) enhance(kit);
    } finally {
      document.body.removeAttribute("data-music-kit-loading");
    }
    if (document.body.classList.contains("queue-window") && document.activeElement === document.body) {
      (document.querySelector('#queueList [aria-current="true"]') || document.getElementById("queueClose"))?.focus({preventScroll: true});
    }
  }, {once: true});
})();
