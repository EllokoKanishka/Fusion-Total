const assert = require('node:assert/strict');
const test = require('node:test');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const moduleUrl = pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/dictation.mjs')).href;
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

class Element {
  constructor() {
    this.value = ''; this.selectionStart = 0; this.selectionEnd = 0; this.textContent = '';
    this.dataset = {}; this.listeners = new Map(); this.children = []; this.options = []; this.hidden = false;
    const classes = new Set();
    this.classList = { add: x => classes.add(x), remove: x => classes.delete(x), toggle: (x, on) => on ? classes.add(x) : classes.delete(x) };
  }
  addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(fn); }
  removeEventListener(name, fn) { this.listeners.get(name)?.delete(fn); }
  emit(name, event = {}) { return Promise.all([...(this.listeners.get(name) || [])].map(fn => fn({ target: this, preventDefault() {}, ...event }))); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); if (item.tag === 'option') this.options.push(item); }
  replaceChildren() { this.children = []; }
  setRangeText(text, start, end) { this.value = this.value.slice(0, start) + text + this.value.slice(end); this.selectionStart = start + text.length; this.selectionEnd = this.selectionStart; }
  focus() {} remove() {} click() { this.clicked = true; } pause() {}
  play() { this.playCalls = (this.playCalls || 0) + 1; return Promise.resolve(); }
}

async function fixture({ api, fetchFn, storage, dialog, getUserMedia, ContextOverride } = {}) {
  const { createDictationController, STORAGE_KEY } = await import(moduleUrl);
  const elements = new Proxy({}, { get(target, name) { return target[name] ||= new Element(); } });
  elements.dictationCommandsToggle.checked = true;
  elements.dictationWorkspace.hidden = true;
  const saved = { schemaVersion: 2, activeId: 'A', sessions: ['A', 'B'].map(id => ({ id, title: id, text: 'Texto original', selectionStart: 14, selectionEnd: 14, createdAt: 1, updatedAt: 1 })) };
  const values = new Map([[STORAGE_KEY, JSON.stringify(saved)]]);
  const memory = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  const timers = new Map(); const frames = new Map(); let timerId = 0; let now = 0; let loud = true; let ids = 0;
  const logs = []; const recorders = []; const downloads = [];
  const track = { stopCalls: 0, stop() { this.stopCalls++; } };
  const stream = { getTracks: () => [track] };
  class Recorder extends Element {
    static isTypeSupported() { return true; }
    constructor() { super(); this.mimeType = 'audio/webm'; this.state = 'inactive'; recorders.push(this); }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.emit('dataavailable', { data: new Blob(['x'.repeat(1200)]) }); this.emit('stop'); }
  }
  class Context {
    createAnalyser() { return { fftSize: 1024, getByteTimeDomainData(data) { data.fill(loud ? 180 : 128); } }; }
    createMediaStreamSource() { return { connect() {} }; }
    close() { return Promise.resolve(); }
  }
  const windowRef = new Element();
  Object.assign(windowRef, {
    crypto: { randomUUID: () => `new-${++ids}` }, performance: { now: () => now }, Blob, Event: class { constructor(type) { this.type = type; } },
    MediaRecorder: Recorder, AudioContext: ContextOverride || Context, navigator: { mediaDevices: { getUserMedia: getUserMedia || (async () => stream) } },
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; }, clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame(fn) { const id = ++timerId; frames.set(id, fn); return id; }, cancelAnimationFrame(id) { frames.delete(id); },
    URL: { createObjectURL(blob) { downloads.push(blob); return 'blob:fixture'; }, revokeObjectURL() {} }, confirm: () => true,
  });
  const documentRoot = new Element();
  documentRoot.body = new Element(); documentRoot.createElement = tag => { const el = new Element(); el.tag = tag; return el; };
  const baseApi = async (route, body) => {
    if (route === '/api/dictation/assistant') return { selected: 'rules', available: [] };
    if (api) return api(route, body);
    if (route === '/api/dictation/speak') return { audio_url: '/fixture.wav' };
    return { instruction: { kind: 'noop' } };
  };
  const controller = createDictationController({ api: baseApi, elements, refreshMainStatus: async () => {}, log: x => logs.push(x), documentRoot, windowRef, fetchFn: fetchFn || (async () => ({ ok: true, json: async () => ({ instruction: { kind: 'dictate', text: ' tardío' } }) })), storage: storage || memory, dialog });
  await tick();
  return {
    controller, elements, logs, windowRef, memory, values, downloads, track, documentRoot, recorders, timers,
    async edit(value) { await elements.dictationEditor.emit('beforeinput'); elements.dictationEditor.value = value; elements.dictationEditor.selectionStart = value.length; elements.dictationEditor.selectionEnd = value.length; await elements.dictationEditor.emit('input'); },
    async flushSave() { for (const [id, entry] of [...timers]) if (entry.ms === 350) { timers.delete(id); entry.fn(); } await tick(); },
    async startRecording() {
      await elements.dictationMicBtn.emit('click');
      for (let i = 0; i < 3; i++) { now += 100; const current = [...frames.values()]; frames.clear(); current.forEach(fn => fn()); }
    },
    async detectSpeech() { for (let i = 0; i < 3; i++) { now += 100; const current = [...frames.values()]; frames.clear(); current.forEach(fn => fn()); } },
    async silence(ms) {
      loud = false;
      for (let elapsed = 0; elapsed < ms; elapsed += 100) {
        now += 100;
        const current = [...frames.values()]; frames.clear(); current.forEach(fn => fn());
      }
      loud = true;
      await tick();
    },
    async finishRecording() { recorders.at(-1).stop(); await tick(); },
    async upload() { await this.startRecording(); await this.finishRecording(); },
  };
}

test('destructive fallback cannot find sol inside consolida or cross word boundaries', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  for (const [value, target] of [['consolida', 'sol'], ['filosóficamente', 'filosofica'], ['foobar', 'foo.bar'], ['𐐀sol', 'sol']]) {
    const editor = new Element(); editor.value = value; editor.selectionStart = value.length;
    assert.equal(applyEditorInstruction(editor, { kind: 'delete', target }).changed, false);
    assert.equal(editor.value, value);
  }
});

