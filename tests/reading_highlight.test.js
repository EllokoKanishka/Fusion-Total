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
