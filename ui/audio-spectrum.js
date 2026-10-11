"use strict";

// All production motion is driven by measured bands, never a synthetic beat.
window.MusicSpectrum = class MusicSpectrum {
  static STYLES = [["spectrum", "Спектр"], ["waves", "Неоновые волны"], ["liquid", "Жидкий свет"], ["ripple", "Круги на воде"], ["ribbon", "Цветные ленты"], ["orbit", "Орбита"], ["particles", "Частицы"], ["mesh", "Неоновая сетка"], ["binary", "Цифровые волны"], ["terrain", "Золотой ландшафт"], ["silk", "Цветной шёлк"], ["splash", "Всплеск"], ["rain", "Радужный дождь"], ["contour", "Слои цвета"], ["aurora", "Северное сияние"], ["tunnel", "Неоновый тоннель"], ["stars", "Звёздный полёт"], ["flower", "Звуковой цветок"]];
  static DEFAULTS = { palette: "original", color1: "#38d9ef", color2: "#d857ff", scale: 1, brightness: 1, thickness: 1, blur: 0, speed: 1, density: 1, width: 1, height: 1 };
  static RANGES = { scale: [.5, 2], brightness: [.25, 2.5], thickness: [.25, 3], blur: [0, 8], speed: [.25, 2.5], density: [.5, 2], width: [.5, 2], height: [.5, 2] };
  static customize(value = {}) {
    const result = { ...this.DEFAULTS };
    if (["original", "single", "gradient"].includes(value.palette)) result.palette = value.palette;
    for (const key of ["color1", "color2"]) if (/^#[\da-f]{6}$/i.test(value[key])) result[key] = value[key];
    for (const [key, [min, max]] of Object.entries(this.RANGES)) {
      const number = Number(value[key]);
      if (Number.isFinite(number) && value[key] != null) result[key] = Math.max(min, Math.min(max, number));
    }
    return result;
  }
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.levels = new Array(48).fill(0);
    this.bands = [];
    this.lastSampleAt = 0;
    this.lastDrawAt = 0;
    this.phase = 0;
    this.style = "spectrum";
    this.intensity = .65;
    this.custom = { ...MusicSpectrum.DEFAULTS };
  }
  setOptions(options = {}) {
    if (MusicSpectrum.STYLES.some(([id]) => id === options.style)) this.style = options.style;
    if (Number.isFinite(Number(options.intensity))) this.intensity = Math.max(.2, Math.min(1, Number(options.intensity)));
    this.custom = MusicSpectrum.customize(options.customizations?.[this.style]);
    const colorKey = [this.custom.palette, this.custom.color1, this.custom.color2].join(":");
    if (colorKey !== this.colorKey) {
      this.colorKey = colorKey;
      const rgb = hex => [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16));
      const colors = [rgb(this.custom.color1), rgb(this.custom.color2)];
      // Precompute the palette once, rather than allocate RGB arrays per texture pixel.
      this.colorRamp = Array.from({ length: 360 }, (_, hue) => {
        const blend = this.custom.palette === "single" ? 0 : (1 + Math.sin(hue * Math.PI / 180)) / 2;
        return colors[0].map((channel, i) => Math.round(channel * (1 - blend) + colors[1][i] * blend)).join(",");
      });
    }
  }
  sample(data, scale = 1) {
    const bands = data?.bands || data?.data || data?.spectrum || data;
    if (!bands || typeof bands.length !== "number") return;
    const divisor = Number.isFinite(scale) && scale > 0 ? scale : 1;
    this.bands = Array.from(bands).slice(0, 2048).map(value => Math.min(1, Math.max(0, (Number(value) || 0) / divisor)));
    this.lastSampleAt = performance.now();
  }
  clear() {
    this.bands = [];
    this.levels.fill(0);
    this.lastSampleAt = 0;
    this.context.setTransform(1, 0, 0, 1, 0, 0);
    this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }
  draw(now = performance.now()) {
    const c = this.context, canvas = this.canvas;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
      canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio);
    }
    c.setTransform(ratio, 0, 0, ratio, 0, 0);
    c.clearRect(0, 0, w, h);
    const elapsed = this.lastDrawAt ? Math.max(0, Math.min(64, now - this.lastDrawAt)) : 16;
    this.lastDrawAt = now;
    let energy = 0;
    const fresh = now - this.lastSampleAt < 800;
    for (let i = 0; i < this.levels.length; i++) {
      const target = fresh ? this.bands[Math.min(this.bands.length - 1, Math.floor(i * this.bands.length / this.levels.length))] || 0 : 0;
      const easing = 1 - Math.pow(target > this.levels[i] ? .6 : .88, elapsed / 16);
      this.levels[i] += (target - this.levels[i]) * easing;
      energy += this.levels[i];
    }
    energy /= this.levels.length;
    if (energy < .002 || !w || !h) return;
    const custom = this.custom, thick = custom.thickness, density = custom.density;
    this.phase += elapsed * .001 * energy * 2 * custom.speed;
    c.save();
    c.globalAlpha = this.intensity;
    c.translate(w / 2, h / 2); c.scale(custom.scale * custom.width, custom.scale * custom.height); c.translate(-w / 2, -h / 2);
    // Blur uses a shared logical size so thumbnail and desktop presets stay comparable.
    c.filter = custom.brightness === 1 && custom.blur === 0 ? "none" : `brightness(${custom.brightness}) blur(${Math.min(24, custom.blur * Math.min(w, h) / 180)}px)`;
    c.lineJoin = "round";
    c.lineCap = "round";
    const p = this.phase, levels = this.levels;
    const color = (hue, alpha = 1) => {
      if (custom.palette === "original") return `hsla(${hue},95%,65%,${alpha})`;
      return `rgba(${this.colorRamp[Math.floor((hue % 360 + 360) % 360)]},${Math.max(0, Math.min(1, alpha))})`;
    };
    if (this.style === "spectrum") {
      const bars = Math.round(48 * density), step = w / bars;
      for (let i = 0; i < bars; i++) {
        const level = levels[Math.floor(i / bars * 48)];
        const bh = level * h * .8;
        const g = c.createLinearGradient(0, h - bh, 0, h);
        g.addColorStop(0, color(175 + i * 3, Math.min(1, level * 2)));
        g.addColorStop(1, color(235 + i * 3, 0));
        c.fillStyle = g;
        const width = Math.min(step, Math.max(.5, (step - 2) * thick));
        c.fillRect(i * step + (step - width) / 2, h - bh, width, bh);
      }
    } else if (this.style === "waves" || this.style === "ribbon") {
      const ribbon = this.style === "ribbon";
      for (let layer = 0; layer < Math.round(5 * density); layer++) {
        const points = [];
        for (let i = 0; i <= 96; i++) {
          const x = i / 96, level = levels[Math.min(47, Math.floor(x * 48))];
          const envelope = Math.sin(x * Math.PI);
          const y = h * .5 + Math.sin(x * Math.PI * (ribbon ? 9 : 4) + p * 2 + layer * .6) * (energy * .22 + level * .24) * h * envelope;
          points.push([x * w, y]);
        }
        c.beginPath(); points.forEach(([x, y], i) => i ? c.lineTo(x, y) : c.moveTo(x, y));
        c.lineWidth = (ribbon ? Math.max(2, h * .025) : 1.3) * thick;
        c.strokeStyle = color(ribbon ? 155 + layer * 42 : 265 + layer * 13, Math.min(.85, energy * 3));
        c.stroke();
        if (!ribbon) {
          for (let i = points.length - 1; i >= 0; i--) c.lineTo(points[i][0], points[i][1] + h * .07 * energy * Math.sin(i * .12 + layer));
          c.closePath(); c.fillStyle = color(280 + layer * 9, energy * .18); c.fill();
        }
      }
    } else if (this.style === "liquid") {
      // Sample into a reusable bounded texture, then interpolate its pixels.
      // Direct CSS-sized cells expose a grid in the neon contours.
      const cols = Math.min(160, Math.max(48, Math.ceil(w / 2))), rows = Math.min(90, Math.max(32, Math.ceil(h / 2)));
      if (!this.liquidCanvas) {
        this.liquidCanvas = document.createElement("canvas");
        this.liquidContext = this.liquidCanvas.getContext("2d");
      }
      const texture = this.liquidCanvas, fieldContext = this.liquidContext;
      if (texture.width !== cols || texture.height !== rows) { texture.width = cols; texture.height = rows; }
      fieldContext.clearRect(0, 0, cols, rows);
      for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
        const nx = x / cols, ny = y / rows;
        const bandPosition = nx * 47, bandIndex = Math.floor(bandPosition);
        const band = levels[bandIndex] * (1 - (bandPosition - bandIndex)) + levels[Math.min(47, bandIndex + 1)] * (bandPosition - bandIndex);
        const field = Math.sin(nx * 17 * density + Math.sin(ny * 9 * density + p) * 2 + p) + Math.cos(ny * 15 * density - Math.sin(nx * 8 * density - p) * 2);
        const edge = Math.pow(Math.max(0, 1 - Math.abs(field) * .75 / thick), 2);
        fieldContext.fillStyle = color(140 + field * 65 + band * 80, edge * Math.min(.8, energy * 2.5) * (.35 + band));
        fieldContext.fillRect(x, y, 1, 1);
      }
      c.imageSmoothingEnabled = true;
      c.imageSmoothingQuality = "high";
      c.drawImage(texture, 0, 0, w, h);
    } else if (this.style === "ripple") {
      const size = Math.hypot(w, h) * .55;
      const rings = Math.round(18 * density);
      for (let ring = 0; ring < rings; ring++) {
        const radius = (ring + 1) / rings * size;
        c.beginPath();
        for (let i = 0; i <= 96; i++) {
          const angle = i / 96 * Math.PI * 2;
          const band = levels[Math.floor(i / 96 * 47)];
          const r = radius * (1 + Math.sin(angle * 7 + p * 2 - ring * .55) * band * .16);
          const x = w / 2 + Math.cos(angle) * r, y = h / 2 + Math.sin(angle) * r * .58;
          i ? c.lineTo(x, y) : c.moveTo(x, y);
        }
        c.strokeStyle = color(180 + ring * 8, Math.min(.7, energy * 2) * (1 - ring / (rings + 4)));
        c.lineWidth = (1 + levels[(ring * 2) % 48] * 4) * thick; c.stroke();
      }
    } else if (this.style === "orbit") {
      const r = Math.min(w, h) * .22;
      const rays = Math.round(48 * density);
      for (let i = 0; i < rays; i++) {
        const level = levels[Math.floor(i / rays * 48)];
        const angle = i / rays * Math.PI * 2 + p * .25;
        c.beginPath();
        c.moveTo(w / 2 + Math.cos(angle) * r, h / 2 + Math.sin(angle) * r);
        c.lineTo(w / 2 + Math.cos(angle) * (r + level * h * .25), h / 2 + Math.sin(angle) * (r + level * h * .25));
        c.strokeStyle = color(170 + i * 4, Math.min(.9, level * 2)); c.lineWidth = Math.max(1, Math.min(w, h) / 90) * thick; c.stroke();
      }
    } else if (this.style === "mesh" || this.style === "terrain") {
      const mesh = this.style === "mesh", cols = Math.round((mesh ? 28 : 52) * density), rows = Math.round((mesh ? 18 : 26) * density);
      const point = (x, z) => {
        const depth = (z + 1) / rows, spread = .12 + depth * .92;
        const nx = x / cols, band = levels[Math.min(47, Math.floor(nx * 47))];
        const wave = Math.sin(nx * 10 + z * .34 + p * 1.5) * (band * .14 + energy * .1);
        return [w * (.5 + (nx - .5) * spread), h * (.32 + depth * .59 - wave * Math.pow(depth, .65))];
      };
      if (mesh) {
        c.lineWidth = .7 * thick;
        for (let z = 0; z < rows; z++) {
          c.beginPath();
          for (let x = 0; x <= cols; x++) { const q = point(x, z); x ? c.lineTo(...q) : c.moveTo(...q); }
          c.strokeStyle = color(178 + z * 1.5, energy * (.35 + z / rows)); c.stroke();
        }
        for (let x = 0; x <= cols; x++) {
          c.beginPath();
          for (let z = 0; z < rows; z++) { const q = point(x, z); z ? c.lineTo(...q) : c.moveTo(...q); }
          c.strokeStyle = color(185, energy * .55); c.stroke();
        }
      }
      for (let z = 0; z < rows; z++) for (let x = 0; x <= cols; x++) {
        const q = point(x, z), band = levels[Math.floor(x / cols * 47)];
        c.beginPath(); c.arc(q[0], q[1], Math.max(.4, (z / rows + .2) * (mesh ? 1.1 : 1.65) * (.5 + band)) * thick, 0, Math.PI * 2);
        c.fillStyle = color(mesh ? 180 : 40 + Math.sin(x / cols * 8 + z * .3 + p) * 165, Math.min(.85, energy * (mesh ? 1.6 : 2.2))); c.fill();
      }
    } else if (this.style === "binary") {
      const cols = Math.min(96, Math.max(20, Math.floor(w / 13 * density))), rows = Math.round(9 * density);
      const fontSize = Math.max(7, Math.min(15, w / cols * .72)) * Math.sqrt(thick);
      c.font = `${fontSize}px ui-monospace, Consolas, monospace`; c.textAlign = "center";
      for (let row = 0; row < rows; row++) for (let x = 0; x < cols; x++) {
        const nx = x / (cols - 1), band = levels[Math.floor(nx * 47)];
        const y = h * .5 + Math.sin(nx * 7 + p * 2 + row * .34) * h * (.08 + band * .2) + (row - (rows - 1) / 2) * fontSize * .8;
        c.fillStyle = color(180 + nx * 125, Math.min(.85, energy * 1.8) * (1 - Math.abs(row - (rows - 1) / 2) / (rows * .67)));
        c.fillText((x * 7 + row * 3 + Math.floor(p * 3)) % 5 < 2 ? "1" : "0", nx * w, y);
      }
    } else if (this.style === "silk") {
      for (let layer = 0; layer < Math.round(7 * density); layer++) {
        const yAt = (nx, side) => {
          const band = levels[Math.floor(nx * 47)];
          const curve = Math.sin(nx * 7 + p + layer * .42) * (.1 + band * .2);
          const fold = Math.sin(nx * 15 - p * 1.3 + layer) * energy * .045;
          return h * (.5 + curve + fold + side * (.025 + band * .05) * thick * Math.sin(nx * Math.PI));
        };
        c.beginPath();
        for (let i = 0; i <= 96; i++) { const nx = i / 96; i ? c.lineTo(nx * w, yAt(nx, 1)) : c.moveTo(0, yAt(0, 1)); }
        for (let i = 96; i >= 0; i--) c.lineTo(i / 96 * w, yAt(i / 96, -1));
        c.closePath();
        const g = c.createLinearGradient(0, 0, w, h);
        g.addColorStop(0, color(155 + layer * 28, energy * .16)); g.addColorStop(.5, color(195 + layer * 23, energy * .75)); g.addColorStop(1, color(300 + layer * 9, energy * .16));
        c.fillStyle = g; c.fill(); c.strokeStyle = color(170 + layer * 26, energy * .55); c.lineWidth = .6 * thick; c.stroke();
      }
    } else if (this.style === "splash") {
      const center = w * .5, baseline = h * .69, spread = Math.min(w * .43, h * 1.4);
      const splashes = Math.round(36 * density);
      for (let i = 0; i < splashes; i++) {
        const nx = i / (splashes - 1), level = levels[Math.floor(nx * 47)], x = center + (nx - .5) * spread * 2;
        const lift = h * level * (.28 + .22 * Math.sin(nx * Math.PI));
        const g = c.createLinearGradient(x, baseline - lift, x, baseline + h * .13);
        g.addColorStop(0, color(nx * 300, Math.min(.8, energy * 2))); g.addColorStop(.75, color(nx * 300 + 20, energy * .45)); g.addColorStop(1, color(nx * 300 + 30, 0));
        c.fillStyle = g; c.beginPath();
        c.moveTo(x - spread / 24 * thick, baseline); c.quadraticCurveTo(x - spread / 100 * thick, baseline - lift * .2, x + Math.sin(p + i) * spread / 90, baseline - lift);
        c.quadraticCurveTo(x + spread / 100 * thick, baseline - lift * .1, x + spread / 24 * thick, baseline);
        c.quadraticCurveTo(x, baseline + h * .13 * level, x - spread / 24 * thick, baseline); c.fill();
        for (let drop = 0; drop < 2; drop++) {
          const dy = lift * (.9 + drop * .25 + Math.sin(p * 2 + i * 1.7 + drop) * .08);
          c.beginPath(); c.arc(x + Math.sin(i * 3 + p + drop) * spread * .025, baseline - dy, Math.max(.45, level * h * .012 / (drop + 1)) * thick, 0, Math.PI * 2);
          c.fillStyle = color(nx * 300, energy * .7); c.fill();
        }
      }
    } else if (this.style === "rain") {
      const drops = Math.round(64 * density);
      for (let i = 0; i < drops; i++) {
        const nx = (i + .5) / drops, level = levels[i % 48];
        const x = nx * w, y = h * (.1 + ((i * .618 + p * .14) % .8)), length = h * (.08 + level * .3);
        const g = c.createLinearGradient(x, y - length, x, y);
        g.addColorStop(0, color(nx * 320, 0)); g.addColorStop(1, color(nx * 320, Math.min(.8, level * 1.4)));
        c.strokeStyle = g; c.lineWidth = Math.max(.7, Math.min(2, w / 500)) * thick; c.beginPath(); c.moveTo(x, y - length); c.lineTo(x, y); c.stroke();
        c.beginPath(); c.ellipse(x, y, Math.max(.5, level * Math.min(w, h) * .009) * thick, Math.max(1, level * h * .018) * thick, 0, 0, Math.PI * 2);
        c.fillStyle = color(nx * 320, Math.min(.85, level * 1.6)); c.fill();
      }
    } else if (this.style === "contour") {
      const size = Math.min(w * .48, h * .65), layers = Math.round(12 * density);
      const trace = (radius, reverse) => {
        for (let i = 0; i <= 96; i++) {
          const a = (reverse ? 96 - i : i) / 96 * Math.PI * 2;
          const band = levels[Math.floor((a / (Math.PI * 2)) * 47)];
          const r = radius * (1 + Math.sin(a * 3 + p * .5) * .15 + Math.cos(a * 5 - p) * .075 + band * .09);
          const x = w * .5 + Math.cos(a) * r * 1.2, y = h * .5 + Math.sin(a) * r * .85;
          i ? c.lineTo(x, y) : c.moveTo(x, y);
        }
        c.closePath();
      };
      for (let layer = layers; layer > 0; layer--) {
        c.beginPath(); trace(size * layer / layers, false); trace(size * Math.max(0, layer - .82 * thick) / layers, true);
        const hue = layer > 6 ? 12 + layer * 2 : 180 + layer * 10;
        c.fillStyle = color(hue, Math.min(.48, energy * 1.4)); c.fill("evenodd");
        c.strokeStyle = color(hue + 12, energy * .45); c.lineWidth = .7 * thick; c.stroke();
      }
    } else if (this.style === "aurora") {
      const curtains = Math.round(6 * density);
      for (let layer = 0; layer < curtains; layer++) {
        const gradient = c.createLinearGradient(0, h * .15, 0, h * .85);
        gradient.addColorStop(0, color(140 + layer * 24, 0));
        gradient.addColorStop(.55, color(140 + layer * 24, energy * .6));
        gradient.addColorStop(1, color(200 + layer * 18, 0));
        c.beginPath();
        for (let i = 0; i <= 96; i++) {
          const nx = i / 96, level = levels[Math.floor(nx * 47)];
          const y = h * (.4 + Math.sin(nx * 6 + p * .65 + layer * .6) * (.1 + level * .15));
          i ? c.lineTo(nx * w, y) : c.moveTo(0, y);
        }
        for (let i = 96; i >= 0; i--) {
          const nx = i / 96, level = levels[Math.floor(nx * 47)];
          c.lineTo(nx * w, h * (.4 + Math.sin(nx * 6 + p * .65 + layer * .6) * (.1 + level * .15) + (.12 + level * .2) * thick));
        }
        c.closePath(); c.fillStyle = gradient; c.fill();
      }
    } else if (this.style === "tunnel") {
      const rings = Math.round(18 * density), sides = 8;
      for (let ring = 0; ring < rings; ring++) {
        const depth = ((ring / rings + p * .06) % 1), radius = Math.pow(depth, 1.8) * Math.hypot(w, h) * .6;
        c.beginPath();
        for (let i = 0; i <= sides; i++) {
          const angle = i / sides * Math.PI * 2 + Math.sin(p * .3) * .25;
          const r = radius * (1 + levels[(i * 6) % 48] * .15);
          const x = w / 2 + Math.cos(angle) * r, y = h / 2 + Math.sin(angle) * r;
          i ? c.lineTo(x, y) : c.moveTo(x, y);
        }
        c.strokeStyle = color(185 + ring * 7, energy * Math.min(1, depth * 2)); c.lineWidth = (.5 + depth * 2) * thick; c.stroke();
      }
    } else if (this.style === "stars") {
      for (let i = 0; i < Math.round(100 * density); i++) {
        const seed = Math.sin(i * 127.1 + 311.7) * 43758.5453;
        const angle = i * 2.399963, depth = (seed - Math.floor(seed) + p * (.07 + levels[i % 48] * .04)) % 1;
        const radius = depth * depth * Math.hypot(w, h) * .6, previous = Math.max(0, depth - .02 - energy * .03);
        c.beginPath();
        c.moveTo(w / 2 + Math.cos(angle) * previous * previous * Math.hypot(w, h) * .6, h / 2 + Math.sin(angle) * previous * previous * Math.hypot(w, h) * .6);
        c.lineTo(w / 2 + Math.cos(angle) * radius, h / 2 + Math.sin(angle) * radius);
        c.strokeStyle = color(190 + i * 11, depth * Math.min(1, energy * 2)); c.lineWidth = (.4 + depth * 2) * thick; c.stroke();
      }
    } else if (this.style === "flower") {
      const petals = Math.round(7 * density), size = Math.min(w, h) * .36;
      for (let layer = 0; layer < 7; layer++) {
        c.beginPath();
        for (let i = 0; i <= 192; i++) {
          const angle = i / 192 * Math.PI * 2, level = levels[Math.floor(i / 192 * 47)];
          const r = size * (.24 + Math.abs(Math.sin(angle * petals / 2 + p * .5)) * (.4 + level * .5)) * (1 - layer * .09);
          const x = w / 2 + Math.cos(angle + p * .1) * r, y = h / 2 + Math.sin(angle + p * .1) * r;
          i ? c.lineTo(x, y) : c.moveTo(x, y);
        }
        c.closePath(); c.strokeStyle = color(165 + layer * 23, Math.min(.9, energy * 2)); c.lineWidth = (1 + energy) * thick; c.stroke();
      }
    } else {
      for (let i = 0; i < Math.round(96 * density); i++) {
        const level = levels[i % 48], seed = i * 2.399963;
        const x = (.5 + Math.sin(seed + p * (.15 + level)) * .45) * w;
        const y = (.5 + Math.cos(seed * 1.71 + p * .4) * .43) * h;
        const r = Math.max(.4, level * Math.min(w, h) * .025) * thick;
        c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2);
        c.fillStyle = color(175 + i * 4, Math.min(.85, level * 2)); c.fill();
      }
    }
    c.restore();
  }
};