test('accent-insensitive matches preserve decomposed accents and original UTF-16 ranges', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const editor = new Element(); editor.value = '😀 cri\u0301tica y crítica'; editor.selectionStart = editor.value.length;
  applyEditorInstruction(editor, { kind: 'replace', target: 'critica', text: 'análisis', all_matches: true });
  assert.equal(editor.value, '😀 análisis y análisis');
});

test('typed interpretation is discarded after manual edit or selection change', async () => {
  for (const change of ['text', 'selection']) {
    const pending = deferred(); const f = await fixture({ api: () => pending.promise });
    f.elements.dictationCommandInput.value = 'orden';
    const running = f.elements.dictationCommandBtn.emit('click');
    if (change === 'text') await f.edit('Mi texto nuevo');
    else { f.elements.dictationEditor.selectionStart = 0; f.elements.dictationEditor.selectionEnd = 5; }
    const expected = f.elements.dictationEditor.value;
    pending.resolve({ instruction: { kind: 'clear' } }); await running;
    assert.equal(f.elements.dictationEditor.value, expected, change);
  }
});

test('assistant result cannot edit another session with identical text', async () => {
  const pending = deferred(); const f = await fixture({ api: () => pending.promise });
  f.elements.dictationAssistantSelect.value = 'local';
  const running = f.controller.applyInstruction({ kind: 'noop' }, 'Lucy, orden', true);
  f.controller.loadSession('B');
  pending.resolve({ instruction: { kind: 'insert', text: ' respuesta vieja' } }); await running;
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
});

test('proofreading rejects a changed revision even when text is edited back to its original value', async () => {
  const pending = deferred(); const f = await fixture({ api: () => pending.promise });
  const running = f.controller.applyInstruction({ kind: 'proofread', scope: 'all' });
  await f.edit('Cambio'); await f.edit('Texto original');
  pending.resolve({ text: 'Respuesta vieja', changed_parts: 1 }); await running;
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
});

test('a later command wins when assistant responses arrive in reverse order', async () => {
  const pending = [deferred(), deferred()]; let calls = 0;
  const f = await fixture({ api: () => pending[calls++].promise });
  f.elements.dictationAssistantSelect.value = 'local';
  const first = f.controller.applyInstruction({ kind: 'noop' }, 'Lucy, primera', true);
  const second = f.controller.applyInstruction({ kind: 'noop' }, 'Lucy, segunda', true);
  pending[1].resolve({ instruction: { kind: 'insert', text: ' nueva' } }); await second;
  pending[0].resolve({ instruction: { kind: 'clear' } }); await first;
  assert.equal(f.elements.dictationEditor.value, 'Texto original nueva');
});

test('STT arriving after editing, switching sessions or closing never mutates the draft', async () => {
  for (const change of ['edit', 'session', 'close', 'stop']) {
    const pending = deferred(); const f = await fixture({ fetchFn: () => pending.promise });
    await f.upload();
    if (change === 'edit') await f.edit('Texto nuevo');
    if (change === 'session') f.controller.loadSession('B');
    if (change === 'close') f.controller.close();
    if (change === 'stop') f.controller.stopListening();
    const expected = f.elements.dictationEditor.value;
    pending.resolve({ ok: true, json: async () => ({ transcript: 'voz vieja', instruction: { kind: 'dictate', text: ' vieja' } }) });
    await tick(); await tick();
    assert.equal(f.elements.dictationEditor.value, expected, change);
    f.controller.dispose();
  }
});

test('late microphone permission cannot restart dictation after closing', async () => {
  const pending = deferred(); const f = await fixture({ getUserMedia: () => pending.promise });
  const running = f.elements.dictationMicBtn.emit('click');
  f.controller.close(); pending.resolve({ getTracks: () => [f.track] }); await running;
  assert.ok(f.track.stopCalls > 0);
  assert.equal(f.elements.dictationMicBtn.textContent, 'Iniciar dictado');
});

