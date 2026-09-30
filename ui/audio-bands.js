"use strict";

// Both sources expose 48 logarithmic bands in linear amplitude. Web Audio's
// byte values are clipped dB, which flatten dynamics; read the unbounded float
// dB spectrum and convert it back to amplitude before applying the same gain.
function musicFrequencyBands(decibels) {
  if (!decibels?.length) return [];
  const end = decibels.length - 1;
  // Web Audio uses Blackman (coherent gain .42), loopback uses Hann (.5).
  const gain = 12 * .5 / .42;
  return Array.from({ length: 48 }, (_, band) => {
    const lo = Math.floor(Math.pow(end, band / 48));
    const hi = Math.max(lo + 1, Math.floor(Math.pow(end, (band + 1) / 48)));
    let peak = 0;
    for (let bin = lo; bin < hi && bin < decibels.length; bin++) {
      const db = decibels[bin];
      if (Number.isFinite(db)) peak = Math.max(peak, Math.pow(10, db / 20));
    }
    return Math.min(1, peak * gain);
  });
}

if (typeof module === "object" && module.exports) module.exports = { musicFrequencyBands };
