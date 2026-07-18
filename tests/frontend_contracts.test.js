const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const entry = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/app.js'), 'utf8');
const app = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/js/bootstrap.mjs'), 'utf8');
const preparation = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/js/preparation.mjs'), 'utf8');
const audioExport = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/js/audio_export.mjs'), 'utf8');
const notes = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/js/notes.mjs'), 'utf8');
const media = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/js/media.mjs'), 'utf8');
const frontend = `${app}\n${preparation}\n${audioExport}\n${notes}\n${media}`;
const html = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/index.html'), 'utf8');

test('frontend owns abortable reads and one export poller', () => {
  assert.match(entry, /import\('\.\/js\/bootstrap\.mjs'\)/);
  assert.match(app, /new AbortController\(\)/);
  assert.match(app, /activeReadController\.abort\(\)/);
  assert.match(audioExport, /pollingJobId !== data\.job_id/);
  assert.match(audioExport, /pollingJobId === jobId/);
});

test('frontend exposes reader, prepare, export, notes, dialogue and PDF actions', () => {
  for (const endpoint of [
    '/api/read',
    '/api/quick-text',
    '/api/next',
    '/api/document/clear',
    '/api/prepare/start',
    '/api/prepare/cancel',
    '/api/audio-export',
    '/api/notes/create',
    '/api/dialogue/turn',
    '/api/tools/pdf-to-docx',
    '/api/media/transcribe',
    '/api/media/translate',
  ]) {
    assert.ok(frontend.includes(endpoint), `missing endpoint ${endpoint}`);
  }
});

test('interactive controls have explicit semantics and live status regions', () => {
  assert.match(html, /id="dropzone"[^>]+tabindex="0"[^>]+role="button"[^>]+aria-label=/);
  assert.match(html, /id="chatLog"[^>]+aria-live="polite"/);
  assert.match(html, /id="voiceSelect"[^>]+aria-label="Voz"/);
  assert.match(html, /id="audioExportMode"[^>]+aria-label=/);
  assert.match(html, /id="quickTextInput"[^>]+aria-label="Texto rápido para leer"/);
  assert.match(html, /id="quickTextInfo"[^>]+aria-live="polite"/);
  assert.match(html, /id="mediaInfo"[^>]+aria-live="polite"/);
});

test('media downloads validate the response before saving a browser file', () => {
  assert.match(media, /if \(!response\.ok\)/);
  assert.match(media, /await response\.blob\(\)/);
  assert.match(media, /anchor\.download = element\.dataset\.downloadFilename/);
  assert.match(media, /No pude descargar el archivo:/);
});

test('frontend cleanup owns aborts, pollers, timers and media tracks', () => {
  assert.match(app, /window\.addEventListener\('beforeunload'/);
  assert.match(app, /activeReadController\.abort\(\)/);
  assert.match(app, /preparation\.dispose\(\)/);
  assert.match(app, /audioExport\.dispose\(\)/);
  assert.match(app, /mediaController\.dispose\(\)/);
  assert.match(app, /clearDialogueTimers\(dialogue\)/);
  assert.match(app, /getTracks\(\)\.forEach\(track => track\.stop\(\)\)/);
});

test('UI element collection is isolated and resolves each declared element once', async () => {
  const { collectElements, ELEMENT_IDS } = await import('../fusion_reader_v2/web/static/js/ui.mjs');
  const calls = [];
  const elements = collectElements({ getElementById(id) { calls.push(id); return { id }; } });
  assert.equal(new Set(ELEMENT_IDS).size, ELEMENT_IDS.length);
  assert.deepEqual(calls, ELEMENT_IDS);
  assert.equal(elements.dialogueBtn.id, 'dialogueBtn');
  assert.equal(elements.voiceSelect.id, 'voiceSelect');
});
