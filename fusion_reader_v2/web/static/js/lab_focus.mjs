export function renderLaboratoryFocus(element, focus = {}) {
  const item = focus && typeof focus === 'object' ? focus : {};
  const heading = document.createElement('strong');
  heading.textContent = 'Foco del laboratorio';
  element.replaceChildren(heading);
  const title = String(item.title || '').trim();
  if (!title) { element.appendChild(document.createTextNode('Sin foco activo.')); return; }
  const role = item.role === 'main' ? 'principal' : 'consulta';
  const excerpt = String(item.text || '').trim();
  const clipped = excerpt.length > 240 ? `${excerpt.slice(0, 240).trimEnd()}...` : excerpt;
  element.appendChild(document.createTextNode(`${title} | ${role} | bloque ${Number(item.chunk_number || 0)} de ${Number(item.total || 0)}`));
  for (const text of [item.query ? `Búsqueda: ${String(item.query)}` : '', clipped]) {
    if (text) { element.appendChild(document.createElement('br')); element.appendChild(document.createTextNode(text)); }
  }
}
