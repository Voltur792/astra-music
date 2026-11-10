// Load the host's Kit without giving a nested frame access to host RPCs.
window.attachMusicKit = bridge => {
  let request;
  bridge.loadUi = options => request ||= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "http://astra-plugin.localhost/bridge/astra-bridge.js";
    script.onload = () => {
      const load = window.astra?.loadUi;
      window.astra = bridge;
      if (typeof load === "function" && load !== bridge.loadUi) load(options).then(resolve, reject);
      else reject(new Error("Astra UI Kit is unavailable"));
    };
    script.onerror = reject;
    document.head.append(script);
  });
};