test('quota failure stays visible, keeps draft recoverable by TXT and blocks switching away', async () => {
  const storage = { getItem: () => null, setItem() { throw new Error('quota'); } };
  const f = await fixture({ storage }); await f.edit('Borrador que no puede perderse'); await f.flushSave();
  assert.match(f.elements.dictationSaveStatus.textContent, /no.*guard|sin guardar/i);
  assert.equal(f.elements.dictationSaveStatus.dataset.state, 'error');
  await f.controller.createNewSession();
  assert.equal(f.elements.dictationEditor.value, 'Borrador que no puede perderse');
  await f.elements.dictationDownloadBtn.emit('click');
  assert.equal(await f.downloads[0].text(), 'Borrador que no puede perderse');
  let prevented = false;
  await f.windowRef.emit('beforeunload', { preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
});

test('storage read failure is visible and never overwrites unreadable stored data', async () => {
  let writes = 0;
  const f = await fixture({ storage: { getItem() { throw new Error('denied'); }, setItem() { writes++; } } });
  await f.edit('Recuperación manual'); await f.flushSave();
  assert.equal(writes, 0);
  assert.equal(f.elements.dictationSaveStatus.dataset.state, 'error');
});

test('successful retry clears the save warning only after data is persisted', async () => {
  let fail = true; let saved = '';
  const f = await fixture({ storage: { getItem: () => null, setItem(_key, value) { if (fail) throw new Error('quota'); saved = value; } } });
  await f.edit('Texto seguro'); await f.flushSave(); assert.equal(f.elements.dictationSaveStatus.dataset.state, 'error');
  fail = false; await f.edit('Texto seguro actualizado'); await f.flushSave();
  assert.equal(f.elements.dictationSaveStatus.dataset.state, 'saved');
  assert.equal(JSON.parse(saved).sessions[0].text, 'Texto seguro actualizado');
});

test('stopping playback settles the read promise and removes media listeners', async () => {
  const f = await fixture();
  const running = f.controller.applyInstruction({ kind: 'read', scope: 'all' });
  await tick(); f.controller.stopSpeech();
  assert.equal(await Promise.race([running.then(() => 'settled'), new Promise(resolve => setTimeout(() => resolve('pending'), 40))]), 'settled');
  assert.equal(f.elements.dictationPlayer.listeners.get('ended')?.size || 0, 0);
  assert.equal(f.elements.dictationPlayer.listeners.get('error')?.size || 0, 0);
});

test('a delayed TTS response cannot start playback after stop', async () => {
  const pending = deferred();
  const f = await fixture({ api: () => pending.promise });
  const running = f.controller.applyInstruction({ kind: 'read', scope: 'all' });
  await tick(); f.controller.stopSpeech(); pending.resolve({ audio_url: '/late.wav' });
  await running;
  assert.equal(f.elements.dictationPlayer.playCalls || 0, 0);
  assert.equal(f.elements.dictationPlayer.src, undefined);
  assert.equal(f.elements.dictationPlayer.listeners.get('ended')?.size || 0, 0);
});

test('fresh STT and assistant responses still apply and can be undone', async () => {
  const pending = deferred(); const f = await fixture({ fetchFn: () => pending.promise });
  await f.upload();
  pending.resolve({ ok: true, json: async () => ({ transcript: 'nuevo', instruction: { kind: 'dictate', text: 'nuevo' } }) });
  await tick(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'Texto original nuevo');
  f.controller.undo();
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
  f.controller.dispose();
  const assistant = await fixture({ api: async () => ({ instruction: { kind: 'insert', text: ' aceptada' } }) });
  assistant.elements.dictationAssistantSelect.value = 'local';
  await assistant.controller.applyInstruction({ kind: 'noop' }, 'Lucy, orden', true);
  assert.equal(assistant.elements.dictationEditor.value, 'Texto original aceptada');
  assistant.controller.undo();
  assert.equal(assistant.elements.dictationEditor.value, 'Texto original');
});

test('an older recording cannot supersede a typed command issued while it was captured', async () => {
  const pending = deferred(); let uploads = 0;
  const f = await fixture({ api: () => pending.promise, fetchFn: async () => { uploads++; return { ok: true, json: async () => ({ instruction: { kind: 'clear' } }) }; } });
  await f.startRecording();
  f.elements.dictationCommandInput.value = 'orden reciente';
  const running = f.elements.dictationCommandBtn.emit('click');
  await f.finishRecording();
  assert.equal(uploads, 0);
  pending.resolve({ instruction: { kind: 'insert', text: ' reciente' } }); await running;
  assert.equal(f.elements.dictationEditor.value, 'Texto original reciente');
  f.controller.dispose();
});

test('disabling commands invalidates an already transcribing destructive instruction', async () => {
  const pending = deferred(); const f = await fixture({ fetchFn: () => pending.promise });
  await f.upload();
  f.elements.dictationCommandsToggle.checked = false;
  await f.elements.dictationCommandsToggle.emit('change');
  pending.resolve({ ok: true, json: async () => ({ transcript: 'Lucy, borrá todo', instruction: { kind: 'clear' } }) });
  await tick(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
  f.controller.dispose();
});

test('cancelled confirmation preserves text and a stale accepted confirmation cannot clear a newer revision', async () => {
  for (const choice of ['cancel', 'stale']) {
    const pending = deferred(); const f = await fixture({ dialog: { confirm: () => pending.promise } });
    const running = f.elements.dictationClearBtn.emit('click');
    if (choice === 'stale') await f.edit('Texto posterior');
    const expected = f.elements.dictationEditor.value;
    pending.resolve(choice !== 'cancel'); await running;
    assert.equal(f.elements.dictationEditor.value, expected);
  }
});

test('dictation microphone does not reenter a pending permission request', async () => {
  const pending = deferred(); let calls = 0;
  const f = await fixture({ getUserMedia: () => { calls++; return pending.promise; } });
  const first = f.elements.dictationMicBtn.emit('click');
  const second = f.elements.dictationMicBtn.emit('click');
  await tick();
  try { assert.equal(calls, 1); }
  finally { pending.resolve({ getTracks: () => [f.track] }); await Promise.all([first, second]); f.controller.dispose(); }
});

test('dictation closes its microphone after AudioContext initialization fails', async () => {
  const f = await fixture({ ContextOverride: class { constructor() { throw new Error('fixture_context_failure'); } } });
  await f.elements.dictationMicBtn.emit('click');
  assert.ok(f.track.stopCalls >= 1);
  f.controller.dispose();
});

test('dictation recording has a deadline independent of animation frames', async () => {
  const f = await fixture();
  await f.startRecording();
  const timer = [...f.timers.values()].find(entry => entry.ms === 10000);
  assert.ok(timer, 'capture owns a wall-clock timer');
  timer.fn();
  assert.equal(f.recorders[0].state, 'inactive');
  f.controller.dispose();
});

test('hidden dictation tab stops and releases capture without resuming automatically', async () => {
  const f = await fixture();
  await f.startRecording();
  f.documentRoot.hidden = true;
  await f.documentRoot.emit('visibilitychange');
  assert.ok(f.track.stopCalls >= 1);
  assert.equal(f.recorders.at(-1).state, 'inactive');
  f.controller.dispose();
});

test('dictation byte limit discards capture and releases the microphone', async () => {
  const f = await fixture();
  await f.startRecording();
  await f.recorders.at(-1).emit('dataavailable', { data: new Blob(['x'.repeat(4 * 1024 * 1024 + 1)]) });
  assert.ok(f.track.stopCalls >= 1);
  assert.match(f.elements.dictationStatus.textContent, /límite/i);
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
  f.controller.dispose();
});


test('Lucy swaps colours simultaneously and missing or overlapping targets change nothing', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const editor = new Element(); editor.value = 'La casa es azul, la casa es verde y la pradera es azul.';
  editor.selectionStart = editor.selectionEnd = editor.value.length;
  const batch = { kind: 'replace_many', replacements: [['verde', 'azul'], ['azul', 'verde']] };
  const result = applyEditorInstruction(editor, batch);
  assert.equal(editor.value, 'La casa es verde, la casa es azul y la pradera es verde.');
  assert.equal(result.changes, 3);
  const before = editor.value;
  assert.equal(applyEditorInstruction(editor, { ...batch, replacements: [['verde', 'azul'], ['rojo', 'gris']] }).changed, false);
  assert.equal(editor.value, before);
  editor.value = 'casa azul';
  assert.equal(applyEditorInstruction(editor, { ...batch, replacements: [['casa', 'hogar'], ['casa azul', 'pradera']] }).changed, false);
  assert.equal(editor.value, 'casa azul');
});

test('Lucy inside a recorded sentence separates dictation from an imperative but keeps names and quotes literal', async () => {
  const { splitLucyUtterance } = await import(moduleUrl);
  assert.deepEqual(splitLucyUtterance('La casa es azul. Lucy, cambia azul por verde.'), { prefix: 'La casa es azul.', command: 'Lucy, cambia azul por verde.' });
  assert.deepEqual(splitLucyUtterance('La casa es azul Lucy cambia azul por verde'), { prefix: 'La casa es azul', command: 'Lucy cambia azul por verde' });
  assert.equal(splitLucyUtterance('Lucy vive en una casa azul.'), null);
  assert.equal(splitLucyUtterance('Mi amiga Lucy vive aquí.'), null);
  assert.equal(splitLucyUtterance('Mi amiga Lucy ponía música.'), null);
  assert.equal(splitLucyUtterance('Lucy cambió su horario.'), null);
  assert.equal(splitLucyUtterance('Lucy, podrías dejarlo más claro').prefix, '');
  assert.equal(splitLucyUtterance('El cuento dice «Lucy, cambia azul por verde». '), null);
});

test('voice command confirms once, pauses capture, resumes after audio and has one undo', async () => {
  const calls = []; let f;
  f = await fixture({ api: async (route, body) => {
    if (route === '/api/dictation/speak') {
      calls.push(body.text);
      assert.notEqual(f.recorders.at(-1)?.state, 'recording');
      return { audio_url: '/confirmation.wav' };
    }
    return { instruction: { kind: 'noop' } };
  }, fetchFn: async () => ({ ok: true, json: async () => ({ transcript: 'Lucy, cambia verde por azul, azul por verde', instruction: { kind: 'replace_many', replacements: [['verde', 'azul'], ['azul', 'verde']] } }) }) });
  await f.edit('La casa es azul y la pradera es verde.');
  await f.startRecording(); await f.finishRecording(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde y la pradera es azul.');
  assert.equal(calls.length, 1);
  assert.match(calls[0], /^Listo/); assert.ok(calls[0].length <= 160);
  assert.notEqual(f.recorders.at(-1).state, 'recording');
  await f.elements.dictationPlayer.emit('ended'); await tick();
  assert.equal(f.recorders.at(-1).state, 'recording');
  await f.elements.dictationUndoBtn.emit('click');
  assert.equal(f.elements.dictationEditor.value, 'La casa es azul y la pradera es verde.');
  f.controller.dispose();
});

test('Lucy alone arms the next phrase and confirms its edit without inserting the wake word', async () => {
  const transcripts = ['Lucy.', 'cambia verde por azul']; const requests = [];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: transcripts.shift(), instruction: { kind: 'noop' } }) }),
    api: async (route, body) => {
      requests.push([route, body]);
      if (route === '/api/dictation/interpret') { assert.equal(body.require_wake_word, true); return { instruction: { kind: 'replace', target: 'verde', text: 'azul' } }; }
      return { audio_url: '/confirmation.wav' };
    }
  });
  await f.edit('La casa es verde.'); await f.startRecording(); await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde.');
  assert.equal(requests.length, 0);
  await f.detectSpeech(); await f.finishRecording(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'La casa es azul.');
  assert.equal(requests.filter(([route]) => route === '/api/dictation/speak').length, 1);
  await f.elements.dictationPlayer.emit('ended'); await tick();
  assert.equal(f.recorders.at(-1).state, 'recording'); f.controller.dispose();
});

