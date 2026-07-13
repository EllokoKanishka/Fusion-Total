const assert = require('node:assert/strict');
const test = require('node:test');

test('compact note labels prefer saved labels and meaningful words', async () => {
  const { compactNoteLabel } = await import('../fusion_reader_v2/web/static/js/notes.mjs');
  assert.equal(compactNoteLabel({ label: 'Idea central', text: 'ignored' }), 'Idea central');
  assert.equal(compactNoteLabel({ text: 'Nota sobre la memoria colectiva profunda' }), 'memoria colectiva profunda');
});

test('notes controller saves with one request and balances busy ownership', async () => {
  const { createNotesController } = await import('../fusion_reader_v2/web/static/js/notes.mjs');
  const calls = [];
  let releases = 0;
  const elements = {
    noteInput: { value: 'Mi nota' }, notesSummary: { textContent: '' }, notesInfo: { textContent: '' },
    notesList: { replaceChildren() {}, appendChild() {} }
  };
  const controller = createNotesController({
    elements, getStatus: () => ({ doc_id: 'book', current: 1 }), renderMainStatus: () => {},
    beginBusyLease: () => () => { releases += 1; }, busyControls: { setNoteText() {} }, log: () => {},
    api: async (path, payload) => { calls.push([path, payload]); return { items: [], note: { doc_id: 'book', chunk_number: 1 } }; },
    documentRoot: {}, prompt: () => null, confirm: () => false
  });
  await controller.save();
  assert.deepEqual(calls, [['/api/notes/create', { text: 'Mi nota' }]]);
  assert.equal(elements.noteInput.value, '');
  assert.equal(releases, 1);
});
