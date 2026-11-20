const { spawn } = require('node:child_process');
const { test, expect } = require('@playwright/test');

let server;
let baseURL;

test.beforeAll(async () => {
  server = spawn(process.env.PYTHON || 'python3', ['-m', 'tests.e2e.synthetic_server'], {
    cwd: process.cwd(),
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  baseURL = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('synthetic server startup timeout')), 15000);
    server.once('exit', code => reject(new Error(`synthetic server exited with ${code}`)));
    server.stdout.on('data', chunk => {
      const match = String(chunk).match(/READY (\d+)/);
      if (match) {
        clearTimeout(timeout);
        resolve(`http://127.0.0.1:${match[1]}`);
      }
    });
  });
});

test.afterAll(async () => {
  if (!server || server.exitCode !== null) return;
  server.kill('SIGTERM');
  await new Promise(resolve => server.once('exit', resolve));
});

test('external research permission applies to one submitted chat turn', async ({ page }) => {
  const consent = page.locator('#allowExternalResearch');
  await page.goto(baseURL);
  await expect(consent).not.toBeChecked();
  await consent.check();
  for (const allowed of [true, false]) {
    await page.locator('#chatInput').fill('Buscá fuentes sobre el concepto de técnica.');
    const responsePromise = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/chat');
    await page.locator('#sendChatBtn').click();
    const response = await responsePromise;
    expect(response.request().postDataJSON().allow_external_research).toBe(allowed);
    if (!allowed) await expect(page.locator('#chatLog')).toContainText('Activá «Buscar en internet»');
    await expect(consent).not.toBeChecked();
  }
});

