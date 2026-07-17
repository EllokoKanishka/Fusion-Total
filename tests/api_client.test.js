const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const moduleUrl = pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/api.mjs')).href;

test('api performs one request and returns JSON', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (...args) => {
    calls.push(args);
    return { ok: true, status: 200, json: async () => ({ ok: true, value: 7 }) };
  };
  try {
    const { api } = await import(moduleUrl);
    assert.equal((await api('/api/test', { hello: 'world' })).value, 7);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][1].method, 'POST');
  } finally {
    global.fetch = originalFetch;
  }
});

test('api preserves aborts and normalizes network failures', async () => {
  const originalFetch = global.fetch;
  const { api, ApiError } = await import(moduleUrl);
  try {
    global.fetch = async () => { throw new DOMException('stopped', 'AbortError'); };
    await assert.rejects(api('/api/test'), { name: 'AbortError' });
    global.fetch = async () => { throw new Error('offline'); };
    await assert.rejects(api('/api/test'), (error) => error instanceof ApiError && error.message === 'network_error');
  } finally {
    global.fetch = originalFetch;
  }
});
