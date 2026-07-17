export function createAudioExportController({ api, elements, beginBusyLease, wait, log, getStatus }) {
  let pollingJobId = '';

  function syncInputs() {
    const mode = String(elements.audioExportMode.value || 'current');
    elements.audioExportBlockWrap.classList.toggle('audio-export-hidden', mode !== 'block');
    elements.audioExportRangeWrap.classList.toggle('audio-export-hidden', mode !== 'range');
  }

  function renderStatus(item) {
    const data = item && typeof item === 'object' ? item : {};
    const state = String(data.state || 'idle');
    const cached = Number(data.cached_blocks || 0);
    const generated = Number(data.generated_blocks || 0);
    if (['running', 'queued', 'canceling'].includes(state)) {
      const detail = data.detail || `Generando bloque ${Number(data.completed_blocks || 0) + 1} de ${Number(data.total_blocks || 0)}...`;
      elements.audioExportInfo.textContent = `${detail} Cacheados: ${cached} · Generados: ${generated}`;
    } else if (state === 'done') {
      elements.audioExportInfo.textContent = `Listo: guardado en Descargas. Cacheados: ${cached} · Generados: ${generated}`;
    } else if (state === 'cancelled') {
      elements.audioExportInfo.textContent = 'Exportación cancelada.';
    } else if (state === 'error') {
      elements.audioExportInfo.textContent = data.error || data.detail || 'No pude exportar audio.';
    } else {
      elements.audioExportInfo.textContent = 'Sin exportación de audio activa.';
    }
    if (data.download_url && state === 'done') {
      elements.audioExportDownload.href = data.download_url;
      elements.audioExportDownload.classList.remove('is-hidden');
    } else {
      elements.audioExportDownload.removeAttribute('href');
      elements.audioExportDownload.classList.add('is-hidden');
    }
    if (['running', 'queued', 'canceling'].includes(state) && data.job_id && pollingJobId !== data.job_id) {
      pollingJobId = data.job_id;
      poll(data.job_id).catch(() => {});
    }
    if (!['running', 'queued', 'canceling'].includes(state) && (!data.job_id || data.job_id === pollingJobId)) {
      pollingJobId = '';
    }
  }

  async function poll(jobId) {
    while (jobId && pollingJobId === jobId) {
      await wait(1000);
      const data = await api(`/api/audio-export/status/${jobId}`);
      renderStatus(data);
      if (!['running', 'queued', 'canceling'].includes(String(data.state || 'idle'))) return data;
    }
    return null;
  }

  async function start() {
    const mode = String(elements.audioExportMode.value || 'current');
    const payload = { mode };
    if (mode === 'block') payload.block = Number(elements.audioExportBlockInput.value || 0);
    if (mode === 'range') {
      payload.start = Number(elements.audioExportStartInput.value || 0);
      payload.end = Number(elements.audioExportEndInput.value || 0);
    }
    const releaseBusy = beginBusyLease();
    try {
      const data = await api('/api/audio-export', payload);
      renderStatus(data);
      log(data.detail || 'Exportación de audio iniciada.');
    } catch (error) {
      log(`No pude exportar audio: ${error.message}`);
    } finally {
      releaseBusy();
    }
  }

  async function cancel() {
    const current = getStatus();
    const jobId = pollingJobId || String(current && current.audio_export && current.audio_export.job_id || '');
    if (!jobId) {
      log('No hay exportación de audio en curso.');
      return;
    }
    try {
      const data = await api(`/api/audio-export/cancel/${jobId}`, {});
      renderStatus(data);
      log('Cancelando exportación de audio...');
    } catch (error) {
      log(`No pude cancelar la exportación: ${error.message}`);
    }
  }

  return { cancel, dispose: () => { pollingJobId = ''; }, renderStatus, start, syncInputs };
}
