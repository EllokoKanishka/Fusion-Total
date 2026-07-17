export function createMediaController({ elements, log, refreshMainStatus }) {
  const endpoints = {
    transcribe: '/api/media/transcribe',
    translate: '/api/media/translate'
  };
  let pollingJobId = '';
  let timer = null;

  function setLink(element, item, fallbackLabel) {
    if (!element) return;
    if (item && item.download_url) {
      element.href = item.download_url;
      element.textContent = `Descargar ${item.filename || fallbackLabel}`;
      element.classList.remove('is-hidden');
    } else {
      element.href = '#';
      element.classList.add('is-hidden');
    }
  }

  function setBusy(busy) {
    [elements.mediaTranscribeBtn, elements.mediaTranslateBtn].forEach(button => {
      if (button) button.disabled = Boolean(busy);
    });
    if (elements.mediaCancelBtn) elements.mediaCancelBtn.disabled = !busy;
  }

  function render(data) {
    if (!data || !elements.mediaInfo) return;
    const state = String(data.state || 'idle');
    const operation = data.operation === 'translate' ? 'Traducción' : 'Transcripción';
    elements.mediaInfo.textContent = state === 'idle'
      ? (data.detail || 'Sin procesamiento multimedia activo.')
      : `${operation}: ${data.detail || data.stage || state}`;
    if (elements.mediaProgress) elements.mediaProgress.value = Number(data.progress || 0);
    const running = ['queued', 'running', 'canceling'].includes(state);
    setBusy(running);
    if (elements.mediaMountBtn) {
      elements.mediaMountBtn.disabled = state !== 'done';
      elements.mediaMountBtn.classList.toggle('is-hidden', state !== 'done');
    }
    const output = data.output && typeof data.output === 'object' ? data.output : {};
    setLink(elements.mediaPdfDownload, output.pdf, 'PDF');
    setLink(elements.mediaTranslatedPdfDownload, output.translated_pdf, 'PDF en castellano');
    setLink(elements.mediaAudioDownload, output.audio, 'audio en castellano');
    if (data.job_id) pollingJobId = String(data.job_id);
    if (running) schedulePoll();
    else clearPoll();
  }

  function clearPoll() {
    if (timer) window.clearTimeout(timer);
    timer = null;
  }

  function schedulePoll() {
    clearPoll();
    timer = window.setTimeout(() => poll().catch(error => {
      if (elements.mediaInfo) elements.mediaInfo.textContent = `No pude actualizar el proceso: ${error.message}`;
    }), 900);
  }

  async function poll() {
    const endpoint = pollingJobId ? `/api/media/status/${encodeURIComponent(pollingJobId)}` : '/api/media/status';
    const response = await fetch(endpoint);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'media_status_failed');
    render(data);
    return data;
  }

  async function start(operation, file) {
    if (!file) return;
    clearPoll();
    setBusy(true);
    setLink(elements.mediaPdfDownload, null, 'PDF');
    setLink(elements.mediaTranslatedPdfDownload, null, 'PDF en castellano');
    setLink(elements.mediaAudioDownload, null, 'audio en castellano');
    if (elements.mediaProgress) elements.mediaProgress.value = 0;
    if (elements.mediaInfo) elements.mediaInfo.textContent = `${file.name}: subiendo...`;
    const body = new FormData();
    body.append('file', file, file.name);
    try {
      const response = await fetch(endpoints[operation], { method: 'POST', body });
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.detail || data.error || 'media_upload_failed');
      pollingJobId = String(data.job_id || '');
      render(data);
      log(`${file.name}: procesamiento multimedia iniciado.`);
    } catch (error) {
      setBusy(false);
      if (elements.mediaInfo) elements.mediaInfo.textContent = `Falló la carga: ${error.message}`;
      throw error;
    }
  }

  async function cancel() {
    if (!pollingJobId) return;
    const response = await fetch(`/api/media/cancel/${encodeURIComponent(pollingJobId)}`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'media_cancel_failed');
    render(data);
  }

  async function mount() {
    if (!pollingJobId) return;
    const response = await fetch(`/api/media/mount/${encodeURIComponent(pollingJobId)}`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'media_mount_failed');
    if (elements.mediaInfo) elements.mediaInfo.textContent = 'Resultado montado como documento activo.';
    log('Transcripción montada como documento activo.');
    await refreshMainStatus();
  }

  function dispose() {
    clearPoll();
  }

  return { start, cancel, mount, poll, render, dispose };
}
