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
