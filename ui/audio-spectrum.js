"use strict";

// All production motion is driven by measured bands, never a synthetic beat.
window.MusicSpectrum = class MusicSpectrum {
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
  }
  setOptions(options = {}) {
    if (["spectrum", "waves", "liquid", "ripple", "ribbon", "orbit", "particles", "mesh", "binary", "terrain", "silk", "splash", "rain", "contour"].includes(options.style)) this.style = options.style;
    if (Number.isFinite(Number(options.intensity))) this.intensity = Math.max(.2, Math.min(1, Number(options.intensity)));
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
    this.phase += elapsed * .001 * energy * 2;
    c.save();
    c.globalAlpha = this.intensity;
    c.lineJoin = "round";
    c.lineCap = "round";
    const p = this.phase, levels = this.levels;
    const color = (hue, alpha = 1) => `hsla(${hue},95%,65%,${alpha})`;
    if (this.style === "spectrum") {
      const step = w / levels.length;
      levels.forEach((level, i) => {
        const bh = level * h * .8;
        const g = c.createLinearGradient(0, h - bh, 0, h);
        g.addColorStop(0, color(175 + i * 3, Math.min(1, level * 2)));
        g.addColorStop(1, color(235 + i * 3, 0));
        c.fillStyle = g;
        c.fillRect(i * step + 1, h - bh, Math.max(1, step - 2), bh);
      });
    } else if (this.style === "waves" || this.style === "ribbon") {
      const ribbon = this.style === "ribbon";
      for (let layer = 0; layer < 5; layer++) {
        const points = [];
        for (let i = 0; i <= 96; i++) {
          const x = i / 96, level = levels[Math.min(47, Math.floor(x * 48))];
          const envelope = Math.sin(x * Math.PI);
          const y = h * .5 + Math.sin(x * Math.PI * (ribbon ? 9 : 4) + p * 2 + layer * .6) * (energy * .22 + level * .24) * h * envelope;
          points.push([x * w, y]);
        }
        c.beginPath(); points.forEach(([x, y], i) => i ? c.lineTo(x, y) : c.moveTo(x, y));
        c.lineWidth = ribbon ? Math.max(2, h * .025) : 1.3;
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
        const field = Math.sin(nx * 17 + Math.sin(ny * 9 + p) * 2 + p) + Math.cos(ny * 15 - Math.sin(nx * 8 - p) * 2);
        const edge = Math.pow(Math.max(0, 1 - Math.abs(field) * .75), 2);
        fieldContext.fillStyle = color(140 + field * 65 + band * 80, edge * Math.min(.8, energy * 2.5) * (.35 + band));
        fieldContext.fillRect(x, y, 1, 1);
      }
      c.imageSmoothingEnabled = true;
      c.imageSmoothingQuality = "high";
      c.drawImage(texture, 0, 0, w, h);
    } else if (this.style === "ripple") {
      const size = Math.hypot(w, h) * .55;
      for (let ring = 0; ring < 18; ring++) {
        const radius = (ring + 1) / 18 * size;
        c.beginPath();
        for (let i = 0; i <= 96; i++) {
          const angle = i / 96 * Math.PI * 2;
          const band = levels[Math.floor(i / 96 * 47)];
          const r = radius * (1 + Math.sin(angle * 7 + p * 2 - ring * .55) * band * .16);
          const x = w / 2 + Math.cos(angle) * r, y = h / 2 + Math.sin(angle) * r * .58;
          i ? c.lineTo(x, y) : c.moveTo(x, y);
        }
        c.strokeStyle = color(180 + ring * 8, Math.min(.7, energy * 2) * (1 - ring / 22));
        c.lineWidth = 1 + levels[ring * 2] * 4; c.stroke();
      }
    } else if (this.style === "orbit") {
      const r = Math.min(w, h) * .22;
      levels.forEach((level, i) => {
        const angle = i / 48 * Math.PI * 2 + p * .25;
        c.beginPath();
        c.moveTo(w / 2 + Math.cos(angle) * r, h / 2 + Math.sin(angle) * r);
        c.lineTo(w / 2 + Math.cos(angle) * (r + level * h * .25), h / 2 + Math.sin(angle) * (r + level * h * .25));
        c.strokeStyle = color(170 + i * 4, Math.min(.9, level * 2)); c.lineWidth = Math.max(1, Math.min(w, h) / 90); c.stroke();
      });
    } else if (this.style === "mesh" || this.style === "terrain") {
      const mesh = this.style === "mesh", cols = mesh ? 28 : 52, rows = mesh ? 18 : 26;
      const point = (x, z) => {
        const depth = (z + 1) / rows, spread = .12 + depth * .92;
        const nx = x / cols, band = levels[Math.min(47, Math.floor(nx * 47))];
        const wave = Math.sin(nx * 10 + z * .34 + p * 1.5) * (band * .14 + energy * .1);
        return [w * (.5 + (nx - .5) * spread), h * (.32 + depth * .59 - wave * Math.pow(depth, .65))];
      };
      if (mesh) {
        c.lineWidth = .7;
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
        c.beginPath(); c.arc(q[0], q[1], Math.max(.4, (z / rows + .2) * (mesh ? 1.1 : 1.65) * (.5 + band)), 0, Math.PI * 2);
        c.fillStyle = color(mesh ? 180 : 40 + Math.sin(x / cols * 8 + z * .3 + p) * 165, Math.min(.85, energy * (mesh ? 1.6 : 2.2))); c.fill();
      }
    } else if (this.style === "binary") {
      const cols = Math.min(72, Math.max(20, Math.floor(w / 13))), rows = 9;
      const fontSize = Math.max(7, Math.min(15, w / cols * .72));
      c.font = `${fontSize}px ui-monospace, Consolas, monospace`; c.textAlign = "center";
      for (let row = 0; row < rows; row++) for (let x = 0; x < cols; x++) {
        const nx = x / (cols - 1), band = levels[Math.floor(nx * 47)];
        const y = h * .5 + Math.sin(nx * 7 + p * 2 + row * .34) * h * (.08 + band * .2) + (row - 4) * fontSize * .8;
        c.fillStyle = color(180 + nx * 125, Math.min(.85, energy * 1.8) * (1 - Math.abs(row - 4) / 6));
        c.fillText((x * 7 + row * 3 + Math.floor(p * 3)) % 5 < 2 ? "1" : "0", nx * w, y);
      }
    } else if (this.style === "silk") {
      for (let layer = 0; layer < 7; layer++) {
        const yAt = (nx, side) => {
          const band = levels[Math.floor(nx * 47)];
          const curve = Math.sin(nx * 7 + p + layer * .42) * (.1 + band * .2);
          const fold = Math.sin(nx * 15 - p * 1.3 + layer) * energy * .045;
          return h * (.5 + curve + fold + side * (.025 + band * .05) * Math.sin(nx * Math.PI));
        };
        c.beginPath();
        for (let i = 0; i <= 96; i++) { const nx = i / 96; i ? c.lineTo(nx * w, yAt(nx, 1)) : c.moveTo(0, yAt(0, 1)); }
        for (let i = 96; i >= 0; i--) c.lineTo(i / 96 * w, yAt(i / 96, -1));
        c.closePath();
        const g = c.createLinearGradient(0, 0, w, h);
        g.addColorStop(0, color(155 + layer * 28, energy * .16)); g.addColorStop(.5, color(195 + layer * 23, energy * .75)); g.addColorStop(1, color(300 + layer * 9, energy * .16));
        c.fillStyle = g; c.fill(); c.strokeStyle = color(170 + layer * 26, energy * .55); c.lineWidth = .6; c.stroke();
      }
    } else if (this.style === "splash") {
      const center = w * .5, baseline = h * .69, spread = Math.min(w * .43, h * 1.4);
      for (let i = 0; i < 36; i++) {
        const nx = i / 35, level = levels[Math.floor(nx * 47)], x = center + (nx - .5) * spread * 2;
        const lift = h * level * (.28 + .22 * Math.sin(nx * Math.PI));
        const g = c.createLinearGradient(x, baseline - lift, x, baseline + h * .13);
        g.addColorStop(0, color(nx * 300, Math.min(.8, energy * 2))); g.addColorStop(.75, color(nx * 300 + 20, energy * .45)); g.addColorStop(1, color(nx * 300 + 30, 0));
        c.fillStyle = g; c.beginPath();
        c.moveTo(x - spread / 24, baseline); c.quadraticCurveTo(x - spread / 100, baseline - lift * .2, x + Math.sin(p + i) * spread / 90, baseline - lift);
        c.quadraticCurveTo(x + spread / 100, baseline - lift * .1, x + spread / 24, baseline);
        c.quadraticCurveTo(x, baseline + h * .13 * level, x - spread / 24, baseline); c.fill();
        for (let drop = 0; drop < 2; drop++) {
          const dy = lift * (.9 + drop * .25 + Math.sin(p * 2 + i * 1.7 + drop) * .08);
          c.beginPath(); c.arc(x + Math.sin(i * 3 + p + drop) * spread * .025, baseline - dy, Math.max(.45, level * h * .012 / (drop + 1)), 0, Math.PI * 2);
          c.fillStyle = color(nx * 300, energy * .7); c.fill();
        }
      }
    } else if (this.style === "rain") {
      for (let i = 0; i < 64; i++) {
        const nx = (i + .5) / 64, level = levels[i % 48];
        const x = nx * w, y = h * (.1 + ((i * .618 + p * .14) % .8)), length = h * (.08 + level * .3);
        const g = c.createLinearGradient(x, y - length, x, y);
        g.addColorStop(0, color(nx * 320, 0)); g.addColorStop(1, color(nx * 320, Math.min(.8, level * 1.4)));
        c.strokeStyle = g; c.lineWidth = Math.max(.7, Math.min(2, w / 500)); c.beginPath(); c.moveTo(x, y - length); c.lineTo(x, y); c.stroke();
        c.beginPath(); c.ellipse(x, y, Math.max(.5, level * Math.min(w, h) * .009), Math.max(1, level * h * .018), 0, 0, Math.PI * 2);
        c.fillStyle = color(nx * 320, Math.min(.85, level * 1.6)); c.fill();
      }
    } else if (this.style === "contour") {
      const size = Math.min(w * .48, h * .65), layers = 12;
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
        c.beginPath(); trace(size * layer / layers, false); trace(size * (layer - .82) / layers, true);
        const hue = layer > 6 ? 12 + layer * 2 : 180 + layer * 10;
        c.fillStyle = color(hue, Math.min(.48, energy * 1.4)); c.fill("evenodd");
        c.strokeStyle = color(hue + 12, energy * .45); c.lineWidth = .7; c.stroke();
      }
    } else {
      for (let i = 0; i < 96; i++) {
        const level = levels[i % 48], seed = i * 2.399963;
        const x = (.5 + Math.sin(seed + p * (.15 + level)) * .45) * w;
        const y = (.5 + Math.cos(seed * 1.71 + p * .4) * .43) * h;
        const r = Math.max(.4, level * Math.min(w, h) * .025);
        c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2);
        c.fillStyle = color(175 + i * 4, Math.min(.85, level * 2)); c.fill();
      }
    }
    c.restore();
  }
};
