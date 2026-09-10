const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.join(__dirname, '..', 'fusion_reader_v2', 'web', 'static');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const bootstrap = fs.readFileSync(path.join(root, 'js', 'bootstrap.mjs'), 'utf8');
const notes = fs.readFileSync(path.join(root, 'js', 'notes.mjs'), 'utf8');
const dictation = fs.readFileSync(path.join(root, 'js', 'dictation.mjs'), 'utf8');
const dialog = fs.readFileSync(path.join(root, 'js', 'panda_dialog.mjs'), 'utf8');

test('Panda dialog replaces browser-native confirmations across reader workspaces', () => {
  assert.match(html, /id="pandaDialog"/);
  assert.match(html, /panda-fusion-emblem\.webp/);
  assert.match(html, /panda-princess-dialog\.png/);
  assert.match(html, /panda-dialog-hand-sign/);
  assert.match(html, /id="pandaDialogAccept"/);
  assert.match(dialog, /const mode = acceptsText \? 'prompt' : \(cancelLabel \? 'confirm' : 'notice'\)/);
  assert.match(dialog, /root\.dataset\.mode = mode/);
  assert.match(dialog, /mode === 'confirm' \? 'Sí' : acceptLabel/);
  assert.match(dialog, /export function createPandaDialog/);
  assert.match(bootstrap, /createPandaDialog\(\)/);
  assert.match(bootstrap, /pandaDialog\.confirm/);
  assert.match(notes, /dialog\.prompt/);
  assert.match(notes, /dialog\.confirm/);
  assert.match(dictation, /dialog\.confirm/);
  assert.doesNotMatch(bootstrap, /\balert\(/);
});
