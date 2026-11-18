// The audio clock is the authority, including seeks and playback-rate changes.
export function wordAtTime(words, time) {
  let low = 0;
  let high = words.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (words[middle].start <= time) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}

export function createReadingHighlight({ player, container, indicator, request }) {
  let text = '';
  let identity = '';
  let spans = [];
  let words = [];
  let painted = 0;
  let currentSpan = null;
  let generation = 0;
  let controller = null;
  let pollTimer = null;
  let frame = null;
  let disposed = false;
  let trackedSource = '';
  let audioUrl = '';
  let paintedBoundary = 0;
  let follow = true;
  let enabled = true;
  const viewport = container.closest('.reader');

  function revealCurrent() {
    if (!enabled || !viewport || !currentSpan || !viewport.clientHeight) return;
    const area = viewport.getBoundingClientRect();
    const word = currentSpan.getBoundingClientRect();
    const margin = Math.min(24, Math.max(0, (viewport.clientHeight - word.height) / 2));
    if (word.top < area.top + margin) viewport.scrollTop += word.top - area.top - margin;
    else if (word.bottom > area.bottom - margin) viewport.scrollTop += word.bottom - area.bottom + margin;
  }

  function cancelFrame() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }

  function sync() {
    const index = wordAtTime(words, Number(player.currentTime) || 0);
    const word = index >= 0 ? words[index] : null;
    const boundary = words.length && player.ended ? text.length : word ? word.end_offset : 0;
    if (boundary !== paintedBoundary) {
      while (painted > 0 && spans[painted - 1].end > boundary) {
        spans[--painted].node.classList.remove('is-read');
      }
      while (painted < spans.length && spans[painted].end <= boundary) {
        spans[painted++].node.classList.add('is-read');
      }
      paintedBoundary = boundary;
    }
    const match = spans[painted - 1];
    const active = word && !player.ended && match?.start === word.start_offset && match.end === word.end_offset
      ? match.node : null;
    if (currentSpan !== active) {
      currentSpan?.classList.remove('is-current');
      active?.classList.add('is-current');
      currentSpan = active;
      if (follow) revealCurrent();
    }
    if (!disposed && words.length && !player.paused && !player.ended && frame === null) {
      frame = requestAnimationFrame(() => { frame = null; sync(); });
    }
  }

  function clearTimings() {
    generation += 1;
    controller?.abort();
    controller = null;
    clearTimeout(pollTimer);
    pollTimer = null;
    cancelFrame();
    words = [];
    sync();
    cancelFrame();
    indicator.textContent = '';
    indicator.title = '';
  }

  function clearAudio() {
    audioUrl = '';
    trackedSource = '';
    clearTimings();
  }

  function setText(nextText, nextIdentity) {
    if (text === nextText && identity === nextIdentity) return;
    clearAudio();
    text = String(nextText || '');
    identity = nextIdentity;
    spans = [];
    painted = 0;
    currentSpan = null;
    const fragment = document.createDocumentFragment();
    let offset = 0;
    for (const match of text.matchAll(/[\p{L}\p{N}_]+(?:['’][\p{L}\p{N}_]+)*/gu)) {
      fragment.append(document.createTextNode(text.slice(offset, match.index)));
      const node = document.createElement('span');
      node.className = 'reader-word';
      node.textContent = match[0];
      const end = match.index + match[0].length;
      spans.push({ node, start: match.index, end });
      fragment.append(node);
      offset = end;
    }
    fragment.append(document.createTextNode(text.slice(offset)));
    container.replaceChildren(fragment);
  }

  function load(audioURL) {
    clearTimings();
    trackedSource = player.src;
    audioUrl = audioURL;
    if (enabled) requestTimings();
  }

  function requestTimings() {
    const token = generation;
    const source = trackedSource;
    const requestedUrl = audioUrl;
    controller = new AbortController();
    const signal = controller.signal;
    const started = performance.now();
    let firstRequest = true;
    indicator.textContent = 'Sincronizando palabras…';
    async function poll() {
      try {
        const result = await request(requestedUrl, signal, firstRequest);
        firstRequest = false;
        if (disposed || token !== generation || player.src !== source) return;
        if (result.state === 'pending' || result.state === 'waiting') {
          if (performance.now() - started > 180000) throw new Error('timing_timeout');
          pollTimer = setTimeout(poll, 700);
          return;
        }
        if (!result.ok || result.state !== 'ready') throw new Error('timing_unavailable');
        let previousOffset = -1;
        let previousTime = -1;
        words = (result.words || []).filter(word => {
          const valid = Number.isFinite(word.start) && Number.isFinite(word.end)
            && Number.isInteger(word.start_offset) && Number.isInteger(word.end_offset)
            && word.start >= 0 && word.end > word.start && word.start >= previousTime
            && word.start_offset >= previousOffset && word.end_offset > word.start_offset
            && word.end_offset <= text.length;
          if (valid) { previousOffset = word.end_offset; previousTime = word.start; }
          return valid;
        });
        if (!words.length) throw new Error('empty_timings');
        const partial = Number(result.coverage) < 0.98;
        indicator.textContent = partial ? 'Resaltado parcial' : 'Palabras sincronizadas';
        indicator.title = partial ? 'Algunas palabras se marcan al alcanzar la siguiente palabra reconocida.' : '';
        sync();
      } catch (error) {
        if (disposed || token !== generation || signal.aborted) return;
        words = [];
        sync();
        indicator.textContent = 'Resaltado no disponible para este audio.';
      }
    }
    poll();
  }

  const events = ['timeupdate', 'seeked', 'ended', 'ratechange', 'loadedmetadata'];
  for (const event of events) player.addEventListener(event, sync);
  const onFollow = () => { follow = true; sync(); revealCurrent(); };
  player.addEventListener('play', onFollow);
  player.addEventListener('seeking', onFollow);
  const onManualScroll = () => { follow = false; };
  viewport?.addEventListener('wheel', onManualScroll, { passive: true });
  viewport?.addEventListener('touchmove', onManualScroll, { passive: true });
  viewport?.addEventListener('pointerdown', onManualScroll);
  const onPause = () => { cancelFrame(); sync(); };
  player.addEventListener('pause', onPause);
  const onEmptied = () => {
    if (!player.getAttribute('src') || player.src !== trackedSource) clearAudio();
    else sync();
  };
  player.addEventListener('emptied', onEmptied);
  function dispose() {
    disposed = true;
    clearAudio();
    for (const event of events) player.removeEventListener(event, sync);
    player.removeEventListener('pause', onPause);
    player.removeEventListener('play', onFollow);
    player.removeEventListener('seeking', onFollow);
    viewport?.removeEventListener('wheel', onManualScroll);
    viewport?.removeEventListener('touchmove', onManualScroll);
    viewport?.removeEventListener('pointerdown', onManualScroll);
    player.removeEventListener('emptied', onEmptied);
  }
  function setEnabled(value) {
    const next = Boolean(value);
    indicator.hidden = !next;
    if (enabled === next) return;
    enabled = next;
    if (!enabled) clearTimings();
    else if (audioUrl && trackedSource === player.src) requestTimings();
  }
  return { setText, load, clearAudio, sync, setEnabled, dispose };
}