test('a pause longer than one second does not cut a voice order in progress', async () => {
  let uploads = 0;
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({
      transcript: ++uploads === 1 ? 'Lucy.' : 'cambiá original por claro', instruction: { kind: 'noop' }
    }) }),
    api: async route => route === '/api/dictation/interpret'
      ? { instruction: { kind: 'replace', target: 'original', text: 'claro' } } : {},
  });
  await f.startRecording();
  await f.finishRecording();
  assert.equal(f.elements.dictationStatus.dataset.mode, 'armed');
  await f.silence(9000);
  await f.detectSpeech();
  assert.equal(f.elements.dictationStatus.dataset.mode, 'capturing');
  await f.silence(1300);
  assert.equal(uploads, 1);
  await f.detectSpeech();
  await f.silence(1900);
  assert.equal(uploads, 2);
  assert.equal(f.elements.dictationEditor.value, 'Texto claro');
  f.controller.dispose();
});

test('an empty transcription keeps a retry already in the microphone and displays the result', async () => {
  const failed = deferred();
  let uploads = 0;
  const f = await fixture({
    fetchFn: async () => {
      uploads++;
      if (uploads === 1) return { ok: true, json: async () => ({ transcript: 'Lucy.' }) };
      if (uploads === 2) return failed.promise;
      return { ok: true, json: async () => ({ transcript: 'cambiá original por claro' }) };
    },
    api: async route => route === '/api/dictation/interpret'
      ? { instruction: { kind: 'replace', target: 'original', text: 'claro' } } : {},
  });
  await f.startRecording();
  await f.finishRecording();
  await f.detectSpeech();
  await f.finishRecording();
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(uploads, 2);
  failed.resolve({ ok: true, json: async () => ({ ok: false, detail: 'empty_transcript' }) });
  await tick();
  assert.equal(uploads, 3);
  assert.equal(f.elements.dictationEditor.value, 'Texto claro');
  assert.match(f.elements.dictationReviewStatus.textContent, /Cambié original por claro/);
  f.controller.dispose();
});

