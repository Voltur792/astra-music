const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(writeText, fallback) {
  const source = fs.readFileSync('ui/script.js', 'utf8');
  const match = source.match(/async function copyTokenLink\(input, button\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'copy helper exists');
  const context = vm.createContext({navigator: {clipboard: {writeText}}, document: {execCommand: fallback}});
  vm.runInContext(match[0], context);
  return context.copyTokenLink;
}

test('copies the exact OAuth URL through clipboard', async () => {
  let copied;
  const copy = setup(async value => { copied = value; });
  const button = {};
  await copy({value: 'https://example.test/?a=1&b=2'}, button);
  assert.equal(copied, 'https://example.test/?a=1&b=2');
  assert.equal(button.textContent, 'Ссылка скопирована');
});

test('selects the URL and tries legacy copy when clipboard is denied', async () => {
  const actions = [];
  const copy = setup(async () => { throw new Error('denied'); }, command => {actions.push(command); return false;});
  const button = {};
  await copy({value: 'url', focus: () => actions.push('focus'), select: () => actions.push('select')}, button);
  assert.deepEqual(actions, ['focus', 'select', 'copy']);
  assert.equal(button.textContent, 'Нажмите Ctrl+C');
});
