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
