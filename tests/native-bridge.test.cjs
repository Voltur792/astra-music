const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

for (const [page, audioOwner] of [['player-v12.html', true], ['desktop-widget.html', false]]) {
  test(`${page} only reports audio health when it owns playback`, async () => {
    const requests = [], timers = [], cleanup = [];
    const context = vm.createContext({
      window: {MUSIC_PLAYER_READY: audioOwner, addEventListener: (_, listener) => cleanup.push(listener)},
      location: {pathname: `/private/${page}`},
      fetch: async (url, options) => {
        requests.push({url, ...JSON.parse(options.body)});
        return {ok: true, json: async () => ({ok: true})};
      },
      setInterval: (callback, delay) => {timers.push({callback, delay}); return 7;},
      clearInterval: id => assert.equal(id, 7),
    });
    vm.runInContext(fs.readFileSync('ui/native-bridge.js', 'utf8'), context);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(context.window.MUSIC_AUDIO_HOST, audioOwner);
    assert.equal(requests.length, audioOwner ? 1 : 0);
    assert.equal(timers.length, audioOwner ? 1 : 0);
    if (audioOwner) {
      assert.equal(requests[0].method, 'music_audio_host_ping');
      assert.equal(requests[0].url, '/private/api');
      assert.equal(timers[0].delay, 1000);
      timers[0].callback();
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(requests.length, 2);
      cleanup[0]();
    }
  });
}

test('a missing player script reloads twice and cannot report healthy playback', async () => {
  const requests = [], timers = [], events = {}, storage = new Map();
  let reloads = 0;
  const context = vm.createContext({
    window: {addEventListener: (name, callback) => events[name] = callback},
    location: {pathname: '/private/player-v12.html', reload: () => reloads++},
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    fetch: async (_, options) => {requests.push(JSON.parse(options.body)); return {ok: true, json: async () => ({ok: true})};},
    setInterval: callback => {timers.push(callback); return 1;}, clearInterval() {},
    setTimeout: callback => callback(),
  });
  vm.runInContext(fs.readFileSync('ui/native-bridge.js', 'utf8'), context);
  timers[0]();
  assert.equal(requests.length, 0);
  events.load(); events.load(); events.load();
  assert.equal(reloads, 2);
  context.window.MUSIC_PLAYER_READY = true;
  events.load(); timers[0]();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(storage.size, 0);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, 'music_audio_host_ping');
});
