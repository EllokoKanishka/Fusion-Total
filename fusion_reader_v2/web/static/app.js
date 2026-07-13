const els = {
  dropzone: document.getElementById('dropzone'),
  chooseFileBtn: document.getElementById('chooseFileBtn'),
  fileInput: document.getElementById('fileInput'),
  uploadInfo: document.getElementById('uploadInfo'),
  importProgress: document.getElementById('importProgress'),
  autoReadToggle: document.getElementById('autoReadToggle'),
  pdfToWordTool: document.getElementById('pdfToWordTool'),
  pdfToWordInput: document.getElementById('pdfToWordInput'),
  pdfToWordInfo: document.getElementById('pdfToWordInfo'),
  pdfToWordDownload: document.getElementById('pdfToWordDownload'),
  referenceModeToggle: document.getElementById('referenceModeToggle'),
  prepareBtn: document.getElementById('prepareBtn'),
  cancelPrepareBtn: document.getElementById('cancelPrepareBtn'),
  clearDocBtn: document.getElementById('clearDocBtn'),
  prepareInfo: document.getElementById('prepareInfo'),
  prepareProgress: document.getElementById('prepareProgress'),
  audioExportMode: document.getElementById('audioExportMode'),
  audioExportBlockWrap: document.getElementById('audioExportBlockWrap'),
  audioExportBlockInput: document.getElementById('audioExportBlockInput'),
  audioExportRangeWrap: document.getElementById('audioExportRangeWrap'),
  audioExportStartInput: document.getElementById('audioExportStartInput'),
  audioExportEndInput: document.getElementById('audioExportEndInput'),
  audioExportBtn: document.getElementById('audioExportBtn'),
  audioExportCancelBtn: document.getElementById('audioExportCancelBtn'),
  audioExportInfo: document.getElementById('audioExportInfo'),
  audioExportDownload: document.getElementById('audioExportDownload'),
  notesSummary: document.getElementById('notesSummary'),
  noteInput: document.getElementById('noteInput'),
  saveNoteBtn: document.getElementById('saveNoteBtn'),
  notesInfo: document.getElementById('notesInfo'),
  notesList: document.getElementById('notesList'),
  docTitle: document.getElementById('docTitle'),
  docMeta: document.getElementById('docMeta'),
  chunk: document.getElementById('chunk'),
  ttsChip: document.getElementById('ttsChip'),
  ttsDot: document.getElementById('ttsDot'),
  ttsStatus: document.getElementById('ttsStatus'),
  sttChip: document.getElementById('sttChip'),
  sttDot: document.getElementById('sttDot'),
  sttStatus: document.getElementById('sttStatus'),
  log: document.getElementById('log'),
  player: document.getElementById('player'),
  prevBtn: document.getElementById('prevBtn'),
  readBtn: document.getElementById('readBtn'),
  repeatBtn: document.getElementById('repeatBtn'),
  nextBtn: document.getElementById('nextBtn'),
  jumpInput: document.getElementById('jumpInput'),
  jumpBtn: document.getElementById('jumpBtn'),
  continuousToggle: document.getElementById('continuousToggle'),
  chatLog: document.getElementById('chatLog'),
  chatInput: document.getElementById('chatInput'),
  sendChatBtn: document.getElementById('sendChatBtn'),
  clearLabHistoryBtn: document.getElementById('clearLabHistoryBtn'),
  reasoningNormalBtn: document.getElementById('reasoningNormalBtn'),
  reasoningThinkingBtn: document.getElementById('reasoningThinkingBtn'),
  reasoningSupremeBtn: document.getElementById('reasoningSupremeBtn'),
  reasoningPensamientoCriticoBtn: document.getElementById('reasoningPensamientoCriticoBtn'),
  profileSelect: document.getElementById('profileSelect'),
  veilSelect: document.getElementById('veilSelect'),
  freeModeBtn: document.getElementById('freeModeBtn'),
  reasoningCaption: document.getElementById('reasoningCaption'),
  dialogueBtn: document.getElementById('dialogueBtn'),
  dialogueInfo: document.getElementById('dialogueInfo'),
  dialoguePlayer: document.getElementById('dialoguePlayer'),
  labFocus: document.getElementById('labFocus'),
  mainDocTitle: document.getElementById('mainDocTitle'),
  mainDocMeta: document.getElementById('mainDocMeta'),
  referenceList: document.getElementById('referenceList'),
  voiceSelect: document.getElementById('voiceSelect')
};
const LAB_NOTES_DOC_ID = '__laboratory__';
let status = null;
let notesState = { docId: '', current: 0, items: [] };
let lastRenderedDocId = '';
let lastRenderedBlockIndex = 0;
let lastRenderedBlockText = '';
let audioLifecycleSequence = 0;
let activeReadController = null;
let activeReadRequest = 0;
let audioExportPollingJobId = '';
let voiceCatalogRefreshInFlight = false;
const busyControls = createBusyControlState(
  (availability, busyLeaseCount) => applyControlState(els, availability, busyLeaseCount),
  null,
  els.noteInput ? els.noteInput.value : ''
);
const dialogue = {
  active: false,
  stream: null,
  audioContext: null,
  analyser: null,
  monitorId: 0,
  recorder: null,
  pcmChunks: [],
  pcmPreRoll: [],
  pcmPreRollSamples: 0,
  recording: false,
  finalizing: false,
  processing: false,
  speaking: false,
  chunkIndex: null,
  turnId: 0,
  trace: null,
  suppressUntil: 0,
  localSpeechStartedAt: 0,
  bargeInMs: 240,
  bargeInSpeechMs: 0,
  localSelfMuteMs: 700,
  speechMs: 0,
  silenceMs: 0,
  startedAt: 0,
  lastTick: 0,
  noiseFloor: 0.012,
  minThreshold: 0.018,
  thresholdMultiplier: 2.15,
  speechStartMs: 35,
  silenceStopMs: 1250,
  minRecordMs: 650,
  maxRecordMs: 18000,
  preRollMs: 900,
  finalFlushMs: 180,
  turnStartedAt: 0,
  finalizeTimeoutId: 0,
  captureStopAt: 0,
  captureStopReason: '',
  micDeviceLabel: '',
  sampleRate: 48000
};

function beginBusyLease() {
  return busyControls.beginBusyLease();
}

async function api(path, body, requestOptions = {}) {
  const options = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  };
  Object.assign(options, requestOptions || {});
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || data.detail || data.message || 'request_failed');
    err.data = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

function setReferenceMode(enabled) {
  if (els.referenceModeToggle) {
    els.referenceModeToggle.checked = Boolean(enabled);
  }
}

function friendlyTtsMessage(detail) {
  const clean = String(detail || '').trim();
  if (!clean) {
    return 'El servicio de voz no está disponible. Iniciá TTS o seleccioná otro motor.';
  }
  if (clean.startsWith('tts_owner_')) {
    return 'El TTS de Fusion está vivo pero no quedó validado como propio. Reiniciá el TTS de Fusion o seleccioná otro motor.';
  }
  if (clean.startsWith('tts_foreign_doctora_lucy_port')) {
    return 'Fusion detectó una voz de otro proyecto y no la va a usar como si fuera propia.';
  }
  if (clean.startsWith('tts_historic_unassigned_port')) {
    return 'El puerto histórico 7852 no es válido para la voz de Fusion.';
  }
  if (clean.includes('timed out') || clean.includes('timeout')) {
    return 'La voz tardó demasiado en responder. Probemos otra vez en unos segundos.';
  }
  if (clean.startsWith('http_') || clean.includes('Connection refused') || clean.includes('refused')) {
    return 'El servicio de voz no respondió desde Fusion. Iniciá TTS o seleccioná otro motor.';
  }
  return clean;
}

function ttsActionAvailable(data) {
  const services = data && data.services && typeof data.services === 'object' ? data.services : {};
  const tts = services.tts && typeof services.tts === 'object' ? services.tts : data && data.tts || {};
  return Boolean(tts && (tts.ready || tts.ok));
}

function renderGracefulResearchFailure(data, traceText='') {
  if (!data || !data.external_research || !data.answer) {
    return false;
  }
  addChatMessage('assistant', data.answer);
  if (traceText) {
    addChatMessage('system', traceText);
  }
  const info = dialogueModeSummary(data);
  setDialogueInfo(`${laboratoryModeSummary()} ${info}${traceText ? ` | ${traceText}` : ''}`);
  log(`Investigación externa incompleta: ${data.detail || data.error || 'external_research_failed'}. ${traceText}`.trim());
  return true;
}

