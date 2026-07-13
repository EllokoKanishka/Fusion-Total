export function createPreparationController({ api, elements, beginBusyLease, wait, log }) {
  function renderStatus(prepare) {
    if (!prepare) return;
    elements.prepareProgress.value = Math.max(0, Math.min(100, Number(prepare.percent || 0)));
    const total = prepare.total || 0;
    const done = (prepare.cached || 0) + (prepare.generated || 0) + (prepare.failed || 0);
    const label = total
      ? `bloque ${Math.min(done, total)} de ${total} — ${prepare.percent || 0} %`
      : 'sin bloques preparados';
    if (prepare.status === 'running' || prepare.status === 'canceling') {
      elements.prepareInfo.textContent = `Preparando documento: ${label}. Cache ${prepare.cached || 0}, nuevos ${prepare.generated || 0}${prepare.failed ? `, fallidos ${prepare.failed}` : ''}.`;
    } else if (prepare.status === 'done') {
      elements.prepareInfo.textContent = `Documento listo: ${label}. Cache ${prepare.cached || 0}, nuevos ${prepare.generated || 0}${prepare.failed ? `, fallidos ${prepare.failed}` : ''}.`;
    } else if (prepare.status === 'canceled') {
      elements.prepareInfo.textContent = `Preparación cancelada: ${label}.`;
    } else if (prepare.status === 'error') {
      elements.prepareInfo.textContent = prepare.message
        ? `${prepare.message} ${total ? `(${label})` : ''}`.trim()
        : 'No pude preparar el documento.';
    } else {
      elements.prepareInfo.textContent = total ? 'Audio pendiente de preparar.' : 'Audio sin preparar.';
    }
  }

  async function poll() {
    while (true) {
      await wait(1000);
      const data = await api('/api/prepare/status');
      renderStatus(data);
      if (!['running', 'canceling'].includes(data.status)) return data;
    }
  }

  async function start() {
    const releaseBusy = beginBusyLease();
    let started = false;
    try {
      const data = await api('/api/prepare/start', { start: 'cursor' });
      renderStatus(data);
      log('Preparando audio del documento en segundo plano...');
      started = true;
    } catch (error) {
      log(`No pude preparar el documento: ${error.message}`);
    } finally {
      releaseBusy();
    }
    if (started) await poll();
  }

  async function cancel() {
    try {
      const data = await api('/api/prepare/cancel', {});
      renderStatus(data);
      log('Cancelando preparación de audio...');
    } catch (error) {
      log(`No pude cancelar: ${error.message}`);
    }
  }

  return { cancel, poll, renderStatus, start };
}
