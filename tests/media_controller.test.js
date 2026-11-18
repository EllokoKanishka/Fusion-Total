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
    mediaSttPromptInput: element(),
    mediaSttHotwordsInput: element(),
    mediaPostCorrectionToggle: element(),
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


test('optional ASR post-correction is scoped to the selected upload and participates in preflight', async () => {
  global.window = { setTimeout, clearTimeout };
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url.includes('/capabilities')) return { ok: true, async json() { return { ok: true }; } };
    return { ok: true, async json() { return { ok: true, job_id: 'corrected', state: 'running', output: {} }; } };
  };
  const ui = elements();
  ui.mediaPostCorrectionToggle.checked = true;
  const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });
  const file = new Blob(['audio']);
  file.name = 'foundation.wav';
  await controller.start('transcribe', file);
  assert.equal(requests.length, 2);
  assert.equal(new URL(requests[0].url, 'http://local').searchParams.get('post_correct'), '1');
  assert.equal(new URL(requests[1].url, 'http://local').searchParams.get('post_correct'), '1');
  assert.equal(ui.mediaPostCorrectionToggle.disabled, true);
  controller.render({ job_id: 'corrected', state: 'done', output: {} });
  assert.equal(ui.mediaPostCorrectionToggle.disabled, false);
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

test('STT context is attached only to the media upload request and bounded', async () => {
  global.window = { setTimeout, clearTimeout };
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url.includes('/capabilities')) return { ok: true, async json() { return { ok: true }; } };
    return { ok: true, async json() { return { ok: true, job_id: 'ctx', state: 'running', output: {} }; } };
  };
  const ui = elements();
  ui.mediaSttPromptInput.value = '  Audiolibro de Fundación\n de Isaac Asimov.  ';
  ui.mediaSttHotwordsInput.value = `Hari Seldon, Gaal Dornick, ${'x'.repeat(3000)}`;
  const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });
  const file = new Blob(['audio']);
  file.name = 'foundation.wav';
  await controller.start('transcribe', file);

  assert.equal(requests.length, 2);
  assert.doesNotMatch(requests[0].url, /stt_prompt|stt_hotwords/);
  const upload = new URL(requests[1].url, 'http://local');
  assert.equal(upload.searchParams.get('stt_prompt'), 'Audiolibro de Fundación de Isaac Asimov.');
  assert.match(upload.searchParams.get('stt_hotwords'), /^Hari Seldon, Gaal Dornick, x+/);
  assert.equal(upload.searchParams.get('stt_hotwords').length, 2400);
  assert.equal(ui.mediaSttPromptInput.disabled, true);
  assert.equal(ui.mediaSttHotwordsInput.disabled, true);
  assert.equal(ui.mediaSttPromptInput.value, '');
  assert.equal(ui.mediaSttHotwordsInput.value, '');
  controller.render({ job_id: 'ctx', state: 'done', output: {} });
  assert.equal(ui.mediaSttPromptInput.disabled, false);
  assert.equal(ui.mediaSttHotwordsInput.disabled, false);
  await controller.start('transcribe', file);
  const second = new URL(requests[3].url, 'http://local');
  assert.equal(second.searchParams.has('stt_prompt'), false);
  assert.equal(second.searchParams.has('stt_hotwords'), false);
  controller.dispose();
});

test('STT hints survive failed upload and are consumed only after acceptance', async () => {
  global.window = { setTimeout, clearTimeout };
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  for (const failure of ['preflight', 'upload', 'cancel']) {
    const ui = elements();
    ui.mediaSttPromptInput.value = 'Kant y Hegel';
    ui.mediaSttHotwordsInput.value = 'Aufhebung';
    const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });
    global.fetch = async url => {
      if (failure === 'cancel') { await controller.cancel(); return { ok: true, async json() { return {}; } }; }
      if (url.includes('/capabilities') && failure !== 'preflight') return { ok: true, async json() { return { ok: true }; } };
      return { ok: false, async json() { return { ok: false, error: 'fixture_failure' }; } };
    };
    const file = new Blob(['audio']); file.name = 'filosofia.wav';
    if (failure === 'cancel') await controller.start('transcribe', file);
    else await assert.rejects(controller.start('transcribe', file), /fixture_failure/);
    assert.equal(ui.mediaSttPromptInput.value, 'Kant y Hegel');
    assert.equal(ui.mediaSttHotwordsInput.value, 'Aufhebung');
    controller.dispose();
  }
});

