const assert = require('node:assert/strict');
const test = require('node:test');

function element() {
  return {
    disabled: false,
    textContent: '',
    value: 0,
    checked: false,
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    removeEventListener() {},
  };
}

function elements() {
  return {
    mediaInfo: element(),
    mediaProgress: element(),
    mediaTranscribeBtn: element(),
    mediaTranslateBtn: element(),
    mediaCancelBtn: element(),
    mediaMountBtn: element(),
    mediaOriginalPdfToggle: element(),
    mediaTranslatedPdfToggle: element(),
    mediaSpanishAudioToggle: element(),
    mediaPdfDownload: element(),
    mediaTranslatedPdfDownload: element(),
    mediaAudioDownload: element(),
  };
}

test('media polling recovers after a transient status failure', async () => {
  global.window = { setTimeout, clearTimeout, URL: { createObjectURL() {}, revokeObjectURL() {} } };
  global.document = { createElement() { return element(); }, body: { appendChild() {} } };
  let requests = 0;
  global.fetch = async () => {
    requests += 1;
    if (requests === 1) throw new Error('temporary_disconnect');
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          job_id: 'abc',
          operation: 'transcribe',
          state: 'done',
          detail: 'terminado',
          progress: 100,
          transcript_characters: 10,
          output: {},
        };
      },
    };
  };
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  const ui = elements();
  const controller = createMediaController({
    elements: ui,
    log() {},
    async refreshMainStatus() {},
    pollDelayMs: 1,
  });
  controller.render({ job_id: 'abc', operation: 'transcribe', state: 'running', progress: 10, output: {} });
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(requests, 2);
  assert.match(ui.mediaInfo.textContent, /terminado/);
  controller.dispose();
});

test('cancel during preflight prevents upload and never cancels the previous job', async () => {
  global.window = { setTimeout, clearTimeout };
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  for (const phase of ['request', 'body']) {
    let finishPreflight;
    let signal;
    const requests = [];
    const pending = new Promise(resolve => { finishPreflight = resolve; });
    global.fetch = async (url, options) => {
      requests.push(url);
      signal = options.signal;
      if (phase === 'request') await pending;
      return { ok: true, async json() { if (phase === 'body') await pending; return { ok: true }; } };
    };
    const ui = elements();
    const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });
    controller.render({ job_id: 'previous-job', state: 'done', output: {} });
    const file = new Blob(['audio']);
    file.name = 'conference.wav';
    const started = controller.start('transcribe', file);
    await Promise.resolve();
    await controller.cancel();
    await controller.cancel();
    assert.equal(signal.aborted, true);
    finishPreflight();
    await started;
    assert.equal(requests.length, 1);
    assert.match(requests[0], /capabilities/);
    assert.equal(ui.mediaInfo.textContent, 'Carga cancelada.');
    assert.equal(ui.mediaTranscribeBtn.disabled, false);
    controller.dispose();
  }
});

test('preflight and upload share cancellation ownership until the response body completes', async () => {
  global.window = { setTimeout, clearTimeout };
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  let uploaded;
  const reachedUpload = new Promise(resolve => { uploaded = resolve; });
  let finishBody;
  const pendingBody = new Promise(resolve => { finishBody = resolve; });
  const signals = [];
  global.fetch = async (url, options) => {
    signals.push(options.signal);
    if (url.includes('/capabilities')) return { ok: true, async json() { return { ok: true }; } };
    uploaded();
    return { ok: true, async json() { await pendingBody; return { ok: true, job_id: 'new', state: 'running' }; } };
  };
  const ui = elements();
  const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });
  const file = new Blob(['audio']);
  file.name = 'conference.wav';
  const started = controller.start('transcribe', file);
  await reachedUpload;
  assert.equal(signals[0], signals[1]);
  await controller.cancel();
  finishBody();
  await started;
  assert.equal(ui.mediaInfo.textContent, 'Carga cancelada.');
  assert.equal(signals.length, 2);
  controller.dispose();
});
