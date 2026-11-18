const DEFAULT_PAGE_CHARS = 1800;
const DEFAULT_ASSISTANT_CONTEXT_CHARS = 12000;
const WAKE_COMMAND_WINDOW_MS = 20000;
const MAX_DICTATION_UTTERANCE_MS = 10000;
const END_OF_UTTERANCE_SILENCE_MS = 1000;
const STORAGE_KEY = 'pandafusion.dictation.v1';
const SESSION_SCHEMA_VERSION = 2;
const MAX_SAVED_SESSIONS = 30;

function cleanText(value) {
  return String(value || '').trim();
}

export function isBareLucyInvocation(value) {
  return /^l[uú]c(?:y|[ií])(?:[\s,.:;!?_\-…]*)$/iu.test(cleanText(value));
}

export function hasLucyInvocation(value) {
  return /^l[uú]c(?:y|[ií])(?![\p{L}\p{N}_])/iu.test(cleanText(value));
}

// Inline invocations require an imperative; mentions and quoted dialogue stay literal.
export function splitLucyUtterance(value) {
  const text = cleanText(value);
  if (hasLucyInvocation(text)) return { prefix: '', command: text };
  const wake = /(?<![\p{L}\p{N}_])l[uú]c(?:y|[ií])(?=$|[\s,.:;!?])(?![\p{L}\p{N}_])/giu;
  const imperative = /^(?:(?:quiero\s+)?que\s+)?(?:cambi[aá]|cambia|reemplaz[aá]|reemplaza|sustitu[ií]|sustituye|borr[aáe]|borres|elimin[aáe]|elimines|quit[aáe]|quites|deshac[eé]|deshacer|rehac[eé]|rehacer|correg[ií]|corrige|revis[aá]|revisa|l[eé]eme|lee|par[aá]|para|detener|escrib[ií]|escribe|pon[eé]|pon|agreg[aá])(?=$|[^\p{L}\p{N}_])/iu;
  let quote = '';
  const closing = { '"': '"', "'": "'", '«': '»', '“': '”' };
  for (const match of text.matchAll(wake)) {
    quote = '';
    for (const char of text.slice(0, match.index)) {
      if (quote) { if (char === quote) quote = ''; }
      else if (closing[char]) quote = closing[char];
    }
    const tail = text.slice(match.index + match[0].length).replace(/^[\s,.:;!?]+/u, '');
    if (!quote && (!tail || imperative.test(tail))) return { prefix: text.slice(0, match.index).trim(), command: text.slice(match.index) };
  }
  return null;
}

function rejectsLastEdit(transcript) {
  const words = transcript.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/^luc[iy]\s+/, '');
  return /^(?:no (?:no )?)?(?:quedo mal(?: escrito)?|esta mal(?: escrito)?|asi no|eso no era(?: lo que pedi)?)$/.test(words) ||
    /^(?:no )?no (?:quedo como queria|era eso|es lo que pedi)$/.test(words);
}

export function commandConfirmation(instruction, result) {
  if (!result?.changed) return String(result?.message || 'No hice cambios.').slice(0, 160);
  if (instruction.kind === 'replace_many') return `Listo. Apliqué ${result.changes} sustituciones.`;
  if (instruction.kind === 'replace' && String(instruction.target).length <= 40 && String(instruction.text).length <= 40) {
    return `Listo. Cambié ${instruction.target} por ${instruction.text}.`;
  }
  const summary = { undo: 'Deshice el último cambio.', redo: 'Rehice el cambio.', delete: 'Borré el texto indicado.', delete_sentence: 'Borré la frase.', clear: 'Limpié el borrador.', proofread: 'Corregí el tramo.' };
  return `Listo. ${summary[instruction.kind] || 'Apliqué el cambio.'}`;
}

export function invokedInterpretationPayload(transcript) {
  const clean = cleanText(transcript);
  return {
    text: hasLucyInvocation(clean) ? clean : `Lucy, ${clean}`,
    commands_enabled: true,
    require_wake_word: true
  };
}

export function assistantFailureActivity(error) {
  const detail = error && error.data && error.data.detail ? error.data.detail : (error && error.message) || 'El asistente falló.';
  const technical = error && error.data && error.data.technical_detail ? error.data.technical_detail : '';
  const model = error && error.data && error.data.assistant_model ? error.data.assistant_model : 'asistente';
  const elapsed = error && error.data ? Number(error.data.assistant_ms || 0) : 0;
  const unchanged = /no cambi[eé] el texto/i.test(detail) ? '' : ' No cambié el texto.';
  return `${detail} [${model} · ${technical || 'sin detalle'} · ${elapsed} ms]${unchanged}`;
}

export function microphoneFailureMessage(error) {
  const name = cleanText(error && error.name);
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'El escritorio no autorizó el micrófono.';
  }
  if (name === 'NotFoundError') {
    return 'No encontré un micrófono disponible.';
  }
  if (name === 'NotReadableError') {
    return 'El micrófono está ocupado por otra aplicación.';
  }
  if (name === 'OverconstrainedError') {
    return 'El micrófono no admite esta configuración.';
  }
  const detail = cleanText(error && error.message);
  return detail ? `No pude abrir el micrófono: ${detail}.` : 'No pude abrir el micrófono.';
}