test('mixed utterance keeps its dictated prefix and applies only the invoked command', async () => {
  const calls = [];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: 'La casa es verde. Lucy, cambia verde por azul.', instruction: { kind: 'dictate', text: 'La casa es verde. Lucy, cambia verde por azul.' } }) }),
    api: async (route, body) => {
      calls.push([route, body]);
      if (route === '/api/dictation/interpret') return { instruction: { kind: 'replace', target: 'verde', text: 'azul' } };
      return { audio_url: '/confirmation.wav' };
    }
  });
  await f.edit(''); await f.startRecording(); await f.finishRecording(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'La casa es azul.');
  assert.equal(calls.find(([route]) => route === '/api/dictation/interpret')[1].text, 'Lucy, cambia verde por azul.');
  await f.elements.dictationPlayer.emit('ended'); await tick();
  await f.elements.dictationUndoBtn.emit('click');
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde.'); f.controller.dispose();
});

test('failed confirmation preserves the applied edit and resumes recording', async () => {
  const f = await fixture({
    api: async () => { throw new Error('tts unavailable'); },
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: 'Lucy cambia verde por azul', instruction: { kind: 'replace', target: 'verde', text: 'azul' } }) })
  });
  await f.edit('verde'); await f.startRecording(); await f.finishRecording(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'azul');
  assert.equal(f.recorders.at(-1).state, 'recording');
  assert.ok(f.logs.some(line => /No pude reproducir la confirmación/.test(line))); f.controller.dispose();
});

test('closing during confirmation synthesis suppresses late playback and capture', async () => {
  const pending = deferred();
  const f = await fixture({ api: async () => pending.promise,
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: 'Lucy cambia verde por azul', instruction: { kind: 'replace', target: 'verde', text: 'azul' } }) })
  });
  await f.edit('verde'); await f.startRecording(); await f.finishRecording();
  f.controller.close(); pending.resolve({ audio_url: '/too-late.wav' }); await tick();
  assert.equal(f.elements.dictationPlayer.playCalls || 0, 0);
  assert.notEqual(f.recorders.at(-1).state, 'recording'); f.controller.dispose();
});

test('ambiguous compound order cannot be reinterpreted by a model or reported as successful', async () => {
  const calls = [];
  const f = await fixture({ api: async (route, body) => { calls.push([route, body]); return {}; } });
  f.elements.dictationAssistantSelect.value = 'local14b';
  await f.controller.applyInstruction({ kind: 'noop', scope: 'ambiguous_replacement' }, 'Lucy, cambia verde por azul, borra todo', true);
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
  assert.equal(calls.some(([route]) => route === '/api/dictation/assist'), false);
  assert.match(calls[0][1].text, /Pedilos por separado/); f.controller.dispose();
});

test('ordinary dictation and disabled commands do not request a spoken confirmation', async () => {
  const calls = [];
  const f = await fixture({ api: async route => { calls.push(route); return {}; },
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: 'Lucy cambia verde por azul', instruction: { kind: 'dictate', text: 'Lucy cambia verde por azul' } }) })
  });
  f.elements.dictationCommandsToggle.checked = false;
  await f.edit(''); await f.startRecording(); await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'Lucy cambia verde por azul');
  assert.equal(calls.length, 0); f.controller.dispose();
});

test('an assistant edit receives exactly one short confirmation', async () => {
  const calls = [];
  const f = await fixture({ api: async (route, body) => {
    calls.push([route, body]);
    if (route === '/api/dictation/assist') return { instruction: { kind: 'replace', target: 'original', text: 'nuevo' } };
    return {};
  } });
  f.elements.dictationAssistantSelect.value = 'local14b';
  await f.controller.applyInstruction({ kind: 'noop' }, 'Lucy, modificá el texto', true);
  assert.equal(f.elements.dictationEditor.value, 'Texto nuevo');
  assert.equal(calls.filter(([route]) => route === '/api/dictation/speak').length, 1); f.controller.dispose();
});


test('a dictated prefix followed by bare Lucy is saved and arms the next recording', async () => {
  const texts = ['La casa es verde. Lucy.', 'cambia verde por azul'];
  const f = await fixture({ fetchFn: async () => ({ ok: true, json: async () => ({ transcript: texts.shift(), instruction: { kind: 'noop' } }) }),
    api: async route => route === '/api/dictation/interpret' ? { instruction: { kind: 'replace', target: 'verde', text: 'azul' } } : {} });
  await f.edit(''); await f.startRecording(); await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde.');
  await f.detectSpeech(); await f.finishRecording(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'La casa es azul.'); f.controller.dispose();
});

test('playback rejection resumes capture without reverting the edit', async () => {
  const f = await fixture({ api: async () => ({ audio_url: '/confirmation.wav' }),
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: 'Lucy cambia verde por azul', instruction: { kind: 'replace', target: 'verde', text: 'azul' } }) }) });
  f.elements.dictationPlayer.play = () => Promise.reject(new Error('NotAllowedError'));
  await f.edit('verde'); await f.startRecording(); await f.finishRecording(); await tick();
  assert.equal(f.elements.dictationEditor.value, 'azul');
  assert.equal(f.recorders.at(-1).state, 'recording'); f.controller.dispose();
});


test('Spanish accented imperatives and a final bare wake word work in mixed dictation', async () => {
  const { splitLucyUtterance, hasLucyInvocation } = await import(moduleUrl);
  assert.equal(splitLucyUtterance('Una casa azul. Lucy, cambiá azul por verde').command, 'Lucy, cambiá azul por verde');
  assert.deepEqual(splitLucyUtterance('Una casa azul. Lucy'), { prefix: 'Una casa azul.', command: 'Lucy' });
  assert.equal(hasLucyInvocation('Lucí cambia azul por verde'), true);
  assert.equal(hasLucyInvocation('Lucyfer vive aquí'), false);
});

