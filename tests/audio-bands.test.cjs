const test = require('node:test');
const assert = require('node:assert/strict');
const { musicFrequencyBands } = require('../ui/audio-bands.js');

test('20 dB change preserves a tenfold change in measured amplitude', () => {
  const quiet = musicFrequencyBands(new Float32Array(512).fill(-60));
  const loud = musicFrequencyBands(new Float32Array(512).fill(-40));
  assert.equal(quiet.length, 48);
  for (let i = 0; i < 48; i++) assert.ok(Math.abs(loud[i] / quiet[i] - 10) < 1e-5);
  assert.ok(loud.every(value => value < .2), 'ordinary dB levels must not saturate the effect');
});

test('bass and treble contribute to different logarithmic bands', () => {
  const bass = new Float32Array(512).fill(-Infinity); bass[2] = -30;
  const treble = new Float32Array(512).fill(-Infinity); treble[350] = -30;
  const low = musicFrequencyBands(bass), high = musicFrequencyBands(treble);
  const activeLow = low.map((x, i) => x > 0 ? i : -1).filter(i => i >= 0);
  const activeHigh = high.map((x, i) => x > 0 ? i : -1).filter(i => i >= 0);
  assert.ok(activeLow.length > 0 && activeHigh.length > 0);
  assert.ok(Math.max(...activeLow) < Math.min(...activeHigh));
});

test('silence, DC and invalid frequency values never create motion', () => {
  const data = new Float32Array(512).fill(-Infinity);
  data[0] = 0; data[25] = NaN; data[50] = Infinity;
  assert.deepEqual(musicFrequencyBands(data), Array(48).fill(0));
});
