const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Evaluate the actual bootstrap function with controlled I/O; no copied implementation.
const source = fs.readFileSync(path.join(__dirname, '../fusion_reader_v2/web/static/js/bootstrap.mjs'), 'utf8');
const start = source.indexOf('async function pollImportJob(');
const end = source.indexOf('\nasync function loadFile(', start);
assert.ok(start >= 0 && end > start);

function polling(states) {
  let calls = 0;
  const context = {
    wait: async () => {},
    api: async () => {
      assert.ok(calls < states.length, 'polling must stop at a terminal state');
      return states[calls++];
    },
    setImportProgress: () => {}, log: () => {},
    els: { uploadInfo: { textContent: '' } },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  return { run: () => context.pollImportJob('fixture-job'), calls: () => calls };
}

test('import polling stops when cancellation becomes terminal', async () => {
  const poll = polling([{ status: 'canceling' }, { status: 'cancelled', message: 'Importación cancelada.' }]);
  await assert.rejects(poll.run(), /Importación cancelada/);
  assert.equal(poll.calls(), 2);
});

test('import polling returns the published result on success', async () => {
  const result = { doc_id: 'accepted' };
  const poll = polling([{ status: 'running' }, { status: 'done', result }]);
  assert.equal(await poll.run(), result);
  assert.equal(poll.calls(), 2);
});

test('import polling reports conversion errors', async () => {
  const poll = polling([{ status: 'error', error: 'conversion_failed' }]);
  await assert.rejects(poll.run(), /conversion_failed/);
  assert.equal(poll.calls(), 1);
});
