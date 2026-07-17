const assert = require('node:assert/strict');
const test = require('node:test');

function elements(mode = 'current') {
  const classes = { toggle() {}, add() {}, remove() {} };
  return {
    audioExportMode: { value: mode }, audioExportBlockInput: { value: '2' },
    audioExportStartInput: { value: '3' }, audioExportEndInput: { value: '5' },
    audioExportBlockWrap: { classList: classes }, audioExportRangeWrap: { classList: classes },
    audioExportInfo: { textContent: '' },
    audioExportDownload: { classList: classes, removeAttribute() {}, href: '' }
  };
}

test('audio export controller sends one range request and balances busy ownership', async () => {
  const { createAudioExportController } = await import('../fusion_reader_v2/web/static/js/audio_export.mjs');
  const requests = [];
  let releases = 0;
  const controller = createAudioExportController({
    elements: elements('range'), getStatus: () => ({}), wait: async () => {}, log: () => {},
    beginBusyLease: () => () => { releases += 1; },
    api: async (path, payload) => { requests.push([path, payload]); return { state: 'done' }; }
  });
  await controller.start();
  assert.deepEqual(requests, [['/api/audio-export', { mode: 'range', start: 3, end: 5 }]]);
  assert.equal(releases, 1);
});

test('audio export controller does not cancel without an owned job', async () => {
  const { createAudioExportController } = await import('../fusion_reader_v2/web/static/js/audio_export.mjs');
  let requests = 0;
  const controller = createAudioExportController({
    elements: elements(), getStatus: () => ({}), wait: async () => {}, log: () => {},
    beginBusyLease: () => () => {}, api: async () => { requests += 1; return {}; }
  });
  await controller.cancel();
  assert.equal(requests, 0);
});
