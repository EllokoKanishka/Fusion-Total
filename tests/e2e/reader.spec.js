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

test('voice-first reader daily flow uses one request per action', async ({ page }) => {
  const requestCounts = new Map();
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) {
      requestCounts.set(url.pathname, (requestCounts.get(url.pathname) || 0) + 1);
    }
  });

  await page.goto(baseURL);
  await expect(page.locator('#docTitle')).toContainText('Ningún documento activo');
  await expect(page.locator('#mediaToolsTitle')).toContainText('Audio y video');
  await expect(page.locator('#mediaTranscribeBtn')).toHaveCount(0);
  await expect(page.locator('#mediaTranslateBtn')).toBeVisible();
  await expect(page.locator('#mediaOriginalPdfToggle')).toBeChecked();
  await expect(page.locator('#mediaTranslatedPdfToggle')).toBeChecked();
  await expect(page.locator('#mediaSpanishAudioToggle')).toBeChecked();

  await page.locator('#dictationToggleBtn').click();
  await expect(page.locator('#dictationWorkspace')).toBeVisible();
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
  await page.locator('#dictationEditor').fill('Primer párrafo.\n\nSegundo párrafo para la lectora.');
  await page.locator('#dictationCommandInput').fill('reemplazá lectora por voz');
  await page.locator('#dictationCommandBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('Primer párrafo.\n\nSegundo párrafo para la voz.');
  await page.locator('#dictationUndoBtn').click();
  await expect(page.locator('#dictationEditor')).toHaveValue('Primer párrafo.\n\nSegundo párrafo para la lectora.');
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
  await expect.poll(() => requestCounts.get('/api/quick-text') || 0).toBe(quickTextBefore + 1);
  await page.locator('#quickClearBtn').click();
  await expect(page.locator('#docTitle')).toContainText('Ningún documento activo');

  await page.locator('#fileInput').setInputFiles({
    name: 'e2e.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Primer bloque para lectura.\n\nSegundo bloque para navegar y tomar notas.'),
  });
  await expect(page.locator('#docTitle')).toContainText('e2e.txt');

  const readsBefore = requestCounts.get('/api/read') || 0;
  await page.locator('#readBtn').click();
  await expect.poll(() => requestCounts.get('/api/read') || 0).toBe(readsBefore + 1);

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

  page.once('dialog', dialog => dialog.accept());
  await page.locator('#clearDocBtn').click();
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
