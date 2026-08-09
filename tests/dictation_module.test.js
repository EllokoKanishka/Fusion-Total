const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const { pathToFileURL } = require('node:url');

const moduleUrl = pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/dictation.mjs')).href;

function editor(value, start = value.length, end = start) {
  return {
    value,
    selectionStart: start,
    selectionEnd: end,
    setRangeText(replacement, from, to) {
      this.value = `${this.value.slice(0, from)}${replacement}${this.value.slice(to)}`;
      this.selectionStart = from + replacement.length;
      this.selectionEnd = this.selectionStart;
    }
  };
}

test('dictation inserts at the caret and preserves natural spacing', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const target = editor('El tiempo', 9);
  const result = applyEditorInstruction(target, { kind: 'dictate', text: 'es una sustancia extraña.' });
  assert.equal(result.changed, true);
  assert.equal(target.value, 'El tiempo es una sustancia extraña.');
});

test('corrections prefer the latest occurrence before the caret', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const target = editor('rosa y rosa', 11);
  applyEditorInstruction(target, { kind: 'replace', target: 'rosa', text: 'jazmín' });
  assert.equal(target.value, 'rosa y jazmín');
});

test('read scopes resolve paragraphs, last virtual page and text anchors', async () => {
  const { readTextForInstruction } = await import(moduleUrl);
  const target = editor('Primero.\n\nSegundo jardín.\n\nTercero.', 4);
  assert.equal(readTextForInstruction(target, { scope: 'paragraph_number', number: 2 }).text, 'Segundo jardín.');
  assert.equal(readTextForInstruction(target, { scope: 'from_text', target: 'jardín' }).text, 'jardín.\n\nTercero.');
  assert.match(readTextForInstruction(target, { scope: 'last_page' }, 10).text, /Tercero/);
});

test('speech text is chunked without dropping long sentences', async () => {
  const { splitSpeechText } = await import(moduleUrl);
  const input = Array.from({ length: 90 }, (_, index) => `palabra${index}`).join(' ');
  const chunks = splitSpeechText(input, 180);
  assert.ok(chunks.length > 1);
  assert.equal(chunks.join(' '), input);
  assert.ok(chunks.every(chunk => chunk.length <= 180));
});

test('delete from removes an anchored tail and tolerates punctuation differences', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const target = editor('Una tarde en Buenos Aires. Lo sé.', 32);
  const result = applyEditorInstruction(target, { kind: 'delete_from', target: 'Buenos.Aires' });
  assert.equal(result.changed, true);
  assert.equal(target.value, 'Una tarde en ');
});

test('selection rewrites never spill outside the selected range', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const target = editor('Antes. Párrafo torpe. Después.', 7, 21);
  const result = applyEditorInstruction(target, { kind: 'replace_selection', text: 'Párrafo limpio.' });
  assert.equal(result.changed, true);
  assert.equal(target.value, 'Antes. Párrafo limpio. Después.');
});

test('assistant context is bounded around the caret', async () => {
  const { dictationAssistantContext } = await import(moduleUrl);
  const target = editor('a'.repeat(20000), 15000, 15010);
  const context = dictationAssistantContext(target, 12000);
  assert.equal(context.draft.length, 12000);
  assert.equal(context.draft.slice(context.selection_start, context.selection_end), 'a'.repeat(10));
});

test('bare Lucy arms one following utterance instead of becoming an empty command', async () => {
  const { createWakeCommandGate, isBareLucyInvocation } = await import(moduleUrl);
  let now = 1000;
  const gate = createWakeCommandGate({ now: () => now, ttlMs: 20000 });

  assert.equal(isBareLucyInvocation(' Lucy… '), true);
  assert.equal(isBareLucyInvocation('Lucy, borrá el final'), false);
  gate.arm();
  gate.hold();
  now += 60000;
  assert.equal(gate.isArmed(), true);
  assert.equal(gate.claim(), true);
  assert.equal(gate.command('borrá desde Buenos Aires'), 'Lucy, borrá desde Buenos Aires');
  assert.equal(gate.claim(), false);

  gate.arm();
  now += 20001;
  assert.equal(gate.isArmed(), false);
});

test('an armed utterance uses the wake-only interpretation contract', async () => {
  const { invokedInterpretationPayload } = await import(moduleUrl);
  assert.deepEqual(invokedInterpretationPayload('borrá las últimas 20 palabras'), {
    text: 'Lucy, borrá las últimas 20 palabras',
    commands_enabled: true,
    require_wake_word: true
  });
});

test('delete last words removes only the requested tail', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const target = editor('uno dos tres cuatro cinco', 25);
  const result = applyEditorInstruction(target, { kind: 'delete_last_words', number: 3 });
  assert.equal(result.changed, true);
  assert.equal(target.value, 'uno dos ');
});

test('replace last words changes only the requested tail', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const target = editor('uno dos tres cuatro cinco', 25);
  const result = applyEditorInstruction(target, {
    kind: 'replace_last_words',
    number: 3,
    text: 'un final distinto'
  });
  assert.equal(result.changed, true);
  assert.equal(target.value, 'uno dos un final distinto');
});

test('assistant failure activity does not repeat the unchanged-text notice', async () => {
  const { assistantFailureActivity } = await import(moduleUrl);
  const message = assistantFailureActivity({
    data: {
      detail: 'El asistente no pudo interpretar la orden; no cambié el texto.',
      technical_detail: 'assistant_invalid_json',
      assistant_model: 'gpt-5-nano',
      assistant_ms: 120
    }
  });
  assert.equal((message.match(/no cambi[eé] el texto/gi) || []).length, 1);
  assert.match(message, /assistant_invalid_json/);
});
