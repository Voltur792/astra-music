const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('native music sampling survives missing rendering frames and mode changes', async () => {
  const intervals = [], samples = [], nodes = new Map();
  let now = 10000, closed = 0, frames = 0;
  const node = () => ({
    classList: { add() {}, remove() {}, toggle() {} }, style: {},
    querySelector: () => node(), prepend() {}, setAttribute() {},
    removeAttribute() {}, addEventListener() {}, load() {},
    paused: true, ended: false, currentTime: 0, duration: 30, volume: 0,
    captureStream: () => ({ getAudioTracks: () => [{}] }),
  });
  const audio = node(); nodes.set('#audio', audio);
  class AudioContext {
    state = 'running';
    createMediaStreamSource() { return { connect() {} }; }
    createAnalyser() { return { frequencyBinCount: 512, getFloatFrequencyData: data => data.fill(-32) }; }
    resume() { return Promise.resolve(); }
    close() { closed++; return Promise.resolve(); }
  }
  const context = vm.createContext({
    document: { querySelector: name => {
      if (!nodes.has(name)) nodes.set(name, node());
      return nodes.get(name);
    }, createElement: node },
    window: { MUSIC_AUDIO_HOST: true, AudioContext, addEventListener() {}, astra: {
      callBackend: async (method, args) => {
        if (method === 'music_visualizer_sample') samples.push(args);
        return {};
      },
    } },
    MusicSpectrum: class { clear() {} setOptions() {} sample() {} },
    Date: { now: () => now }, Float32Array, console,
    setInterval: (callback, period) => { intervals.push({ callback, period }); return intervals.length; },
    clearInterval() {}, requestAnimationFrame: () => { frames++; return 1; }, cancelAnimationFrame() {},
  });
  vm.runInContext(fs.readFileSync('ui/audio-bands.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('ui/player-v12.js', 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext('current = { revision: 8 }; audio.paused = false; visualSettings.mode = "all"; setVisualPlaying(true)', context);
  const tick = intervals.find(item => item.period === 40).callback;
  tick(); assert.equal(samples.length, 0);
  vm.runInContext('visualSettings.mode = "music"', context);
  tick(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(samples.length, 1);
  assert.equal(samples[0].revision, 8);
  assert.equal(samples[0].bands.length, 48);
  assert.ok(samples[0].bands.every(value => value > .3 && value < .4));
  assert.equal(frames, 0, 'the invisible player must not rely on animation frames');
  vm.runInContext('visualSettings.mode = "off"', context);
  now += 2000; tick(); assert.equal(closed, 1);
  vm.runInContext('visualSettings.mode = "music"', context);
  tick(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(samples.length, 2);
  vm.runInContext('audio.paused = true', context);
  now += 2000; tick(); assert.equal(samples.length, 2);
  assert.equal(closed, 2);
});
