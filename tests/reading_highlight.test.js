const assert = require('node:assert/strict');
const test = require('node:test');

test('highlight follows media time forward and backward, including exact word starts', async () => {
  const { wordAtTime } = await import('../fusion_reader_v2/web/static/js/reading_highlight.mjs');
  const words = [{ start: 0.2 }, { start: 0.5 }, { start: 1.4 }, { start: 2.1 }];
  for (const [time, expected] of [[0, -1], [0.2, 0], [0.49, 0], [0.5, 1], [2.3, 3], [1.6, 2], [0.1, -1]]) {
    assert.equal(wordAtTime(words, time), expected);
  }
  assert.equal(wordAtTime([], 1), -1);
});

test('a cancelled word-timing request releases pending playback and ignores late results', async () => {
  const { createReadingHighlight } = await import('../fusion_reader_v2/web/static/js/reading_highlight.mjs');
  const player = Object.assign(new EventTarget(), { src: '/audio/old.wav', paused: true, currentTime: 0 });
  const indicator = {};
  let finishRequest;
  let signal;
  const highlight = createReadingHighlight({
    player,
    container: { closest: () => null },
    indicator,
    request: (_url, requestSignal) => {
      signal = requestSignal;
      return new Promise(resolve => { finishRequest = resolve; });
    },
  });
  let ready = false;
  const waiting = highlight.load(player.src).then(() => { ready = true; });
  await Promise.resolve();
  assert.equal(ready, false);
  highlight.clearAudio();
  await waiting;
  assert.equal(ready, true);
  assert.equal(signal.aborted, true);
  finishRequest({ ok: false, state: 'unavailable' });
  await Promise.resolve();
  assert.equal(indicator.textContent, '');
  highlight.dispose();
});

test('playback can continue when word timings are unavailable', async () => {
  const { createReadingHighlight } = await import('../fusion_reader_v2/web/static/js/reading_highlight.mjs');
  const player = Object.assign(new EventTarget(), { src: '/audio/fixture.wav', paused: true, currentTime: 0 });
  const indicator = {};
  const highlight = createReadingHighlight({
    player,
    container: { closest: () => null },
    indicator,
    request: async () => ({ ok: false, state: 'unavailable' }),
  });
  await highlight.load(player.src);
  assert.match(indicator.textContent, /no disponible/);
  highlight.dispose();
});
