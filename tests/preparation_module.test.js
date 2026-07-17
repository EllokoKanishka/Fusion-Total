const assert = require('node:assert/strict');
const test = require('node:test');

test('preparation controller balances busy ownership and renders completion', async () => {
  const { createPreparationController } = await import('../fusion_reader_v2/web/static/js/preparation.mjs');
  const calls = [];
  let releases = 0;
  const elements = { prepareProgress: { value: 0 }, prepareInfo: { textContent: '' } };
  const controller = createPreparationController({
    elements,
    beginBusyLease: () => () => { releases += 1; },
    wait: async () => {},
    log: message => calls.push(['log', message]),
    api: async (path, payload) => {
      calls.push([path, payload]);
      if (path.endsWith('/start')) return { status: 'running', percent: 10, total: 2 };
      return { status: 'done', percent: 100, total: 2, cached: 1, generated: 1 };
    }
  });
  await controller.start();
  assert.equal(releases, 1);
  assert.equal(elements.prepareProgress.value, 100);
  assert.match(elements.prepareInfo.textContent, /Documento listo/);
  assert.equal(calls.filter(([path]) => path === '/api/prepare/start').length, 1);
});

test('preparation controller cancellation issues exactly one request', async () => {
  const { createPreparationController } = await import('../fusion_reader_v2/web/static/js/preparation.mjs');
  let requests = 0;
  const controller = createPreparationController({
    elements: { prepareProgress: { value: 0 }, prepareInfo: { textContent: '' } },
    beginBusyLease: () => () => {},
    wait: async () => {},
    log: () => {},
    api: async () => { requests += 1; return { status: 'canceled', percent: 0 }; }
  });
  await controller.cancel();
  assert.equal(requests, 1);
});
