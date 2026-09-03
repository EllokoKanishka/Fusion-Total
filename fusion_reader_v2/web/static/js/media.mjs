export function createMediaController({ elements, log, refreshMainStatus, pollDelayMs = 900 }) {
  const endpoints = {
    transcribe: '/api/media/transcribe',
    translate: '/api/media/translate'
  };
  let pollingJobId = '';
  let timer = null;
  let uploadController = null;
  let pollFailures = 0;
  const downloadHandlers = new Map();

  function setLink(element, item, fallbackLabel) {
    if (!element) return;
    if (item && item.download_url) {
      element.href = item.download_url;
      element.dataset.downloadUrl = item.download_url;
      element.dataset.downloadFilename = item.filename || fallbackLabel;
      element.textContent = `Descargar ${item.filename || fallbackLabel}`;
      element.classList.remove('is-hidden');
    } else {
      element.href = '#';
      delete element.dataset.downloadUrl;
      delete element.dataset.downloadFilename;
      element.classList.add('is-hidden');
    }
  }

  async function downloadArtifact(element) {
    const url = element && element.dataset.downloadUrl;
    if (!url) return;
    const response = await fetch(url);
    if (!response.ok) {
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        // The server may return an empty or non-JSON error response.
      }
      throw new Error(payload.detail || payload.error || 'media_download_failed');
    }
    const blob = await response.blob();
    const objectUrl = window.URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = element.dataset.downloadFilename || 'fusion-reader-media';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.URL.revokeObjectURL(objectUrl);
  }

  function bindDownload(element) {
    if (!element) return;
    const handler = event => {
      event.preventDefault();
      downloadArtifact(element).catch(error => {
        if (elements.mediaInfo) elements.mediaInfo.textContent = `No pude descargar el archivo: ${error.message}`;
      });
    };
    downloadHandlers.set(element, handler);
    element.addEventListener('click', handler);
  }

  [
    elements.mediaPdfDownload,
    elements.mediaTranslatedPdfDownload,
    elements.mediaAudioDownload
  ].forEach(bindDownload);

  function setBusy(busy, dismissible = false, state = 'idle') {
    if (elements.mediaTranscribeBtn) elements.mediaTranscribeBtn.disabled = Boolean(busy);
    if (elements.mediaTranslateBtn) elements.mediaTranslateBtn.disabled = Boolean(busy);
    [
      elements.mediaOriginalPdfToggle,
      elements.mediaTranslatedPdfToggle,
      elements.mediaSpanishAudioToggle
    ].forEach(input => {
      if (input) input.disabled = Boolean(busy);
    });
    if (elements.mediaCancelBtn) {
      elements.mediaCancelBtn.disabled = !(busy || dismissible);
      elements.mediaCancelBtn.textContent = busy
        ? 'Cancelar'
        : (state === 'done' ? 'Cerrar resultado' : 'Cerrar');
    }
  }

  function render(data) {
    if (!data || !elements.mediaInfo) return;
    const state = String(data.state || 'idle');
    const operation = data.operation === 'translate' ? 'Traducción' : 'Transcripción';
    const diagnostic = ['error', 'partial'].includes(state) && data.error ? ` Código: ${data.error}` : '';
    elements.mediaInfo.textContent = state === 'idle'
      ? (data.detail || 'Sin procesamiento multimedia activo.')
      : `${operation}: ${data.detail || data.stage || state}${diagnostic}`;
    if (elements.mediaProgress) elements.mediaProgress.value = Number(data.progress || 0);
    const running = ['queued', 'running', 'canceling'].includes(state);
    const dismissible = ['done', 'partial', 'error', 'cancelled'].includes(state);
    setBusy(running, dismissible, state);
    if (elements.mediaMountBtn) {
      const mountable = ['done', 'partial'].includes(state) && Number(data.transcript_characters || 0) > 0;
      elements.mediaMountBtn.disabled = !mountable;
      elements.mediaMountBtn.classList.toggle('is-hidden', !mountable);
    }
    const output = data.output && typeof data.output === 'object' ? data.output : {};
    setLink(elements.mediaPdfDownload, output.pdf, 'PDF');
    setLink(elements.mediaTranslatedPdfDownload, output.translated_pdf, 'PDF en castellano');
    setLink(elements.mediaAudioDownload, output.audio, 'audio en castellano');
    if (data.job_id) pollingJobId = String(data.job_id);
    else if (state === 'idle') pollingJobId = '';
    if (running) schedulePoll();
    else clearPoll();
  }

  function clearPoll() {
    if (timer) window.clearTimeout(timer);
    timer = null;
  }

  function schedulePoll() {
    clearPoll();
    const delay = Math.min(8000, pollDelayMs * (2 ** Math.min(pollFailures, 3)));
    timer = window.setTimeout(() => poll().catch(error => {
      pollFailures += 1;
      if (elements.mediaInfo) elements.mediaInfo.textContent = `Reconectando con el proceso: ${error.message}`;
      schedulePoll();
    }), delay);
  }

  async function poll() {
    const endpoint = pollingJobId ? `/api/media/status/${encodeURIComponent(pollingJobId)}` : '/api/media/status';
    const response = await fetch(endpoint);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'media_status_failed');
    pollFailures = 0;
    render(data);
    return data;
  }

  function selectedOutputs() {
    return {
      original_pdf: Boolean(elements.mediaOriginalPdfToggle && elements.mediaOriginalPdfToggle.checked),
      translated_pdf: Boolean(elements.mediaTranslatedPdfToggle && elements.mediaTranslatedPdfToggle.checked),
      spanish_audio: Boolean(elements.mediaSpanishAudioToggle && elements.mediaSpanishAudioToggle.checked)
    };
  }

  async function start(operation, file) {
    if (!file) return;
    const outputs = selectedOutputs();
    if (operation === 'translate' && !Object.values(outputs).some(Boolean)) {
      if (elements.mediaInfo) elements.mediaInfo.textContent = 'Elegí al menos uno de los tres resultados.';
      return;
    }
    clearPoll();
    pollFailures = 0;
    setBusy(true, false, 'queued');
    setLink(elements.mediaPdfDownload, null, 'PDF');
    setLink(elements.mediaTranslatedPdfDownload, null, 'PDF en castellano');
    setLink(elements.mediaAudioDownload, null, 'audio en castellano');
    if (elements.mediaProgress) elements.mediaProgress.value = 0;
    if (elements.mediaInfo) elements.mediaInfo.textContent = `${file.name}: subiendo...`;
    const body = new FormData();
    body.append('file', file, file.name);
    try {
      const params = new URLSearchParams();
      if (operation === 'translate') {
        Object.entries(outputs).forEach(([name, enabled]) => params.set(name, enabled ? '1' : '0'));
      }
      params.set('file_bytes', String(Number(file.size || 0)));
      const preflight = await fetch(`/api/media/capabilities?operation=${encodeURIComponent(operation)}&${params.toString()}`);
      const capability = await preflight.json();
      if (!preflight.ok || capability.ok === false) {
        throw new Error(capability.detail || capability.error || 'media_preflight_failed');
      }
      const endpoint = params.size ? `${endpoints[operation]}?${params.toString()}` : endpoints[operation];
      uploadController = new AbortController();
      const response = await fetch(endpoint, { method: 'POST', body, signal: uploadController.signal });
      uploadController = null;
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.detail || data.error || 'media_upload_failed');
      pollingJobId = String(data.job_id || '');
      render(data);
      log(`${file.name}: procesamiento multimedia iniciado.`);
    } catch (error) {
      uploadController = null;
      setBusy(false, false, 'idle');
      const cancelled = error && error.name === 'AbortError';
      if (elements.mediaInfo) elements.mediaInfo.textContent = cancelled ? 'Carga cancelada.' : `Falló la carga: ${error.message}`;
      if (cancelled) return;
      throw error;
    }
  }

  async function cancel() {
    if (uploadController) {
      uploadController.abort();
      uploadController = null;
      return;
    }
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
    if (uploadController) uploadController.abort();
    uploadController = null;
    downloadHandlers.forEach((handler, element) => element.removeEventListener('click', handler));
    downloadHandlers.clear();
  }

  return { start, cancel, mount, poll, render, dispose };
}
