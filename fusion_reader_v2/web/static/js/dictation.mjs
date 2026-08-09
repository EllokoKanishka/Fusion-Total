const DEFAULT_PAGE_CHARS = 1800;
const DEFAULT_ASSISTANT_CONTEXT_CHARS = 12000;
const WAKE_COMMAND_WINDOW_MS = 20000;
const STORAGE_KEY = 'pandafusion.dictation.v1';

function cleanText(value) {
  return String(value || '').trim();
}

export function isBareLucyInvocation(value) {
  return /^lucy(?:[\s,.:;!?_\-…]*)$/iu.test(cleanText(value));
}

export function createWakeCommandGate({ now = () => Date.now(), ttlMs = WAKE_COMMAND_WINDOW_MS } = {}) {
  let armedUntil = 0;
  return {
    arm() {
      armedUntil = now() + Math.max(1000, Number(ttlMs || WAKE_COMMAND_WINDOW_MS));
      return armedUntil;
    },
    clear() {
      armedUntil = 0;
    },
    hold() {
      if (this.isArmed()) armedUntil = Number.MAX_SAFE_INTEGER;
    },
    isArmed() {
      if (!armedUntil || now() > armedUntil) {
        armedUntil = 0;
        return false;
      }
      return true;
    },
    claim() {
      const armed = this.isArmed();
      armedUntil = 0;
      return armed;
    },
    command(transcript) {
      const clean = cleanText(transcript);
      return clean ? `Lucy, ${clean}` : 'Lucy';
    }
  };
}

function snapshot(editor) {
  return {
    value: String(editor.value || ''),
    selectionStart: Number(editor.selectionStart || 0),
    selectionEnd: Number(editor.selectionEnd || 0)
  };
}

function restoreSnapshot(editor, state) {
  editor.value = String(state && state.value || '');
  const start = Math.max(0, Math.min(Number(state && state.selectionStart || 0), editor.value.length));
  const end = Math.max(start, Math.min(Number(state && state.selectionEnd || start), editor.value.length));
  editor.selectionStart = start;
  editor.selectionEnd = end;
}

function replaceRange(editor, replacement, start, end) {
  const safeStart = Math.max(0, Math.min(Number(start || 0), editor.value.length));
  const safeEnd = Math.max(safeStart, Math.min(Number(end || safeStart), editor.value.length));
  if (typeof editor.setRangeText === 'function') {
    editor.setRangeText(String(replacement || ''), safeStart, safeEnd, 'end');
    return;
  }
  editor.value = `${editor.value.slice(0, safeStart)}${replacement}${editor.value.slice(safeEnd)}`;
  editor.selectionStart = safeStart + String(replacement || '').length;
  editor.selectionEnd = editor.selectionStart;
}

