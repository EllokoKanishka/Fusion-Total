const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const moduleUrl = pathToFileURL(path.resolve('fusion_reader_v2/web/static/js/audio.mjs')).href;

test('audio module computes PCM stats and appends chunks', async () => {
  const { appendPcmChunk, dialoguePcmStats } = await import(moduleUrl);
  const chunks = [];
  assert.equal(appendPcmChunk(chunks, new Float32Array([0.25, -0.5])), 2);
  const stats = dialoguePcmStats(chunks, {
    sampleRate: 1000,
    minThreshold: 0.1,
    noiseFloor: 0.01,
    thresholdMultiplier: 2
  });
  assert.equal(stats.samples, 2);
  assert.equal(stats.durationMs, 2);
  assert.equal(stats.peak, 0.5);
  assert.equal(stats.voiceDetected, true);
});

test('audio module emits a valid mono WAV header', async () => {
  const { encodeDialogueWav } = await import(moduleUrl);
  const wav = encodeDialogueWav([new Float32Array([0, 0.5, -0.5])], 16000);
  const bytes = new Uint8Array(await wav.arrayBuffer());
  assert.equal(new TextDecoder().decode(bytes.slice(0, 4)), 'RIFF');
  assert.equal(new TextDecoder().decode(bytes.slice(8, 12)), 'WAVE');
  assert.equal(wav.type, 'audio/wav');
});
