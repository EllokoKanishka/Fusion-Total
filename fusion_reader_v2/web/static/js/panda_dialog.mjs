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
    const previousFocus = documentRoot.activeElement;
    const acceptsText = typeof value !== 'undefined';
    const mode = acceptsText ? 'prompt' : (cancelLabel ? 'confirm' : 'notice');
    title.textContent = cleanText(heading) || 'Panda Fusión';
    message.textContent = cleanText(detail);
    accept.textContent = mode === 'confirm' ? 'Sí' : acceptLabel;
    cancel.textContent = mode === 'confirm' ? 'No' : cancelLabel;
    accept.classList.toggle('danger-btn', Boolean(danger));
    fieldWrap.hidden = !acceptsText;
    cancel.hidden = mode === 'notice';
    fieldLabel.textContent = label || heading;
    field.value = acceptsText ? String(value || '') : '';
    root.hidden = false;
    root.dataset.open = 'true';
    root.dataset.mode = mode;
    (acceptsText ? field : danger && mode === 'confirm' ? cancel : accept).focus();

    return new Promise(resolve => {
      const finish = accepted => {
        root.hidden = true;
        delete root.dataset.open;
        delete root.dataset.mode;
        cancel.removeEventListener('click', onCancel);
        accept.removeEventListener('click', onAccept);
        root.removeEventListener('keydown', onKeydown);
        previousFocus?.focus?.();
        resolve(accepted ? (acceptsText ? field.value : true) : (acceptsText ? null : false));
      };
      const onCancel = () => finish(false);
      const onAccept = () => finish(true);
      const onKeydown = event => {
        if (event.key === 'Escape') { event.preventDefault(); finish(false); }
        if (event.key === 'Enter' && acceptsText && event.target === field && !event.isComposing) {
          event.preventDefault();
          finish(true);
        }
        if (event.key === 'Tab') {
          const controls = [...(acceptsText ? [field] : []), ...(cancel.hidden ? [] : [cancel]), accept];
          const index = controls.indexOf(event.target);
          if (index < 0 || (event.shiftKey ? index === 0 : index === controls.length - 1)) {
            event.preventDefault();
            controls[event.shiftKey ? controls.length - 1 : 0].focus();
          }
        }
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
