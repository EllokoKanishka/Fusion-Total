export function friendlyTtsMessage(detail) {
  const clean = String(detail || '').trim();
  if (!clean) return 'El servicio de voz no está disponible. Iniciá TTS o seleccioná otro motor.';
  if (clean.startsWith('tts_owner_')) return 'El TTS de Fusion está vivo pero no quedó validado como propio. Reiniciá el TTS de Fusion o seleccioná otro motor.';
  if (clean.startsWith('tts_foreign_doctora_lucy_port')) return 'Fusion detectó una voz de otro proyecto y no la va a usar como si fuera propia.';
  if (clean.startsWith('tts_historic_unassigned_port')) return 'El puerto histórico 7852 no es válido para la voz de Fusion.';
  if (clean.includes('timed out') || clean.includes('timeout')) return 'La voz tardó demasiado en responder. Probemos otra vez en unos segundos.';
  if (clean.startsWith('http_') || clean.includes('Connection refused') || clean.includes('refused')) return 'El servicio de voz no respondió desde Fusion. Iniciá TTS o seleccioná otro motor.';
  return clean;
}

export function appendPcmChunk(target, chunk) {
  if (!chunk || !chunk.length) return 0;
  target.push(chunk);
  return chunk.length;
}

export function dialoguePcmStats(chunks, settings = {}) {
  let samples = 0;
  let sumSquares = 0;
  let peak = 0;
  for (const chunk of chunks || []) {
    if (!chunk) continue;
    for (let index = 0; index < chunk.length; index += 1) {
      const sample = Number(chunk[index] || 0);
      peak = Math.max(peak, Math.abs(sample));
      sumSquares += sample * sample;
      samples += 1;
    }
  }
  const sampleRate = Number(settings.sampleRate || 0);
  const rms = samples ? Math.sqrt(sumSquares / samples) : 0;
  return {
    samples,
    rms,
    peak,
    durationMs: samples && sampleRate ? Math.round(samples * 1000 / sampleRate) : 0,
    voiceDetected: peak >= Math.max(
      Number(settings.minThreshold || 0),
      Number(settings.noiseFloor || 0) * Number(settings.thresholdMultiplier || 0)
    )
  };
}

export function encodeDialogueWav(chunks, sampleRate, BlobType = Blob) {
  const safeRate = Math.max(8000, Number(sampleRate || 48000));
  const totalSamples = chunks.reduce((sum, chunk) => sum + (chunk ? chunk.length : 0), 0);
  const buffer = new ArrayBuffer(44 + totalSamples * 2);
  const view = new DataView(buffer);
  let offset = 0;
  const writeString = value => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
    offset += value.length;
  };
  const writeUint32 = value => { view.setUint32(offset, value, true); offset += 4; };
  const writeUint16 = value => { view.setUint16(offset, value, true); offset += 2; };
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
    if (!chunk) continue;
    for (let index = 0; index < chunk.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, chunk[index] || 0));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return new BlobType([buffer], { type: 'audio/wav' });
}