test('rejecting the last voice edit restores the draft and accepts a new order without Lucy', async () => {
  const original = 'La casa es azul, la casa es verde y la pradera es azul.';
  const spoken = [];
  const replies = [
    { transcript: 'Lucy cambia azul por verde y verde por azul', instruction: {
      kind: 'replace_many', replacements: [['azul', 'verde'], ['verde', 'azul']]
    } },
    { transcript: 'No, quedó mal escrito.', instruction: { kind: 'dictate', text: 'No, quedó mal escrito.' } },
    { transcript: 'cambia verde por blanca', instruction: { kind: 'dictate', text: 'cambia verde por blanca' } }
  ];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => replies.shift() }),
    api: async (route, body) => {
      if (route === '/api/dictation/speak') {
        spoken.push(body.text);
        return { audio_url: '/confirmation.wav' };
      }
      assert.equal(route, '/api/dictation/interpret');
      assert.equal(body.text, 'Lucy, cambia verde por blanca');
      return { instruction: { kind: 'replace', target: 'verde', text: 'blanca' } };
    }
  });
  await f.edit(original);
  await f.startRecording();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde, la casa es azul y la pradera es verde.');
  await f.elements.dictationPlayer.emit('ended');
  await tick();
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, original);
  assert.match(spoken[1], /Deshice el último cambio/);
  await f.elements.dictationPlayer.emit('ended');
  await tick();
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'La casa es azul, la casa es blanca y la pradera es azul.');
  assert.equal(f.elements.dictationEditor.selectionStart, f.elements.dictationEditor.value.length);
  f.controller.dispose();
});

test('a contextual rejection cannot undo text changed manually after the voice edit', async () => {
  const replies = [
    { transcript: 'Lucy cambia verde por azul', instruction: { kind: 'replace', target: 'verde', text: 'azul' } },
    { transcript: 'No, quedó mal escrito.', instruction: { kind: 'dictate', text: 'No, quedó mal escrito.' } }
  ];
  const spoken = [];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => replies.shift() }),
    api: async (_route, body) => { spoken.push(body.text); return { audio_url: '/confirmation.wav' }; }
  });
  await f.edit('verde');
  await f.startRecording();
  await f.finishRecording();
  await f.edit('Texto revisado a mano.');
  await f.elements.dictationPlayer.emit('ended');
  await tick();
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'Texto revisado a mano. No, quedó mal escrito.');
  assert.equal(spoken.length, 1);
  f.controller.dispose();
});

test('sentence deletion preserves neighboring sentences, paragraph breaks and decimal points', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const cases = [
    ['Primera. La Dra. García anotó 3.14. Tercera.', '3.14', 'current', 'Primera. Tercera.'],
    ['Primera. ¿La querés borrar? Tercera.', 'querés', 'current', 'Primera. Tercera.'],
    ['Primera. segunda frase sin punto', '', 'last', 'Primera.'],
    ['Primera. Segunda.\n\nTercera.', 'Segunda', 'current', 'Primera.\n\nTercera.'],
    ['Frase única sin punto', 'única', 'current', ''],
  ];
  for (const [value, caret, scope, expected] of cases) {
    const editor = new Element();
    editor.value = value;
    editor.selectionStart = editor.selectionEnd = value.indexOf(caret);
    assert.equal(applyEditorInstruction(editor, { kind: 'delete_sentence', scope }).changed, true);
    assert.equal(editor.value, expected);
  }
  assert.equal(applyEditorInstruction(new Element(), { kind: 'delete_sentence', scope: 'current' }).changed, false);
});

test('voice removes the sentence and confirms locally even if the selected model is unavailable', async () => {
  const calls = [];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({
      transcript: 'Lucy, borrá toda la frase.', instruction: { kind: 'delete_sentence', scope: 'current' },
    }) }),
    api: async (route, body) => {
      calls.push([route, body.text]);
      assert.equal(route, '/api/dictation/speak');
      return { audio_url: '/confirmation.wav' };
    },
  });
  f.elements.dictationAssistantSelect.value = 'openai';
  await f.edit('La casa es azul. La pradera es verde.');
  await f.startRecording();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'La casa es azul.');
  assert.deepEqual(calls, [['/api/dictation/speak', 'Listo. Borré la frase.']]);
  await f.elements.dictationPlayer.emit('ended');
  await tick();
  assert.equal(f.recorders.at(-1).state, 'recording');
  await f.elements.dictationUndoBtn.emit('click');
  assert.equal(f.elements.dictationEditor.value, 'La casa es azul. La pradera es verde.');
  f.controller.dispose();
});

test('natural edits use the excerpt offset and undo all changes together', async () => {
  const prefix = 'Antes. '.repeat(1800);
  const original = prefix + 'La filosofia y el analisis.';
  const f = await fixture({ api: async (route, body) => {
    if (route === '/api/dictation/interpret') return { instruction: { kind: 'noop' } };
    if (route === '/api/dictation/assist') {
      assert.ok(body.draft.length < original.length);
      assert.equal('context_start' in body, false);
      const patch = (before, after) => {
        const start = body.draft.indexOf(before);
        return { start, end: start + before.length, before, after };
      };
      return { instruction: { kind: 'edit', edits: [patch('filosofia', 'filosofía'), patch('analisis', 'análisis')] } };
    }
    return {};
  } });
  await f.edit(original);
  f.elements.dictationAssistantSelect.value = 'local14b';
  f.elements.dictationCommandInput.value = 'Lucy, corregí las tildes de esos dos sustantivos';
  await f.elements.dictationCommandBtn.emit('click');
  assert.equal(f.elements.dictationEditor.value, prefix + 'La filosofía y el análisis.');
  await f.elements.dictationUndoBtn.emit('click');
  assert.equal(f.elements.dictationEditor.value, original);
  f.controller.dispose();
});

