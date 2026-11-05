(() => {
  const palettes = {
    yandex: {background: "#171717", accent: "#ffcc00"},
    vk: {background: "#101e33", accent: "#2787f5"},
    spotify: {background: "#101c15", accent: "#1ed760"},
    default: {background: "#18181b", accent: "#a1a1aa"},
  };
  const valid = value => typeof value === "string" && /^#[\da-f]{6}$/i.test(value);
  function foreground(color) {
    const rgb = color.slice(1).match(/../g).map(x => parseInt(x, 16) / 255)
      .map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4);
    const luminance = rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
    return (luminance + .05) / .05 > 1.05 / (luminance + .05) ? "#000000" : "#ffffff";
  }
  function apply(service, settings = {}, root = document.documentElement) {
    const palette = settings.mode === "custom" && valid(settings.background) && valid(settings.accent)
      ? settings : palettes[service] || palettes.default;
    root.dataset.musicTheme = settings.mode === "custom" ? "custom" : service || "default";
    for (const [key, value] of Object.entries({bg: palette.background, accent: palette.accent,
      text: foreground(palette.background), "on-accent": foreground(palette.accent)})) {
      root.style.setProperty("--music-" + key, value);
    }
  }
  window.MusicTheme = {apply};
  apply();
})();
