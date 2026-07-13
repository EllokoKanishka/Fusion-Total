const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const moduleUrl = pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/dialogue.mjs')).href;

test('dialogue state instances are isolated and timers have an owner', async () => {
  const { createDialogueState, clearDialogueTimers } = await import(moduleUrl);
  const first = createDialogueState();
  const second = createDialogueState();
  first.pcmChunks.push(new Float32Array([1]));
  first.monitorId = 4;
  first.finalizeTimeoutId = 7;
  const cleared = [];
  clearDialogueTimers(first, id => cleared.push(['timeout', id]), id => cleared.push(['frame', id]));
  assert.equal(second.pcmChunks.length, 0);
  assert.deepEqual(cleared, [['timeout', 7], ['frame', 4]]);
  assert.equal(first.monitorId, 0);
  assert.equal(first.finalizeTimeoutId, 0);
});