test('the model can ask a question and use the unprefixed answer with the original request', async () => {
  const original = 'La casa es verde. La pradera es azul.';
  const replies = ['Lucy, sacá esa frase', 'la de la pradera'];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: replies.shift(), instruction: { kind: 'noop' } }) }),
    api: async (route, body) => {
      if (route === '/api/dictation/assist') {
        if (!body.text.includes('El usuario aclaró')) {
          return { instruction: { kind: 'clarify', text: '¿Cuál de las dos frases?' } };
        }
        assert.match(body.text, /sacá esa frase/);
        assert.match(body.text, /la de la pradera/);
        return { instruction: { kind: 'edit', edits: [
          { start: 17, end: original.length, before: ' La pradera es azul.', after: '' }
        ] } };
      }
      return { audio_url: '/confirmation.wav' };
    },
  });
  f.elements.dictationAssistantSelect.value = 'openai';
  await f.edit(original);
  await f.startRecording();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, original);
  await f.elements.dictationPlayer.emit('ended');
  await tick();
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde.');
  f.controller.dispose();
});

test('an invalid second patch leaves the first one unapplied', async () => {
  const { applyEditorInstruction } = await import(moduleUrl);
  const editor = new Element();
  editor.value = 'Una casa. Una pradera.';
  const result = applyEditorInstruction(editor, { kind: 'edit', edits: [
    { start: 4, end: 8, before: 'casa', after: 'torre' },
    { start: 14, end: 21, before: 'ventana', after: 'río' }
  ] });
  assert.equal(result.changed, false);
  assert.equal(editor.value, 'Una casa. Una pradera.');
});

test('a deletion with a literal match still uses the selected editor to repair the surrounding grammar', async () => {
  const original = 'La casa es verde y la pradera es azul. El caballo nada.';
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({
      transcript: 'Lucy, borrá la pradera es azul',
      instruction: { kind: 'delete', target: 'la pradera es azul' },
    }) }),
    api: async (route) => route === '/api/dictation/assist'
      ? { instruction: { kind: 'edit', edits: [{ start: 16, end: 37, before: ' y la pradera es azul', after: '' }] } }
      : {},
  });
  f.elements.dictationAssistantSelect.value = 'openai';
  await f.edit(original);
  await f.startRecording();
  await f.finishRecording();
  await tick();
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde. El caballo nada.');
  f.controller.dispose();
});

test('an order spoken before the bare wake transcription returns is buffered and interpreted once', async () => {
  const first = deferred();
  let uploads = 0;
  const f = await fixture({
    fetchFn: async () => {
      uploads++;
      if (uploads === 1) return first.promise;
      return { ok: true, json: async () => ({ transcript: 'cambia azul por verde', instruction: { kind: 'dictate' } }) };
    },
    api: async (route, body) => {
      if (route === '/api/dictation/interpret') {
        assert.equal(body.text, 'Lucy, cambia azul por verde');
        return { instruction: { kind: 'replace', target: 'azul', text: 'verde' } };
      }
      return {};
    },
  });
  await f.edit('La casa es azul.');
  await f.startRecording();
  await f.finishRecording();
  const followUp = f.recorders.at(-1);
  assert.equal(followUp.state, 'recording');
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(uploads, 1);
  first.resolve({ ok: true, json: async () => ({ transcript: 'Che, Lusi.', instruction: { kind: 'noop' } }) });
  await tick();
  assert.equal(uploads, 2);
  assert.equal(f.elements.dictationEditor.value, 'La casa es verde.');
  f.controller.dispose();
});

test('short quiet speech is submitted with float PCM detection', async () => {
  let uploads = 0;
  class QuietAudio {
    createAnalyser() {
      return { fftSize: 1024, getFloatTimeDomainData(data) { data.fill(0.0048); } };
    }
    createMediaStreamSource() { return { connect() {} }; }
    close() { return Promise.resolve(); }
  }
  const f = await fixture({
    ContextOverride: QuietAudio,
    fetchFn: async () => {
      uploads++;
      return { ok: true, json: async () => ({ transcript: 'Lucy.', instruction: { kind: 'noop' } }) };
    },
  });
  await f.startRecording();
  await f.finishRecording();
  assert.equal(uploads, 1);
  assert.equal(f.elements.dictationStatus.dataset.mode, 'armed');
  f.controller.dispose();
});

test('the voice order button arms the next clip without requiring the name', async () => {
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: 'borrá la última frase', instruction: { kind: 'dictate' } }) }),
    api: async (route, body) => {
      if (route !== '/api/dictation/interpret') return {};
      assert.equal(body.text, 'Lucy, borrá la última frase');
      return { instruction: { kind: 'delete_sentence', scope: 'last' } };
    },
  });
  await f.edit('Primera frase. Segunda frase.');
  await f.elements.dictationVoiceCommandBtn.emit('click');
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'Primera frase.');
  f.controller.dispose();
});

test('instructions the local grammar would treat as literal insertion go to the selected editor', async () => {
  const original = 'El bosque estaba oscuro.';
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({
      transcript: 'Lucy, agregá muy antes de oscuro', instruction: { kind: 'insert', text: 'muy antes de oscuro' },
    }) }),
    api: async (route) => route === '/api/dictation/assist'
      ? { instruction: { kind: 'edit', edits: [{ start: 17, end: 23, before: 'oscuro', after: 'muy oscuro' }] } }
      : {},
  });
  f.elements.dictationAssistantSelect.value = 'openai';
  await f.edit(original);
  await f.startRecording();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'El bosque estaba muy oscuro.');
  f.controller.dispose();
});

test('an immediate spoken refinement keeps the prior instruction and does not append the feedback', async () => {
  const replies = ['Lucy, poné por la tarde', 'No, me refería a por la mañana'];
  const commands = [];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => ({ transcript: replies.shift(), instruction: { kind: 'noop' } }) }),
    api: async (route, body) => {
      if (route === '/api/dictation/speak') return { audio_url: '/short.wav' };
      commands.push(body.text);
      const before = commands.length === 1 ? 'Vuelvo.' : 'por la tarde';
      const start = body.draft.indexOf(before);
      return { instruction: { kind: 'edit', edits: [
        { start, end: start + before.length, before, after: commands.length === 1 ? 'Vuelvo por la tarde.' : 'por la mañana' }
      ] } };
    },
  });
  f.elements.dictationAssistantSelect.value = 'openai';
  await f.edit('Vuelvo.');
  await f.startRecording();
  await f.finishRecording();
  await f.elements.dictationPlayer.emit('ended');
  await tick();
  await f.detectSpeech();
  await f.finishRecording();
  assert.match(commands[1], /anterior.*poné por la tarde.*Ajuste.*por la mañana/s);
  assert.equal(f.elements.dictationEditor.value, 'Vuelvo por la mañana.');
  f.controller.dispose();
});

