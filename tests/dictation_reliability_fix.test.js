const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const { pathToFileURL } = require('node:url');

const moduleUrl = pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/dictation.mjs')).href;

test('armed wake payload never duplicates Lucy when STT repeats the wake word', async () => {
  const { invokedInterpretationPayload, hasLucyInvocation } = await import(moduleUrl);
  assert.equal(hasLucyInvocation('Lucy, corregí el texto'), true);
  assert.equal(hasLucyInvocation('Lúci, corregí el texto'), true);
  assert.equal(hasLucyInvocation('corregí el texto'), false);
  assert.deepEqual(invokedInterpretationPayload('Lucy, corregí el texto'), {
    text: 'Lucy, corregí el texto',
    commands_enabled: true,
    require_wake_word: true
  });
  assert.deepEqual(invokedInterpretationPayload('corregí el texto'), {
    text: 'Lucy, corregí el texto',
    commands_enabled: true,
    require_wake_word: true
  });
});
