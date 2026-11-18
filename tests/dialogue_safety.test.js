const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const source = fs.readFileSync('fusion_reader_v2/web/static/js/bootstrap.mjs', 'utf8');
const block = source.slice(source.indexOf('function stopDialoguePlaybackForTypedTurn('), source.indexOf("\nels.prevBtn.addEventListener"));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

async function fixture({ capture, Context, api, fetch } = {}) {
  const { createDialogueState } = await import(pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/dialogue.mjs')).href);
  const audio = await import(pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/audio.mjs')).href);
  const dialogue = createDialogueState();
  const timers = new Map(); let timerId = 0; let now = 0;
  const track = { calls: 0, label: 'Fixture', stop() { this.calls++; } };
  const stream = { getAudioTracks: () => [track], getTracks: () => [track] };
  const player = { plays: 0, pause() {}, removeAttribute() {}, play() { this.plays++; return Promise.resolve(); } };
  class AudioContext {
    constructor() { this.sampleRate = 1000; this.destination = {}; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createAnalyser() { return { fftSize: 1024, getByteTimeDomainData(data) { data.fill(128); }, disconnect() {} }; }
    createScriptProcessor() { return { connect() {}, disconnect() {} }; }
    createGain() { return { gain: {}, connect() {}, disconnect() {} }; }
    close() { return Promise.resolve(); }
  }
  const messages = [];
  const context = {
    dialogue, ...audio, Blob, URLSearchParams, AbortController, Float32Array, Uint8Array,
    performance: { now: () => now }, navigator: { mediaDevices: { getUserMedia: capture || (async () => stream) } },
    document: { hidden: false }, window: { AudioContext: Context || AudioContext,
      setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; }, clearTimeout(id) { timers.delete(id); } },
    requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    els: { dialoguePlayer: player, dialogueBtn: {} },
    setDialogueInfo() {}, currentReasoningLabel: () => 'Normal', laboratoryModeSummary: () => 'Libre',
    currentReasoningMode: () => 'normal', visibleChunkIndex: () => 0,
    addChatMessage(role, text) { messages.push([role, text]); return { isConnected: true, remove() {} }; },
    refresh: async () => {}, refreshNotes: async () => {}, log() {}, dialogueModeSummary: () => '',
    fmtMs: () => '', formatDialogueTrace: () => '', renderGracefulResearchFailure: () => false,
    api: api || (async () => ({ ok: true, answer: 'Respuesta', audio_url: '/reply.wav' })),
    fetch: fetch || (async () => ({ ok: true, json: async () => ({ ok: true, answer: 'Respuesta', audio_url: '/reply.wav' }) })),
    dialogueFlushWaitMs: () => 180,
  };
  vm.createContext(context); vm.runInContext(block, context);
  return { ...context, context, stream, track, player, messages, timers, setNow(value) { now = value; } };
}

test('stopping dialogue invalidates a late typed response even if transport ignores abort', async () => {
  const request = deferred();
  const f = await fixture({ api: (route) => route === '/api/dialogue/turn' ? request.promise : Promise.resolve({}) });
  f.dialogue.active = true;
  const running = f.sendTypedDialogue('Hola');
  f.stopDialogue();
  request.resolve({ ok: true, answer: 'TARDIO', audio_url: '/late.wav' });
  await running;
  assert.equal(f.player.plays, 0);
  assert.equal(f.messages.some(([, text]) => text === 'TARDIO'), false);
});

test('stale audio response cannot play or clear the processing state of a new turn', async () => {
  const first = deferred(); const second = deferred(); let signal;
  const f = await fixture({ fetch: async (url, options) => { signal = options.signal; return { ok: true, json: () => first.promise }; },
    api: route => route === '/api/dialogue/turn' ? second.promise : Promise.resolve({}) });
  f.dialogue.active = true;
  const audio = f.sendDialogueAudio(new Blob(['x'.repeat(2000)]), 'audio/wav');
  await tick();
  f.stopDialogue();
  f.dialogue.active = true;
  const typed = f.sendTypedDialogue('Turno nuevo');
  first.resolve({ ok: true, answer: 'Vieja', audio_url: '/old.wav' });
  await audio;
  assert.equal(f.dialogue.processing, true);
  assert.equal(f.player.plays, 0);
  assert.equal(signal.aborted, true);
  second.resolve({ ok: true, answer: 'Nueva', audio_url: '/new.wav' });
  await typed;
  assert.equal(f.player.plays, 1);
  f.stopDialogue();
});

test('microphone start is single flight and a stopped pending permission releases its track', async () => {
  const pending = deferred(); let calls = 0;
  const f = await fixture({ capture: () => { calls++; return pending.promise; } });
  const first = f.startDialogue(); const second = f.startDialogue();
  await tick();
  try { assert.equal(calls, 1); }
  finally { f.stopDialogue(); pending.resolve(f.stream); await Promise.all([first, second]); }
  assert.equal(f.dialogue.active, false);
  assert.ok(f.track.calls >= 1);
});

test('AudioContext construction failure releases the acquired microphone', async () => {
  const f = await fixture({ Context: class { constructor() { throw new Error('AudioContext fixture failure'); } } });
  await f.startDialogue();
  assert.ok(f.track.calls >= 1);
  assert.equal(f.dialogue.stream, null);
  assert.equal(f.dialogue.active, false);
});

test('PCM callback bounds captured samples without animation frames', async () => {
  const f = await fixture();
  f.dialogue.active = true; f.dialogue.stream = f.stream; f.dialogue.sampleRate = 1000;
  f.beginDialogueRecording();
  for (let index = 0; index < 70; index++) f.handleDialoguePcm({ inputBuffer: { getChannelData: () => new Float32Array(1000) } });
  const samples = f.dialogue.pcmChunks.reduce((sum, chunk) => sum + chunk.length, 0);
  assert.ok(samples <= 20000, `${samples} samples exceed capture window`);
  f.stopDialogue();
});

test('a timer closes the dialogue recording when animation frames stop', async () => {
  const f = await fixture();
  f.dialogue.active = true; f.dialogue.stream = f.stream;
  f.beginDialogueRecording();
  const timer = [...f.timers.values()].find(item => item.ms === f.dialogue.maxRecordMs);
  assert.ok(timer, 'capture deadline has its own timer');
  f.setNow(f.dialogue.maxRecordMs + 1); timer.fn();
  assert.equal(f.dialogue.recording, false);
  f.stopDialogue();
});

test('hidden dialogue page releases the microphone through its real visibility listener', async () => {
  const f = await fixture();
  let listener;
  f.context.document.addEventListener = (name, fn) => { if (name === 'visibilitychange') listener = fn; };
  const registration = source.split('\n').find(line => line.startsWith("document.addEventListener('visibilitychange'"));
  assert.ok(registration, 'dialogue has a visibility listener');
  vm.runInContext(registration, f.context);
  f.dialogue.active = true; f.dialogue.stream = f.stream;
  f.context.document.hidden = true;
  listener();
  assert.equal(f.dialogue.active, false);
  assert.ok(f.track.calls >= 1);
});