test('after a rejected review, a stalled confirmation releases the mic and Lucy accepts a new order', async () => {
  const packets = [
    { transcript: 'Lucy, mejorá la prosa', instruction: { kind: 'revise_prose', scope: 'all' } },
    { transcript: 'Lucy.', instruction: { kind: 'noop' } },
    { transcript: 'cambiá original por claro', instruction: { kind: 'dictate' } }
  ];
  const spoken = [];
  const f = await fixture({
    fetchFn: async () => ({ ok: true, json: async () => packets.shift() }),
    api: async (route, body) => {
      if (route === '/api/dictation/review') {
        assert.deepEqual(body, { text: 'Texto original', requests: ['mejorá la prosa'], previous: '' });
        throw new Error('No pude terminar la revisión de prosa');
      }
      if (route === '/api/dictation/interpret') return { instruction: { kind: 'replace', target: 'original', text: 'claro' } };
      if (route === '/api/dictation/speak') {
        spoken.push(body.text);
        return { audio_url: '/confirmation.wav' };
      }
      return {};
    }
  });
  f.elements.dictationModeSelect.value = 'review';
  await f.startRecording();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
  assert.notEqual(f.recorders.at(-1).state, 'recording');
  const playbackTimeout = [...f.timers.values()].find(timer => timer.ms === 20000);
  assert.ok(playbackTimeout);
  playbackTimeout.fn();
  await tick();
  assert.equal(f.recorders.at(-1).state, 'recording');

  f.elements.dictationModeSelect.value = 'direct';
  await f.elements.dictationModeSelect.emit('change');
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(f.elements.dictationStatus.dataset.mode, 'armed');
  await f.detectSpeech();
  await f.finishRecording();
  assert.equal(f.elements.dictationEditor.value, 'Texto claro');
  await f.elements.dictationPlayer.emit('ended');
  await tick();
  assert.equal(f.recorders.at(-1).state, 'recording');
  assert.equal(spoken.length, 2);
  f.controller.dispose();
});

test('stopping a confirmation while its synthesis is pending resumes capture and suppresses late audio', async () => {
  const pending = deferred();
  const f = await fixture({
    api: async () => pending.promise,
    fetchFn: async () => ({
      ok: true,
      json: async () => ({ transcript: 'Lucy cambia original por claro', instruction: { kind: 'replace', target: 'original', text: 'claro' } })
    })
  });
  await f.startRecording();
  await f.finishRecording();
  assert.notEqual(f.recorders.at(-1).state, 'recording');
  f.controller.stopSpeech();
  await tick();
  assert.equal(f.recorders.at(-1).state, 'recording');
  pending.resolve({ audio_url: '/stale.wav' });
  await tick();
  assert.equal(f.elements.dictationPlayer.playCalls || 0, 0);
  f.controller.dispose();
});

test('review refines a proposal and applies only the original selection in one undo step', async () => {
  const requests = [];
  const f = await fixture({ api: async (route, body) => {
    if (route === '/api/dictation/review') {
      requests.push(body);
      return { ok: true, text: body.previous ? 'nuevo' : 'revisado', note: 'Observación de prueba.' };
    }
    return {};
  } });
  f.elements.dictationModeSelect.value = 'review';
  f.elements.dictationEditor.selectionStart = 6;
  f.elements.dictationEditor.selectionEnd = 14;
  await f.controller.applyInstruction({ kind: 'revise_prose', scope: 'selection' }, 'Lucy, mejorá la prosa de la selección', true);
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
  assert.equal(f.elements.dictationReviewProposed.textContent, 'revisado');
  f.elements.dictationEditor.selectionStart = f.elements.dictationEditor.selectionEnd = 0;
  await f.controller.applyInstruction({ kind: 'noop' }, 'Lucy, más breve', true);
  assert.deepEqual(requests, [
    { text: 'original', requests: ['mejorá la prosa de la selección'], previous: '' },
    { text: 'original', requests: ['mejorá la prosa de la selección', 'más breve'], previous: 'revisado' }
  ]);
  await f.controller.applyInstruction({ kind: 'noop' }, 'Lucy, aplicalo', true);
  assert.equal(f.elements.dictationEditor.value, 'Texto nuevo');
  assert.equal(f.elements.dictationReviewPanel.hidden, true);
  f.controller.undo();
  assert.equal(f.elements.dictationEditor.value, 'Texto original');
  f.controller.dispose();
});

test('review never applies a discarded proposal or a response for a changed draft or mode', async () => {
  for (const change of ['discard', 'draft', 'mode']) {
    const pending = deferred();
    const f = await fixture({ api: () => pending.promise });
    f.elements.dictationModeSelect.value = 'review';
    const running = f.controller.applyInstruction({ kind: 'noop' }, 'Lucy, hacelo más claro', true);
    if (change === 'discard') {
      pending.resolve({ text: 'Texto revisado' });
      await running;
      await f.elements.dictationReviewDiscardBtn.emit('click');
    } else {
      if (change === 'draft') await f.edit('Texto nuevo');
      else {
        f.elements.dictationModeSelect.value = 'direct';
        await f.elements.dictationModeSelect.emit('change');
      }
      pending.resolve({ text: 'Texto revisado' });
      await running;
    }
    assert.equal(f.elements.dictationReviewPanel.hidden, true);
    await f.elements.dictationReviewApplyBtn.emit('click');
    assert.equal(f.elements.dictationEditor.value, change === 'draft' ? 'Texto nuevo' : 'Texto original');
    f.controller.dispose();
  }
});