test('accepted upload uses captured hints and preserves newly edited hints for the next file', async () => {
  global.window = { setTimeout, clearTimeout };
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  const ui = elements();
  ui.mediaSttPromptInput.value = 'Kant';
  ui.mediaSttHotwordsInput.value = 'Crítica';
  ui.mediaOriginalPdfToggle.checked = true;
  const requests = [];
  global.fetch = async url => {
    requests.push(url);
    if (url.includes('/capabilities')) {
      ui.mediaSttPromptInput.value = 'Hegel';
      ui.mediaSttHotwordsInput.value = 'Aufhebung';
      return { ok: true, async json() { return { ok: true }; } };
    }
    return { ok: true, async json() { return { ok: true, job_id: 'translated', state: 'done', output: {} }; } };
  };
  const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });
  const file = new Blob(['audio']); file.name = 'kant.wav';
  await controller.start('translate', file);
  const request = new URL(requests[1], 'http://local');
  assert.equal(request.searchParams.get('stt_prompt'), 'Kant');
  assert.equal(request.searchParams.get('stt_hotwords'), 'Crítica');
  assert.equal(ui.mediaSttPromptInput.value, 'Hegel');
  assert.equal(ui.mediaSttHotwordsInput.value, 'Aufhebung');
  controller.dispose();
});

test('artifact downloads stream through the browser without fetching a page Blob', async () => {
  const handlers = new Map();
  let anchor;
  let clicked = false;
  global.window = { setTimeout, clearTimeout };
  global.fetch = async () => { throw new Error('download must not fetch a Blob'); };
  global.document = {
    body: { appendChild() {} },
    createElement() { anchor = { click() { clicked = true; }, remove() {} }; return anchor; },
  };
  const ui = elements();
  ui.mediaAudioDownload.addEventListener = (name, handler) => handlers.set(name, handler);
  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');
  const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });
  ui.mediaAudioDownload.dataset.downloadUrl = '/api/media/artifacts/owned/audio';
  ui.mediaAudioDownload.dataset.downloadFilename = 'owned.wav';
  handlers.get('click')({ preventDefault() {} });
  await Promise.resolve();
  assert.equal(clicked, true);
  assert.equal(anchor.href, '/api/media/artifacts/owned/audio');
  assert.equal(anchor.download, 'owned.wav');
  controller.dispose();
});

test('laboratory renders imported markup as literal text without an HTML sink', async () => {
  function node(tag = '#text', text = '') {
    return { tag, textContent: text, children: [],
      set innerHTML(_value) { throw new Error('HTML parsing is forbidden'); },
      replaceChildren(...items) { this.children = items; },
      appendChild(item) { this.children.push(item); },
    };
  }
  global.document = { createElement: tag => node(tag), createTextNode: text => node('#text', text) };
  const { renderLaboratoryFocus } = await import('../fusion_reader_v2/web/static/js/lab_focus.mjs');
  const target = node('div');
  renderLaboratoryFocus(target, { title: '<img src=x onerror=alert(1)>', query: '<script>bad()</script>', text: '<svg onload=bad()>' });
  const text = target.children.map(child => child.textContent).join(' ');
  assert.match(text, /<img src=x onerror=alert\(1\)>/);
  assert.match(text, /<script>bad\(\)<\/script>/);
  assert.equal(target.children.some(child => ['img', 'script', 'svg'].includes(child.tag)), false);
});