function log(text) {
  els.log.textContent = text;
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function dialogueFlushWaitMs() {
  return dialogue.finalFlushMs;
}

function visibleChunkIndex() {
  const current = status && Number(status.current || 0);
  return current > 0 ? current - 1 : null;
}

function fmtMs(ms) {
  const value = Math.max(0, Number(ms || 0));
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}s`;
  }
  return `${Math.round(value)}ms`;
}

function formatDialogueTrace(data, trace, responseWallMs) {
  const server = data && data.trace && typeof data.trace === 'object' ? data.trace : {};
  const sttTimings = server.stt_timings && typeof server.stt_timings === 'object' ? server.stt_timings : {};
  const recordedMs = trace && Number(trace.recordedMs || 0) > 0
    ? Number(trace.recordedMs || 0)
    : (trace && trace.speechStopAt && trace.speechStartAt ? trace.speechStopAt - trace.speechStartAt : 0);
  const fromStopMs = trace && trace.speechStopAt && trace.responseAt ? trace.responseAt - trace.speechStopAt : responseWallMs;
  const uploadAndServerMs = trace && trace.sendStartedAt && trace.responseAt ? trace.responseAt - trace.sendStartedAt : responseWallMs;
  const parts = [
    `Traza turno ${trace && trace.turnId || '?'}`,
    `audio ${fmtMs(recordedMs)}`,
    `WAV ${Math.round(Number(trace && (trace.audioSizeBytes || trace.blobSize) || 0) / 1024)}KB`,
    `RMS ${Number(trace && trace.micRms || 0).toFixed(4)}`,
    `pico ${Number(trace && trace.micPeak || 0).toFixed(4)}`,
    `voz ${trace && trace.voiceDetected ? 'sí' : 'no'}`,
    `corte ${trace && trace.captureStopReason || 'n/d'}`,
    `silencio corte ${fmtMs(dialogue.silenceStopMs)}`,
    `flush ${fmtMs(trace && trace.flushWaitMs || 0)}`,
    `subida+servidor ${fmtMs(uploadAndServerMs)}`,
    `STT ${fmtMs(data && data.stt_ms)}`
  ];
  if (sttTimings.convert_ms !== undefined || sttTimings.decode_ms !== undefined) {
    parts.push(`ffmpeg ${fmtMs(sttTimings.convert_ms || 0)}`);
    parts.push(`whisper ${fmtMs(sttTimings.decode_ms || 0)}`);
  }
  parts.push(`intención ${fmtMs(server.intent_ms || 0)}`);
  parts.push(`nota ${fmtMs(server.note_ms || 0)}`);
  parts.push(`chat ${fmtMs(data && data.chat_ms)}`);
  parts.push(`voz ${fmtMs(data && data.tts_ms)}`);
  parts.push(`desde fin de habla ${fmtMs(fromStopMs)}`);
  parts.push(`total servidor ${fmtMs(server.server_total_ms || data && data.duration_ms || 0)}`);
  return parts.join(' | ');
}

function selectLocalFemaleSpanishVoice() {
  if (!('speechSynthesis' in window) || typeof window.speechSynthesis.getVoices !== 'function') {
    return null;
  }
  const voices = window.speechSynthesis.getVoices() || [];
  if (!voices.length) {
    return null;
  }
  const spanish = voices.filter(voice => String(voice.lang || '').toLowerCase().startsWith('es'));
  const pool = spanish.length ? spanish : voices;
  const femaleHints = /(female|mujer|femenina|m[oó]nica|monica|paulina|helena|elena|sabina|soledad|laura|lucia|luc[ií]a|maria|mar[ií]a|carmen|isabel|paloma|google espa[ñn]ol)/i;
  const maleHints = /(male|hombre|masculina|pablo|jorge|juan|carlos|diego|miguel|antonio|enrique|ricardo)/i;
  return (
    pool.find(voice => femaleHints.test(`${voice.name} ${voice.voiceURI}`) && !maleHints.test(`${voice.name} ${voice.voiceURI}`)) ||
    spanish.find(voice => !maleHints.test(`${voice.name} ${voice.voiceURI}`)) ||
    spanish[0] ||
    null
  );
}

if ('speechSynthesis' in window) {
  window.speechSynthesis.onvoiceschanged = () => {
    selectLocalFemaleSpanishVoice();
  };
}

function speakLocal(text, onDone) {
  if (!('speechSynthesis' in window)) {
    if (typeof onDone === 'function') {
      onDone();
    }
    return;
  }
  const clean = String(text || '').trim();
  if (!clean) {
    if (typeof onDone === 'function') {
      onDone();
    }
    return;
  }
  try {
    window.speechSynthesis.cancel();
    dialogue.localSpeechStartedAt = performance.now();
    dialogue.suppressUntil = Math.max(dialogue.suppressUntil || 0, dialogue.localSpeechStartedAt + dialogue.localSelfMuteMs);
    const utterance = new SpeechSynthesisUtterance(clean);
    utterance.lang = 'es-ES';
    const selectedVoice = selectLocalFemaleSpanishVoice();
    if (selectedVoice) {
      utterance.voice = selectedVoice;
      utterance.lang = selectedVoice.lang || 'es-ES';
    }
    utterance.rate = 1.08;
    utterance.onend = () => {
      if (typeof onDone === 'function') {
        onDone();
      }
    };
    utterance.onerror = () => {
      if (typeof onDone === 'function') {
        onDone();
      }
    };
    window.speechSynthesis.speak(utterance);
  } catch (_) {
    if (typeof onDone === 'function') {
      onDone();
    }
  }
}

function setImportProgress(percent) {
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  els.importProgress.value = value;
}

function setPrepareProgress(percent) {
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  els.prepareProgress.value = value;
}

function renderPrepareStatus(prepare) {
  if (!prepare) {
    return;
  }
  setPrepareProgress(prepare.percent || 0);
  const total = prepare.total || 0;
  const done = (prepare.cached || 0) + (prepare.generated || 0) + (prepare.failed || 0);
  const label = total ? `bloque ${Math.min(done, total)} de ${total} — ${prepare.percent || 0} %` : 'sin bloques preparados';
  if (prepare.status === 'running' || prepare.status === 'canceling') {
    els.prepareInfo.textContent = `Preparando documento: ${label}. Cache ${prepare.cached || 0}, nuevos ${prepare.generated || 0}${prepare.failed ? `, fallidos ${prepare.failed}` : ''}.`;
  } else if (prepare.status === 'done') {
    els.prepareInfo.textContent = `Documento listo: ${label}. Cache ${prepare.cached || 0}, nuevos ${prepare.generated || 0}${prepare.failed ? `, fallidos ${prepare.failed}` : ''}.`;
  } else if (prepare.status === 'canceled') {
    els.prepareInfo.textContent = `Preparación cancelada: ${label}.`;
  } else if (prepare.status === 'error') {
    els.prepareInfo.textContent = prepare.message ? `${prepare.message} ${total ? `(${label})` : ''}`.trim() : 'No pude preparar el documento.';
  } else {
    els.prepareInfo.textContent = total ? 'Audio pendiente de preparar.' : 'Audio sin preparar.';
  }
}

function syncAudioExportInputs() {
  const mode = String(els.audioExportMode.value || 'current');
  els.audioExportBlockWrap.classList.toggle('audio-export-hidden', mode !== 'block');
  els.audioExportRangeWrap.classList.toggle('audio-export-hidden', mode !== 'range');
}

function renderAudioExportStatus(item) {
  const data = item && typeof item === 'object' ? item : {};
  const state = String(data.state || 'idle');
  const cached = Number(data.cached_blocks || 0);
  const generated = Number(data.generated_blocks || 0);
  if (state === 'running' || state === 'queued' || state === 'canceling') {
    const detail = data.detail || `Generando bloque ${Number(data.completed_blocks || 0) + 1} de ${Number(data.total_blocks || 0)}...`;
    els.audioExportInfo.textContent = `${detail} Cacheados: ${cached} · Generados: ${generated}`;
  } else if (state === 'done') {
    els.audioExportInfo.textContent = `Listo: guardado en Descargas. Cacheados: ${cached} · Generados: ${generated}`;
  } else if (state === 'cancelled') {
    els.audioExportInfo.textContent = 'Exportación cancelada.';
  } else if (state === 'error') {
    els.audioExportInfo.textContent = data.error || data.detail || 'No pude exportar audio.';
  } else {
    els.audioExportInfo.textContent = 'Sin exportación de audio activa.';
  }
  if (data.download_url && state === 'done') {
    els.audioExportDownload.href = data.download_url;
    els.audioExportDownload.classList.remove('is-hidden');
  } else {
    els.audioExportDownload.removeAttribute('href');
    els.audioExportDownload.classList.add('is-hidden');
  }
  if ((state === 'running' || state === 'queued' || state === 'canceling') && data.job_id && audioExportPollingJobId !== data.job_id) {
    audioExportPollingJobId = data.job_id;
    pollAudioExport(data.job_id).catch(() => {});
  }
  if (!['running', 'queued', 'canceling'].includes(state) && (!data.job_id || data.job_id === audioExportPollingJobId)) {
    audioExportPollingJobId = '';
  }
}

function voiceLabel(filename) {
  const labels = {
    'female_01.wav': 'M01 — Afrodita',
    'female_02.wav': 'M02 — Atenea',
    'female_03.wav': 'M03 — Hera',
    'female_04.wav': 'M04 — Freyja',
    'female_05.wav': 'M05 — Isis',
    'female_06.wav': 'M06 — Lakshmi',
    'female_07.wav': 'M07 — Selene',
    'Lucy_Cunningham.wav': 'M08 — Perséfone',
    'Lisa_Gerrard.wav': 'M09 — Hécate',
    'male_01.wav': 'V01 — Zeus',
    'male_02.wav': 'V02 — Odín',
    'male_03.wav': 'V03 — Shiva',
    'male_04.wav': 'V04 — Anubis',
    'male_05.wav': 'V05 — Apolo',
    'Morgan_Freeman CC3.wav': 'V06 — Hermes',
    'James_Earl_Jones CC3.wav': 'V07 — Ares',
    'David_Attenborough CC3.wav': 'V08 — Heimdall',
    'Clint_Eastwood CC3.wav': 'V09 — Hades',
    'Clint_Eastwood CC3 (enhanced).wav': 'V10 — Thor',
    'arnold.wav': 'V11 — Hércules'
  };
  return labels[filename] || filename;
}

function voiceGroup(filename) {
  const m = ['female_01.wav', 'female_02.wav', 'female_03.wav', 'female_04.wav', 'female_05.wav', 'female_06.wav', 'female_07.wav', 'Lucy_Cunningham.wav', 'Lisa_Gerrard.wav'];
  const v = ['male_01.wav', 'male_02.wav', 'male_03.wav', 'male_04.wav', 'male_05.wav', 'Morgan_Freeman CC3.wav', 'James_Earl_Jones CC3.wav', 'David_Attenborough CC3.wav', 'Clint_Eastwood CC3.wav', 'Clint_Eastwood CC3 (enhanced).wav', 'arnold.wav'];
  if (m.includes(filename) || filename.startsWith('female_')) return 'Voces M';
  if (v.includes(filename) || filename.startsWith('male_')) return 'Voces V';
  return 'Otras voces';
}

function voiceSortKey(filename) {
  const order = [
    'female_01.wav', 'female_02.wav', 'female_03.wav', 'female_04.wav', 'female_05.wav', 'female_06.wav', 'female_07.wav', 'Lucy_Cunningham.wav', 'Lisa_Gerrard.wav',
    'male_01.wav', 'male_02.wav', 'male_03.wav', 'male_04.wav', 'male_05.wav', 'Morgan_Freeman CC3.wav', 'James_Earl_Jones CC3.wav', 'David_Attenborough CC3.wav', 'Clint_Eastwood CC3.wav', 'Clint_Eastwood CC3 (enhanced).wav', 'arnold.wav'
  ];
  const idx = order.indexOf(filename);
  return idx === -1 ? 999 : idx;
}

function voiceColor(filename) {
  const colors = {
    'female_01.wav': '#ffb3ba', 'female_02.wav': '#ffdfba', 'female_03.wav': '#ffffba', 'female_04.wav': '#baffc9', 'female_05.wav': '#bae1ff', 'female_06.wav': '#eecbff', 'female_07.wav': '#ffc1e3', 'Lucy_Cunningham.wav': '#21d07a', 'Lisa_Gerrard.wav': '#38c6d8',
    'male_01.wav': '#ff7474', 'male_02.wav': '#ffc857', 'male_03.wav': '#4cc9f0', 'male_04.wav': '#7209b7', 'male_05.wav': '#4895ef', 'Morgan_Freeman CC3.wav': '#f1f5ef', 'James_Earl_Jones CC3.wav': '#ff4d4d', 'David_Attenborough CC3.wav': '#a0d468', 'Clint_Eastwood CC3.wav': '#8e44ad', 'Clint_Eastwood CC3 (enhanced).wav': '#9b59b6', 'arnold.wav': '#e67e22'
  };
  return colors[filename] || 'var(--text)';
}

async function refreshVoices() {
  try {
    const data = await api('/api/voices');
    renderVoices(data);
  } catch (err) {
    log(`No pude cargar el catálogo de voces: ${err.message}`);
  }
}

function renderVoices(data) {
  if (!data || !Array.isArray(data.voices)) return;
  const hadMany = els.voiceSelect.options.length > 1;
  const gotMany = data.voices.length > 1;
  if (!gotMany && hadMany) {
    if (data.current) els.voiceSelect.value = data.current;
    return;
  }
  els.voiceSelect.replaceChildren();

  const sorted = [...data.voices].sort((a, b) => voiceSortKey(a) - voiceSortKey(b));

  const groups = {
    'Voces M': [],
    'Voces V': [],
    'Otras voces': []
  };

  sorted.forEach(v => {
    const group = voiceGroup(v);
    if (!groups[group]) groups[group] = [];
    groups[group].push(v);
  });

  ['Voces M', 'Voces V', 'Otras voces'].forEach(groupName => {
    const voices = groups[groupName];
    if (voices.length === 0) return;

    const g = document.createElement('optgroup');
    g.label = groupName;

    voices.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = `● ${voiceLabel(v)}`;
      opt.title = v;
      opt.dataset.voiceColor = voiceColor(v);
      if (v === data.current) opt.selected = true;
      g.appendChild(opt);
    });
    els.voiceSelect.appendChild(g);
  });
}

async function changeVoice() {
  const voice = els.voiceSelect.value;
  if (!voice) return;
  const releaseBusy = beginBusyLease();
  try {
    resetAudioLifecycle('Cambiando voz; audio anterior detenido.');
    const data = await api('/api/voice', { voice });
    renderStatus(data);
    log(`Voz cambiada a ${voice}.`);
  } catch (err) {
    log(`No pude cambiar la voz: ${err.message}`);
    await refreshVoices();
  } finally {
    releaseBusy();
  }
}

async function ensureVoiceCatalog() {
  if (!els.voiceSelect || els.voiceSelect.options.length > 1 || voiceCatalogRefreshInFlight) return;
  voiceCatalogRefreshInFlight = true;
  try {
    await refreshVoices();
  } finally {
    voiceCatalogRefreshInFlight = false;
  }
}

function renderVoiceStatus(voice) {
  if (voice && els.voiceSelect.value !== voice) {
    els.voiceSelect.value = voice;
  }
  if (els.voiceSelect.options.length <= 1) {
    ensureVoiceCatalog();
  }
}

function resetReaderViewport() {
  const reader = document.querySelector('.reader');
  if (reader && typeof reader.scrollTop === 'number') {
    reader.scrollTop = 0;
  }
  if (els.chunk && typeof els.chunk.scrollTop === 'number') {
    els.chunk.scrollTop = 0;
  }
}

function renderStatus(data) {
  const selectedNotesDocId = data.doc_id || LAB_NOTES_DOC_ID;
  const shouldRefreshNotes = selectedNotesDocId !== notesState.docId || data.current !== notesState.current || Boolean(data.notes && data.notes.count !== notesState.items.length);
  const nextDocId = String(data.doc_id || data.document && data.document.doc_id || '');
  const nextBlockIndex = Number(data.current || data.document && data.document.current || 0);
  status = data;
  busyControls.setStatus(data, els.noteInput ? els.noteInput.value : '');
  renderReasoningStatus(data.reasoning || {});
  renderProfileStatus(data.profile || {});
  renderVeilStatus(data.veil || {});
  renderLaboratoryMode(data.laboratory_mode || {});
  if (!dialogue.active && !dialogue.processing && !dialogue.speaking) {
    setDialogueInfo(`Diálogo apagado. ${laboratoryModeSummary()}`);
  }
  const header = documentHeaderState(data);
  els.docTitle.textContent = header.title;
  els.docMeta.textContent = header.meta;
  const mainDocument = data.main_document && typeof data.main_document === 'object' ? data.main_document : {};
  els.mainDocTitle.textContent = mainDocument.title || data.title || 'Ningún documento principal';
  els.mainDocMeta.textContent = mainDocument.doc_id ? `${mainDocument.doc_id} | ${mainDocument.total || data.total || 0} bloques` : 'Sin lectura activa.';
  renderReferenceDocuments(Array.isArray(data.reference_documents) ? data.reference_documents : []);
  renderLabFocus(data.laboratory_focus || {});
  els.jumpInput.max = data.total || 1;
  els.jumpInput.value = data.current || 1;
  els.chunk.textContent = header.chunk;
  els.chunk.classList.toggle('empty', !data.text);
  renderAudioExportStatus(data.audio_export || {});
  const didChangeViewport = nextDocId !== lastRenderedDocId || nextBlockIndex !== lastRenderedBlockIndex || header.chunk !== lastRenderedBlockText;
  if (didChangeViewport) {
    resetReaderViewport();
  }
  lastRenderedDocId = nextDocId;
  lastRenderedBlockIndex = nextBlockIndex;
  lastRenderedBlockText = header.chunk;
  const ttsState = describeTtsStatus(data);
  const ttsOk = ttsState.state !== 'down';
  els.ttsDot.classList.toggle('ok', ttsOk);
  els.ttsDot.classList.toggle('warn', ttsState.state === 'fallback');
  els.ttsStatus.textContent = ttsState.label;
  if (els.ttsChip) els.ttsChip.title = ttsState.tooltip || ttsState.label;
  const ttsMessage = ttsState.tooltip || 'TTS no disponible';
  const canRead = Boolean(data && data.document && data.document.loaded && data.text);
  if (els.readBtn) {
    els.readBtn.title = canRead ? (ttsActionAvailable(data) ? 'Leer bloque actual' : 'Intentar leer; el backend comprobará cache y TTS actual') : ttsMessage;
  }
  if (els.repeatBtn) {
    els.repeatBtn.title = canRead ? 'Repetir bloque actual' : ttsMessage;
  }
  const sttState = describeSttStatus(data);
  const sttOk = sttState.state !== 'down';
  els.sttDot.classList.toggle('ok', sttOk);
  els.sttDot.classList.toggle('warn', sttState.state === 'fallback');
  els.sttStatus.textContent = sttState.label;
  if (els.sttChip) els.sttChip.title = sttState.tooltip || sttState.label;
  renderPrepareStatus(data.prepare);
  renderVoiceStatus(data.voice);
  if (shouldRefreshNotes) {
    refreshNotes().catch(() => {});
  }
}

function describeTtsStatus(data) {
  const services = data && data.services && typeof data.services === 'object' ? data.services : {};
  const tts = services.tts && typeof services.tts === 'object' ? services.tts : data && data.tts || {};
  const ok = Boolean(tts && (tts.ready || tts.ok));
  if (!ok) {
    const detail = friendlyTtsMessage(tts && tts.detail);
    const isBlocked = String(tts && tts.detail || '').startsWith('tts_owner_');
    return { state: 'down', label: isBlocked ? 'TTS bloqueado' : 'TTS off', tooltip: detail };
  }
  const url = String(tts.url || '');
  if (url.includes(':7853')) {
    return { state: 'gpu', label: 'TTS 7853', tooltip: 'TTS GPU 7853 listo' };
  }
  if (url.includes(':7851')) {
    return { state: 'fallback', label: 'TTS 7851', tooltip: 'TTS CPU 7851 fallback' };
  }
  return { state: 'ready', label: 'TTS listo', tooltip: 'TTS listo' };
}

function describeSttStatus(data) {
  const services = data && data.services && typeof data.services === 'object' ? data.services : {};
  const stt = services.stt && typeof services.stt === 'object' ? services.stt : {};
  const ready = Boolean(stt && (stt.ready || stt.ok));
  if (!ready) {
    return { state: 'down', label: 'STT: no disponible', tooltip: 'STT no disponible' };
  }
  const selected = String(stt.selected || stt.provider || '').trim();
  const provider = String(stt.provider || selected || '').trim();
  const model = String(stt.model || '').trim();
  const command = String(stt.command || '').trim();
  const primary = stt.primary && typeof stt.primary === 'object' ? stt.primary : {};
  const primaryProvider = String(primary.provider || 'faster_whisper_server').trim();
  const primaryUrl = String(primary.url || 'http://127.0.0.1:8021').trim();
  const primaryOk = Boolean(primary.ok);
  const primaryDetail = String(primary.detail || '').trim();
  const primaryLabel = primaryProvider === 'faster_whisper_server' ? 'faster-whisper 8021' : `${primaryProvider || 'primario'} 8021`;
  if (selected === 'whisper_cli' || provider === 'whisper_cli') {
    const tooltip = [
      command ? `command: ${command}` : '',
      model ? `model: ${model}` : '',
      `primary: ${primaryLabel}`,
      primaryUrl ? `url: ${primaryUrl}` : '',
      primaryDetail ? `detail: ${primaryDetail}` : '',
    ].filter(Boolean).join(' | ');
    return {
      state: 'fallback',
      label: 'STT: whisper_cli · fallback operativo · primario 8021 offline',
      tooltip: tooltip || 'STT whisper_cli fallback; primario 8021 offline',
    };
  }
  if (selected === 'faster_whisper_server' || provider === 'faster_whisper_server' || primaryOk) {
    const tooltip = [
      `provider: ${primaryLabel}`,
      primaryUrl ? `url: ${primaryUrl}` : '',
      model ? `model: ${model}` : '',
    ].filter(Boolean).join(' | ');
    return {
      state: 'ready',
      label: 'STT: faster-whisper 8021 · primario online',
      tooltip: tooltip || 'STT faster-whisper 8021 primario online',
    };
  }
  return {
    state: 'ready',
    label: `STT: ${selected || provider || 'listo'}`,
    tooltip: [command ? `command: ${command}` : '', model ? `model: ${model}` : ''].filter(Boolean).join(' | ') || 'STT listo',
  };
}

function currentReasoningMode() {
  return String(status && status.reasoning && status.reasoning.mode || 'thinking');
}

function currentReasoningLabel() {
  return String(status && status.reasoning && status.reasoning.label || 'Pensamiento');
}

function currentLaboratoryMode() {
  return String(status && status.laboratory_mode && status.laboratory_mode.mode || 'document');
}

function laboratoryModeSummary() {
  const profile = String(status && status.profile && status.profile.label || 'Académica');
  return `${profile}. ${currentLaboratoryMode() === 'free' ? 'Modo libre.' : 'Anclado al texto.'}`;
}

function documentHeaderState(data) {
  const item = data && data.document && typeof data.document === 'object' ? data.document : {};
  const anchor = data && data.anchor && typeof data.anchor === 'object' ? data.anchor : {};
  const loaded = Boolean(item.loaded);
  const title = String(item.title || data && data.title || '').trim();
  const current = Number(item.current || data && data.current || 0);
  const total = Number(item.total || data && data.total || 0);
  const hasChunkText = Boolean(data && String(data.text || '').trim());
  const anchorMode = String(anchor.mode || currentLaboratoryMode() || 'document');
  const usesDocument = Boolean(anchor.uses_document);
  const documentActive = Boolean(loaded || usesDocument || (title && (current > 0 || total > 0 || hasChunkText)));
  const documentAvailable = Boolean(anchor.document_available !== undefined ? anchor.document_available : documentActive);
  if (anchorMode === 'free') {
    return {
      title: 'Modo libre',
      meta: documentAvailable && title ? `Documento disponible: ${title}` : 'Sin documento activo',
      chunk: data && data.text ? data.text : 'Subí un documento para empezar.'
    };
  }
  if (documentActive && title) {
    return {
      title: `Documento — ${title}`,
      meta: `Bloque ${current || 0} de ${total || 0}`,
      chunk: data && data.text ? data.text : 'Subí un documento para empezar.'
    };
  }
  return {
    title: 'Ningún documento activo',
    meta: 'Sin documento activo',
    chunk: data && data.text ? data.text : 'Subí un documento para empezar.'
  };
}

function dialogueAppliedReasoningLabel(data) {
  const applied = String(data && (data.reasoning_mode_applied || data.reasoning_mode) || currentReasoningMode());
  if (applied === 'supreme') {
    return 'Pensamiento supremo';
  }
  if (applied === 'contrapunto' || applied === 'pensamiento_critico') {
    return 'Pensamiento crítico';
  }
  if (applied === 'normal') {
    return 'Normal';
  }
  return 'Pensamiento';
}

function dialogueModeSummary(data) {
  const requested = String(data && data.reasoning_mode_requested || currentReasoningMode());
  const applied = String(data && (data.reasoning_mode_applied || data.reasoning_mode) || requested);
  if (Boolean(data && data.reasoning_degraded) && requested === 'supreme' && applied === 'thinking') {
    return 'Supremo pedido; diálogo usa Pensamiento para cuidar latencia.';
  }
  const profile = String(status && status.profile && status.profile.label || 'Académica');
  const veilLabel = String(status && status.veil && status.veil.label || 'Lucy');
  return `${profile} (${veilLabel}). ${dialogueAppliedReasoningLabel(data)} activo.`;
}

function pendingThoughtLabel() {
  const mode = currentReasoningMode();
  const scope = currentLaboratoryMode() === 'free' ? 'con laboratorio libre' : 'con el documento abierto';
  const profile = String(status && status.profile && status.profile.label || 'Académica');
  const veilLabel = String(status && status.veil && status.veil.label || 'Lucy');
  if (mode === 'supreme' || mode === 'contrapunto' || mode === 'pensamiento_critico') {
    return `${profile} (${veilLabel}): Repensando en profundidad ${scope}...`;
  }
  if (mode === 'normal') {
    return `${profile} (${veilLabel}): Respondiendo ${scope}...`;
  }
  return `${profile} (${veilLabel}): Pensando ${scope}...`;
}

function renderReasoningStatus(reasoning) {
  const item = reasoning && typeof reasoning === 'object' ? reasoning : {};
  const mode = String(item.mode || 'thinking');
  const buttons = {
    normal: els.reasoningNormalBtn,
    thinking: els.reasoningThinkingBtn,
    supreme: els.reasoningSupremeBtn,
    pensamiento_critico: els.reasoningPensamientoCriticoBtn
  };
  Object.entries(buttons).forEach(([key, button]) => {
    button.classList.toggle('active', key === mode || (key === 'pensamiento_critico' && mode === 'contrapunto'));
    button.setAttribute('aria-pressed', (key === mode || (key === 'pensamiento_critico' && mode === 'contrapunto')) ? 'true' : 'false');
  });
  const label = String(item.label || (mode === 'supreme' ? 'Pensamiento supremo' : (mode === 'contrapunto' || mode === 'pensamiento_critico') ? 'Pensamiento crítico' : mode === 'normal' ? 'Normal' : 'Pensamiento'));
  const description = String(item.description || '');
  const passes = Number(item.passes || (mode === 'supreme' ? 3 : 1));
  const think = Object.prototype.hasOwnProperty.call(item, 'think') ? Boolean(item.think) : mode !== 'normal';
  els.reasoningCaption.textContent = `${label} | ${think ? 'thinking activo' : 'sin thinking'} | ${passes} pasada${passes === 1 ? '' : 's'}${description ? ` | ${description}` : ''}`;
}

function renderLaboratoryMode(modeInfo) {
  const item = modeInfo && typeof modeInfo === 'object' ? modeInfo : {};
  const mode = String(item.mode || 'document');
  els.freeModeBtn.classList.toggle('active', mode === 'free');
  els.freeModeBtn.setAttribute('aria-pressed', mode === 'free' ? 'true' : 'false');
  els.freeModeBtn.textContent = mode === 'free' ? 'Modo libre activo' : 'Modo libre';
  els.freeModeBtn.title = String(item.description || '');
  els.chatInput.placeholder = mode === 'free' ? 'Escribí lo que quieras conversar...' : 'Escribí sobre el texto actual...';
}

async function clearDocument() {
  if (!confirm('¿Limpiar el documento activo?')) return;
  const releaseBusy = beginBusyLease();
  try {
    resetAudioLifecycle('Documento y audio anteriores descartados.');
    const res = await fetch('/api/document/clear', { method: 'POST' });
    const data = await res.json();
    renderStatus(data);
    addChatMessage('system', 'Documento activo eliminado.');
  } catch (err) {
    alert('Error al limpiar documento: ' + err.message);
  } finally {
    releaseBusy();
  }
}

async function setLaboratoryMode(mode) {
  const targetMode = String(mode || '').trim();
  try {
    const data = await api('/api/laboratory/mode', { mode: targetMode });
    if (!status) {
      status = {};
    }
    status.laboratory_mode = data;
    renderLaboratoryMode(data);
    log(`${data.label || 'Modo de laboratorio'} activado.`);
    if (dialogue.active && !dialogue.processing && !dialogue.speaking) {
      setDialogueInfo(`Escuchando... ${dialogueModeSummary({})} ${laboratoryModeSummary()}`);
    } else if (!dialogue.active) {
      setDialogueInfo(`Diálogo apagado. ${laboratoryModeSummary()}`);
    }
  } catch (err) {
    log(`No pude cambiar el modo del laboratorio: ${err.message}`);
  }
}

function renderProfileStatus(profile) {
  const item = profile && typeof profile === 'object' ? profile : {};
  const mode = String(item.mode || 'academica');
  els.profileSelect.value = mode;
}

function renderVeilStatus(veilInfo) {
  const item = veilInfo && typeof veilInfo === 'object' ? veilInfo : {};
  const mode = String(item.mode || 'lucy');
  const available = Array.isArray(item.available) ? item.available : [];
  if (available.length > 0 && els.veilSelect.children.length === 0) {
    els.veilSelect.replaceChildren();
    for (const v of available) {
      const opt = document.createElement('option');
      opt.value = v.mode;
      opt.textContent = `Velo: ${v.label}`;
      opt.title = v.description;
      els.veilSelect.appendChild(opt);
    }
  }
  els.veilSelect.value = mode;
  els.veilSelect.title = item.description || '';
}

async function setProfileMode(mode) {
  const targetMode = String(mode || '').trim();
  try {
    const data = await api('/api/profile', { mode: targetMode });
    if (!status) {
      status = {};
    }
    status.profile = data;
    renderProfileStatus(data);
    log(`Perfil de Lucy cambiado a ${data.label || targetMode}.`);
    if (dialogue.active && !dialogue.processing && !dialogue.speaking) {
      setDialogueInfo(`Escuchando... ${dialogueModeSummary({})} ${laboratoryModeSummary()}`);
    } else if (!dialogue.active) {
      setDialogueInfo(`Diálogo apagado. ${laboratoryModeSummary()}`);
    }
  } catch (err) {
    log(`No pude cambiar el perfil: ${err.message}`);
    if (status && status.profile) els.profileSelect.value = status.profile.mode;
  }
}

async function setVeilMode(mode) {
  const targetMode = String(mode || '').trim();
  try {
    const data = await api('/api/veil', { mode: targetMode });
    if (!status) {
      status = {};
    }
    status.veil = data;
    renderVeilStatus(data);
    log(`Velo cambiado a ${data.label || targetMode}.`);
    if (dialogue.active && !dialogue.processing && !dialogue.speaking) {
      setDialogueInfo(`Escuchando... ${dialogueModeSummary({})} ${laboratoryModeSummary()}`);
    } else if (!dialogue.active) {
      setDialogueInfo(`Diálogo apagado. ${laboratoryModeSummary()}`);
    }
  } catch (err) {
    log(`No pude cambiar el velo: ${err.message}`);
    if (status && status.veil) els.veilSelect.value = status.veil.mode;
  }
}

async function setReasoningMode(mode) {
  const targetMode = String(mode || '').trim();
  if (!targetMode || currentReasoningMode() === targetMode) {
    return;
  }
  const releaseBusy = beginBusyLease();
  try {
    const data = await api('/api/reasoning/mode', { mode: targetMode });
    if (!status) {
      status = {};
    }
    status.reasoning = data;
    status.dialogue_reasoning = data.dialogue_reasoning || status.dialogue_reasoning || {};
    renderReasoningStatus(data);
    const dialogueReasoning = status && status.dialogue_reasoning && typeof status.dialogue_reasoning === 'object' ? status.dialogue_reasoning : {};
    if (String(data.mode || targetMode) === 'supreme' && String(dialogueReasoning.applied_mode || '') === 'thinking' && Boolean(dialogueReasoning.degraded)) {
      log(`Modo de razonamiento: ${data.label || targetMode}. En Dialogar se usa Pensamiento para cuidar latencia.`);
    } else {
      log(`Modo de razonamiento: ${data.label || targetMode}.`);
    }
    if (dialogue.active && !dialogue.processing && !dialogue.speaking) {
      setDialogueInfo(`Escuchando... ${dialogueModeSummary({ reasoning_mode_requested: String(data.mode || targetMode), reasoning_mode_applied: String(dialogueReasoning.applied_mode || data.mode || targetMode), reasoning_degraded: Boolean(dialogueReasoning.degraded) })} ${laboratoryModeSummary()}`);
    }
  } catch (err) {
    log(`No pude cambiar el modo mental: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

function renderLabFocus(focus) {
  const item = focus && typeof focus === 'object' ? focus : {};
  const title = String(item.title || '').trim();
  if (!title) {
    els.labFocus.innerHTML = '<strong>Foco del laboratorio</strong>Sin foco activo.';
    return;
  }
  const role = item.role === 'main' ? 'principal' : 'consulta';
  const query = item.query ? `<br>Búsqueda: ${String(item.query)}` : '';
  const excerpt = String(item.text || '').trim();
  const clipped = excerpt.length > 240 ? `${excerpt.slice(0, 240).trimEnd()}...` : excerpt;
  els.labFocus.innerHTML = `<strong>Foco del laboratorio</strong>${title} | ${role} | bloque ${Number(item.chunk_number || 0)} de ${Number(item.total || 0)}${query}${clipped ? `<br>${clipped}` : ''}`;
}

async function promoteReference(docId) {
  const releaseBusy = beginBusyLease();
  try {
    resetAudioLifecycle('Cambiando documento principal; audio anterior detenido.');
    const data = await api('/api/reference/promote', { doc_id: docId });
    renderStatus(data);
    log(data.message || 'Documento de consulta promovido a principal.');
  } catch (err) {
    log(`No pude promover la consulta: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

async function removeReference(docId) {
  try {
    const data = await api('/api/reference/remove', { doc_id: docId });
    renderReferenceDocuments(data.items || []);
    if (status) {
      status.reference_documents = data.items || [];
    }
    log('Documento de consulta quitado.');
  } catch (err) {
    log(`No pude quitar la consulta: ${err.message}`);
  }
}

function renderReferenceDocuments(items) {
  const references = Array.isArray(items) ? items : [];
  els.referenceList.replaceChildren();
  if (!references.length) {
    const empty = document.createElement('div');
    empty.className = 'reference-empty';
    empty.textContent = 'Todavía no cargaste documentos de consulta.';
    els.referenceList.appendChild(empty);
    return;
  }
  for (const item of references) {
    const card = document.createElement('details');
    card.className = 'reference-card';
    const summary = document.createElement('summary');
    const title = document.createElement('span');
    title.className = 'reference-title';
    title.textContent = item.title || item.doc_id || 'Consulta';
    const caret = document.createElement('span');
    caret.className = 'reference-caret';
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = '▾';
    summary.append(title, caret);
    const content = document.createElement('div');
    content.className = 'reference-content';
    const meta = document.createElement('span');
    meta.className = 'reference-meta';
    meta.textContent = `${item.doc_id || ''}${item.source_type ? ` | ${item.source_type}` : ''}${item.total ? ` | ${item.total} bloques` : ''}`;
    const preview = document.createElement('p');
    preview.className = 'reference-meta';
    preview.textContent = item.preview || 'Sin extracto.';
    const actions = document.createElement('div');
    actions.className = 'reference-actions';
    const promoteBtn = document.createElement('button');
    promoteBtn.type = 'button';
    promoteBtn.textContent = 'Hacer principal';
    promoteBtn.addEventListener('click', () => promoteReference(item.doc_id));
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.textContent = 'Quitar';
    removeBtn.addEventListener('click', () => removeReference(item.doc_id));
    actions.append(promoteBtn, removeBtn);
    content.append(meta, preview, actions);
    card.append(summary, content);
    els.referenceList.appendChild(card);
  }
}

function noteReference(note) {
  if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') {
    return `L${Number(note && note.anchor_number || 1)}`;
  }
  return `B${Number(note && note.chunk_number || note && note.anchor_number || 1)}`;
}

function renderNotes(items, activeDocId = '') {
  const notes = Array.isArray(items) ? items : [];
  const selectedDocId = activeDocId || status && status.doc_id || '';
  const laboratoryMode = selectedDocId === LAB_NOTES_DOC_ID;
  const hasLabNotes = notes.some(note => String(note && note.source_kind || '').toLowerCase() === 'laboratory');
  const hasDocumentNotes = notes.some(note => String(note && note.source_kind || '').toLowerCase() !== 'laboratory');
  notesState = {
    docId: selectedDocId,
    current: status && status.current || 0,
    items: notes
  };
  const currentCount = notes.filter(note => String(note && note.source_kind || '').toLowerCase() !== 'laboratory' && Number(note.chunk_number || 0) === Number(notesState.current || 0)).length;
  els.notesSummary.textContent = `${laboratoryMode ? 'Notas del laboratorio' : (hasLabNotes && hasDocumentNotes ? 'Notas del documento y laboratorio' : 'Notas del documento')} (${notes.length})`;
  if (!notes.length) {
    els.notesInfo.textContent = laboratoryMode ? 'Sin notas del laboratorio todavía.' : 'Sin notas todavía.';
  } else if (laboratoryMode) {
    els.notesInfo.textContent = `${notes.length} nota${notes.length === 1 ? '' : 's'} en el laboratorio.`;
  } else if (hasLabNotes && hasDocumentNotes) {
    const labCount = notes.filter(note => String(note && note.source_kind || '').toLowerCase() === 'laboratory').length;
    els.notesInfo.textContent = `${currentCount} nota${currentCount === 1 ? '' : 's'} en este bloque y ${labCount} de laboratorio.`;
  } else {
    els.notesInfo.textContent = `${currentCount} nota${currentCount === 1 ? '' : 's'} en este bloque.`;
  }
  els.notesList.replaceChildren();
  if (!notes.length) {
    return;
  }
  for (const note of notes) {
    const row = document.createElement('details');
    row.className = 'note-row';
    if (String(note && note.source_kind || '').toLowerCase() !== 'laboratory' && Number(note.chunk_number || 0) === Number(notesState.current || 0)) {
      row.classList.add('current');
    }
    const summary = document.createElement('summary');
    const label = document.createElement('span');
    label.className = 'note-label';
    label.textContent = `${noteReference(note)} ${compactNoteLabel(note)}`.trim();
    label.title = note.text || '';
    const renameBtn = document.createElement('button');
    renameBtn.type = 'button';
    renameBtn.className = 'note-rename';
    renameBtn.textContent = '+';
    renameBtn.title = 'Editar nombre';
    renameBtn.setAttribute('aria-label', 'Editar nombre de la nota');
    renameBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      renameNote(note);
    });
    summary.append(label, renameBtn);
    const text = document.createElement('p');
    text.className = 'note-text';
    text.textContent = note.text || '';
    const quote = document.createElement('p');
    quote.className = 'note-quote';
    quote.textContent = note.quote ? `Texto: ${note.quote}` : '';
    const actions = document.createElement('div');
    actions.className = 'note-actions';
    const goBtn = document.createElement('button');
    goBtn.type = 'button';
    if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') {
      goBtn.textContent = 'Sin bloque';
      goBtn.disabled = true;
    } else {
      goBtn.textContent = 'Ir al bloque';
      goBtn.addEventListener('click', event => {
        event.preventDefault();
        goToNote(note);
      });
    }
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.textContent = 'Editar';
    editBtn.addEventListener('click', event => {
      event.preventDefault();
      editNote(note);
    });
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.textContent = 'Borrar';
    deleteBtn.addEventListener('click', event => {
      event.preventDefault();
      deleteNote(note);
    });
    actions.append(goBtn, editBtn, deleteBtn);
    row.append(summary, text);
    if (note.quote) {
      row.append(quote);
    }
    row.append(actions);
    els.notesList.appendChild(row);
  }
}

function compactNoteLabel(note) {
  const saved = String(note && note.label || '').trim();
  if (saved) {
    return saved;
  }
  const raw = String(note && note.text || '').trim();
  const words = raw.match(/[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+/g) || [];
  const stop = new Set(['a', 'al', 'bloque', 'como', 'con', 'de', 'del', 'el', 'en', 'es', 'esa', 'ese', 'esta', 'este', 'la', 'las', 'lo', 'los', 'nota', 'notas', 'para', 'por', 'que', 'se', 'sobre', 'toma', 'tomar', 'tomá', 'tome', 'un', 'una', 'y']);
  const selected = [];
  for (const word of words) {
    if (/^\\d+$/.test(word)) {
      continue;
    }
    if (stop.has(word.toLowerCase())) {
      continue;
    }
    selected.push(word);
    if (selected.length >= 3) {
      break;
    }
  }
  return (selected.length ? selected : words.slice(0, 3)).join(' ');
}

async function refreshNotes() {
  if (!status) {
    notesState = { docId: '', current: 0, items: [] };
    els.notesSummary.textContent = 'Notas del documento';
    els.notesInfo.textContent = 'Cargá un documento para tomar notas.';
    els.notesList.replaceChildren();
    return;
  }
  if (!status.doc_id) {
    const data = await api(`/api/notes?doc_id=${encodeURIComponent(LAB_NOTES_DOC_ID)}`);
    renderNotes(data.items || [], data.doc_id || LAB_NOTES_DOC_ID);
    return;
  }
  const [docData, labData] = await Promise.all([
    api(`/api/notes?doc_id=${encodeURIComponent(status.doc_id)}`),
    api(`/api/notes?doc_id=${encodeURIComponent(LAB_NOTES_DOC_ID)}`).catch(() => ({ items: [] }))
  ]);
  const merged = [...(docData.items || []), ...(labData.items || [])];
  renderNotes(merged, status.doc_id);
}

async function saveCurrentNote() {
  const text = els.noteInput.value.trim();
  if (!text) {
    log('Escribí una nota antes de guardarla.');
    return;
  }
  if (!status || !status.doc_id) {
    log('Cargá un documento antes de guardar notas.');
    return;
  }
  const releaseBusy = beginBusyLease();
  try {
    const data = await api('/api/notes/create', { text });
    els.noteInput.value = '';
    busyControls.setNoteText(els.noteInput.value);
    renderNotes(data.items || [], data.note && data.note.doc_id || status.doc_id);
    log(`Nota guardada como ${noteReference(data.note || {})}.`);
  } catch (err) {
    log(`No pude guardar la nota: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

async function goToNote(note) {
  if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') {
    log(`La nota ${noteReference(note)} pertenece al laboratorio y no tiene bloque.`);
    return;
  }
  try {
    const data = await api('/api/jump', { index: Number(note.chunk_number || 1) });
    renderStatus(data);
    log(`Salté al bloque ${note.chunk_number || 1}.`);
  } catch (err) {
    log(`No pude ir a la nota: ${err.message}`);
  }
}

async function renameNote(note) {
  const currentLabel = compactNoteLabel(note);
  const nextLabel = window.prompt('Nombre corto de la nota', currentLabel);
  if (nextLabel === null) {
    return;
  }
  const label = nextLabel.trim();
  if (!label) {
    log('El nombre de la nota no puede quedar vacío.');
    return;
  }
  try {
    const data = await api('/api/notes/rename', { note_id: note.note_id, doc_id: note.doc_id, label });
    renderNotes(data.items || []);
    log('Nombre de nota actualizado.');
  } catch (err) {
    log(`No pude renombrar la nota: ${err.message}`);
  }
}

async function editNote(note) {
  const nextText = window.prompt('Editar nota', note.text || '');
  if (nextText === null) {
    return;
  }
  const text = nextText.trim();
  if (!text) {
    log('La nota no puede quedar vacía.');
    return;
  }
  try {
    const data = await api('/api/notes/update', { note_id: note.note_id, doc_id: note.doc_id, text });
    renderNotes(data.items || []);
    log('Nota actualizada.');
  } catch (err) {
    log(`No pude editar la nota: ${err.message}`);
  }
}

async function deleteNote(note) {
  if (!window.confirm('Borrar esta nota?')) {
    return;
  }
  try {
    const data = await api('/api/notes/delete', { note_id: note.note_id, doc_id: note.doc_id });
    renderNotes(data.items || []);
    log('Nota borrada.');
  } catch (err) {
    log(`No pude borrar la nota: ${err.message}`);
  }
}

function invalidatePendingRead() {
  audioLifecycleSequence += 1;
  activeReadRequest += 1;
  if (activeReadController) {
    activeReadController.abort();
    activeReadController = null;
  }
}

function resetAudioLifecycle(message = '') {
  invalidatePendingRead();
  els.continuousToggle.checked = false;
  try { els.player.pause(); } catch (_) {}
  try { els.player.currentTime = 0; } catch (_) {}
  els.player.removeAttribute('src');
  els.player.load();
  if (message) log(message);
}

function playAudio(data, expectedSequence, expectedRequest) {
  if (!data.audio_url) {
    return false;
  }
  const currentGeneration = Number(status && status.document_generation || 0);
  const currentDocId = String(status && status.doc_id || '');
  const currentIndex = Math.max(0, Number(status && status.current || 1) - 1);
  const currentVoice = String(status && status.voice || '');
  const currentLanguage = String(status && status.language || '');
  if (data.stale || data.cancelled || expectedSequence !== audioLifecycleSequence || expectedRequest !== activeReadRequest || Number(data.document_generation || 0) !== currentGeneration || String(data.requested_doc_id || '') !== currentDocId || Number(data.requested_chunk_index) !== currentIndex || String(data.voice || '') !== currentVoice || String(data.language || '') !== currentLanguage) {
    log('Audio descartado porque cambió el documento o el bloque.');
    return false;
  }
  els.player.src = data.audio_url;
  els.player.play().catch(() => {
    log('Audio generado. Tocá play si el navegador bloqueó la reproducción automática.');
  });
  return true;
}

function addChatMessage(kind, text) {
  const node = document.createElement('div');
  node.className = `chat-msg ${kind}`;
  const label = kind === 'user' ? 'Vos' : kind === 'assistant' ? 'Laboratorio' : 'Sistema';
  node.textContent = `${label}: ${text}`;
  els.chatLog.appendChild(node);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  return node;
}

function setDialogueInfo(text) {
  els.dialogueInfo.textContent = text;
}

async function refresh() {
  const data = await api('/api/status');
  renderStatus(data);
}

function canReadFile(file) {
  const name = file.name.toLowerCase();
  const accepted = ['.txt', '.md', '.markdown', '.pdf', '.doc', '.docm', '.docx', '.dot', '.dotx', '.odt', '.ott', '.sxw', '.pages', '.rtf', '.html', '.htm', '.csv', '.log'];
  return accepted.some(ext => name.endsWith(ext)) || file.type.startsWith('text/');
}

async function pollImportJob(jobId) {
  while (true) {
    await wait(700);
    const data = await api(`/api/import-status?id=${encodeURIComponent(jobId)}`);
    setImportProgress(data.percent || 0);
    const total = data.total ? ` ${data.current || 0}/${data.total}` : '';
    els.uploadInfo.textContent = `${data.filename}: ${data.message || data.stage || 'convirtiendo...'}${total}`;
    log(data.message || 'Convirtiendo documento...');
    if (data.status === 'done') {
      setImportProgress(100);
      return data.result;
    }
    if (data.status === 'error') {
      throw new Error(data.error || data.message || 'import_failed');
    }
  }
}

async function loadFile(file) {
  if (!file) {
    return;
  }
  if (!canReadFile(file)) {
    log('Ese formato todavía no lo reconozco. Probá PDF, DOCX/DOTX, ODT, RTF, TXT o MD.');
    els.uploadInfo.textContent = `${file.name}: formato no soportado todavía.`;
    return;
  }
  const role = els.referenceModeToggle.checked ? 'reference' : 'main';
  const releaseBusy = beginBusyLease();
  try {
    if (role === 'main') {
      resetAudioLifecycle('Cargando documento nuevo; audio anterior detenido.');
    }
    log(role === 'reference' ? 'Agregando documento de consulta...' : 'Preparando documento...');
    setImportProgress(0);
    els.uploadInfo.textContent = `${file.name}: convirtiendo para ${role === 'reference' ? 'consulta' : 'lectura'}...`;
    const url = `/api/import-file/start?filename=${encodeURIComponent(file.name)}&mime=${encodeURIComponent(file.type || '')}&role=${encodeURIComponent(role)}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      body: file
    });
    const started = await res.json();
    if (!res.ok || started.ok === false) {
      throw new Error(started.error || 'import_start_failed');
    }
    setImportProgress(started.percent || 1);
    els.uploadInfo.textContent = `${file.name}: documento recibido. Convirtiendo...`;
    const data = await pollImportJob(started.job_id);
    renderStatus(data);
    const convertedKb = data.converted_bytes ? ` Texto convertido: ${Math.max(1, Math.round(data.converted_bytes / 1024))} KB.` : '';
    els.uploadInfo.textContent = `${file.name} ${data.role === 'reference' ? 'agregado como consulta' : 'cargado como documento principal'}. ${data.total || 0} bloques listos. ${data.import_detail || ''}.${convertedKb}`;
    setReferenceMode(false);
    if (data.role !== 'reference' && els.autoReadToggle.checked) {
      log('Texto cargado. Generando voz del primer bloque...');
      await readCurrent();
    } else if (data.role === 'reference') {
      log(data.message || 'Documento de consulta agregado.');
    } else {
      log('Texto cargado. La voz ya puede leer el bloque actual.');
    }
  } catch (err) {
    log(`No pude cargar el archivo: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

function canConvertPdf(file) {
  if (!file) {
    return false;
  }
  return file.name.toLowerCase().endsWith('.pdf') || String(file.type || '').toLowerCase().includes('pdf');
}

 async function convertPdfToWord(file) {
  if (!file) return;
  if (!canConvertPdf(file)) {
    els.pdfToWordInfo.textContent = `${file.name}: solo PDF.`;
    els.pdfToWordDownload.classList.add('is-hidden');
    log('La herramienta PDF → Word solo acepta archivos PDF.');
    return;
  }
  els.pdfToWordInfo.textContent = 'Subiendo...';
  els.pdfToWordDownload.classList.add('is-hidden');
  log('Iniciando conversión de PDF...');

  const form = new FormData();
  form.append('file', file, file.name);

  try {
    const res = await fetch('/api/tools/pdf-to-docx', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'upload_failed');

    const jobId = data.job_id;
    log(`Conversión en segundo plano: job ${jobId}`);

    // Polling loop
    let attempts = 0;
    const maxAttempts = 1800; // 1 hour at 2s polling

    while (attempts < maxAttempts) {
      const statusRes = await fetch(`/api/tools/pdf-to-docx/status/${jobId}`);
      const statusData = await statusRes.json();

      if (!statusRes.ok || statusData.ok === false) throw new Error(statusData.error || 'status_failed');

      const s = statusData;
      if (s.state === 'done') {
        const warningText = Array.isArray(s.warnings) && s.warnings.length ? ` Avisos: ${s.warnings.join(' | ')}` : '';
        els.pdfToWordInfo.textContent = `Listo: guardado en Descargas. ${s.filename}.${warningText}`.trim();
        els.pdfToWordDownload.href = s.download_url;
        els.pdfToWordDownload.textContent = 'Descargar';
        els.pdfToWordDownload.classList.remove('is-hidden');
        log(`Conversión finalizada: ${s.filename}`);
        break;
      } else if (s.state === 'error') {
        throw new Error(s.error || 'conversion_failed');
      } else if (s.state === 'cancelled') {
        els.pdfToWordInfo.textContent = 'Conversión cancelada.';
        log('Conversión cancelada por el usuario.');
        break;
      } else {
        // progress
        els.pdfToWordInfo.textContent = s.message || 'Procesando...';

        if (!document.getElementById('pdfCancelBtn')) {
          const cancelBtn = document.createElement('a');
          cancelBtn.id = 'pdfCancelBtn';
          cancelBtn.href = '#';
          cancelBtn.textContent = ' [Cancelar]';
          cancelBtn.className = 'pdf-cancel-link';
          cancelBtn.onclick = async (e) => {
            e.preventDefault();
            cancelBtn.textContent = ' [Cancelando...]';
            await fetch(`/api/tools/pdf-to-docx/cancel/${jobId}`, { method: 'POST' });
          };
          els.pdfToWordInfo.appendChild(cancelBtn);
        }
      }

      await new Promise(r => setTimeout(r, 2000));
      attempts++;
    }
  } catch (err) {
    const msg = String(err && err.message || 'conversion_failed');
    els.pdfToWordInfo.textContent = `Error: ${msg}`;
    els.pdfToWordDownload.classList.add('is-hidden');
    log(`No pude convertir el PDF: ${msg}`);
  }
}

async function navigate(path, body = {}) {
  const releaseBusy = beginBusyLease();
  try {
    invalidatePendingRead();
    const data = await api(path, body);
    renderStatus(data);
    log('Ubicación actualizada.');
  } catch (err) {
    log(`No pude navegar: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

async function readCurrent() {
  const releaseBusy = beginBusyLease();
  const controller = new AbortController();
  try {
    invalidatePendingRead();
    const sequence = audioLifecycleSequence;
    const request = activeReadRequest;
    activeReadController = controller;
    log('Solicitud aceptada: comprobando cache y generando el bloque si hace falta...');
    const data = await api('/api/read', { play: false }, { signal: controller.signal });
    if (!playAudio(data, sequence, request)) return;
    log(`${data.cached ? 'Audio listo desde cache.' : 'Audio neural generado.'} Listo en ${data.ready_ms} ms; síntesis ${data.synthesis_ms || 0} ms.`);
  } catch (err) {
    if (err && err.name === 'AbortError') {
      log('Lectura cancelada por cambio de documento o bloque.');
      return;
    }
    const friendly = err && err.data && (err.data.error || err.data.detail) ? friendlyTtsMessage(err.data.error || err.data.detail) : friendlyTtsMessage(err.message);
    log(`Falló la voz: ${friendly}`);
  } finally {
    if (activeReadController === controller) {
      activeReadController = null;
    }
    releaseBusy();
  }
}

async function pollPrepare() {
  while (true) {
    await wait(1000);
    const data = await api('/api/prepare/status');
    renderPrepareStatus(data);
    if (!['running', 'canceling'].includes(data.status)) {
      return data;
    }
  }
}

async function prepareDocument() {
  const releaseBusy = beginBusyLease();
  let started = false;
  try {
    const data = await api('/api/prepare/start', { start: 'cursor' });
    renderPrepareStatus(data);
    log('Preparando audio del documento en segundo plano...');
    started = true;
  } catch (err) {
    log(`No pude preparar el documento: ${err.message}`);
  } finally {
    releaseBusy();
  }
  if (started) {
    await pollPrepare();
  }
}

async function cancelPrepare() {
  try {
    const data = await api('/api/prepare/cancel', {});
    renderPrepareStatus(data);
    log('Cancelando preparación de audio...');
  } catch (err) {
    log(`No pude cancelar: ${err.message}`);
  }
}

async function pollAudioExport(jobId) {
  while (jobId && audioExportPollingJobId === jobId) {
    await wait(1000);
    const data = await api(`/api/audio-export/status/${jobId}`);
    renderAudioExportStatus(data);
    if (!['running', 'queued', 'canceling'].includes(String(data.state || 'idle'))) {
      return data;
    }
  }
  return null;
}

async function startAudioExport() {
  const mode = String(els.audioExportMode.value || 'current');
  const payload = { mode };
  if (mode === 'block') {
    payload.block = Number(els.audioExportBlockInput.value || 0);
  } else if (mode === 'range') {
    payload.start = Number(els.audioExportStartInput.value || 0);
    payload.end = Number(els.audioExportEndInput.value || 0);
  }
  const releaseBusy = beginBusyLease();
  try {
    const data = await api('/api/audio-export', payload);
    renderAudioExportStatus(data);
    log(data.detail || 'Exportación de audio iniciada.');
  } catch (err) {
    log(`No pude exportar audio: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

async function cancelAudioExport() {
  const jobId = audioExportPollingJobId || String(status && status.audio_export && status.audio_export.job_id || '');
  if (!jobId) {
    log('No hay exportación de audio en curso.');
    return;
  }
  try {
    const data = await api(`/api/audio-export/cancel/${jobId}`, {});
    renderAudioExportStatus(data);
    log('Cancelando exportación de audio...');
  } catch (err) {
    log(`No pude cancelar la exportación: ${err.message}`);
  }
}

async function readNextWhenAudioEnds() {
  if (!els.continuousToggle.checked || !status || !status.total || status.current >= status.total) {
    return;
  }
  const releaseBusy = beginBusyLease();
  try {
    log('Avanzando al siguiente bloque...');
    const nextData = await api('/api/next', {});
    renderStatus(nextData);
  } catch (err) {
    log(`No pude avanzar: ${err.message}`);
    return;
  } finally {
    releaseBusy();
  }
  await readCurrent();
}

async function sendChat() {
  const message = els.chatInput.value.trim();
  if (!message) {
    return;
  }
  els.chatInput.value = '';
  addChatMessage('user', message);
  const releaseBusy = beginBusyLease();
  if (dialogue.active) {
    try {
      await sendTypedDialogue(message);
    } finally {
      releaseBusy();
    }
    return;
  }
  try {
    addChatMessage('system', pendingThoughtLabel());
    const data = await api('/api/chat', { message, chunk_index: visibleChunkIndex() });
    const pending = els.chatLog.querySelector('.chat-msg.system:last-child');
    if (pending && /Pensando|Repensando|Respondiendo/.test(pending.textContent)) {
      pending.remove();
    }
    addChatMessage('assistant', data.answer || '(sin respuesta)');
    if (data.note) {
      await refreshNotes();
    }
    await refresh().catch(() => {});
    log(`Chat listo con ${data.model || 'modelo local'} en ${data.duration_ms || 0} ms. ${currentReasoningLabel()} (${data.reasoning_passes || 1} pasada${Number(data.reasoning_passes || 1) === 1 ? '' : 's'}).`);
  } catch (err) {
    if (renderGracefulResearchFailure(err.data || null)) {
      await refresh().catch(() => {});
      return;
    }
    addChatMessage('system', `Falló el chat: ${err.message}`);
    log(`Falló el chat: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

function stopDialoguePlaybackForTypedTurn() {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  els.dialoguePlayer.pause();
  els.dialoguePlayer.currentTime = 0;
  dialogue.speaking = false;
  dialogue.bargeInSpeechMs = 0;
  dialogue.speechMs = 0;
  dialogue.silenceMs = 0;
  dialogue.suppressUntil = performance.now() + 180;
}

async function playDialogueAnswer(data) {
  if (data.audio_url) {
    dialogue.speaking = true;
    els.dialoguePlayer.src = data.audio_url;
    try {
      await els.dialoguePlayer.play();
    } catch (_) {
      dialogue.speaking = false;
      log('Voz generada. Tocá play si el navegador bloqueó la reproducción automática.');
    }
  } else if (data.answer && data.provider === 'text_ack') {
    dialogue.speaking = true;
    speakLocal(data.answer, () => {
      dialogue.speaking = false;
      if (dialogue.active) {
      setDialogueInfo(`Escuchando... ${currentReasoningLabel()}. ${laboratoryModeSummary()}`);
    }
  });
  }
}

async function sendTypedDialogue(message) {
  if (dialogue.speaking) {
    stopDialoguePlaybackForTypedTurn();
  }
  dialogue.processing = true;
  dialogue.recording = false;
  dialogue.finalizing = false;
  dialogue.pcmChunks = [];
  dialogue.pcmPreRoll = [];
  dialogue.pcmPreRollSamples = 0;
  const pending = addChatMessage('system', currentReasoningMode() === 'supreme' ? 'Dialogando por voz; Supremo se baja a Pensamiento para no romper latencia...' : 'Dialogando por voz...');
  const startedAt = performance.now();
  try {
    const data = await api('/api/dialogue/turn', { text: message, chunk_index: visibleChunkIndex() });
    if (pending && pending.isConnected) {
      pending.remove();
    }
    if (data.model === 'reader_control') {
      addChatMessage('system', 'Respuesta detenida.');
    } else {
      addChatMessage('assistant', data.answer || '(sin respuesta)');
    }
    if (data.note) {
      await refreshNotes();
    }
    await refresh().catch(() => {});
    const wallMs = Math.round(performance.now() - startedAt);
    const info = `${dialogueModeSummary(data)} | chat ${fmtMs(data.chat_ms)} | voz ${fmtMs(data.tts_ms)} | total ${fmtMs(data.duration_ms || wallMs)} | ${Number(data.reasoning_passes || 1)} pasada${Number(data.reasoning_passes || 1) === 1 ? '' : 's'}`;
    setDialogueInfo(`${laboratoryModeSummary()} ${info}`);
    log(`Diálogo escrito listo con ${data.model || 'modelo local'} en ${wallMs} ms. ${dialogueModeSummary(data)}`);
    await playDialogueAnswer(data);
  } catch (err) {
    if (pending && pending.isConnected) {
      pending.remove();
    }
    if (renderGracefulResearchFailure(err.data || null)) {
      await refresh().catch(() => {});
      return;
    }
    addChatMessage('system', `Falló el diálogo: ${err.message}`);
    setDialogueInfo(`Falló el diálogo: ${err.message}`);
    log(`Falló el diálogo: ${err.message}`);
  } finally {
    dialogue.processing = false;
    if (!dialogue.speaking && dialogue.active) {
      setDialogueInfo(`Escuchando... ${dialogueModeSummary(status && status.dialogue_reasoning ? { reasoning_mode_requested: status.reasoning && status.reasoning.mode, reasoning_mode_applied: status.dialogue_reasoning.applied_mode, reasoning_degraded: status.dialogue_reasoning.degraded } : {})} ${laboratoryModeSummary()}`);
    }
  }
}

async function clearLaboratoryHistory() {
  const releaseBusy = beginBusyLease();
  try {
    const data = await api('/api/laboratory/reset', {});
    els.chatLog.innerHTML = '';
    addChatMessage('system', 'Historial de laboratorio borrado.');
    log(`Historial de laboratorio borrado (${data.chat_items || 0} chat, ${data.dialogue_items || 0} diálogo).`);
  } catch (err) {
    addChatMessage('system', `No pude borrar el historial: ${err.message}`);
    log(`No pude borrar el historial: ${err.message}`);
  } finally {
    releaseBusy();
  }
}

function dialogueMimeType() {
  if (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
    return 'audio/webm;codecs=opus';
  }
  if (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm')) {
    return 'audio/webm';
  }
  return '';
}

async function toggleDialogue() {
  if (dialogue.active) {
    stopDialogue();
    return;
  }
  await startDialogue();
}

async function microphonePermissionState() {
  if (!navigator.permissions || !navigator.permissions.query) {
    return '';
  }
  try {
    const permission = await navigator.permissions.query({ name: 'microphone' });
    return permission && permission.state ? permission.state : '';
  } catch (_) {
    return '';
  }
}

async function startDialogue() {
  if (!navigator.mediaDevices || !(window.AudioContext || window.webkitAudioContext)) {
    setDialogueInfo('Tu navegador no permite grabar audio desde esta página.');
    return;
  }
  try {
    const permissionState = await microphonePermissionState();
    if (permissionState === 'denied') {
      setDialogueInfo('El micrófono está bloqueado en el navegador. Permitilo para usar Dialogar.');
      return;
    }
    if (permissionState === 'prompt') {
      setDialogueInfo('Permiso de micrófono pendiente. Aprobalo en el navegador para empezar a escuchar.');
    }
    api('/api/prepare/cancel', {}).catch(() => {});
    dialogue.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
        sampleRate: 48000,
        sampleSize: 16
      }
    });
    const audioTrack = dialogue.stream.getAudioTracks()[0];
    dialogue.micDeviceLabel = audioTrack && audioTrack.label ? audioTrack.label : 'Micrófono activo';
    dialogue.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    dialogue.sampleRate = dialogue.audioContext.sampleRate || 48000;
    const source = dialogue.audioContext.createMediaStreamSource(dialogue.stream);
    dialogue.analyser = dialogue.audioContext.createAnalyser();
    dialogue.analyser.fftSize = 1024;
    source.connect(dialogue.analyser);
    const processor = dialogue.audioContext.createScriptProcessor(4096, 1, 1);
    const silentGain = dialogue.audioContext.createGain();
    silentGain.gain.value = 0;
    source.connect(processor);
    processor.connect(silentGain);
    silentGain.connect(dialogue.audioContext.destination);
    processor.onaudioprocess = handleDialoguePcm;
    dialogue.processor = processor;
    dialogue.silentGain = silentGain;
    dialogue.active = true;
    dialogue.speechMs = 0;
    dialogue.silenceMs = 0;
    dialogue.pcmChunks = [];
    dialogue.pcmPreRoll = [];
    dialogue.pcmPreRollSamples = 0;
    dialogue.noiseFloor = 0.012;
    dialogue.lastTick = performance.now();
    els.dialogueBtn.textContent = 'Detener diálogo';
    setDialogueInfo(`Escuchando... ${currentReasoningLabel()}. ${laboratoryModeSummary()} Hacé una pausa corta y respondo. Mic: ${dialogue.micDeviceLabel}`);
    monitorDialogue();
  } catch (err) {
    const permissionState = await microphonePermissionState();
    if (permissionState === 'denied' || (err && err.name === 'NotAllowedError')) {
      setDialogueInfo('El micrófono está bloqueado o fue rechazado. Permitilo en el navegador y volvé a intentar.');
      return;
    }
    setDialogueInfo(`No pude abrir el micrófono: ${err.message}`);
  }
}

function stopDialogue() {
  dialogue.active = false;
  dialogue.processing = false;
  dialogue.speaking = false;
  dialogue.bargeInSpeechMs = 0;
  dialogue.suppressUntil = 0;
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  if (dialogue.monitorId) {
    cancelAnimationFrame(dialogue.monitorId);
  }
  if (dialogue.finalizeTimeoutId) {
    window.clearTimeout(dialogue.finalizeTimeoutId);
  }
  dialogue.finalizeTimeoutId = 0;
  if (dialogue.stream) {
    dialogue.stream.getTracks().forEach(track => track.stop());
  }
  if (dialogue.processor) {
    try {
      dialogue.processor.disconnect();
    } catch (_) {}
  }
  if (dialogue.silentGain) {
    try {
      dialogue.silentGain.disconnect();
    } catch (_) {}
  }
  if (dialogue.audioContext) {
    dialogue.audioContext.close().catch(() => {});
  }
  els.dialoguePlayer.pause();
  els.dialoguePlayer.removeAttribute('src');
  dialogue.stream = null;
  dialogue.audioContext = null;
  dialogue.analyser = null;
  dialogue.processor = null;
  dialogue.silentGain = null;
  dialogue.pcmChunks = [];
  dialogue.pcmPreRoll = [];
  dialogue.pcmPreRollSamples = 0;
  dialogue.captureStopReason = '';
  dialogue.micDeviceLabel = '';
  dialogue.finalizing = false;
  els.dialogueBtn.textContent = 'Dialogar';
  setDialogueInfo(`Diálogo apagado. ${laboratoryModeSummary()}`);
}

function monitorDialogue() {
  if (!dialogue.active || !dialogue.analyser) {
    return;
  }
  const now = performance.now();
  const delta = Math.max(16, Math.min(250, now - (dialogue.lastTick || now)));
  dialogue.lastTick = now;
  const level = micLevel();
  const threshold = Math.max(dialogue.minThreshold, dialogue.noiseFloor * dialogue.thresholdMultiplier);
  const releaseThreshold = threshold * 0.72;
  const isSpeech = dialogue.recording ? level >= releaseThreshold : level >= threshold;
  if (now < (dialogue.suppressUntil || 0)) {
    dialogue.speechMs = 0;
    dialogue.silenceMs += delta;
    dialogue.monitorId = requestAnimationFrame(monitorDialogue);
    return;
  }
  if (isSpeech) {
    dialogue.speechMs += delta;
    dialogue.silenceMs = 0;
  } else {
    dialogue.silenceMs += delta;
    dialogue.speechMs = Math.max(0, dialogue.speechMs - delta * 0.5);
    if (!dialogue.recording && !dialogue.processing && !dialogue.speaking) {
      dialogue.noiseFloor = dialogue.noiseFloor * 0.96 + level * 0.04;
    }
  }
  if (dialogue.speaking && isSpeech && !dialogue.recording) {
    dialogue.bargeInSpeechMs += delta;
    if (dialogue.bargeInSpeechMs >= dialogue.bargeInMs) {
      stopAssistantSpeechForBargeIn();
    }
  } else if (!isSpeech) {
    dialogue.bargeInSpeechMs = 0;
  }
  if (dialogue.speaking && !dialogue.recording) {
    // Mientras habla Fusion, no grabamos su propia voz como si fuera el usuario.
  } else if (!dialogue.speaking && !dialogue.processing && !dialogue.recording && !dialogue.finalizing && dialogue.speechMs >= dialogue.speechStartMs) {
    beginDialogueRecording();
  }
  if (dialogue.recording) {
    const elapsed = now - dialogue.startedAt;
    if ((elapsed >= dialogue.minRecordMs && dialogue.silenceMs >= dialogue.silenceStopMs) || elapsed >= dialogue.maxRecordMs) {
      stopDialogueRecording();
    }
  }
  dialogue.monitorId = requestAnimationFrame(monitorDialogue);
}

function stopAssistantSpeechForBargeIn() {
  const interruptedWhileSpeech = dialogue.bargeInSpeechMs > 0;
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  els.dialoguePlayer.pause();
  els.dialoguePlayer.currentTime = 0;
  dialogue.speaking = false;
  dialogue.bargeInSpeechMs = 0;
  if (interruptedWhileSpeech) {
    // Conservamos el arranque de la frase que disparo el barge-in para que
    // comandos cortos como "toma nota..." no pierdan sus primeras silabas.
    dialogue.speechMs = Math.max(dialogue.speechMs, dialogue.speechStartMs);
  } else {
    dialogue.speechMs = 0;
  }
  dialogue.silenceMs = 0;
  dialogue.pcmChunks = [];
  dialogue.finalizing = false;
  dialogue.suppressUntil = performance.now() + 40;
  addChatMessage('system', 'Interrumpiste la respuesta.');
  setDialogueInfo('Te escucho...');
}

function micLevel() {
  const data = new Uint8Array(dialogue.analyser.fftSize);
  dialogue.analyser.getByteTimeDomainData(data);
  let sum = 0;
  for (const value of data) {
    const centered = (value - 128) / 128;
    sum += centered * centered;
  }
  return Math.sqrt(sum / data.length);
}

function dialoguePreRollLimitSamples() {
  return Math.max(0, Math.round((dialogue.sampleRate || 48000) * (dialogue.preRollMs / 1000)));
}

function appendPcmChunk(target, chunk) {
  if (!chunk || !chunk.length) {
    return 0;
  }
  target.push(chunk);
  return chunk.length;
}

function dialoguePcmStats(chunks) {
  let samples = 0;
  let sumSquares = 0;
  let peak = 0;
  for (const chunk of chunks || []) {
    if (!chunk) {
      continue;
    }
    for (let i = 0; i < chunk.length; i += 1) {
      const sample = Number(chunk[i] || 0);
      const abs = Math.abs(sample);
      peak = Math.max(peak, abs);
      sumSquares += sample * sample;
      samples += 1;
    }
  }
  const rms = samples ? Math.sqrt(sumSquares / samples) : 0;
  return {
    samples,
    rms,
    peak,
    durationMs: samples && dialogue.sampleRate ? Math.round(samples * 1000 / dialogue.sampleRate) : 0,
    voiceDetected: peak >= Math.max(dialogue.minThreshold, dialogue.noiseFloor * dialogue.thresholdMultiplier)
  };
}

function trimPcmPreRoll() {
  const limit = dialoguePreRollLimitSamples();
  while (dialogue.pcmPreRoll.length > 1 && dialogue.pcmPreRollSamples > limit) {
    const removed = dialogue.pcmPreRoll.shift();
    dialogue.pcmPreRollSamples = Math.max(0, dialogue.pcmPreRollSamples - (removed ? removed.length : 0));
  }
}

function handleDialoguePcm(event) {
  if (!dialogue.active || !event || !event.inputBuffer) {
    return;
  }
  const source = event.inputBuffer.getChannelData(0);
  if (!source || !source.length) {
    return;
  }
  const chunk = new Float32Array(source.length);
  chunk.set(source);
  if (!dialogue.recording && !dialogue.finalizing) {
    dialogue.pcmPreRollSamples += appendPcmChunk(dialogue.pcmPreRoll, chunk);
    trimPcmPreRoll();
    return;
  }
  if (dialogue.recording || (dialogue.finalizing && performance.now() <= (dialogue.captureStopAt || 0))) {
    appendPcmChunk(dialogue.pcmChunks, chunk);
  }
}

function encodeDialogueWav(chunks, sampleRate) {
  const safeRate = Math.max(8000, Number(sampleRate || 48000));
  const totalSamples = chunks.reduce((sum, chunk) => sum + (chunk ? chunk.length : 0), 0);
  const buffer = new ArrayBuffer(44 + totalSamples * 2);
  const view = new DataView(buffer);
  let offset = 0;
  const writeString = value => {
    for (let i = 0; i < value.length; i += 1) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
    offset += value.length;
  };
  const writeUint32 = value => {
    view.setUint32(offset, value, true);
    offset += 4;
  };
  const writeUint16 = value => {
    view.setUint16(offset, value, true);
    offset += 2;
  };
  writeString('RIFF');
  writeUint32(36 + totalSamples * 2);
  writeString('WAVE');
  writeString('fmt ');
  writeUint32(16);
  writeUint16(1);
  writeUint16(1);
  writeUint32(safeRate);
  writeUint32(safeRate * 2);
  writeUint16(2);
  writeUint16(16);
  writeString('data');
  writeUint32(totalSamples * 2);
  for (const chunk of chunks) {
    if (!chunk) {
      continue;
    }
    for (let i = 0; i < chunk.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, chunk[i] || 0));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

function finishDialogueRecording() {
  if (!dialogue.finalizing) {
    return;
  }
  dialogue.finalizing = false;
  dialogue.captureStopAt = 0;
  dialogue.finalizeTimeoutId = 0;
  const stats = dialoguePcmStats(dialogue.pcmChunks);
  const blob = encodeDialogueWav(dialogue.pcmChunks, dialogue.sampleRate);
  if (dialogue.trace) {
    dialogue.trace.audioSamples = stats.samples;
    dialogue.trace.captureDurationMs = stats.durationMs;
    dialogue.trace.micRms = stats.rms;
    dialogue.trace.micPeak = stats.peak;
    dialogue.trace.voiceDetected = stats.voiceDetected;
    dialogue.trace.audioSizeBytes = blob.size;
    dialogue.trace.audioMime = blob.type || 'audio/wav';
  }
  dialogue.pcmChunks = [];
  sendDialogueAudio(blob, 'audio/wav');
}

function beginDialogueRecording() {
  if (!dialogue.active || dialogue.recording || dialogue.processing || dialogue.finalizing || !dialogue.stream) {
    return;
  }
  const now = performance.now();
  dialogue.turnId += 1;
  dialogue.pcmChunks = dialogue.pcmPreRoll.slice();
  dialogue.recording = true;
  dialogue.finalizing = false;
  dialogue.chunkIndex = visibleChunkIndex();
  dialogue.startedAt = now;
  dialogue.turnStartedAt = now;
  dialogue.trace = {
    turnId: dialogue.turnId,
    speechStartAt: now,
    chunkIndex: dialogue.chunkIndex,
    chunkNumber: dialogue.chunkIndex === null || dialogue.chunkIndex === undefined ? null : dialogue.chunkIndex + 1,
    silenceStopMs: dialogue.silenceStopMs,
    finalFlushMs: dialogue.finalFlushMs,
    flushWaitMs: dialogueFlushWaitMs(),
    micDeviceLabel: dialogue.micDeviceLabel
  };
  dialogue.silenceMs = 0;
  setDialogueInfo('Te escucho...');
}

function stopDialogueRecording() {
  if (!dialogue.recording || dialogue.finalizing) {
    return;
  }
  dialogue.recording = false;
  dialogue.finalizing = true;
  dialogue.captureStopAt = performance.now() + dialogueFlushWaitMs();
  const heardMs = Math.max(0, performance.now() - (dialogue.turnStartedAt || performance.now()));
  const elapsed = Math.max(0, performance.now() - (dialogue.startedAt || performance.now()));
  dialogue.captureStopReason = elapsed >= dialogue.maxRecordMs ? 'timeout' : 'silence';
  if (dialogue.trace) {
    dialogue.trace.speechStopAt = performance.now();
    dialogue.trace.recordedMs = heardMs;
    dialogue.trace.captureStopReason = dialogue.captureStopReason;
  }
  setDialogueInfo(`Procesando (${Math.round(heardMs)} ms de audio)...`);
  if (dialogue.finalizeTimeoutId) {
    window.clearTimeout(dialogue.finalizeTimeoutId);
  }
  dialogue.finalizeTimeoutId = window.setTimeout(finishDialogueRecording, dialogueFlushWaitMs());
}

async function sendDialogueAudio(blob, mimeType) {
  if (!dialogue.active || dialogue.processing) {
    return;
  }
  dialogue.finalizing = false;
  dialogue.speechMs = 0;
  dialogue.silenceMs = 0;
  if (blob.size < 1200) {
    setDialogueInfo(`Escuchando... ${currentReasoningLabel()}. ${laboratoryModeSummary()}`);
    return;
  }
  dialogue.processing = true;
  const requestStartedAt = performance.now();
  const audioMime = mimeType || blob.type || 'audio/webm';
  const turnTrace = dialogue.trace ? { ...dialogue.trace, sendStartedAt: requestStartedAt, blobSize: blob.size, audioMime } : { sendStartedAt: requestStartedAt, blobSize: blob.size, audioMime };
  try {
    const params = new URLSearchParams({ filename: 'dialogue.wav' });
    params.set('audio_size_bytes', String(blob.size || 0));
    params.set('capture_ms', String(Math.round(turnTrace.captureDurationMs || turnTrace.recordedMs || 0)));
    params.set('mic_rms', String(Number(turnTrace.micRms || 0).toFixed(6)));
    params.set('mic_peak', String(Number(turnTrace.micPeak || 0).toFixed(6)));
    params.set('voice_detected', turnTrace.voiceDetected ? '1' : '0');
    params.set('cut_reason', String(turnTrace.captureStopReason || 'unknown'));
    params.set('mime', String(audioMime));
    if (dialogue.chunkIndex !== null && dialogue.chunkIndex !== undefined) {
      params.set('chunk_index', String(dialogue.chunkIndex));
    }
    const res = await fetch(`/api/dialogue/turn?${params.toString()}`, {
      method: 'POST',
      headers: { 'Content-Type': audioMime },
      body: blob
    });
    const data = await res.json();
    turnTrace.responseAt = performance.now();
    if (!res.ok || data.ok === false) {
      const wallMs = Math.round(performance.now() - requestStartedAt);
      const traceText = formatDialogueTrace(data, turnTrace, wallMs);
      if (renderGracefulResearchFailure(data, traceText)) {
        await refresh().catch(() => {});
        return;
      }
      const provider = data.stt_provider ? ` (${data.stt_provider})` : '';
      const detail = data.detail ? `: ${data.detail}` : '';
      throw new Error(`${data.error || 'dialogue_failed'}${provider}${detail}`);
    }
    const wallMs = Math.round(performance.now() - requestStartedAt);
    const traceText = formatDialogueTrace(data, turnTrace, wallMs);
    if (data.ignored || data.detail === 'hallucinated_transcript') {
      addChatMessage('system', 'Ignoré una transcripción espuria de Whisper.');
      addChatMessage('system', traceText);
      setDialogueInfo(`${laboratoryModeSummary()} ${dialogueModeSummary(data)} | ${traceText}`);
      return;
    }
    addChatMessage('user', data.transcript || '(audio)');
    if (data.model === 'reader_control') {
      addChatMessage('system', 'Respuesta detenida.');
    } else {
      addChatMessage('assistant', data.answer || '(sin respuesta)');
    }
    if (data.note) {
      await refreshNotes();
    }
    await refresh().catch(() => {});
    addChatMessage('system', traceText);
    if (data.detail === 'empty_transcript' || data.detail === 'empty_audio') {
      log(`Diálogo sin transcripción útil (${data.detail}). ${traceText}`);
    } else {
      log(`Diálogo por audio listo. ${dialogueModeSummary(data)} ${traceText}`);
    }
    setDialogueInfo(`${laboratoryModeSummary()} ${dialogueModeSummary(data)} | ${traceText}`);
    await playDialogueAnswer(data);
  } catch (err) {
    addChatMessage('system', `Falló el diálogo: ${err.message}`);
    setDialogueInfo(`Falló el diálogo: ${err.message}`);
    log(`Falló el diálogo: ${err.message}`);
  } finally {
    dialogue.processing = false;
    dialogue.chunkIndex = null;
    if (!dialogue.speaking && dialogue.active) {
      setDialogueInfo(`Escuchando... ${dialogueModeSummary(status && status.dialogue_reasoning ? { reasoning_mode_requested: status.reasoning && status.reasoning.mode, reasoning_mode_applied: status.dialogue_reasoning.applied_mode, reasoning_degraded: status.dialogue_reasoning.degraded } : {})} ${laboratoryModeSummary()}`);
    }
  }
}

els.prevBtn.addEventListener('click', () => navigate('/api/previous'));
els.nextBtn.addEventListener('click', () => navigate('/api/next'));
els.repeatBtn.addEventListener('click', readCurrent);
els.readBtn.addEventListener('click', readCurrent);
els.jumpBtn.addEventListener('click', () => navigate('/api/jump', { index: Number(els.jumpInput.value || 1) }));
els.prepareBtn.addEventListener('click', prepareDocument);
els.cancelPrepareBtn.addEventListener('click', cancelPrepare);
els.audioExportMode.addEventListener('change', syncAudioExportInputs);
els.audioExportBtn.addEventListener('click', startAudioExport);
els.audioExportCancelBtn.addEventListener('click', cancelAudioExport);
els.clearDocBtn.addEventListener('click', clearDocument);
els.saveNoteBtn.addEventListener('click', saveCurrentNote);
els.noteInput.addEventListener('input', () => busyControls.setNoteText(els.noteInput.value));
els.sendChatBtn.addEventListener('click', sendChat);
els.clearLabHistoryBtn.addEventListener('click', clearLaboratoryHistory);
els.reasoningNormalBtn.addEventListener('click', () => setReasoningMode('normal'));
els.reasoningThinkingBtn.addEventListener('click', () => setReasoningMode('thinking'));
els.reasoningSupremeBtn.addEventListener('click', () => setReasoningMode('supreme'));
els.reasoningPensamientoCriticoBtn.addEventListener('click', () => setReasoningMode('pensamiento_critico'));
els.profileSelect.addEventListener('change', event => setProfileMode(event.target.value));
els.veilSelect.addEventListener('change', event => setVeilMode(event.target.value));
els.voiceSelect.addEventListener('change', changeVoice);
els.freeModeBtn.addEventListener('click', () => setLaboratoryMode(currentLaboratoryMode() === 'free' ? 'document' : 'free'));
els.dialogueBtn.addEventListener('click', toggleDialogue);
els.chatInput.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendChat();
  }
});
els.player.addEventListener('ended', readNextWhenAudioEnds);
syncAudioExportInputs();
els.dialoguePlayer.addEventListener('ended', () => {
  dialogue.speaking = false;
  if (dialogue.active) {
    setDialogueInfo(`Escuchando... ${currentReasoningLabel()}.`);
  }
});
els.chooseFileBtn.addEventListener('click', event => {
  event.preventDefault();
  event.stopPropagation();
  els.fileInput.click();
});
els.pdfToWordTool.addEventListener('click', event => {
  event.preventDefault();
  event.stopPropagation();
  els.pdfToWordInput.click();
});
els.pdfToWordTool.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    els.pdfToWordInput.click();
  }
});
els.dropzone.addEventListener('click', () => els.fileInput.click());
els.dropzone.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    els.fileInput.click();
  }
});
els.fileInput.addEventListener('change', () => {
  loadFile(els.fileInput.files && els.fileInput.files[0]);
  els.fileInput.value = '';
});
els.pdfToWordInput.addEventListener('change', () => {
  convertPdfToWord(els.pdfToWordInput.files && els.pdfToWordInput.files[0]);
  els.pdfToWordInput.value = '';
});
['dragenter', 'dragover'].forEach(name => {
  els.dropzone.addEventListener(name, event => {
    event.preventDefault();
    els.dropzone.classList.add('dragover');
  });
});
['dragleave', 'drop'].forEach(name => {
  els.dropzone.addEventListener(name, event => {
    event.preventDefault();
    els.dropzone.classList.remove('dragover');
  });
});
els.dropzone.addEventListener('drop', event => {
  const files = event.dataTransfer && event.dataTransfer.files;
  loadFile(files && files[0]);
});
['dragenter', 'dragover'].forEach(name => {
  els.pdfToWordTool.addEventListener(name, event => {
    event.preventDefault();
    event.stopPropagation();
    els.pdfToWordTool.classList.add('dragover');
  });
});
['dragleave', 'drop'].forEach(name => {
  els.pdfToWordTool.addEventListener(name, event => {
    event.preventDefault();
    event.stopPropagation();
    els.pdfToWordTool.classList.remove('dragover');
  });
});
els.pdfToWordTool.addEventListener('drop', event => {
  const files = event.dataTransfer && event.dataTransfer.files;
  convertPdfToWord(files && files[0]);
});

refresh().then(() => refreshVoices()).catch(err => log(`Arranque incompleto: ${err.message}`));