function insertionText(editor, text) {
  const incoming = String(text || '');
  const start = Number(editor.selectionStart || 0);
  const selected = Number(editor.selectionEnd || 0) > start;
  if (selected || start === 0 || !incoming || /^\s|^[,.;:!?)]/.test(incoming)) return incoming;
  const before = editor.value.slice(0, start);
  return /[\s([{\n]$/.test(before) ? incoming : ` ${incoming}`;
}

function occurrenceRanges(value, target) {
  const source = String(value || '');
  const needle = cleanText(target);
  if (!needle) return [];
  const lowered = source.toLocaleLowerCase('es');
  const loweredNeedle = needle.toLocaleLowerCase('es');
  const ranges = [];
  let offset = 0;
  while (offset <= lowered.length - loweredNeedle.length) {
    const index = lowered.indexOf(loweredNeedle, offset);
    if (index < 0) break;
    ranges.push([index, index + needle.length]);
    offset = index + Math.max(1, loweredNeedle.length);
  }
  if (ranges.length) return ranges;
  const normalizeWithMap = input => {
    let text = '';
    const map = [];
    for (let index = 0; index < input.length; index += 1) {
      const normalized = input[index].normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
      for (const char of normalized) {
        if (!/[a-z0-9ñ]/i.test(char)) continue;
        text += char;
        map.push(index);
      }
    }
    return { text, map };
  };
  const normalizedSource = normalizeWithMap(source);
  const normalizedNeedle = normalizeWithMap(needle).text;
  if (normalizedNeedle.length < 3) return [];
  let normalizedOffset = 0;
  while (normalizedOffset <= normalizedSource.text.length - normalizedNeedle.length) {
    const index = normalizedSource.text.indexOf(normalizedNeedle, normalizedOffset);
    if (index < 0) break;
    const start = normalizedSource.map[index];
    const end = normalizedSource.map[index + normalizedNeedle.length - 1] + 1;
    ranges.push([start, end]);
    normalizedOffset = index + normalizedNeedle.length;
  }
  return ranges;
}

function preferredRange(ranges, caret) {
  if (!ranges.length) return null;
  const before = ranges.filter(range => range[1] <= caret);
  return (before.length ? before[before.length - 1] : ranges[ranges.length - 1]);
}

export function applyEditorInstruction(editor, instruction) {
  const item = instruction && typeof instruction === 'object' ? instruction : {};
  const kind = String(item.kind || 'noop');
  if (kind === 'dictate' || kind === 'insert') {
    const text = kind === 'dictate' ? insertionText(editor, item.text) : String(item.text || '');
    replaceRange(editor, text, editor.selectionStart, editor.selectionEnd);
    return { changed: Boolean(text), message: kind === 'dictate' ? 'Texto agregado.' : 'Inserción aplicada.' };
  }
  if (kind === 'replace_selection') {
    const start = Number(editor.selectionStart || 0);
    const end = Number(editor.selectionEnd || start);
    if (end <= start) return { changed: false, message: 'No hay una selección para reescribir.' };
    const replacement = String(item.text || '');
    replaceRange(editor, replacement, start, end);
    return { changed: true, message: 'Selección reescrita.' };
  }
  if (kind === 'delete_from') {
    const ranges = occurrenceRanges(editor.value, item.target);
    if (!ranges.length) return { changed: false, message: `No encontré “${item.target || ''}”.` };
    const range = preferredRange(ranges, Number(editor.selectionStart || editor.value.length));
    replaceRange(editor, '', range[0], editor.value.length);
    return { changed: true, message: `Texto borrado desde “${item.target}” hasta el final.` };
  }
  if (kind === 'replace' || kind === 'delete') {
    const ranges = occurrenceRanges(editor.value, item.target);
    if (!ranges.length) return { changed: false, message: `No encontré “${item.target || ''}”.` };
    const replacement = kind === 'replace' ? String(item.text || '') : '';
    if (item.all_matches) {
      for (const range of [...ranges].reverse()) replaceRange(editor, replacement, range[0], range[1]);
      return { changed: true, message: `${ranges.length} coincidencia${ranges.length === 1 ? '' : 's'} modificada${ranges.length === 1 ? '' : 's'}.` };
    }
    const range = preferredRange(ranges, Number(editor.selectionStart || editor.value.length));
    replaceRange(editor, replacement, range[0], range[1]);
    return { changed: true, message: kind === 'replace' ? 'Corrección aplicada.' : 'Texto borrado.' };
  }
  if (kind === 'clear') {
    const changed = Boolean(editor.value);
    editor.value = '';
    editor.selectionStart = 0;
    editor.selectionEnd = 0;
    return { changed, message: changed ? 'Borrador limpiado; podés deshacerlo.' : 'El borrador ya estaba vacío.' };
  }
  return { changed: false, message: '' };
}

export function paragraphRanges(value) {
  const text = String(value || '');
  const ranges = [];
  const separator = /\n\s*\n+/g;
  let start = 0;
  let match;
  while ((match = separator.exec(text)) !== null) {
    const raw = text.slice(start, match.index);
    const leading = raw.search(/\S/);
    if (leading >= 0) {
      const trailing = raw.length - raw.trimEnd().length;
      ranges.push({ start: start + leading, end: match.index - trailing });
    }
    start = separator.lastIndex;
  }
  const raw = text.slice(start);
  const leading = raw.search(/\S/);
  if (leading >= 0) ranges.push({ start: start + leading, end: text.length - (raw.length - raw.trimEnd().length) });
  return ranges;
}

function rangeForCaret(ranges, caret) {
  return ranges.find(range => caret >= range.start && caret <= range.end) ||
    [...ranges].reverse().find(range => range.end <= caret) || ranges[0] || null;
}

export function readTextForInstruction(editor, instruction, pageChars = DEFAULT_PAGE_CHARS) {
  const text = String(editor.value || '');
  if (!text.trim()) return { text: '', label: '', error: 'El borrador está vacío.' };
  const item = instruction && typeof instruction === 'object' ? instruction : {};
  const scope = String(item.scope || 'selection');
  const selectedStart = Number(editor.selectionStart || 0);
  const selectedEnd = Number(editor.selectionEnd || selectedStart);
  const paragraphs = paragraphRanges(text);
  let range = null;
  if (scope === 'all') range = { start: 0, end: text.length };
  if (scope === 'selection' && selectedEnd > selectedStart) range = { start: selectedStart, end: selectedEnd };
  if (scope === 'selection' && !range) range = rangeForCaret(paragraphs, selectedStart);
  if (scope === 'current_paragraph') range = rangeForCaret(paragraphs, selectedStart);
  if (scope === 'previous_paragraph') {
    const current = rangeForCaret(paragraphs, selectedStart);
    const index = current ? paragraphs.indexOf(current) : -1;
    range = index > 0 ? paragraphs[index - 1] : null;
  }
  if (scope === 'last_paragraph') range = paragraphs[paragraphs.length - 1] || null;
  if (scope === 'paragraph_number') range = paragraphs[Math.max(0, Number(item.number || 1) - 1)] || null;
  if (scope === 'paragraph_matching') {
    const target = cleanText(item.target).toLocaleLowerCase('es');
    range = paragraphs.find(candidate => text.slice(candidate.start, candidate.end).toLocaleLowerCase('es').includes(target)) || null;
  }
  if (scope === 'from_cursor') range = { start: selectedStart, end: text.length };
  if (scope === 'from_text') {
    const target = cleanText(item.target).toLocaleLowerCase('es');
    const index = text.toLocaleLowerCase('es').indexOf(target);
    if (index >= 0) range = { start: index, end: text.length };
  }
  if (scope === 'last_page') {
    let start = Math.max(0, text.length - Math.max(400, Number(pageChars || DEFAULT_PAGE_CHARS)));
    if (start > 0) {
      const paragraphBreak = text.indexOf('\n\n', start);
      const wordBreak = text.indexOf(' ', start);
      start = paragraphBreak >= 0 ? paragraphBreak + 2 : (wordBreak >= 0 ? wordBreak + 1 : start);
    }
    range = { start, end: text.length };
  }
  if (!range) return { text: '', label: '', error: 'No encontré ese tramo en el borrador.' };
  const selected = text.slice(range.start, range.end).trim();
  if (!selected) return { text: '', label: '', error: 'Ese tramo no contiene texto.' };
  return { text: selected, start: range.start, end: range.end, label: scope };
}

export function splitSpeechText(value, maxChars = 620) {
  const clean = String(value || '').trim();
  if (!clean) return [];
  const limit = Math.max(160, Number(maxChars || 620));
  const sentences = clean.split(/(?<=[.!?])\s+|\n+/).filter(Boolean);
  const chunks = [];
  let current = '';
  for (const sentence of sentences) {
    if (sentence.length > limit) {
      if (current) chunks.push(current);
      current = '';
      const words = sentence.split(/\s+/);
      let part = '';
      for (const word of words) {
        const candidate = part ? `${part} ${word}` : word;
        if (part && candidate.length > limit) {
          chunks.push(part);
          part = word;
        } else {
          part = candidate;
        }
      }
      if (part) chunks.push(part);
      continue;
    }
    const candidate = current ? `${current} ${sentence}` : sentence;
    if (current && candidate.length > limit) {
      chunks.push(current);
      current = sentence;
    } else {
      current = candidate;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

export function dictationAssistantContext(editor, maxChars = DEFAULT_ASSISTANT_CONTEXT_CHARS) {
  const value = String(editor.value || '');
  const limit = Math.max(1000, Number(maxChars || DEFAULT_ASSISTANT_CONTEXT_CHARS));
  const selectionStart = Math.max(0, Math.min(Number(editor.selectionStart || 0), value.length));
  const selectionEnd = Math.max(selectionStart, Math.min(Number(editor.selectionEnd || selectionStart), value.length));
  let start = Math.max(0, selectionStart - Math.floor(limit / 2));
  let end = Math.min(value.length, start + limit);
  start = Math.max(0, end - limit);
  return {
    draft: value.slice(start, end),
    selection_start: selectionStart - start,
    selection_end: Math.min(selectionEnd, end) - start,
    context_start: start
  };
}

function recorderMimeType(windowRef) {
  const Recorder = windowRef && windowRef.MediaRecorder;
  if (!Recorder) return '';
  for (const type of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']) {
    if (typeof Recorder.isTypeSupported !== 'function' || Recorder.isTypeSupported(type)) return type;
  }
  return '';
}

export function createDictationController({
  api,
  elements,
  refreshMainStatus,
  log,
  documentRoot = document,
  windowRef = window,
  fetchFn = fetch,
  storage = window.localStorage
}) {
  const editor = elements.dictationEditor;
  const undoStack = [];
  const redoStack = [];
  const activity = [];
  let saveTimer = 0;
  let manualTimer = 0;
  let assistantInstallTimer = 0;
  let wakeExpiryTimer = 0;
  let manualBaseline = null;
  let stream = null;
  let audioContext = null;
  let analyser = null;
  let recorder = null;
  let monitorId = 0;
  let active = false;
  let processing = false;
  let speaking = false;
  let discardRecording = false;
  let speechSequence = 0;
  let recorderStartedAt = 0;
  let voiceDetected = false;
  let silenceMs = 0;
  let speechMs = 0;
  let lastTick = 0;
  let noiseFloor = 0.012;
  let chunks = [];
  const wakeGate = createWakeCommandGate({ now: () => Date.now() });

  function setStatus(message, mode = '') {
    elements.dictationStatus.textContent = message;
    elements.dictationStatus.dataset.mode = mode;
  }

  function addActivity(message) {
    const clean = cleanText(message);
    if (!clean) return;
    activity.unshift(clean);
    activity.splice(20);
    elements.dictationActivity.innerHTML = '';
    for (const item of activity) {
      const row = documentRoot.createElement('div');
      row.className = 'dictation-activity-row';
      row.textContent = item;
      elements.dictationActivity.appendChild(row);
    }
  }

  function renderAssistantStatus(data) {
    const available = Array.isArray(data && data.available) ? data.available : [];
    const select = elements.dictationAssistantSelect;
    const previous = String(data && (data.selected || data.id) || select.value || 'rules');
    select.innerHTML = '';
    for (const item of available) {
      const option = documentRoot.createElement('option');
      option.value = String(item.id || 'rules');
      option.textContent = `${item.label || item.id}${item.model ? ` · ${item.model}` : ''}`;
      option.title = String(item.description || '');
      select.appendChild(option);
    }
    if (!select.options.length) {
      const option = documentRoot.createElement('option');
      option.value = 'rules';
      option.textContent = 'Reglas instantáneas';
      select.appendChild(option);
    }
    select.value = previous;
    if (!select.value) select.value = 'rules';
    const selected = available.find(item => String(item.id || '') === select.value) || {};
    const ready = data && typeof data.ready === 'boolean' ? data.ready : true;
    const installation = data && data.installation && typeof data.installation === 'object' ? data.installation : {};
    const installing = ['queued', 'running'].includes(String(installation.state || ''));
    elements.dictationAssistantStatus.dataset.ready = String(ready);
    elements.dictationAssistantStatus.textContent = installing
      ? `instalando ${installation.model || selected.model || 'modelo local'}…`
      : selected.cloud
        ? (ready ? 'nube · sólo al invocar' : 'nube no disponible')
        : (select.value === 'rules' ? 'sin modelo' : (ready ? 'local · carga bajo demanda' : 'modelo local no instalado'));
    elements.dictationAssistantInstallBtn.hidden = select.value !== 'local' || (ready && !installing);
    elements.dictationAssistantInstallBtn.disabled = installing;
    elements.dictationAssistantInstallBtn.textContent = installing
      ? 'Instalando…'
      : `Instalar ${selected.model || 'modelo local'}`;
  }

  async function refreshAssistantStatus() {
    try {
      renderAssistantStatus(await api('/api/dictation/assistant'));
    } catch (error) {
      renderAssistantStatus({ selected: 'rules', ready: true, available: [] });
      addActivity(`No pude consultar los asistentes: ${error.message}.`);
    }
  }

  async function changeAssistant() {
    const provider = String(elements.dictationAssistantSelect.value || 'rules');
    try {
      const data = await api('/api/dictation/assistant', { provider });
      renderAssistantStatus(data);
      const selected = (data.available || []).find(item => String(item.id || '') === data.selected) || {};
      addActivity(`Asistente: ${selected.label || provider}${selected.model ? ` (${selected.model})` : ''}.`);
    } catch (error) {
      addActivity(`No pude cambiar el asistente: ${error.message}.`);
      await refreshAssistantStatus();
    }
  }

  async function pollAssistantInstallation() {
    windowRef.clearTimeout(assistantInstallTimer);
    assistantInstallTimer = 0;
    try {
      const data = await api('/api/dictation/assistant');
      renderAssistantStatus(data);
      const installation = data.installation || {};
      if (['queued', 'running'].includes(String(installation.state || ''))) {
        assistantInstallTimer = windowRef.setTimeout(pollAssistantInstallation, 1200);
        return;
      }
      if (installation.state === 'done') addActivity(`${installation.model || 'El modelo local'} quedó instalado y listo.`);
      if (installation.state === 'error') addActivity(`No pude instalar el modelo local: ${installation.detail || 'error desconocido'}.`);
    } catch (error) {
      addActivity(`No pude consultar la instalación: ${error.message}.`);
    }
  }

  async function installAssistantModel() {
    const label = cleanText(elements.dictationAssistantSelect.selectedOptions?.[0]?.textContent) || 'el modelo local';
    if (!windowRef.confirm(`¿Descargar e instalar ${label}? La descarga se hace una sola vez y puede ocupar varios GB.`)) return;
    elements.dictationAssistantInstallBtn.disabled = true;
    elements.dictationAssistantInstallBtn.textContent = 'Preparando…';
    try {
      const data = await api('/api/dictation/assistant/install', {});
      addActivity(`Instalación iniciada: ${data.model || label}. Podés seguir usando el dictado.`);
      await pollAssistantInstallation();
    } catch (error) {
      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;
      addActivity(`No pude iniciar la instalación: ${detail}.`);
      await refreshAssistantStatus();
    }
  }

  function clearWakeCommand() {
    wakeGate.clear();
    windowRef.clearTimeout(wakeExpiryTimer);
    wakeExpiryTimer = 0;
  }

  function scheduleWakeExpiry() {
    windowRef.clearTimeout(wakeExpiryTimer);
    wakeExpiryTimer = windowRef.setTimeout(() => {
      if (!wakeGate.isArmed()) {
        wakeExpiryTimer = 0;
        addActivity('La espera de la orden venció; volvé a decir “Lucy”.');
        if (active && !processing && !speaking) setStatus('Escuchando… hacé una pausa y aplico el tramo.', 'listening');
      }
    }, WAKE_COMMAND_WINDOW_MS + 20);
  }

  function armWakeCommand() {
    wakeGate.arm();
    scheduleWakeExpiry();
    addActivity('Lucy quedó atenta. Decí ahora la orden completa.');
  }

  function updateStats() {
    const value = String(editor.value || '');
    const words = value.trim() ? value.trim().split(/\s+/).length : 0;
    const pages = value.length ? Math.ceil(value.length / DEFAULT_PAGE_CHARS) : 0;
    elements.dictationStats.textContent = `${value.length} caracteres · ${words} palabras · ${pages} hoja${pages === 1 ? '' : 's'} virtual${pages === 1 ? '' : 'es'}`;
    elements.dictationUndoBtn.disabled = undoStack.length === 0;
    elements.dictationRedoBtn.disabled = redoStack.length === 0;
  }

  function persistNow() {
    windowRef.clearTimeout(saveTimer);
    saveTimer = 0;
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify({
        title: elements.dictationTitleInput.value,
        text: editor.value,
        updatedAt: Date.now()
      }));
    } catch (_) {}
    updateStats();
  }

  function schedulePersist() {
    windowRef.clearTimeout(saveTimer);
    saveTimer = windowRef.setTimeout(persistNow, 350);
    updateStats();
  }

  function restoreDraft() {
    try {
      const saved = JSON.parse(storage.getItem(STORAGE_KEY) || '{}');
      if (saved && typeof saved === 'object') {
        editor.value = String(saved.text || '');
        elements.dictationTitleInput.value = String(saved.title || 'Dictado sin título');
      }
    } catch (_) {}
    if (!elements.dictationTitleInput.value) elements.dictationTitleInput.value = 'Dictado sin título';
    editor.selectionStart = editor.value.length;
    editor.selectionEnd = editor.value.length;
    updateStats();
  }

  function pushUndo(state) {
    if (!state) return;
    const previous = undoStack[undoStack.length - 1];
    if (previous && previous.value === state.value && previous.selectionStart === state.selectionStart && previous.selectionEnd === state.selectionEnd) return;
    undoStack.push(state);
    if (undoStack.length > 100) undoStack.shift();
  }

  function flushManualHistory() {
    windowRef.clearTimeout(manualTimer);
    manualTimer = 0;
    if (manualBaseline) pushUndo(manualBaseline);
    manualBaseline = null;
  }

  function mutate(instruction) {
    flushManualHistory();
    const before = snapshot(editor);
    const result = applyEditorInstruction(editor, instruction);
    if (result.changed) {
      pushUndo(before);
      redoStack.length = 0;
      schedulePersist();
      editor.focus();
    }
    if (result.message) addActivity(result.message);
    updateStats();
    return result;
  }

  function undo() {
    flushManualHistory();
    const previous = undoStack.pop();
    if (!previous) {
      addActivity('No hay nada para deshacer.');
      return;
    }
    redoStack.push(snapshot(editor));
    restoreSnapshot(editor, previous);
    schedulePersist();
    addActivity('Cambio deshecho.');
  }

  function redo() {
    flushManualHistory();
    const next = redoStack.pop();
    if (!next) {
      addActivity('No hay nada para rehacer.');
      return;
    }
    pushUndo(snapshot(editor));
    restoreSnapshot(editor, next);
    schedulePersist();
    addActivity('Cambio rehecho.');
  }

  function stopSpeech() {
    speechSequence += 1;
    speaking = false;
    try { elements.dictationPlayer.pause(); } catch (_) {}
    elements.dictationPlayer.removeAttribute('src');
    if (active && !processing) startRecorderCycle();
  }

  async function playAudioUrl(url, sequence) {
    if (!url || sequence !== speechSequence) return;
    elements.dictationPlayer.src = url;
    await new Promise((resolve, reject) => {
      const cleanup = () => {
        elements.dictationPlayer.removeEventListener('ended', onEnded);
        elements.dictationPlayer.removeEventListener('error', onError);
      };
      const onEnded = () => { cleanup(); resolve(); };
      const onError = () => { cleanup(); reject(new Error('audio_playback_failed')); };
      elements.dictationPlayer.addEventListener('ended', onEnded, { once: true });
      elements.dictationPlayer.addEventListener('error', onError, { once: true });
      elements.dictationPlayer.play().catch(error => { cleanup(); reject(error); });
    });
  }

  async function speakText(text, label = 'tramo') {
    const parts = splitSpeechText(text);
    if (!parts.length) return;
    stopRecorderCycle(true);
    speaking = true;
    speechSequence += 1;
    const sequence = speechSequence;
    setStatus(`Leyendo ${label}: 1 de ${parts.length}…`, 'speaking');
    try {
      for (let index = 0; index < parts.length && sequence === speechSequence; index += 1) {
        setStatus(`Leyendo ${label}: ${index + 1} de ${parts.length}…`, 'speaking');
        const data = await api('/api/dictation/speak', { text: parts[index] });
        if (!data.audio_url) throw new Error('La voz no devolvió un audio reproducible.');
        await playAudioUrl(data.audio_url, sequence);
      }
      if (sequence === speechSequence) addActivity(`Lectura terminada (${label}).`);
    } catch (error) {
      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;
      addActivity(`No pude leer: ${detail}.`);
    } finally {
      if (sequence === speechSequence) {
        speaking = false;
        setStatus(active ? 'Escuchando el próximo tramo…' : 'Dictado en pausa.', active ? 'listening' : '');
        if (active && !processing) startRecorderCycle();
      }
    }
  }

  async function requestAssistant(transcript) {
    const context = dictationAssistantContext(editor);
    setStatus('Lucy está interpretando la orden…', 'processing');
    try {
      const data = await api('/api/dictation/assist', { text: transcript, ...context });
      const model = data.assistant_model || data.assistant_provider || 'asistente';
      addActivity(`Lucy interpretó la orden con ${model} (${data.assistant_ms || 0} ms).`);
      return applyInstruction(data.instruction, '', false, true);
    } catch (error) {
      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;
      addActivity(`${detail} No cambié el texto.`);
    } finally {
      setStatus(active ? 'Escuchando el próximo tramo…' : 'Dictado en pausa.', active ? 'listening' : '');
    }
  }

  async function applyInstruction(instruction, transcript = '', allowAssistant = false, assistantAttempted = false) {
    const item = instruction && typeof instruction === 'object' ? instruction : { kind: 'noop' };
    if (transcript) addActivity(`Oí: “${transcript}”`);
    if (item.kind === 'undo') return undo();
    if (item.kind === 'redo') return redo();
    if (item.kind === 'stop_listening') {
      addActivity('Dictado detenido por voz.');
      return stopListening();
    }
    if (item.kind === 'read') {
      const selection = readTextForInstruction(editor, item);
      if (selection.error) {
        if (allowAssistant && !assistantAttempted && elements.dictationAssistantSelect.value !== 'rules') {
          return requestAssistant(transcript);
        }
        addActivity(selection.error);
        return;
      }
      editor.selectionStart = selection.start;
      editor.selectionEnd = selection.end;
      return speakText(selection.text, item.scope || 'selección');
    }
    if (item.kind === 'noop' && allowAssistant && !assistantAttempted && elements.dictationAssistantSelect.value !== 'rules') {
      return requestAssistant(transcript);
    }
    if (item.kind === 'noop' && (/^lucy(?:\b|(?=[,.:;!?_-]))/i.test(String(transcript || '').trim()) || assistantAttempted)) {
      addActivity('Lucy oyó la invocación, pero no reconoció una orden segura. No cambié el texto.');
      return;
    }
    const result = mutate(item);
    if (!result.changed && allowAssistant && !assistantAttempted &&
        ['replace', 'delete', 'delete_from'].includes(String(item.kind || '')) &&
        elements.dictationAssistantSelect.value !== 'rules') {
      return requestAssistant(transcript);
    }
    return result;
  }

  async function interpretTypedCommand() {
    const text = cleanText(elements.dictationCommandInput.value);
    if (!text) return;
    elements.dictationCommandInput.value = '';
    try {
      const data = await api('/api/dictation/interpret', {
        text,
        commands_enabled: elements.dictationCommandsToggle.checked
      });
      await applyInstruction(data.instruction, data.transcript, true);
    } catch (error) {
      addActivity(`No pude interpretar la orden: ${error.message}.`);
    }
  }

  function micLevel() {
    if (!analyser) return 0;
    const data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const value of data) {
      const centered = (value - 128) / 128;
      sum += centered * centered;
    }
    return Math.sqrt(sum / data.length);
  }

  function monitorRecorder() {
    if (!active || !recorder || recorder.state !== 'recording') return;
    const now = windowRef.performance.now();
    const delta = Math.max(16, Math.min(250, now - (lastTick || now)));
    lastTick = now;
    const level = micLevel();
    const threshold = Math.max(0.018, noiseFloor * 2.15);
    if (level >= threshold) {
      speechMs += delta;
      silenceMs = 0;
      if (speechMs >= 45 && !voiceDetected) {
        voiceDetected = true;
        if (wakeGate.isArmed()) {
          wakeGate.hold();
          windowRef.clearTimeout(wakeExpiryTimer);
          wakeExpiryTimer = 0;
        }
      }
    } else {
      silenceMs += delta;
      speechMs = Math.max(0, speechMs - delta * 0.5);
      if (!voiceDetected) noiseFloor = noiseFloor * 0.96 + level * 0.04;
    }
    const elapsed = now - recorderStartedAt;
    if ((voiceDetected && elapsed >= 650 && silenceMs >= 1150) || elapsed >= 30000) {
      stopRecorderCycle(false);
      return;
    }
    monitorId = windowRef.requestAnimationFrame(monitorRecorder);
  }

  async function uploadRecording(blob, mime, claimedWakeCommand = false) {
    processing = true;
    setStatus(claimedWakeCommand ? 'Transcribiendo la orden…' : 'Transcribiendo y aplicando…', 'processing');
    try {
      const params = new URLSearchParams({
        filename: mime.includes('ogg') ? 'dictation.ogg' : 'dictation.webm',
        commands: elements.dictationCommandsToggle.checked ? '1' : '0'
      });
      const response = await fetchFn(`/api/dictation/transcribe?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': mime || 'audio/webm' },
        body: blob
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.detail || data.error || 'dictation_failed');
      const transcript = String(data.transcript || '').trim();
      if (!claimedWakeCommand && elements.dictationCommandsToggle.checked && isBareLucyInvocation(transcript)) {
        addActivity(`Oí: “${transcript}”`);
        armWakeCommand();
        log(`Dictado: ${transcript || 'sin texto'} (${data.stt_provider || 'STT'}). Esperando orden.`);
        return;
      }
      let instruction = data.instruction;
      let appliedTranscript = transcript;
      if (claimedWakeCommand) {
        appliedTranscript = wakeGate.command(transcript);
        const interpreted = await api('/api/dictation/interpret', {
          text: appliedTranscript,
          commands_enabled: true
        });
        instruction = interpreted.instruction;
      }
      const invoked = claimedWakeCommand || /^lucy(?:\b|(?=[,.:;!?_-]))/i.test(appliedTranscript);
      await applyInstruction(instruction, appliedTranscript, invoked);
      log(`Dictado: ${appliedTranscript || 'sin texto'} (${data.stt_provider || 'STT'}).`);
    } catch (error) {
      addActivity(`Falló la transcripción: ${error.message}.`);
    } finally {
      processing = false;
      if (active && !speaking) {
        startRecorderCycle();
      }
    }
  }

  function stopRecorderCycle(discard = false) {
    windowRef.cancelAnimationFrame(monitorId);
    monitorId = 0;
    if (!recorder || recorder.state === 'inactive') return;
    discardRecording = discard;
    try { recorder.stop(); } catch (_) {}
  }

  function startRecorderCycle() {
    if (!active || processing || speaking || !stream || (recorder && recorder.state === 'recording')) return;
    const mime = recorderMimeType(windowRef);
    try {
      recorder = mime ? new windowRef.MediaRecorder(stream, { mimeType: mime }) : new windowRef.MediaRecorder(stream);
    } catch (error) {
      setStatus(`No pude iniciar la grabación: ${error.message}.`, 'error');
      stopListening();
      return;
    }
    chunks = [];
    discardRecording = false;
    voiceDetected = false;
    silenceMs = 0;
    speechMs = 0;
    noiseFloor = 0.012;
    recorderStartedAt = windowRef.performance.now();
    lastTick = recorderStartedAt;
    recorder.addEventListener('dataavailable', event => {
      if (event.data && event.data.size) chunks.push(event.data);
    });
    recorder.addEventListener('stop', () => {
      const shouldDiscard = discardRecording || !active || !voiceDetected;
      const blob = new windowRef.Blob(chunks, { type: recorder.mimeType || mime || 'audio/webm' });
      chunks = [];
      recorder = null;
      if (shouldDiscard || blob.size < 900) {
        if (wakeGate.isArmed() && !wakeExpiryTimer) {
          wakeGate.arm();
          scheduleWakeExpiry();
        }
        if (active && !processing && !speaking) windowRef.setTimeout(startRecorderCycle, 180);
        return;
      }
      const claimedWakeCommand = wakeGate.claim();
      if (claimedWakeCommand) {
        windowRef.clearTimeout(wakeExpiryTimer);
        wakeExpiryTimer = 0;
      }
      uploadRecording(blob, blob.type || mime, claimedWakeCommand);
    }, { once: true });
    recorder.start(250);
    setStatus(
      wakeGate.isArmed() ? 'Lucy está escuchando tu orden…' : 'Escuchando… hacé una pausa y aplico el tramo.',
      wakeGate.isArmed() ? 'armed' : 'listening'
    );
    monitorId = windowRef.requestAnimationFrame(monitorRecorder);
  }

  async function startListening() {
    if (active) return;
    if (!windowRef.MediaRecorder || !windowRef.navigator.mediaDevices || !windowRef.navigator.mediaDevices.getUserMedia) {
      setStatus('Este navegador no ofrece captura de audio compatible.', 'error');
      return;
    }
    try {
      stream = await windowRef.navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 }
      });
      const AudioContext = windowRef.AudioContext || windowRef.webkitAudioContext;
      audioContext = new AudioContext();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      audioContext.createMediaStreamSource(stream).connect(analyser);
      active = true;
      elements.dictationMicBtn.textContent = 'Detener dictado';
      elements.dictationMicBtn.classList.add('recording');
      addActivity('Micrófono abierto. Las pausas separan los tramos.');
      startRecorderCycle();
    } catch (error) {
      setStatus(`No pude abrir el micrófono: ${error.message}.`, 'error');
    }
  }

  function stopListening() {
    active = false;
    clearWakeCommand();
    stopRecorderCycle(true);
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (audioContext) audioContext.close().catch(() => {});
    stream = null;
    audioContext = null;
    analyser = null;
    elements.dictationMicBtn.textContent = 'Iniciar dictado';
    elements.dictationMicBtn.classList.remove('recording');
    setStatus('Dictado en pausa.', '');
  }

  function open() {
    elements.appRoot.classList.add('dictation-open');
    elements.dictationWorkspace.hidden = false;
    elements.dictationToggleBtn.setAttribute('aria-expanded', 'true');
    editor.focus();
  }

  function close() {
    stopListening();
    stopSpeech();
    elements.appRoot.classList.remove('dictation-open');
    elements.dictationWorkspace.hidden = true;
    elements.dictationToggleBtn.setAttribute('aria-expanded', 'false');
    elements.dictationToggleBtn.focus();
  }

  async function mountInReader() {
    if (!editor.value.trim()) {
      addActivity('No hay texto para pasar al lector.');
      return;
    }
    try {
      const data = await api('/api/quick-text', {
        text: editor.value,
        title: cleanText(elements.dictationTitleInput.value) || 'Dictado',
        start_offset: Number(editor.selectionStart || 0)
      });
      await refreshMainStatus(data);
      addActivity('Borrador pasado al lector como texto temporal.');
    } catch (error) {
      addActivity(`No pude pasar el borrador al lector: ${error.message}.`);
    }
  }

  function downloadText() {
    if (!editor.value.trim()) {
      addActivity('No hay texto para descargar.');
      return;
    }
    const title = cleanText(elements.dictationTitleInput.value) || 'dictado';
    const filename = `${title.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9_-]+/gi, '-').replace(/^-+|-+$/g, '') || 'dictado'}.txt`;
    const url = windowRef.URL.createObjectURL(new windowRef.Blob([editor.value], { type: 'text/plain;charset=utf-8' }));
    const anchor = documentRoot.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    documentRoot.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    windowRef.URL.revokeObjectURL(url);
    addActivity(`Descargado: ${filename}.`);
  }

  elements.dictationToggleBtn.addEventListener('click', () => elements.dictationWorkspace.hidden ? open() : close());
  elements.dictationCloseBtn.addEventListener('click', close);
  elements.dictationMicBtn.addEventListener('click', () => active ? stopListening() : startListening());
  elements.dictationStopSpeechBtn.addEventListener('click', stopSpeech);
  elements.dictationUndoBtn.addEventListener('click', undo);
  elements.dictationRedoBtn.addEventListener('click', redo);
  elements.dictationReadBtn.addEventListener('click', () => {
    const selection = readTextForInstruction(editor, { kind: 'read', scope: 'selection' });
    if (selection.error) return addActivity(selection.error);
    speakText(selection.text, 'selección');
  });
  elements.dictationUseReaderBtn.addEventListener('click', mountInReader);
  elements.dictationDownloadBtn.addEventListener('click', downloadText);
  elements.dictationClearBtn.addEventListener('click', () => {
    if (editor.value && !windowRef.confirm('¿Limpiar el borrador de dictado? Podrás deshacerlo mientras esta pestaña siga abierta.')) return;
    mutate({ kind: 'clear' });
  });
  elements.dictationCommandBtn.addEventListener('click', interpretTypedCommand);
  elements.dictationAssistantSelect.addEventListener('change', changeAssistant);
  elements.dictationAssistantInstallBtn.addEventListener('click', installAssistantModel);
  elements.dictationCommandsToggle.addEventListener('change', () => {
    if (!elements.dictationCommandsToggle.checked) clearWakeCommand();
  });
  elements.dictationCommandInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      interpretTypedCommand();
    }
  });
  editor.addEventListener('beforeinput', () => {
    if (!manualBaseline) manualBaseline = snapshot(editor);
  });
  editor.addEventListener('input', () => {
    windowRef.clearTimeout(manualTimer);
    manualTimer = windowRef.setTimeout(flushManualHistory, 700);
    schedulePersist();
  });
  elements.dictationTitleInput.addEventListener('input', schedulePersist);

  restoreDraft();
  refreshAssistantStatus();

  return {
    open,
    close,
    undo,
    redo,
    applyInstruction,
    stopListening,
    stopSpeech,
    dispose() {
      flushManualHistory();
      persistNow();
      stopListening();
      stopSpeech();
      windowRef.clearTimeout(saveTimer);
      windowRef.clearTimeout(manualTimer);
      windowRef.clearTimeout(assistantInstallTimer);
      windowRef.clearTimeout(wakeExpiryTimer);
    }
  };
}

export { DEFAULT_ASSISTANT_CONTEXT_CHARS, DEFAULT_PAGE_CHARS, STORAGE_KEY, WAKE_COMMAND_WINDOW_MS };
