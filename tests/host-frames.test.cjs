const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function surface(kind, result, hasWallpaper = false) {
  const listeners = new Map(), replies = [], globalCss = [];
  const node = () => {
    const values = new Map();
    return {style: {getPropertyValue: key => values.get(key) || '', getPropertyPriority: () => '',
      setProperty: (key, value) => values.set(key, value), removeProperty: key => values.delete(key)},
      isConnected: true, contains(other) {return this.child === other || this.child?.contains?.(other) || false;},
      setAttribute() {}, append(child) {this.child = child; child.parentElement = this;}, remove() {}};
  };
  const root = node();
  const wallpaper = hasWallpaper ? node() : null;
  if (wallpaper) {
    wallpaper.getBoundingClientRect = () => ({left: 0, top: 0, right: 1200, bottom: 800});
    wallpaper.querySelector = () => null;
  }
  const window = {addEventListener: (name, listener) => listeners.set(name, listener), removeEventListener() {}};
  const context = vm.createContext({window, document: {
    querySelectorAll: () => wallpaper ? [wallpaper] : [],
    createElement(name) {
      const element = node();
      if (name === 'iframe') element.contentWindow = {postMessage: data => replies.push(data)};
      return element;
    },
    documentElement: {dataset: {theme: 'dark'}, style: {setProperty: (...args) => globalCss.push(args)}},
  }, innerWidth: 1200, innerHeight: 800,
    getComputedStyle: () => ({backgroundImage: 'linear-gradient(blue, black)', position: 'fixed', visibility: 'visible', display: 'block'}),
    crypto: {randomUUID: () => 'test-channel'}, MutationObserver: class {observe() {} disconnect() {}}});
  vm.runInContext(fs.readFileSync('ui/host-frames.js', 'utf8'), context);
  window.MusicHostFrames.mount(root, {callBackend: async () => result}, 'test.html', kind);
  const source = (wallpaper || root).child.child.contentWindow;
  return {root, wallpaper, replies, globalCss, send: data => listeners.get('message')({source, data: {
    channel: 'test-channel', type: 'request', id: 1, ...data,
  }})};
}

test('backend success is unwrapped across direct and SDK JSON envelopes', async () => {
  const success = {success: true, error: ''};
  const encoded = JSON.stringify(success);
  for (const response of [success, encoded, {result_json: encoded}, JSON.stringify({result_json: encoded})]) {
    const frame = surface('page', response);
    await frame.send({operation: 'backend', method: 'music_support_open'});
    assert.equal(frame.replies[0].result.success, true);
    assert.equal(frame.replies[0].result.error, '');
  }
});

test('visualizer paints inside opaque wallpaper instead of beneath the whole app', () => {
  const frame = surface('visualizer', {}, true);
  assert.equal(frame.root.child, undefined);
  assert.equal(frame.wallpaper.child.style.position, 'absolute');
  assert.equal(frame.wallpaper.child.style.zIndex, '1');
  assert.deepEqual(frame.globalCss, []);
});

test('visualizer cannot change global stacking or surface blur', async () => {
  const frame = surface('visualizer', {playing: true});
  assert.equal(frame.root.style.getPropertyValue('z-index'), '');
  assert.equal(frame.root.child.style.zIndex, '1');
  await frame.send({operation: 'css', key: '--z-behind', value: '2'});
  await frame.send({operation: 'css', key: '--blur-surface', value: 'none'});
  assert.deepEqual(frame.globalCss, []);
  assert.equal(frame.replies.length, 0);
  await frame.send({operation: 'backend', method: 'music_visualizer_state'});
  assert.equal(frame.replies[0].result.playing, true);
});
