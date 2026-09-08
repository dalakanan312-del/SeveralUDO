/* Lightweight form-token regression checks; no browser or live tracker. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const events = {};
const location = {href: 'http://tracker.test/p/saves', origin: 'http://tracker.test'};
const main = {dataset: {dynastyEpoch: 'selected-branch'}};
const form = (action, ownEpoch, method = 'post') => ({
  action, method, dataset: ownEpoch === undefined ? {} : {dynastyEpoch: ownEpoch},
  getAttribute(name) { return name === 'action' ? this.action : null; },
});
const forms = [
  form('/infinite/selected/toggle', 'selected-branch'),
  form('/infinite/another/toggle', 'other-branch'),
  form('/infinite/ordinary/toggle', ''),
  form('/api/rolls/save'),
  form('/search', undefined, 'get'),
  form('https://elsewhere.test/post'),
];
const fetches = [];
const context = {
  URL, Request, Headers, location,
  document: {
    querySelector: () => main,
    querySelectorAll: () => forms,
    addEventListener: (name, handler) => { events[name] = handler; },
  },
  window: {fetch: (...args) => { fetches.push(args); return Promise.resolve(); }},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/static/infinite-decades.js'), 'utf8'), context);
const token = row => new URL(row.action, location.href).searchParams.get('_dynasty_epoch');
assert.equal(token(forms[0]), 'selected-branch');
assert.equal(token(forms[1]), 'other-branch');
assert.equal(token(forms[2]), null);
assert.equal(token(forms[3]), 'selected-branch');
assert.equal(forms[4].action, '/search');
assert.equal(forms[5].action, 'https://elsewhere.test/post');
main.dataset.dynastyEpoch = 'changed-selected-branch';
events['htmx:afterSwap']();
events.submit();
assert.equal(token(forms[1]), 'other-branch');
assert.equal(token(forms[2]), null);
assert.equal(token(forms[3]), 'changed-selected-branch');
assert.equal((forms[1].action.match(/_dynasty_epoch/g) || []).length, 1);
context.window.fetch('/api/rolls/save', {method: 'POST'});
assert.equal(fetches[0][1].headers.get('X-Dynasty-Epoch'), 'changed-selected-branch');
console.log('Per-save form tokens: passed (independent saves, normal mode, stale-page protection).');
