function cleanText(value) {
  return String(value || '').trim();
}

export function createPandaDialog({ documentRoot = document } = {}) {
  const root = documentRoot.getElementById('pandaDialog');
  const title = documentRoot.getElementById('pandaDialogTitle');
  const message = documentRoot.getElementById('pandaDialogMessage');
  const fieldWrap = documentRoot.getElementById('pandaDialogFieldWrap');
  const fieldLabel = documentRoot.getElementById('pandaDialogFieldLabel');
  const field = documentRoot.getElementById('pandaDialogField');
  const cancel = documentRoot.getElementById('pandaDialogCancel');
  const accept = documentRoot.getElementById('pandaDialogAccept');

  function ask({ title: heading = 'Panda Fusión', message: detail = '', acceptLabel = 'Aceptar', cancelLabel = 'Cancelar', value, fieldLabel: label = '', danger = false } = {}) {
    if (!root) return Promise.resolve(typeof value === 'undefined' ? window.confirm(detail) : window.prompt(heading, value));
    title.textContent = cleanText(heading) || 'Panda Fusión';
    message.textContent = cleanText(detail);
    accept.textContent = acceptLabel;
    cancel.textContent = cancelLabel;
    accept.classList.toggle('danger-btn', Boolean(danger));
    const acceptsText = typeof value !== 'undefined';
    fieldWrap.hidden = !acceptsText;
    fieldLabel.textContent = label || heading;
    field.value = acceptsText ? String(value || '') : '';
    root.hidden = false;
    root.dataset.open = 'true';
    (acceptsText ? field : accept).focus();

    return new Promise(resolve => {
      const finish = accepted => {
        root.hidden = true;
        delete root.dataset.open;
        cancel.removeEventListener('click', onCancel);
        accept.removeEventListener('click', onAccept);
        root.removeEventListener('keydown', onKeydown);
        resolve(accepted ? (acceptsText ? field.value : true) : (acceptsText ? null : false));
      };
      const onCancel = () => finish(false);
      const onAccept = () => finish(true);
      const onKeydown = event => {
        if (event.key === 'Escape') { event.preventDefault(); finish(false); }
        if (event.key === 'Enter' && event.target !== field) { event.preventDefault(); finish(true); }
      };
      cancel.addEventListener('click', onCancel);
      accept.addEventListener('click', onAccept);
      root.addEventListener('keydown', onKeydown);
    });
  }

  return {
    confirm(options) { return ask(options); },
    prompt(options) { return ask({ ...options, value: Object.prototype.hasOwnProperty.call(options || {}, 'value') ? options.value : '' }); },
    notice(options) { return ask({ ...options, cancelLabel: '', acceptLabel: (options && options.acceptLabel) || 'Entendido' }); }
  };
}
