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
const dictation = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/js/dictation.mjs'), 'utf8');
const frontend = `${app}\n${preparation}\n${audioExport}\n${notes}\n${media}\n${dictation}`;
const html = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/index.html'), 'utf8');
const styles = fs.readFileSync(path.join(root, 'fusion_reader_v2/web/static/styles.css'), 'utf8');

function cssColor(variable) {
  const match = styles.match(new RegExp(`--${variable}:\\s*(#[0-9a-f]{6})`, 'i'));
  assert.ok(match, `missing CSS color --${variable}`);
  return match[1];
}

function relativeLuminance(color) {
  const channels = color.match(/[0-9a-f]{2}/gi).map(value => parseInt(value, 16) / 255);
  const linear = channels.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first, second) {
  const luminances = [relativeLuminance(first), relativeLuminance(second)].sort((a, b) => b - a);
  return (luminances[0] + 0.05) / (luminances[1] + 0.05);
}

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
    '/api/dictation/transcribe',
    '/api/dictation/speak',
    '/api/dictation/assistant',
    '/api/dictation/assistant/install',
    '/api/dictation/assistant/warm',
    '/api/dictation/assist',
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
  assert.match(html, /id="dictationToggleBtn"[^>]+aria-expanded="false"[^>]+aria-controls="dictationWorkspace"/);
  assert.match(html, /id="dictationEditor"[^>]+aria-label="Borrador de dictado"/);
  assert.match(html, /id="dictationVoiceSelect"[^>]+aria-label="Voz de lectura en dictado"/);
  assert.match(html, /id="dictationAssistantSelect"[^>]+aria-label="Asistente de escritura"/);
  assert.match(html, /id="dictationAssistantInstallBtn"[^>]+type="button"[^>]+hidden/);
  assert.match(html, /id="dictationStatus"[^>]+aria-live="polite"/);
});

test('destructive button text meets WCAG AA contrast in its resting state', () => {
  assert.ok(contrastRatio(cssColor('danger'), cssColor('surface-hover')) >= 4.5);
});

test('voice selectors distinguish the live provider catalog from the known fallback', () => {
  assert.match(app, /select\.dataset\.catalogSource/);
  assert.match(app, /Catálogo conocido de Fusion/);
  assert.match(app, /motor de voz todavía no está listo/);
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
  assert.match(app, /dictationController\.dispose\(\)/);
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
    assert.equal(elements.dictationVoiceSelect.id, 'dictationVoiceSelect');
    assert.equal(elements.dictationAssistantSelect.id, 'dictationAssistantSelect');
    assert.equal(elements.dictationEditor.id, 'dictationEditor');
});