test('voice-first reader daily flow uses one request per action', async ({ page }) => {
  const requestCounts = new Map();
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) {
      requestCounts.set(url.pathname, (requestCounts.get(url.pathname) || 0) + 1);
    }
  });

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(baseURL);
  await expect(page.locator('#docTitle')).toContainText('Ningún documento activo');
  await expect(page.locator('#mediaToolsTitle')).toContainText('Audio y video');
  await expect(page.locator('#mediaTranscribeBtn')).toBeVisible();
  await expect(page.locator('#mediaTranslateBtn')).toBeVisible();
  await expect(page.locator('#mediaOriginalPdfToggle')).not.toBeChecked();
  await expect(page.locator('#mediaTranslatedPdfToggle')).toBeChecked();
  await expect(page.locator('#mediaSpanishAudioToggle')).not.toBeChecked();

  await page.locator('#dictationToggleBtn').click();
  await expect(page.locator('#dictationWorkspace')).toBeVisible();
  await expect(page.locator('#dictationVoiceSelect')).toBeVisible();
  await expect(page.locator('#dictationVoiceSelect')).toHaveValue(await page.locator('#voiceSelect').inputValue());
  await expect(page.locator('#dictationAssistantSelect')).toBeVisible();
  await expect(page.locator('#dictationAssistantSelect option')).toHaveCount(4);
  await expect(page.locator('#dictationAssistantSelect')).toHaveValue('rules');
  await expect(page.locator('.left-sidebar')).toBeHidden();
  await expect(page.locator('main')).toBeHidden();
  await expect(page.locator('.lab')).toBeHidden();
  await expect(page.locator('.right-sidebar')).toBeHidden();
  const dictationBox = await page.locator('#dictationWorkspace').boundingBox();
  const viewport = page.viewportSize();
  expect(dictationBox).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(dictationBox.width).toBeGreaterThanOrEqual(viewport.width - 1);
  expect(dictationBox.height).toBeGreaterThanOrEqual(viewport.height - 1);
  await page.locator('#dictationEditor').fill('La casa es azul, la casa es verde y la pradera es azul.');
  const confirmationsBefore = requestCounts.get('/api/dictation/speak') || 0;
  await page.locator('#dictationCommandInput').fill('Lucy, cambia verde por azul, azul por verde');
  await page.locator('#dictationCommandBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('La casa es verde, la casa es azul y la pradera es verde.');
  await expect.poll(() => requestCounts.get('/api/dictation/speak') || 0).toBe(confirmationsBefore + 1);
  await expect(page.locator('#log')).toContainText('Lectura terminada (confirmación)');
  await page.locator('#dictationUndoBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('La casa es azul, la casa es verde y la pradera es azul.');

  await page.locator('#dictationEditor').fill('La casa es azul. La pradera es verde.');
  const deleteConfirmation = page.waitForRequest(request =>
    new URL(request.url()).pathname === '/api/dictation/speak' &&
    request.postDataJSON().text === 'Listo. Borré la frase.');
  await page.locator('#dictationCommandInput').fill('Lucy, borrá toda la frase');
  await page.locator('#dictationCommandBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('La casa es azul.');
  await deleteConfirmation;
  await expect.poll(() => page.locator('#dictationPlayer').evaluate(player => player.ended)).toBe(true);
  await page.locator('#dictationUndoBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('La casa es azul. La pradera es verde.');

  await page.locator('#dictationEditor').fill('Primer párrafo.\n\nSegundo párrafo para la lectora.');
  await page.locator('#dictationCommandInput').fill('reemplazá lectora por voz');
  await page.locator('#dictationCommandBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('Primer párrafo.\n\nSegundo párrafo para la voz.');
  await page.locator('#dictationUndoBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('Primer párrafo.\n\nSegundo párrafo para la lectora.');
  await page.locator('#dictationAssistantSelect').selectOption('local');
  await expect(page.locator('#dictationAssistantInstallBtn')).toBeVisible();
  await page.locator('#dictationAssistantInstallBtn').click();
  await expect(page.locator('#pandaDialog')).toBeVisible();
  await page.locator('#pandaDialogAccept').click();
  await expect(page.locator('#dictationAssistantStatus')).toContainText('local · se prepara al decir Lucy');
  await expect(page.locator('#dictationAssistantInstallBtn')).toBeHidden();
  await page.locator('#dictationEditor').fill('La filosofia es una práctica.');
  const semanticConfirmation = page.waitForRequest(request =>
    new URL(request.url()).pathname === '/api/dictation/speak' &&
    request.postDataJSON().text === 'Listo. Apliqué el cambio.');
  await page.locator('#dictationCommandInput').fill('Lucy, agregale la tilde a filosofia');
  await page.locator('#dictationCommandBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('La filosofía es una práctica.');
  await semanticConfirmation;
  await expect.poll(() => page.locator('#dictationPlayer').evaluate(player => player.ended)).toBe(true);
  await page.locator('#dictationUndoBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('La filosofia es una práctica.');
  await page.locator('#dictationEditor').fill('Una tarde en Buenos Aires. Lo sé.');
  await page.locator('#dictationCommandInput').fill('borrá Bonos Aires Loces');
  const assistantBefore = requestCounts.get('/api/dictation/assist') || 0;
  await page.locator('#dictationCommandBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('Una tarde en ');
  await expect.poll(() => requestCounts.get('/api/dictation/assist') || 0).toBe(assistantBefore + 1);
  await page.locator('#dictationUndoBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('Una tarde en Buenos Aires. Lo sé.');
  await page.locator('#dictationEditor').selectText();
  const dictationSpeechBefore = requestCounts.get('/api/dictation/speak') || 0;
  await page.locator('#dictationReadBtn').click();
  await expect.poll(() => requestCounts.get('/api/dictation/speak') || 0).toBe(dictationSpeechBefore + 1);
  await expect(page.locator('#log')).toContainText('Lectura terminada');
  const dictationReaderBefore = requestCounts.get('/api/quick-text') || 0;
  await page.locator('#dictationUseReaderBtn').click();
  await expect(page.locator('#docTitle')).toContainText('Dictado sin título');
  await expect.poll(() => requestCounts.get('/api/quick-text') || 0).toBe(dictationReaderBefore + 1);
  await page.locator('#dictationCloseBtn').click();
  await expect(page.locator('#dictationWorkspace')).toBeHidden();
  await expect(page.locator('.left-sidebar')).toBeVisible();
  await expect(page.locator('main')).toBeVisible();
  await expect(page.locator('.lab')).toBeVisible();
  await expect(page.locator('.right-sidebar')).toBeVisible();

  await page.locator('#quickTextInput').fill('Fragmento temporal para leer sin crear un archivo.');
  const quickTextBefore = requestCounts.get('/api/quick-text') || 0;
  await page.locator('#quickReadStartBtn').click();
  await expect(page.locator('#docTitle')).toContainText('Texto rápido — Texto pegado');
  await expect(page.locator('#quickTextInfo')).toContainText('temporal');
  const titleBox = await page.locator('.title').boundingBox();
  const readerBox = await page.locator('.reader').boundingBox();
  expect(titleBox.width).toBeGreaterThanOrEqual(240);
  expect(titleBox.height).toBeLessThan(100);
  expect(readerBox.height).toBeGreaterThan(50);
  await page.screenshot({ path: test.info().outputPath('browser-layout-1280.png') });
  await expect.poll(() => requestCounts.get('/api/quick-text') || 0).toBe(quickTextBefore + 1);
  await page.locator('#quickClearBtn').click();
  await expect(page.locator('#docTitle')).toContainText('Ningún documento activo');

  await page.locator('#fileInput').setInputFiles({
    name: 'e2e.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Primer bloque para lectura.\n\nSegundo bloque para navegar y tomar notas.'),
  });
  await expect(page.locator('#docTitle')).toContainText('e2e.txt');

  await page.route('**/api/read/timings', route => route.fulfill({
    json: { ok: true, state: 'ready', coverage: 1, words: [
      { start: 0, end: 0.05, start_offset: 0, end_offset: 6 },
      { start: 0.05, end: 0.1, start_offset: 7, end_offset: 13 }
    ] }
  }));
  const highlightColor = page.locator('#readingHighlightColor');
  const player = page.locator('#player');
  await highlightColor.selectOption('off');
  const timingsBefore = requestCounts.get('/api/read/timings') || 0;
  const readsBefore = requestCounts.get('/api/read') || 0;
  const audioResponse = page.waitForResponse(response => new URL(response.url()).pathname.startsWith('/audio/'));
  await page.locator('#readBtn').click();
  await audioResponse;
  await expect.poll(() => requestCounts.get('/api/read') || 0).toBe(readsBefore + 1);
  expect(requestCounts.get('/api/read/timings') || 0).toBe(timingsBefore);

  await player.evaluate(audio => { audio.pause(); audio.currentTime = 0; });
  await highlightColor.selectOption('green');
  await expect(page.locator('.reader-word.is-current')).toHaveText('Primer');
  await player.evaluate(audio => { audio.currentTime = 0.07; });
  await expect(page.locator('.reader-word.is-current')).toHaveText('bloque');
  await expect(page.locator('.reader-word.is-read')).toHaveCount(2);
  await player.evaluate(audio => { audio.currentTime = 0; });
  await expect(page.locator('.reader-word.is-read')).toHaveCount(1);
  await highlightColor.selectOption('off');
  await expect(page.locator('.reader-word.is-read')).toHaveCount(0);
  await highlightColor.selectOption('green');

  const nextBefore = requestCounts.get('/api/next') || 0;
  await page.locator('#nextBtn').click();
  await expect.poll(() => requestCounts.get('/api/next') || 0).toBe(nextBefore + 1);

  await page.locator('#noteInput').fill('Nota E2E');
  await page.locator('#saveNoteBtn').click();
  await expect(page.locator('#notesList')).toContainText('Nota E2E');

  await page.locator('#voiceSelect').selectOption({ index: 0 });
  await page.locator('#prepareBtn').click();
  await expect.poll(() => requestCounts.get('/api/prepare/start') || 0).toBe(1);
  await page.locator('#cancelPrepareBtn').click();
  await expect.poll(() => requestCounts.get('/api/prepare/cancel') || 0).toBe(1);

  await page.locator('#audioExportBtn').click();
  await expect.poll(() => requestCounts.get('/api/audio-export') || 0).toBe(1);

  await page.locator('#chatInput').fill('Resume el bloque actual');
  await page.locator('#sendChatBtn').click();
  await expect(page.locator('#chatLog')).toContainText('Entendido.');

  await page.locator('#clearDocBtn').click();
  await expect(page.locator('#pandaDialog')).toBeVisible();
  await page.locator('#pandaDialogAccept').click();
  await expect(page.locator('#docTitle')).toContainText('Ningún documento activo');
  await page.locator('#fileInput').setInputFiles({
    name: 'nuevo.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Nuevo\n\nDocumento posterior al clear.'),
  });
  await expect(page.locator('#docTitle')).toContainText('nuevo.md');

  await expect(page.locator('#dropzone')).toHaveAttribute('role', 'button');
  await expect(page.locator('#chatLog')).toHaveAttribute('aria-live', 'polite');
});

async function checkCapturedEdit(page, sample) {
  await page.addInitScript(() => {
    window.__lucyRecorders = [];
    const Recorder = window.MediaRecorder;
    window.MediaRecorder = class extends Recorder {
      constructor(...args) { super(...args); window.__lucyRecorders.push(this); }
    };
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext();
      await context.resume();
      const destination = context.createMediaStreamDestination();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.frequency.value = 220;
      gain.gain.setValueAtTime(0.2, context.currentTime);
      gain.gain.setValueAtTime(0, context.currentTime + 1);
      oscillator.connect(gain).connect(destination);
      oscillator.start();
      // The fixture owns a synthesized input; it never opens the physical microphone.
      window.__lucyInputContext = context;
      return destination.stream;
    };
  });
  let transcriptions = 0;
  let confirmations = 0;
  let assistantRequests = 0;
  let spoken;
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/dictation/assist') assistantRequests++;
  });
  await page.route('**/api/dictation/transcribe?*', async route => {
    transcriptions++;
    expect((route.request().postDataBuffer() || Buffer.alloc(0)).length).toBeGreaterThan(900);
    const response = await page.request.post(`${baseURL}/api/dictation/interpret`, {
      data: { text: sample.command, commands_enabled: true, require_wake_word: true },
    });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(await response.json()) });
  });
  if (sample.review) {
    await page.route('**/api/dictation/review', async route => {
      expect(route.request().postDataJSON()).toEqual({
        text: sample.original, requests: ['lee el texto y mejoralo.'], previous: '', provider: 'rules'
      });
      await route.fulfill({ json: { ok: true, text: sample.expected, note: '' } });
    });
  }
  await page.route('**/api/dictation/speak', async route => {
    confirmations++;
    spoken = route.request().postDataJSON().text;
    expect(await page.evaluate(() => window.__lucyRecorders.every(recorder => recorder.state !== 'recording'))).toBe(true);
    await route.continue();
  });
  await page.goto(baseURL);
  await page.locator('#dictationToggleBtn').click();
  await expect(page.locator('#dictationModeSelect')).toHaveValue('direct');
  if (sample.review) await page.locator('#dictationModeSelect').selectOption('review');
  const original = sample.original ?? 'La casa es azul, la casa es verde y la pradera es azul.';
  await page.locator('#dictationEditor').fill(original);
  if (sample.reopen) {
    await page.locator('#dictationEditor').evaluate(editor => editor.setSelectionRange(0, 0));
    await page.locator('#dictationCloseBtn').click();
    await page.reload();
    await page.locator('#dictationToggleBtn').click();
    await expect(page.locator('#dictationEditor')).toHaveValue(original);
    await expect.poll(() => page.locator('#dictationEditor').evaluate(editor => editor.selectionStart)).toBe(0);
    const providerSelected = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/dictation/assistant' && response.request().method() === 'POST');
    await page.locator('#dictationAssistantSelect').selectOption('openai');
    expect((await (await providerSelected).json()).selected).toBe('openai');
  }
  await page.locator('#dictationMicBtn').click();
  if (sample.review) {
    await expect(page.locator('#dictationReviewPanel')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#dictationReviewProposed')).toHaveText(sample.expected);
    await expect(page.locator('#dictationEditor')).toHaveValue(original);
  } else {
    await expect(page.locator('#dictationEditor')).toHaveValue(sample.expected, { timeout: 15000 });
  }
  await expect.poll(() => page.locator('#dictationPlayer').evaluate(player => player.ended)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.__lucyRecorders.at(-1)?.state)).toBe('recording');
  expect(transcriptions).toBe(1);
  expect(confirmations).toBe(1);
  expect(assistantRequests).toBe(0);
  expect(spoken).toBe(sample.confirmation);
  await page.locator('#dictationMicBtn').click();
  await page.evaluate(() => window.__lucyInputContext.close());
  if (sample.review) {
    await page.locator('#dictationReviewApplyBtn').click();
    await expect(page.locator('#dictationEditor')).toHaveValue(sample.expected);
    await expect(page.locator('#dictationReviewPanel')).toBeHidden();
  }
  await page.locator('#dictationUndoBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue(original);
}

test('Lucy edits captured dictation, suspends recording for confirmation and resumes', async ({ page }) => {
  await checkCapturedEdit(page, {
    command: 'Lucy, cambia verde por azul, azul por verde',
    expected: 'La casa es verde, la casa es azul y la pradera es verde.',
    confirmation: 'Listo. Apliqué 3 sustituciones.',
  });
});

test('Lucy clears the restored draft by voice and can undo it', async ({ page }) => {
  await checkCapturedEdit(page, {
    command: 'Lucy, borrá todo el texto escrito.',
    original: 'Un párrafo guardado antes de cerrar el dictado.\n'.repeat(300),
    expected: '',
    confirmation: 'Listo. Limpié el borrador.',
    reopen: true,
  });
});

test('Lucy proposes a prose review from captured dictation before changing the draft', async ({ page }) => {
  await checkCapturedEdit(page, {
    command: 'Lucy, lee el texto y mejoralo.',
    original: 'La casa era blanco blanco y tenía un patio con plantas.',
    expected: 'La casa era blanca y tenía un patio con plantas.',
    confirmation: 'Propuesta lista.',
    review: true,
  });
});

test('keyboard cancellation of a destructive dictation dialog preserves the draft', async ({ page }) => {
  await page.goto(baseURL);
  await page.locator('#dictationToggleBtn').click();
  await page.locator('#dictationEditor').fill('Este borrador debe conservarse.');
  await page.locator('#dictationClearBtn').click();
  await expect(page.locator('#pandaDialogCancel')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#pandaDialog')).toBeHidden();
  await expect(page.locator('#dictationClearBtn')).toBeFocused();
  await expect(page.locator('#dictationEditor')).toHaveValue('Este borrador debe conservarse.');
  await page.locator('#dictationClearBtn').click();
  await page.keyboard.press('Tab');
  await expect(page.locator('#pandaDialogAccept')).toBeFocused();
  await page.keyboard.press('Space');
  await expect(page.locator('#dictationEditor')).toHaveValue('');
});
