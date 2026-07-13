export const LAB_NOTES_DOC_ID = '__laboratory__';

export function compactNoteLabel(note) {
  const saved = String(note && note.label || '').trim();
  if (saved) return saved;
  const raw = String(note && note.text || '').trim();
  const words = raw.match(/[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+/g) || [];
  const stop = new Set(['a', 'al', 'bloque', 'como', 'con', 'de', 'del', 'el', 'en', 'es', 'esa', 'ese', 'esta', 'este', 'la', 'las', 'lo', 'los', 'nota', 'notas', 'para', 'por', 'que', 'se', 'sobre', 'toma', 'tomar', 'tomá', 'tome', 'un', 'una', 'y']);
  const selected = [];
  for (const word of words) {
    if (/^\d+$/.test(word) || stop.has(word.toLowerCase())) continue;
    selected.push(word);
    if (selected.length >= 3) break;
  }
  return (selected.length ? selected : words.slice(0, 3)).join(' ');
}

export function createNotesController({ api, elements, beginBusyLease, busyControls, getStatus, renderMainStatus, log, documentRoot = document, prompt = window.prompt, confirm = window.confirm }) {
  let state = { docId: '', current: 0, items: [] };
  function reference(note) {
    if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') {
      return `L${Number(note && note.anchor_number || 1)}`;
    }
    return `B${Number(note && note.chunk_number || note && note.anchor_number || 1)}`;
  }

  function render(items, activeDocId = '') {
    const notes = Array.isArray(items) ? items : [];
    const status = getStatus();
    const selectedDocId = activeDocId || status && status.doc_id || '';
    const laboratoryMode = selectedDocId === LAB_NOTES_DOC_ID;
    const isLab = note => String(note && note.source_kind || '').toLowerCase() === 'laboratory';
    const hasLabNotes = notes.some(isLab);
    const hasDocumentNotes = notes.some(note => !isLab(note));
    state = { docId: selectedDocId, current: status && status.current || 0, items: notes };
    const currentCount = notes.filter(note => !isLab(note) && Number(note.chunk_number || 0) === Number(state.current || 0)).length;
    elements.notesSummary.textContent = `${laboratoryMode ? 'Notas del laboratorio' : (hasLabNotes && hasDocumentNotes ? 'Notas del documento y laboratorio' : 'Notas del documento')} (${notes.length})`;
    if (!notes.length) elements.notesInfo.textContent = laboratoryMode ? 'Sin notas del laboratorio todavía.' : 'Sin notas todavía.';
    else if (laboratoryMode) elements.notesInfo.textContent = `${notes.length} nota${notes.length === 1 ? '' : 's'} en el laboratorio.`;
    else if (hasLabNotes && hasDocumentNotes) {
      const labCount = notes.filter(isLab).length;
      elements.notesInfo.textContent = `${currentCount} nota${currentCount === 1 ? '' : 's'} en este bloque y ${labCount} de laboratorio.`;
    } else elements.notesInfo.textContent = `${currentCount} nota${currentCount === 1 ? '' : 's'} en este bloque.`;
    elements.notesList.replaceChildren();
    for (const note of notes) {
      const row = documentRoot.createElement('details');
      row.className = 'note-row';
      if (!isLab(note) && Number(note.chunk_number || 0) === Number(state.current || 0)) row.classList.add('current');
      const summary = documentRoot.createElement('summary');
      const label = documentRoot.createElement('span');
      label.className = 'note-label';
      label.textContent = `${reference(note)} ${compactNoteLabel(note)}`.trim();
      label.title = note.text || '';
      const renameButton = documentRoot.createElement('button');
      Object.assign(renameButton, { type: 'button', className: 'note-rename', textContent: '+', title: 'Editar nombre' });
      renameButton.setAttribute('aria-label', 'Editar nombre de la nota');
      renameButton.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); rename(note); });
      summary.append(label, renameButton);
      const text = documentRoot.createElement('p');
      text.className = 'note-text'; text.textContent = note.text || '';
      const quote = documentRoot.createElement('p');
      quote.className = 'note-quote'; quote.textContent = note.quote ? `Texto: ${note.quote}` : '';
      const actions = documentRoot.createElement('div'); actions.className = 'note-actions';
      const goButton = documentRoot.createElement('button'); goButton.type = 'button';
      if (isLab(note)) { goButton.textContent = 'Sin bloque'; goButton.disabled = true; }
      else { goButton.textContent = 'Ir al bloque'; goButton.addEventListener('click', event => { event.preventDefault(); goTo(note); }); }
      const editButton = documentRoot.createElement('button');
      editButton.type = 'button'; editButton.textContent = 'Editar';
      editButton.addEventListener('click', event => { event.preventDefault(); edit(note); });
      const deleteButton = documentRoot.createElement('button');
      deleteButton.type = 'button'; deleteButton.textContent = 'Borrar';
      deleteButton.addEventListener('click', event => { event.preventDefault(); remove(note); });
      actions.append(goButton, editButton, deleteButton);
      row.append(summary, text); if (note.quote) row.append(quote); row.append(actions);
      elements.notesList.appendChild(row);
    }
  }

  async function refresh() {
    const status = getStatus();
    if (!status) {
      state = { docId: '', current: 0, items: [] };
      elements.notesSummary.textContent = 'Notas del documento';
      elements.notesInfo.textContent = 'Cargá un documento para tomar notas.';
      elements.notesList.replaceChildren(); return;
    }
    if (!status.doc_id) {
      const data = await api(`/api/notes?doc_id=${encodeURIComponent(LAB_NOTES_DOC_ID)}`);
      render(data.items || [], data.doc_id || LAB_NOTES_DOC_ID); return;
    }
    const [docData, labData] = await Promise.all([
      api(`/api/notes?doc_id=${encodeURIComponent(status.doc_id)}`),
      api(`/api/notes?doc_id=${encodeURIComponent(LAB_NOTES_DOC_ID)}`).catch(() => ({ items: [] }))
    ]);
    render([...(docData.items || []), ...(labData.items || [])], status.doc_id);
  }

  async function save() {
    const text = elements.noteInput.value.trim();
    const status = getStatus();
    if (!text) { log('Escribí una nota antes de guardarla.'); return; }
    if (!status || !status.doc_id) { log('Cargá un documento antes de guardar notas.'); return; }
    const releaseBusy = beginBusyLease();
    try {
      const data = await api('/api/notes/create', { text });
      elements.noteInput.value = ''; busyControls.setNoteText('');
      render(data.items || [], data.note && data.note.doc_id || status.doc_id);
      log(`Nota guardada como ${reference(data.note || {})}.`);
    } catch (error) { log(`No pude guardar la nota: ${error.message}`); }
    finally { releaseBusy(); }
  }

  async function goTo(note) {
    if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') { log(`La nota ${reference(note)} pertenece al laboratorio y no tiene bloque.`); return; }
    try { const data = await api('/api/jump', { index: Number(note.chunk_number || 1) }); renderMainStatus(data); log(`Salté al bloque ${note.chunk_number || 1}.`); }
    catch (error) { log(`No pude ir a la nota: ${error.message}`); }
  }

  async function rename(note) {
    const value = prompt('Nombre corto de la nota', compactNoteLabel(note));
    if (value === null) return;
    const label = value.trim(); if (!label) { log('El nombre de la nota no puede quedar vacío.'); return; }
    try { const data = await api('/api/notes/rename', { note_id: note.note_id, doc_id: note.doc_id, label }); render(data.items || []); log('Nombre de nota actualizado.'); }
    catch (error) { log(`No pude renombrar la nota: ${error.message}`); }
  }

  async function edit(note) {
    const value = prompt('Editar nota', note.text || ''); if (value === null) return;
    const text = value.trim(); if (!text) { log('La nota no puede quedar vacía.'); return; }
    try { const data = await api('/api/notes/update', { note_id: note.note_id, doc_id: note.doc_id, text }); render(data.items || []); log('Nota actualizada.'); }
    catch (error) { log(`No pude editar la nota: ${error.message}`); }
  }

  async function remove(note) {
    if (!confirm('Borrar esta nota?')) return;
    try { const data = await api('/api/notes/delete', { note_id: note.note_id, doc_id: note.doc_id }); render(data.items || []); log('Nota borrada.'); }
    catch (error) { log(`No pude borrar la nota: ${error.message}`); }
  }

  return { edit, goTo, refresh, remove, rename, render, save, state: () => ({ ...state }) };
}
