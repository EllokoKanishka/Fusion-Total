export function createDialogueState() {
  return {
    active: false,
    starting: false,
    epoch: 0,
    request: null,
    captureTimerId: 0,
    capturedSamples: 0,
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
}

export function clearDialogueTimers(dialogue, clearTimer = clearTimeout, cancelFrame = cancelAnimationFrame) {
  if (dialogue.finalizeTimeoutId) clearTimer(dialogue.finalizeTimeoutId);
  if (dialogue.captureTimerId) clearTimer(dialogue.captureTimerId);
  if (dialogue.monitorId) cancelFrame(dialogue.monitorId);
  dialogue.finalizeTimeoutId = 0;
  dialogue.captureTimerId = 0;
  dialogue.monitorId = 0;
}
