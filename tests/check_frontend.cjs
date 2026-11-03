const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    textContent: '', disabled: false, open: false,
    classList: {add(){}, remove(){}, toggle(){}}, addEventListener(){},
    setAttribute(){}, append(){}, remove(){},
    showModal(){this.open = true;}, close(){this.open = false;},
  });
  return elements.get(id);
}
let calls = [];
let fail = false;
const context = vm.createContext({
  document: {addEventListener(){}, getElementById: element, createElement: () => element(Symbol())},
  setTimeout: () => 1, clearTimeout(){},
  localStorage: {getItem: () => null, removeItem(){}},
  fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: !fail, status: fail ? 403 : 200, headers: {get: () => 'application/json'},
      json: async () => fail ? {detail: 'Forbidden'} : {deleted_ids: ['a','b'], deleted_count: 2}};
  }, assert,
});
vm.runInContext(fs.readFileSync('tests/frontend.js', 'utf8'), context);
(async () => {
  vm.runInContext("tokenPayload = {role: 'admin'}; activeView = 'generate'; cachedData.schedules = [{id:'a'},{id:'b'},{id:'c'}]; openScheduleDeleteModal(['a','b']);", context);
  assert(element('delete-schedule-dialog').open);
  await vm.runInContext('Promise.all([confirmScheduleDelete(), confirmScheduleDelete()])', context);
  assert.equal(calls.length, 1, 'Double clicks must not issue duplicate requests');
  assert.equal(calls[0].url, '/api/v1/schedules/batch');
  assert.deepEqual(JSON.parse(calls[0].options.body), {schedule_ids:['a','b']});
  assert.equal(vm.runInContext('cachedData.schedules.length', context), 1);
  assert(!element('delete-schedule-dialog').open);
  fail = true;
  vm.runInContext("openScheduleDeleteModal(['c'])", context);
  assert.equal(element('delete-schedule-message').textContent, 'Are you sure you want to delete schedule №c?');
  await vm.runInContext('confirmScheduleDelete()', context);
  assert.equal(calls[1].url, '/api/v1/schedules/c');
  assert(element('delete-schedule-dialog').open);
  assert.equal(element('delete-schedule-error').textContent, 'Forbidden');
  assert.equal(vm.runInContext('cachedData.schedules.length', context), 1);
  assert(!element('confirm-schedule-delete').disabled);
  console.log('Frontend checks passed: batch/single fetch, double-click guard, cache update, error retention and retry controls.');
})().catch(error => {console.error(error); process.exitCode = 1;});