export function shouldRetryWithBasicMicrophone(error) {
  return cleanText(error && error.name) === 'OverconstrainedError';
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
    reserve() {
      if (!this.isArmed()) return false;
      armedUntil = Number.MAX_SAFE_INTEGER;
      return true;
    },
    hold() {
      return this.reserve();
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
  const isWordCharacter = value => Boolean(value) && /[\p{L}\p{M}\p{N}_]/u.test(value);
  const before = (text, index) => [...text.slice(Math.max(0, index - 2), index)].at(-1) || '';
  const after = (text, index) => index < text.length ? String.fromCodePoint(text.codePointAt(index)) : '';
  const startsWithWord = isWordCharacter([...loweredNeedle][0]);
  const endsWithWord = isWordCharacter([...loweredNeedle].at(-1));
  let offset = 0;
  while (lowered.length === source.length && loweredNeedle.length === needle.length && offset <= lowered.length - loweredNeedle.length) {
    const index = lowered.indexOf(loweredNeedle, offset);
    if (index < 0) break;
    const end = index + loweredNeedle.length;
    const leftBounded = !startsWithWord || !isWordCharacter(before(source, index));
    const rightBounded = !endsWithWord || !isWordCharacter(after(source, end));
    if (leftBounded && rightBounded) ranges.push([index, index + needle.length]);
    offset = index + Math.max(1, loweredNeedle.length);
  }
  if (ranges.length) return ranges;
  const normalizeWithMap = input => {
    let text = '';
    const starts = [], ends = [];
    let index = 0;
    for (const original of input) {
      const end = index + original.length;
      const normalized = original.toLocaleLowerCase('es').normalize('NFD').replace(/\p{M}/gu, '');
      if (!normalized && ends.length) ends[ends.length - 1] = end;
      for (const char of normalized) {
        const mapped = /[\p{L}\p{N}_]/u.test(char) ? char : ' ';
        if (mapped === ' ' && text.endsWith(' ')) { ends[ends.length - 1] = end; continue; }
        text += mapped;
        for (let unit = 0; unit < mapped.length; unit++) { starts.push(index); ends.push(end); }
      }
      index = end;
    }
    return { text, starts, ends };
  };
  const normalizedSource = normalizeWithMap(source);
  const normalizedNeedle = normalizeWithMap(needle).text.trim();
  if (!normalizedNeedle) return [];
  let normalizedOffset = 0;
  while (normalizedOffset <= normalizedSource.text.length - normalizedNeedle.length) {
    const index = normalizedSource.text.indexOf(normalizedNeedle, normalizedOffset);
    if (index < 0) break;
    const endIndex = index + normalizedNeedle.length;
    const leftBounded = !isWordCharacter(before(normalizedSource.text, index));
    const rightBounded = !isWordCharacter(after(normalizedSource.text, endIndex));
    if (leftBounded && rightBounded) ranges.push([normalizedSource.starts[index], normalizedSource.ends[endIndex - 1]]);
    normalizedOffset = index + normalizedNeedle.length;
  }
  return ranges;
}

function preferredRange(ranges, caret) {
  if (!ranges.length) return null;
  const before = ranges.filter(range => range[1] <= caret);
  return (before.length ? before[before.length - 1] : ranges[ranges.length - 1]);
}

function sentenceRanges(text) {
  const ranges = [];
  let start = 0;
  const boundaries = /[.!?…]+["'»”’)\]]*(?=\s|$)|\r?\n|$/gu;
  for (const boundary of text.matchAll(boundaries)) {
    const end = boundary.index + boundary[0].trimEnd().length;
    const raw = text.slice(start, end);
    // Keep common abbreviated titles with the name that follows.
    if (boundary[0] === '.' && /(?:^|\s)(?:sr|sra|srta|dr|dra|prof|ud|uds)\.$/iu.test(raw)) continue;
    const leading = raw.search(/\S/u);
    if (leading >= 0) ranges.push({ start: start + leading, end: start + raw.trimEnd().length });
    start = boundary.index + boundary[0].length;
  }
  return ranges;
}

function deleteSentence(editor, scope) {
  const text = editor.value;
  const ranges = sentenceRanges(text);
  const range = scope === 'last' ? ranges.at(-1) : rangeForCaret(ranges, editor.selectionStart);
  if (!range) return { changed: false, message: 'No hay una frase para borrar.' };

  let { start, end } = range;
  const trailing = text.slice(end).match(/^[ \t]+/u)?.[0].length ?? 0;
  if (/^[^\r\n]/u.test(text.slice(end + trailing))) {
    end += trailing;
  } else {
    start -= text.slice(0, start).match(/[ \t]+$/u)?.[0].length ?? 0;
    end += trailing;
  }
  if (!(text.slice(0, start) + text.slice(end)).trim()) {
    start = 0;
    end = text.length;
  }
  replaceRange(editor, '', start, end);
  return { changed: true, message: 'Frase borrada.' };
}

export function applyEditorInstruction(editor, instruction) {
  const item = instruction && typeof instruction === 'object' ? instruction : {};
  const kind = String(item.kind || 'noop');
  if (kind === 'dictate' || kind === 'insert') {
    const text = kind === 'dictate' ? insertionText(editor, item.text) : String(item.text || '');
    replaceRange(editor, text, editor.selectionStart, editor.selectionEnd);
    return { changed: Boolean(text), message: kind === 'dictate' ? 'Texto agregado.' : 'Inserción aplicada.' };
  }
  if (kind === 'replace_many') {
    const pairs = item.replacements;
    if (!Array.isArray(pairs) || pairs.length < 2 || pairs.length > 8) return { changed: false, message: 'No reconocí una orden de cambios válida.' };
    const edits = [];
    for (const pair of pairs) {
      if (!Array.isArray(pair) || pair.length !== 2 || typeof pair[0] !== 'string' || typeof pair[1] !== 'string' || !pair[0].trim() || !pair[1].trim() || pair[0].length > 500 || pair[1].length > 2000) {
        return { changed: false, message: 'No reconocí una orden de cambios válida.' };
      }
      const ranges = occurrenceRanges(editor.value, pair[0]);
      if (!ranges.length) return { changed: false, message: `No encontré “${pair[0].slice(0, 80)}”. No hice cambios.` };
      for (const [start, end] of ranges) edits.push({ start, end, text: pair[1] });
    }
    edits.sort((a, b) => a.start - b.start || a.end - b.end);
    if (edits.some((edit, index) => index && edit.start < edits[index - 1].end)) return { changed: false, message: 'Los cambios se superponen. Pedilos por separado.' };
    const changedEdits = edits.filter(edit => editor.value.slice(edit.start, edit.end) !== edit.text);
    if (!changedEdits.length) return { changed: false, message: 'El texto ya estaba así. No hice cambios.' };
    const source = editor.value;
    const chunks = [];
    let position = 0;
    for (const edit of changedEdits) {
      chunks.push(source.slice(position, edit.start), edit.text);
      position = edit.end;
    }
    chunks.push(source.slice(position));
    replaceRange(editor, chunks.join(''), 0, source.length);
    return { changed: true, changes: changedEdits.length, message: `${changedEdits.length} sustituciones aplicadas.` };
  }
  if (kind === 'replace_selection') {
    const start = Number(editor.selectionStart || 0);
    const end = Number(editor.selectionEnd || start);
    if (end <= start) return { changed: false, message: 'No hay una selección para reescribir.' };
    const replacement = String(item.text || '');
    replaceRange(editor, replacement, start, end);
    return { changed: true, message: 'Selección reescrita.' };
  }
  if (kind === 'delete_sentence') return deleteSentence(editor, item.scope);
  if (kind === 'delete_from') {
    const ranges = occurrenceRanges(editor.value, item.target);
    if (!ranges.length) return { changed: false, message: `No encontré “${item.target || ''}”.` };
    const range = preferredRange(ranges, Number(editor.selectionStart ?? editor.value.length));
    replaceRange(editor, '', range[0], editor.value.length);
    return { changed: true, message: `Texto borrado desde “${item.target}” hasta el final.` };
  }
  if (kind === 'delete_last_words' || kind === 'replace_last_words') {
    const count = Math.max(0, Math.min(10000, Number(item.number || 0)));
    const words = [...String(editor.value || '').matchAll(/\p{L}[\p{L}\p{M}\p{N}'’_-]*/gu)];
    if (!count || !words.length) return { changed: false, message: 'No encontré palabras al final.' };
    const first = words[Math.max(0, words.length - count)];
    const replacement = kind === 'replace_last_words' ? String(item.text || '') : '';
    if (kind === 'replace_last_words' && !replacement) {
      return { changed: false, message: 'No recibí el texto de reemplazo.' };
    }
    replaceRange(editor, replacement, Number(first.index || 0), editor.value.length);
    const removed = Math.min(count, words.length);
    const action = kind === 'replace_last_words' ? 'reemplazada' : 'borrada';
    return { changed: true, message: `${removed} palabra${removed === 1 ? '' : 's'} ${action}${removed === 1 ? '' : 's'} del final.` };
  }
  if (kind === 'replace' || kind === 'delete') {
    const ranges = occurrenceRanges(editor.value, item.target);
    if (!ranges.length) return { changed: false, message: `No encontré “${item.target || ''}”.` };
    const replacement = kind === 'replace' ? String(item.text || '') : '';
    if (item.all_matches) {
      for (const range of [...ranges].reverse()) replaceRange(editor, replacement, range[0], range[1]);
      return { changed: true, message: `${ranges.length} coincidencia${ranges.length === 1 ? '' : 's'} modificada${ranges.length === 1 ? '' : 's'}.` };
    }
    const range = preferredRange(ranges, Number(editor.selectionStart ?? editor.value.length));
    if (editor.value.slice(range[0], range[1]) === replacement) return { changed: false, message: 'El texto ya estaba así. No hice cambios.' };
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
  storage,
  dialog = null
}) {
  const editor = elements.dictationEditor;
  const undoStack = [];
  const redoStack = [];
  let sessionStore = { schemaVersion: SESSION_SCHEMA_VERSION, activeId: '', sessions: [] };
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
  let starting = false;
  let processing = false;
  let speaking = false;
  let recordingCycle = null;
  let draftRevision = 0;
  let workEpoch = 0;
  let requestSequence = 0;
  let listenSequence = 0;
  let uploadOwner = null;
  let lastVoiceCommand = null;
  let cancelPlayback = null;
  let dirty = false;
  let storageReadFailed = false;
  let disposed = false;
  try { if (storage === undefined) storage = windowRef.localStorage; } catch (_) { storageReadFailed = true; }
  let speechSequence = 0;
  let recorderStartedAt = 0;
  let voiceDetected = false;
  let silenceMs = 0;
  let speechMs = 0;
  let lastTick = 0;
  let noiseFloor = 0.012;
  const wakeGate = createWakeCommandGate({ now: () => Date.now() });

  function captureDraft() {
    return { ...snapshot(editor), sessionId: sessionStore.activeId, revision: draftRevision, epoch: workEpoch, request: requestSequence };
  }

  function currentDraft(token) {
    return Boolean(token) && !disposed && token.epoch === workEpoch && token.sessionId === sessionStore.activeId &&
      token.revision === draftRevision && token.value === editor.value &&
      token.selectionStart === editor.selectionStart && token.selectionEnd === editor.selectionEnd &&
      (token.request === undefined || token.request === requestSequence);
  }

  function beginRequest(base = captureDraft()) {
    return currentDraft(base) ? { ...base, request: ++requestSequence } : null;
  }

  function ownsRequest(token) { return token && !disposed && token.epoch === workEpoch && token.request === requestSequence; }

  function discardStale(token) {
    if (currentDraft(token)) return false;
    addActivity('Descarté una respuesta anterior porque cambió el borrador, la selección o la sesión.');
    return true;
  }

  function saveStatus(state, message) {
    if (!elements.dictationSaveStatus) return;
    elements.dictationSaveStatus.dataset.state = state;
    elements.dictationSaveStatus.textContent = message;
  }

  function setStatus(message, mode = '') {
    const labels = {
      listening: 'Escuchando', armed: 'Orden', processing: 'Procesando', speaking: 'Leyendo', error: 'Error'
    };
    elements.dictationStatus.textContent = mode === 'error'
      ? (cleanText(message) || 'Error de micrófono')
      : (labels[mode] || 'En pausa');
    elements.dictationStatus.title = message;
    elements.dictationStatus.dataset.mode = mode;
  }

  function addActivity(message) {
    const clean = cleanText(message);
    if (!clean) return;
    // La actividad técnica ya no ocupa el escritorio; el borrador queda como
    // unidad recuperable y el log general conserva el diagnóstico mínimo.
    log(`Dictado: ${clean}`);
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
    const warmup = data && data.warmup && typeof data.warmup === 'object' ? data.warmup : {};
    const installing = ['queued', 'running'].includes(String(installation.state || ''));
    const isLocalProvider = Boolean(selected && !selected.cloud && select.value !== 'rules');
    elements.dictationAssistantStatus.dataset.ready = String(ready);
    elements.dictationAssistantStatus.textContent = installing
      ? `instalando ${installation.model || selected.model || 'modelo local'}…`
      : selected.cloud
        ? (ready ? 'nube · sólo al invocar' : 'nube no disponible')
        : (select.value === 'rules'
            ? 'sin modelo'
            : (ready
                ? (warmup.state === 'ready' && warmup.model === selected.model ? 'local · modelo preparado' : 'local · se prepara al decir Lucy')
                : 'modelo local no instalado'));
    elements.dictationAssistantInstallBtn.hidden = !isLocalProvider || (ready && !installing);
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
    const approved = dialog
      ? await dialog.confirm({ title: `¿Instalar ${label}?`, message: 'La descarga se realiza una vez y puede ocupar varios GB.', acceptLabel: 'Descargar e instalar' })
      : windowRef.confirm(`¿Descargar e instalar ${label}? La descarga se hace una sola vez y puede ocupar varios GB.`);
    if (!approved) return;
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

  async function prepareAssistantForWake() {
    const value = String(elements.dictationAssistantSelect.value || 'rules');
    if (!['local', 'local14b'].includes(value)) return true;
    setStatus('Preparando el modelo local…', 'processing');
    try {
      const data = await api('/api/dictation/assistant/warm', {});
      const load = Number(data.load_duration_ms || 0);
      const total = Number(data.duration_ms || 0);
      addActivity(`Modelo ${data.model || 'local'} preparado: carga ${load} ms · total ${total} ms.`);
      await refreshAssistantStatus();
      return true;
    } catch (error) {
      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;
      addActivity(`No armé la orden: ${detail}`);
      await refreshAssistantStatus();
      return false;
    }
  }

  function updateStats() {
    const value = String(editor.value || '');
    const words = value.trim() ? value.trim().split(/\s+/).length : 0;
    const pages = value.length ? Math.ceil(value.length / DEFAULT_PAGE_CHARS) : 0;
    elements.dictationStats.textContent = `${value.length} caracteres · ${words} palabras · ${pages} hoja${pages === 1 ? '' : 's'} virtual${pages === 1 ? '' : 'es'}`;
    elements.dictationUndoBtn.disabled = undoStack.length === 0;
    elements.dictationRedoBtn.disabled = redoStack.length === 0;
  }

  function newSessionId() {
    if (windowRef.crypto && typeof windowRef.crypto.randomUUID === 'function') return windowRef.crypto.randomUUID();
    return `dictation-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function activeSession() {
    return sessionStore.sessions.find(item => item.id === sessionStore.activeId) || null;
  }

  function snapshotSession() {
    const now = Date.now();
    const existing = activeSession();
    return {
      id: existing ? existing.id : newSessionId(),
      title: cleanText(elements.dictationTitleInput.value) || 'Dictado sin título',
      text: String(editor.value || ''),
      selectionStart: Number(editor.selectionStart || 0),
      selectionEnd: Number(editor.selectionEnd || 0),
      voice: String(elements.dictationVoiceSelect.value || ''),
      createdAt: Number(existing && existing.createdAt || now),
      updatedAt: now
    };
  }

  function renderSessions() {
    const list = elements.dictationSessionsList;
    if (!list) return;
    list.replaceChildren();
    const sorted = [...sessionStore.sessions].sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0));
    for (const item of sorted) {
      const button = documentRoot.createElement('button');
      button.type = 'button';
      button.className = 'dictation-session-item';
      button.dataset.sessionId = item.id;
      button.setAttribute('aria-current', String(item.id === sessionStore.activeId));
      const title = documentRoot.createElement('span');
      title.className = 'dictation-session-title';
      title.textContent = item.title || 'Dictado sin título';
      const meta = documentRoot.createElement('span');
      meta.className = 'dictation-session-meta';
      const words = String(item.text || '').trim() ? String(item.text).trim().split(/\s+/).length : 0;
      const date = Number(item.updatedAt || 0) ? new Date(Number(item.updatedAt)).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' }) : 'sin fecha';
      meta.textContent = `${words} palabras · ${date}`;
      button.append(title, meta);
      button.addEventListener('click', () => loadSession(item.id));
      list.appendChild(button);
    }
  }

  function persistNow() {
    windowRef.clearTimeout(saveTimer);
    saveTimer = 0;
    try {
      const current = snapshotSession();
      sessionStore.activeId = current.id;
      sessionStore.sessions = [current, ...sessionStore.sessions.filter(item => item.id !== current.id)]
        .slice(0, MAX_SAVED_SESSIONS);
      if (storageReadFailed || !storage) throw new Error('storage_unavailable');
      storage.setItem(STORAGE_KEY, JSON.stringify(sessionStore));
      dirty = false;
      saveStatus('saved', 'Guardado en este navegador.');
    } catch (_) {
      dirty = true;
      saveStatus('error', storageReadFailed
        ? 'No pude recuperar el guardado anterior; no lo sobrescribí. Conservá este borrador con Descargar TXT.'
        : 'Borrador sin guardar: el navegador no pudo conservarlo. Usá Descargar TXT antes de salir.');
    }
    updateStats();
    renderSessions();
    return !dirty;
  }

  function schedulePersist() {
    dirty = true;
    if (elements.dictationSaveStatus?.dataset.state !== 'error') saveStatus('pending', 'Cambios pendientes de guardar…');
    windowRef.clearTimeout(saveTimer);
    saveTimer = windowRef.setTimeout(persistNow, 350);
    updateStats();
  }

  function restoreDraft() {
    try {
      const saved = JSON.parse(storage.getItem(STORAGE_KEY) || '{}');
      if (saved && typeof saved === 'object') {
        if (Number(saved.schemaVersion) === SESSION_SCHEMA_VERSION && Array.isArray(saved.sessions)) {
          sessionStore = {
            schemaVersion: SESSION_SCHEMA_VERSION,
            activeId: String(saved.activeId || ''),
            sessions: saved.sessions.slice(0, MAX_SAVED_SESSIONS).filter(item => item && typeof item === 'object')
          };
        } else if (saved.text || saved.title) {
          sessionStore = {
            schemaVersion: SESSION_SCHEMA_VERSION,
            activeId: newSessionId(),
            sessions: [{
              id: '', title: String(saved.title || 'Dictado sin título'), text: String(saved.text || ''),
              selectionStart: String(saved.text || '').length, selectionEnd: String(saved.text || '').length,
              voice: '', createdAt: Number(saved.updatedAt || Date.now()), updatedAt: Number(saved.updatedAt || Date.now())
            }]
          };
          sessionStore.sessions[0].id = sessionStore.activeId;
        }
      }
    } catch (_) { storageReadFailed = true; }
    if (!sessionStore.sessions.length) {
      const id = newSessionId();
      sessionStore.activeId = id;
      sessionStore.sessions = [{ id, title: 'Dictado sin título', text: '', selectionStart: 0, selectionEnd: 0, voice: '', createdAt: Date.now(), updatedAt: Date.now() }];
    }
    if (!activeSession()) sessionStore.activeId = sessionStore.sessions[0].id;
    const current = activeSession();
    editor.value = String(current.text || '');
    elements.dictationTitleInput.value = String(current.title || 'Dictado sin título');
    if (!elements.dictationTitleInput.value) elements.dictationTitleInput.value = 'Dictado sin título';
    editor.selectionStart = Math.min(editor.value.length, Number(current.selectionStart || editor.value.length));
    editor.selectionEnd = Math.min(editor.value.length, Math.max(editor.selectionStart, Number(current.selectionEnd || editor.selectionStart)));
    updateStats();
    renderSessions();
    saveStatus(storageReadFailed ? 'error' : 'saved', storageReadFailed
      ? 'No pude recuperar el guardado anterior; no lo sobrescribiré. Usá Descargar TXT para conservar lo que escribas.'
      : 'Borrador disponible en este navegador.');
  }

  function loadSession(id) {
    if (id === sessionStore.activeId) return;
    flushManualHistory();
    if (!persistNow()) return;
    const next = sessionStore.sessions.find(item => item.id === id);
    if (!next) return;
    stopListening();
    stopSpeech();
    sessionStore.activeId = id;
    editor.value = String(next.text || '');
    elements.dictationTitleInput.value = String(next.title || 'Dictado sin título');
    editor.selectionStart = Math.min(editor.value.length, Number(next.selectionStart || 0));
    editor.selectionEnd = Math.min(editor.value.length, Math.max(editor.selectionStart, Number(next.selectionEnd || editor.selectionStart)));
    undoStack.length = 0;
    redoStack.length = 0;
    if (next.voice && [...elements.dictationVoiceSelect.options].some(option => option.value === next.voice)) {
      elements.dictationVoiceSelect.value = next.voice;
      elements.dictationVoiceSelect.dispatchEvent(new windowRef.Event('change'));
    }
    persistNow();
    editor.focus();
    addActivity(`Borrador recuperado: ${next.title || 'sin título'}.`);
  }

  async function createNewSession() {
    const token = beginRequest();
    const approved = !editor.value.trim() || (dialog
      ? await dialog.confirm({ title: '¿Abrir un borrador nuevo?', message: 'El actual queda guardado en el historial.', acceptLabel: 'Abrir borrador' })
      : windowRef.confirm('¿Abrir un borrador nuevo? El actual queda guardado en el historial.'));
    if (!approved || discardStale(token)) return;
    flushManualHistory();
    if (!persistNow()) return;
    stopListening();
    stopSpeech();
    const id = newSessionId();
    sessionStore.activeId = id;
    sessionStore.sessions.unshift({ id, title: 'Dictado sin título', text: '', selectionStart: 0, selectionEnd: 0, voice: String(elements.dictationVoiceSelect.value || ''), createdAt: Date.now(), updatedAt: Date.now() });
    editor.value = '';
    editor.selectionStart = 0;
    editor.selectionEnd = 0;
    elements.dictationTitleInput.value = 'Dictado sin título';
    undoStack.length = 0;
    redoStack.length = 0;
    persistNow();
    editor.focus();
    addActivity('Borrador nuevo listo.');
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
      draftRevision += 1;
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
    draftRevision += 1;
    schedulePersist();
    addActivity('Cambio deshecho.');
    return { changed: true };
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
    draftRevision += 1;
    schedulePersist();
    addActivity('Cambio rehecho.');
    return { changed: true };
  }

  function stopSpeech(resumeRecording = true) {
    speechSequence += 1;
    speaking = false;
    if (cancelPlayback) cancelPlayback();
    try { elements.dictationPlayer.pause(); } catch (_) {}
    elements.dictationPlayer.removeAttribute('src');
    if (resumeRecording && active && !processing) startRecorderCycle();
  }

  function seekSpeech(seconds) {
    const player = elements.dictationPlayer;
    if (!player || !Number.isFinite(Number(player.duration)) || !player.src) {
      addActivity('No hay una lectura activa para desplazar.');
      return;
    }
    const target = Math.max(0, Math.min(Number(player.duration), Number(player.currentTime || 0) + Number(seconds || 0)));
    player.currentTime = target;
  }

  async function playAudioUrl(url, sequence) {
    if (!url || sequence !== speechSequence) return;
    elements.dictationPlayer.src = url;
    await new Promise((resolve, reject) => {
      const cleanup = () => {
        elements.dictationPlayer.removeEventListener('ended', onEnded);
        elements.dictationPlayer.removeEventListener('error', onError);
        if (cancelPlayback === onCancel) cancelPlayback = null;
      };
      const onCancel = () => { cleanup(); resolve(); };
      const onEnded = () => { cleanup(); resolve(); };
      const onError = () => { cleanup(); reject(new Error('audio_playback_failed')); };
      elements.dictationPlayer.addEventListener('ended', onEnded, { once: true });
      elements.dictationPlayer.addEventListener('error', onError, { once: true });
      cancelPlayback = onCancel;
      elements.dictationPlayer.play().catch(error => { cleanup(); reject(error); });
    });
  }

  async function speakText(text, label = 'tramo', isCurrent = () => true) {
    const parts = splitSpeechText(text);
    if (!parts.length) return;
    stopSpeech(false);
    stopRecorderCycle(true);
    speaking = true;
    speechSequence += 1;
    const sequence = speechSequence;
    setStatus(label === 'confirmación' ? 'Confirmando el cambio…' : `Leyendo ${label}: 1 de ${parts.length}…`, 'speaking');
    try {
      for (let index = 0; index < parts.length && sequence === speechSequence; index += 1) {
        setStatus(label === 'confirmación' ? 'Confirmando el cambio…' : `Leyendo ${label}: ${index + 1} de ${parts.length}…`, 'speaking');
        const data = await api('/api/dictation/speak', { text: parts[index] });
        if (!isCurrent() || sequence !== speechSequence) return;
        if (!data.audio_url) throw new Error('La voz no devolvió un audio reproducible.');
        await playAudioUrl(data.audio_url, sequence);
      }
      if (sequence === speechSequence) addActivity(`Lectura terminada (${label}).`);
    } catch (error) {
      if (sequence !== speechSequence) return;
      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;
      addActivity(label === 'confirmación' ? `No pude reproducir la confirmación: ${detail}. El resultado del cambio está en el editor.` : `No pude leer: ${detail}.`);
    } finally {
      if (sequence === speechSequence) {
        speaking = false;
        setStatus(active ? 'Escuchando el próximo tramo…' : 'Dictado en pausa.', active ? 'listening' : '');
        if (active && !processing) startRecorderCycle();
      }
    }
  }

  async function requestProofread(instruction, transcript = '', token = beginRequest()) {
    if (discardStale(token)) return;
    const selection = readTextForInstruction(editor, { ...instruction, kind: 'read' });
    if (selection.error) {
      addActivity(selection.error);
      return;
    }
    flushManualHistory();
    const before = snapshot(editor);
    const source = before.value.slice(selection.start, selection.end);
    setStatus('Lucy está corrigiendo el tramo con Qwen 14B…', 'processing');
    try {
      const data = await api('/api/dictation/proofread', { text: source });
      if (discardStale(token)) return;
      if (data.ok === false) throw new Error(data.detail || data.error || 'proofread_failed');
      const revised = String(data.text ?? source);
      if (revised !== source) {
        replaceRange(editor, revised, selection.start, selection.end);
        draftRevision += 1;
        pushUndo(before);
        redoStack.length = 0;
        schedulePersist();
        editor.focus();
      }
      const changed = Number(data.changed_parts || 0);
      const rejected = Number(data.rejected_parts || 0);
      addActivity(
        `Corrección conservadora terminada con ${data.model || 'Qwen 14B'}: ${changed} tramo${changed === 1 ? '' : 's'} cambiado${changed === 1 ? '' : 's'}` +
        `${rejected ? ` · ${rejected} propuesta${rejected === 1 ? '' : 's'} rechazada${rejected === 1 ? '' : 's'} por seguridad` : ''}` +
        ` · ${Number(data.duration_ms || 0)} ms.`
      );
      return { changed: revised !== source, message: '' };
    } catch (error) {
      if (!ownsRequest(token)) return;
      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;
      const technical = error && error.data && error.data.technical_detail ? ` (${error.data.technical_detail})` : '';
      addActivity(`No pude corregir el tramo: ${detail}${technical}. No cambié el texto.`);
    } finally {
      if (ownsRequest(token)) setStatus(active ? 'Escuchando el próximo tramo…' : 'Dictado en pausa.', active ? 'listening' : '');
    }
  }

  async function requestAssistant(transcript, token = beginRequest()) {
    if (discardStale(token)) return;
    const context = dictationAssistantContext(editor);
    setStatus('Lucy está interpretando la orden…', 'processing');
    try {
      const data = await api('/api/dictation/assist', { text: transcript, ...context });
      if (discardStale(token)) return;
      const model = data.assistant_model || data.assistant_provider || 'asistente';
      addActivity(
        `Lucy interpretó la orden con ${model}: total ${data.assistant_ms || 0} ms · carga ${data.assistant_load_ms || 0} ms.`
      );
      return applyInstruction(data.instruction, '', false, true, token);
    } catch (error) {
      if (!ownsRequest(token)) return;
      addActivity(assistantFailureActivity(error));
      return finishCommand({ changed: false, message: 'No pude aplicar la orden. No cambié el texto.' }, { kind: 'noop' }, true, token);
    } finally {
      if (ownsRequest(token)) setStatus(active ? 'Escuchando el próximo tramo…' : 'Dictado en pausa.', active ? 'listening' : '');
    }
  }

  async function finishCommand(result, item, invoked, token) {
    const canFollowUp = active && processing && invoked && ownsRequest(token) &&
      !['dictate', 'undo', 'redo', 'read', 'stop_listening'].includes(item.kind);
    const completedDraft = canFollowUp ? captureDraft() : null;
    if (invoked && ownsRequest(token) && !['dictate', 'read', 'stop_listening'].includes(item.kind)) {
      await speakText(commandConfirmation(item, result), 'confirmación', () => ownsRequest(token));
    }
    if (completedDraft && currentDraft(completedDraft)) {
      lastVoiceCommand = { draft: completedDraft, changed: Boolean(result?.changed), expires: Date.now() + WAKE_COMMAND_WINDOW_MS };
    }
    return result;
  }

  async function applyInstruction(instruction, transcript = '', allowAssistant = false, assistantAttempted = false, token = beginRequest()) {
    if (discardStale(token)) return;
    const item = instruction && typeof instruction === 'object' ? instruction : { kind: 'noop' };
    if (transcript) addActivity(`Oí: “${transcript}”`);
    if (transcript && allowAssistant && item.kind !== 'noop' && item.kind !== 'dictate') {
      addActivity(`Lucy resolvió la orden con reglas instantáneas (${item.kind}).`);
    }
    const invoked = allowAssistant || assistantAttempted || hasLucyInvocation(transcript);
    if (item.kind === 'undo') return finishCommand(undo(), item, invoked, token);
    if (item.kind === 'redo') return finishCommand(redo(), item, invoked, token);
    if (item.kind === 'stop_listening') {
      addActivity('Dictado detenido por voz.');
      return stopListening();
    }
    if (item.kind === 'proofread') {
      return finishCommand(await requestProofread(item, transcript, token), item, invoked, token);
    }
    if (item.kind === 'read') {
      const selection = readTextForInstruction(editor, item);
      if (selection.error) {
        if (allowAssistant && !assistantAttempted && elements.dictationAssistantSelect.value !== 'rules') {
          return requestAssistant(transcript, token);
        }
        addActivity(selection.error);
        return;
      }
      editor.selectionStart = selection.start;
      editor.selectionEnd = selection.end;
      return speakText(selection.text, item.scope || 'selección');
    }
    if (item.kind === 'noop' && item.scope !== 'ambiguous_replacement' && allowAssistant && !assistantAttempted && elements.dictationAssistantSelect.value !== 'rules') {
      return requestAssistant(transcript, token);
    }
    if (item.kind === 'noop' && (hasLucyInvocation(transcript) || assistantAttempted)) {
      const result = { changed: false, message: item.scope === 'ambiguous_replacement' ? 'No reconocí todos los cambios. Pedilos por separado.' : 'No reconocí la orden. No cambié el texto.' };
      addActivity(result.message);
      return finishCommand(result, item, invoked, token);
    }
    const atEnd = editor.selectionStart === editor.value.length && editor.selectionEnd === editor.value.length;
    const result = mutate(item);
    if (active && invoked && atEnd && result.changed && item.kind !== 'dictate') {
      editor.selectionStart = editor.selectionEnd = editor.value.length;
    }
    if (!result.changed && allowAssistant && !assistantAttempted &&
        ['replace', 'delete', 'delete_from'].includes(String(item.kind || '')) &&
        elements.dictationAssistantSelect.value !== 'rules') {
      return requestAssistant(transcript, token);
    }
    return finishCommand(result, item, invoked, token);
  }

  async function interpretTypedCommand() {
    const text = cleanText(elements.dictationCommandInput.value);
    if (!text) return;
    elements.dictationCommandInput.value = '';
    const token = beginRequest();
    try {
      const data = await api('/api/dictation/interpret', {
        text,
        commands_enabled: elements.dictationCommandsToggle.checked
      });
      await applyInstruction(data.instruction, data.transcript, true, false, token);
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
    // Un umbral fijo alto volvía invisible a micrófonos de bajo volumen y
    // retenía la frase hasta el límite largo de la grabación.
    const threshold = Math.max(0.010, noiseFloor * 2.15);
    if (level >= threshold) {
      speechMs += delta;
      silenceMs = 0;
      if (speechMs >= 45 && !voiceDetected) {
        voiceDetected = true;
        if (wakeGate.isArmed()) {
          wakeGate.reserve();
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
    // Las pausas cierran antes el tramo. Si alguien habla de corrido, enviamos
    // cada diez segundos: Dictado no debe sentirse como un audio de medio minuto.
    if ((voiceDetected && elapsed >= 650 && silenceMs >= END_OF_UTTERANCE_SILENCE_MS) || elapsed >= MAX_DICTATION_UTTERANCE_MS) {
      stopRecorderCycle(false);
      return;
    }
    monitorId = windowRef.requestAnimationFrame(monitorRecorder);
  }

  async function uploadRecording(blob, mime, reservedWakeCommand = false, recordedDraft = captureDraft()) {
    let token = beginRequest(recordedDraft);
    if (discardStale(token)) return;
    uploadOwner = token;
    processing = true;
    setStatus(reservedWakeCommand ? 'Transcribiendo la orden…' : 'Transcribiendo y aplicando…', 'processing');
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
      if (discardStale(token)) return;
      if (!response.ok || data.ok === false) throw new Error(data.detail || data.error || 'dictation_failed');
      const transcript = String(data.transcript || '').trim();
      const recentCommand = lastVoiceCommand;
      if (transcript) lastVoiceCommand = null;
      // The microphone resumes after confirmation. Only the immediate reply can
      // reject that edit, and only while the same draft revision is still open.
      const rejecting = elements.dictationCommandsToggle.checked && rejectsLastEdit(transcript);
      const recent = recentCommand && Date.now() <= recentCommand.expires;
      if (rejecting && (recent || hasLucyInvocation(transcript) || reservedWakeCommand)) {
        const edited = recentCommand?.draft;
        const sameEdit = recent && recentCommand.changed && edited.sessionId === token.sessionId && edited.epoch === token.epoch &&
          edited.revision === token.revision && edited.value === token.value;
        const undone = sameEdit && undo()?.changed;
        await speakText(undone ? 'Deshice el último cambio. Decime cómo querés que quede.' : 'Decime qué parte querés cambiar.',
          'confirmación', () => ownsRequest(token));
        if (ownsRequest(token) && active) armWakeCommand();
        return;
      }
      if (elements.dictationCommandsToggle.checked && isBareLucyInvocation(transcript)) {
        if (reservedWakeCommand) wakeGate.claim();
        addActivity(`Oí: “${transcript}”`);
        if (!await prepareAssistantForWake() || discardStale(token)) return;
        armWakeCommand();
        log(`Dictado: ${transcript || 'sin texto'} (${data.stt_provider || 'STT'}). Esperando orden.`);
        return;
      }
      let instruction = data.instruction;
      let appliedTranscript = transcript;
      const split = elements.dictationCommandsToggle.checked ? splitLucyUtterance(transcript) : null;
      if (split?.prefix) {
        mutate({ kind: 'dictate', text: split.prefix });
        token = beginRequest();
        uploadOwner = token;
        appliedTranscript = split.command;
        if (isBareLucyInvocation(appliedTranscript)) {
          if (!await prepareAssistantForWake() || discardStale(token)) return;
          armWakeCommand();
          return;
        }
        const interpreted = await api('/api/dictation/interpret', invokedInterpretationPayload(appliedTranscript));
        if (discardStale(token)) return;
        instruction = interpreted.instruction;
      }
      const claimedWakeCommand = reservedWakeCommand && wakeGate.claim();
      if (claimedWakeCommand) {
        const payload = invokedInterpretationPayload(appliedTranscript);
        appliedTranscript = payload.text;
        const interpreted = await api('/api/dictation/interpret', payload);
        if (discardStale(token)) return;
        instruction = interpreted.instruction;
      }
      const invoked = claimedWakeCommand || hasLucyInvocation(appliedTranscript);
      await applyInstruction(instruction, appliedTranscript, invoked, false, token);
      log(`Dictado: ${appliedTranscript || 'sin texto'} (${data.stt_provider || 'STT'}).`);
    } catch (error) {
      if (!ownsRequest(token)) return;
      addActivity(`Falló la transcripción: ${error.message}.`);
      if (reservedWakeCommand && active && elements.dictationCommandsToggle.checked) {
        wakeGate.arm();
        scheduleWakeExpiry();
        addActivity('Lucy sigue atenta; repetí la orden completa.');
      }
    } finally {
      if (uploadOwner === token) {
        uploadOwner = null;
        processing = false;
        if (active && !speaking) startRecorderCycle();
      }
    }
  }

  function stopRecorderCycle(discard = false) {
    windowRef.cancelAnimationFrame(monitorId);
    monitorId = 0;
    if (!recorder || recorder.state === 'inactive') return;
    if (recordingCycle) {
      recordingCycle.discard = discard;
      windowRef.clearTimeout(recordingCycle.deadlineTimer);
    }
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
    const cycle = { recorder, chunks: [], bytes: 0, deadlineTimer: 0, discard: false, draft: captureDraft() };
    recordingCycle = cycle;
    voiceDetected = false;
    silenceMs = 0;
    speechMs = 0;
    noiseFloor = 0.012;
    recorderStartedAt = windowRef.performance.now();
    lastTick = recorderStartedAt;
    recorder.addEventListener('dataavailable', event => {
      if (recordingCycle !== cycle || cycle.discard) return;
      if (event.data && event.data.size) {
        cycle.bytes += event.data.size;
        if (cycle.bytes > 4 * 1024 * 1024) {
          stopListening();
          setStatus('La captura superó el límite. Volvé a iniciar el dictado.', 'error');
          return;
        }
        cycle.chunks.push(event.data);
      }
    });
    recorder.addEventListener('stop', () => {
      windowRef.clearTimeout(cycle.deadlineTimer);
      if (recordingCycle !== cycle) return;
      const shouldDiscard = cycle.discard || !active || !voiceDetected || !currentDraft(cycle.draft);
      const blob = new windowRef.Blob(cycle.chunks, { type: cycle.recorder.mimeType || mime || 'audio/webm' });
      recordingCycle = null;
      recorder = null;
      if (shouldDiscard || blob.size < 900) {
        if (wakeGate.isArmed() && !wakeExpiryTimer) {
          wakeGate.arm();
          scheduleWakeExpiry();
        }
        if (active && !processing && !speaking) windowRef.setTimeout(startRecorderCycle, 180);
        return;
      }
      const reservedWakeCommand = wakeGate.reserve();
      if (reservedWakeCommand) {
        windowRef.clearTimeout(wakeExpiryTimer);
        wakeExpiryTimer = 0;
      }
      uploadRecording(blob, blob.type || mime, reservedWakeCommand, cycle.draft);
    }, { once: true });
    try {
      recorder.start(250);
      cycle.deadlineTimer = windowRef.setTimeout(() => {
        if (recordingCycle === cycle) stopRecorderCycle(false);
      }, MAX_DICTATION_UTTERANCE_MS);
    } catch (error) {
      stopListening();
      setStatus(`No pude iniciar la grabación: ${error.message}.`, 'error');
      return;
    }
    setStatus(
      wakeGate.isArmed() ? 'Lucy está escuchando tu orden…' : 'Escuchando… hacé una pausa y aplico el tramo.',
      wakeGate.isArmed() ? 'armed' : 'listening'
    );
    monitorId = windowRef.requestAnimationFrame(monitorRecorder);
  }

  async function startListening() {
    if (active || starting || documentRoot.hidden) return;
    starting = true;
    const sequence = ++listenSequence;
    const epoch = workEpoch;
    if (!windowRef.MediaRecorder || !windowRef.navigator.mediaDevices || !windowRef.navigator.mediaDevices.getUserMedia) {
      starting = false;
      setStatus('Este navegador no ofrece captura de audio compatible.', 'error');
      return;
    }
    try {
      let compatibilityCapture = false;
      try {
        const captured = await windowRef.navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 }
        });
        if (sequence !== listenSequence || epoch !== workEpoch || disposed) { captured.getTracks().forEach(track => track.stop()); return; }
        stream = captured;
      } catch (error) {
        if (!shouldRetryWithBasicMicrophone(error)) throw error;
        // WebKitGTK/PipeWire can expose a mic without supporting every advanced
        // browser constraint.  Capture first; the analyser already adapts to it.
        if (sequence !== listenSequence || epoch !== workEpoch || disposed) return;
        const captured = await windowRef.navigator.mediaDevices.getUserMedia({ audio: true });
        if (sequence !== listenSequence || epoch !== workEpoch || disposed) { captured.getTracks().forEach(track => track.stop()); return; }
        stream = captured;
        compatibilityCapture = true;
      }
      const AudioContext = windowRef.AudioContext || windowRef.webkitAudioContext;
      audioContext = new AudioContext();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      audioContext.createMediaStreamSource(stream).connect(analyser);
      active = true;
      elements.dictationMicBtn.textContent = 'Detener dictado';
      elements.dictationMicBtn.classList.add('recording');
      addActivity(compatibilityCapture
        ? 'Micrófono abierto en modo compatible. Las pausas separan los tramos.'
        : 'Micrófono abierto. Las pausas separan los tramos.');
      startRecorderCycle();
    } catch (error) {
      if (sequence !== listenSequence || epoch !== workEpoch || disposed) return;
      stopListening();
      const message = microphoneFailureMessage(error);
      setStatus(message, 'error');
      addActivity(message);
    } finally {
      if (sequence === listenSequence) starting = false;
    }
  }

  function stopListening() {
    starting = false;
    workEpoch += 1;
    listenSequence += 1;
    requestSequence += 1;
    processing = false;
    uploadOwner = null;
    active = false;
    lastVoiceCommand = null;
    clearWakeCommand();
    stopRecorderCycle(true);
    recordingCycle = null;
    recorder = null;
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
    flushManualHistory();
    if (!persistNow()) return;
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

  async function downloadPdf() {
    if (!editor.value.trim()) {
      addActivity('No hay texto para exportar en PDF.');
      return;
    }
    const title = cleanText(elements.dictationTitleInput.value) || 'Dictado';
    const filenameStem = title.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9_-]+/gi, '-').replace(/^-+|-+$/g, '') || 'dictado';
    try {
      const response = await fetchFn('/api/dictation/export/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, text: editor.value, page_numbers: Boolean(elements.dictationPdfPageNumbers.checked) })
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || data.error || 'pdf_export_failed');
      }
      const url = windowRef.URL.createObjectURL(await response.blob());
      const anchor = documentRoot.createElement('a');
      anchor.href = url;
      anchor.download = `${filenameStem}.pdf`;
      documentRoot.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      windowRef.URL.revokeObjectURL(url);
      addActivity(`PDF exportado: ${filenameStem}.pdf.`);
    } catch (error) {
      addActivity(`No pude exportar el PDF: ${error.message}.`);
    }
  }

  elements.dictationToggleBtn.addEventListener('click', () => elements.dictationWorkspace.hidden ? open() : close());
  elements.dictationCloseBtn.addEventListener('click', close);
  elements.dictationMicBtn.addEventListener('click', () => active || starting ? stopListening() : startListening());
  elements.dictationStopSpeechBtn.addEventListener('click', stopSpeech);
  elements.dictationUndoBtn.addEventListener('click', undo);
  elements.dictationRedoBtn.addEventListener('click', redo);
  elements.dictationBackBtn.addEventListener('click', () => seekSpeech(-10));
  elements.dictationReadBtn.addEventListener('click', () => {
    const selection = readTextForInstruction(editor, { kind: 'read', scope: 'selection' });
    if (selection.error) return addActivity(selection.error);
    speakText(selection.text, 'selección');
  });
  elements.dictationForwardBtn.addEventListener('click', () => seekSpeech(10));
  elements.dictationUseReaderBtn.addEventListener('click', mountInReader);
  elements.dictationDownloadBtn.addEventListener('click', downloadText);
  elements.dictationDownloadPdfBtn.addEventListener('click', downloadPdf);
  elements.dictationNewBtn.addEventListener('click', () => { createNewSession(); });
  elements.dictationClearBtn.addEventListener('click', async () => {
    const token = beginRequest();
    const approved = !editor.value || (dialog
      ? await dialog.confirm({ title: '¿Limpiar el borrador?', message: 'Podrás deshacerlo mientras esta pestaña siga abierta.', acceptLabel: 'Sí, limpiar', danger: true })
      : windowRef.confirm('¿Limpiar el borrador de dictado? Podrás deshacerlo mientras esta pestaña siga abierta.'));
    if (!approved || discardStale(token)) return;
    mutate({ kind: 'clear' });
  });
  elements.dictationCommandBtn.addEventListener('click', interpretTypedCommand);
  elements.dictationAssistantSelect.addEventListener('change', changeAssistant);
  elements.dictationAssistantInstallBtn.addEventListener('click', installAssistantModel);
  elements.dictationCommandsToggle.addEventListener('change', () => {
    workEpoch += 1;
    requestSequence += 1;
    clearWakeCommand();
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
    draftRevision += 1;
    windowRef.clearTimeout(manualTimer);
    manualTimer = windowRef.setTimeout(flushManualHistory, 700);
    schedulePersist();
  });
  elements.dictationTitleInput.addEventListener('input', schedulePersist);
  elements.dictationVoiceSelect.addEventListener('change', schedulePersist);

  function beforeLeave(event) {
    stopListening();
    stopSpeech();
    if ((dirty || saveTimer) && !persistNow()) {
      event.preventDefault();
      event.returnValue = '';
    }
  }
  function onVisibilityChange() { if (documentRoot.hidden) stopListening(); }
  documentRoot.addEventListener?.('visibilitychange', onVisibilityChange);
  windowRef.addEventListener?.('beforeunload', beforeLeave);

  restoreDraft();
  refreshAssistantStatus();

  return {
    open,
    close,
    undo,
    redo,
    loadSession,
    createNewSession,
    seekSpeech,
    applyInstruction,
    stopListening,
    stopSpeech,
    dispose() {
      flushManualHistory();
      persistNow();
      stopListening();
      stopSpeech();
      disposed = true;
      documentRoot.removeEventListener?.('visibilitychange', onVisibilityChange);
      windowRef.removeEventListener?.('beforeunload', beforeLeave);
      windowRef.clearTimeout(saveTimer);
      windowRef.clearTimeout(manualTimer);
      windowRef.clearTimeout(assistantInstallTimer);
      windowRef.clearTimeout(wakeExpiryTimer);
    }
  };
}

export { DEFAULT_ASSISTANT_CONTEXT_CHARS, DEFAULT_PAGE_CHARS, STORAGE_KEY, WAKE_COMMAND_WINDOW_MS };
