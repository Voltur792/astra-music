const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const source = fs.readFileSync('ui/player-v12.js', 'utf8');
const registration = source.slice(source.indexOf('// Register on the persistent'), source.indexOf('const visualSettingsTimer'));
test('hardware media commands use shared queue controls and tolerate unsupported actions', () => {
  const handlers = {}, calls = [];
  const context = vm.createContext({navigator: {mediaSession: {setActionHandler(key, handler) {
    if (key === 'stop') throw new Error('Unsupported');
    handlers[key] = handler;
  }}}, control: action => calls.push(action)});
  vm.runInContext(registration, context);
  for (const key of ['nexttrack', 'previoustrack', 'play', 'pause']) handlers[key]();
  assert.deepEqual(calls, ['next', 'previous', 'play', 'pause']);
  const cleanup = source.match(/for \(const key of registeredMediaActions\)[^\n]+/)[0];
  vm.runInContext(cleanup, context);
  assert.ok(Object.values(handlers).every(handler => handler === null));
  vm.runInContext(registration, vm.createContext({navigator: {}, control() {}}));
});
