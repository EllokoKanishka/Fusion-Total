from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old[:90]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")


# WebContext owns durable Dictation projects alongside other web-only runtime state.
replace_once(
    "fusion_reader_v2/web/context.py",
    "from fusion_reader_v2.domain.jobs import JobRegistry\n",
    "from fusion_reader_v2.domain.jobs import JobRegistry\nfrom fusion_reader_v2.dictation_workspace import DictationProjectStore\n",
)
replace_once(
    "fusion_reader_v2/web/context.py",
    "    media: MediaProcessingService = field(init=False)\n",
    "    media: MediaProcessingService = field(init=False)\n    dictation_projects: DictationProjectStore = field(init=False)\n",
)
replace_once(
    "fusion_reader_v2/web/context.py",
    "        # The OpenAI selector is deliberately scoped to reader conversations.\n",
    "        self.dictation_projects = DictationProjectStore(self.settings.paths.runtime / \"dictation_projects.json\")\n        # The OpenAI selector is deliberately scoped to reader conversations.\n",
)

# Dictation routes: durable projects, binary PDF export, and the missing proofreading route registration.
replace_once(
    "fusion_reader_v2/web/routes/dictation.py",
    "from fusion_reader_v2 import FusionReaderV2\n",
    "from fusion_reader_v2 import FusionReaderV2\nfrom fusion_reader_v2.dictation_workspace import DictationProjectStore, render_dictation_pdf\n",
)
replace_once(
    "fusion_reader_v2/web/routes/dictation.py",
    "class DictationResponder(Protocol):\n    path: str\n    headers: object\n",
    "class DictationResponder(Protocol):\n    path: str\n    headers: object\n    context: object\n",
)
replace_once(
    "fusion_reader_v2/web/routes/dictation.py",
    "    def _result(self, status: int, payload: dict) -> None: ...\n\n    def _read_body_to_temp(self, filename: str) -> Path: ...\n",
    "    def _result(self, status: int, payload: dict) -> None: ...\n\n    def _send(self, status: int, content_type: str, raw: bytes) -> None: ...\n\n    def _read_body_to_temp(self, filename: str) -> Path: ...\n",
)
replace_once(
    "fusion_reader_v2/web/routes/dictation.py",
    "def handle_dictation_get(responder: DictationResponder, path: str) -> bool:\n    if path != \"/api/dictation/assistant\":\n        return False\n    responder._json(200, _assistant_status(responder, responder.app.dictation_assistant_status()))\n    return True\n",
    '''def _project_store(responder: DictationResponder) -> DictationProjectStore:\n    store = getattr(getattr(responder, "context", None), "dictation_projects", None)\n    if not isinstance(store, DictationProjectStore):\n        raise RuntimeError("dictation_projects_unavailable")\n    return store\n\n\ndef handle_dictation_get(responder: DictationResponder, path: str) -> bool:\n    if path == "/api/dictation/projects":\n        try:\n            responder._json(200, {"ok": True, "projects": _project_store(responder).list()})\n        except RuntimeError as exc:\n            responder._json(503, {"ok": False, "error": str(exc)})\n        return True\n    if path != "/api/dictation/assistant":\n        return False\n    responder._json(200, _assistant_status(responder, responder.app.dictation_assistant_status()))\n    return True\n''',
)
replace_once(
    "fusion_reader_v2/web/routes/dictation.py",
    "def handle_dictation_post(responder: DictationResponder, path: str, payload: dict) -> bool:\n    if path == \"/api/dictation/assistant/warm\":\n",
    '''def handle_dictation_post(responder: DictationResponder, path: str, payload: dict) -> bool:\n    if path == "/api/dictation/projects":\n        action = str(payload.get("action") or "save").strip().lower()\n        try:\n            store = _project_store(responder)\n            if action == "save":\n                project_payload = payload.get("project") if isinstance(payload.get("project"), dict) else payload\n                responder._json(200, {"ok": True, **store.save(dict(project_payload))})\n            elif action == "load":\n                responder._json(200, {"ok": True, "project": store.load(str(payload.get("project_id") or ""))})\n            elif action == "delete":\n                responder._json(200, {"ok": True, **store.delete(str(payload.get("project_id") or ""))})\n            else:\n                responder._json(400, {"ok": False, "error": "invalid_dictation_project_action"})\n        except KeyError as exc:\n            responder._json(404, {"ok": False, "error": str(exc.args[0] if exc.args else exc)})\n        except (RuntimeError, TypeError, ValueError) as exc:\n            responder._json(400 if not isinstance(exc, RuntimeError) else 503, {"ok": False, "error": str(exc)})\n        return True\n    if path == "/api/dictation/export/pdf":\n        try:\n            raw = render_dictation_pdf(\n                str(payload.get("title") or "Dictado"),\n                str(payload.get("text") or ""),\n                page_numbers=bool(payload.get("page_numbers", True)),\n            )\n        except ValueError as exc:\n            responder._json(400, {"ok": False, "error": str(exc)})\n            return True\n        responder._send(200, "application/pdf", raw)\n        return True\n    if path == "/api/dictation/assistant/warm":\n''',
)

replace_once(
    "fusion_reader_v2/web/routing.py",
    '        "/api/dictation/assistant",\n        "/api/import-status",\n',
    '        "/api/dictation/assistant",\n        "/api/dictation/projects",\n        "/api/import-status",\n',
)
replace_once(
    "fusion_reader_v2/web/routing.py",
    '        "/api/dictation/assist",\n        "/api/dictation/speak",\n',
    '        "/api/dictation/assist",\n        "/api/dictation/proofread",\n        "/api/dictation/projects",\n        "/api/dictation/export/pdf",\n        "/api/dictation/speak",\n',
)

# Dictation HTML: reading navigation, styled PDF export, and a collapsed project/activity drawer.
replace_once(
    "fusion_reader_v2/web/static/index.html",
    '''      <div class="dictation-editor-tools">\n        <button id="dictationUndoBtn" class="compact-btn" type="button">Deshacer</button>\n        <button id="dictationRedoBtn" class="compact-btn" type="button">Rehacer</button>\n        <button id="dictationReadBtn" class="compact-btn" type="button">Leer selección</button>\n        <button id="dictationUseReaderBtn" class="compact-btn" type="button">Pasar al lector</button>\n        <button id="dictationDownloadBtn" class="compact-btn" type="button">Descargar TXT</button>\n        <button id="dictationClearBtn" class="compact-btn danger-btn" type="button">Limpiar</button>\n      </div>''',
    '''      <div class="dictation-editor-tools">\n        <button id="dictationUndoBtn" class="compact-btn" type="button" title="Revierte la última edición">Deshacer</button>\n        <button id="dictationRedoBtn" class="compact-btn" type="button" title="Recupera una edición que acabás de deshacer">Rehacer</button>\n        <button id="dictationReadPrevBtn" class="compact-btn" type="button" title="Volver un tramo en la lectura actual" disabled>◀ Atrás</button>\n        <button id="dictationReadBtn" class="compact-btn" type="button">Leer selección</button>\n        <button id="dictationReadNextBtn" class="compact-btn" type="button" title="Avanzar un tramo en la lectura actual" disabled>Adelante ▶</button>\n        <button id="dictationUseReaderBtn" class="compact-btn" type="button">Pasar al lector</button>\n        <button id="dictationDownloadBtn" class="compact-btn" type="button">Descargar TXT</button>\n        <button id="dictationPdfDownloadBtn" class="compact-btn" type="button">Descargar PDF</button>\n        <label class="toggle compact-toggle dictation-pdf-toggle"><input id="dictationPdfPageNumbersToggle" type="checkbox" checked> Nº de página</label>\n        <button id="dictationClearBtn" class="compact-btn danger-btn" type="button">Limpiar</button>\n      </div>''',
)
replace_once(
    "fusion_reader_v2/web/static/index.html",
    '''      <div>\n        <strong class="dictation-activity-title">Consola</strong>\n        <div id="dictationActivity" class="dictation-activity" aria-live="polite"></div>\n      </div>\n      <audio id="dictationPlayer" class="dictation-player" controls></audio>''',
    '''      <details id="dictationHistoryPanel" class="dictation-history-panel">\n        <summary>Dictados guardados y actividad</summary>\n        <div class="dictation-project-header">\n          <div>\n            <strong>Proyectos de dictado</strong>\n            <span id="dictationProjectStatus" class="dictation-project-status" aria-live="polite">Cargando…</span>\n          </div>\n          <button id="dictationNewProjectBtn" class="compact-btn" type="button">Nuevo dictado</button>\n        </div>\n        <div id="dictationProjectList" class="dictation-project-list" aria-label="Dictados guardados"></div>\n        <div class="dictation-activity-section">\n          <strong class="dictation-activity-title">Actividad</strong>\n          <div id="dictationActivity" class="dictation-activity" aria-live="polite"></div>\n        </div>\n      </details>\n      <audio id="dictationPlayer" class="dictation-player" controls></audio>''',
)
replace_once(
    "fusion_reader_v2/web/static/index.html",
    '“Lucy, reemplazá X por Y”, “Lucy, deshacer”, “Lucy, pará acá”, “Lucy, léeme el último párrafo”,',
    '“Lucy, reemplazá X por Y”, “Lucy, corregí el texto”, “Lucy, deshacer”, “Lucy, pará acá”, “Lucy, léeme el último párrafo”,',
)

# New DOM ids.
replace_once(
    "fusion_reader_v2/web/static/js/ui.mjs",
    "  'dictationUndoBtn', 'dictationRedoBtn', 'dictationReadBtn', 'dictationUseReaderBtn',\n  'dictationDownloadBtn', 'dictationClearBtn', 'dictationCommandInput', 'dictationCommandBtn',\n  'dictationStatus', 'dictationActivity', 'dictationPlayer', 'dictationStats',\n",
    "  'dictationUndoBtn', 'dictationRedoBtn', 'dictationReadPrevBtn', 'dictationReadBtn', 'dictationReadNextBtn', 'dictationUseReaderBtn',\n  'dictationDownloadBtn', 'dictationPdfDownloadBtn', 'dictationPdfPageNumbersToggle', 'dictationClearBtn',\n  'dictationCommandInput', 'dictationCommandBtn', 'dictationStatus', 'dictationHistoryPanel', 'dictationNewProjectBtn',\n  'dictationProjectList', 'dictationProjectStatus', 'dictationActivity', 'dictationPlayer', 'dictationStats',\n",
)

# Let the Dictation controller restore a saved voice through the canonical voice-changing path.
replace_once(
    "fusion_reader_v2/web/static/js/bootstrap.mjs",
    '''const dictationController = createDictationController({\n  api,\n  elements: els,\n  refreshMainStatus: data => renderStatus(data),\n  log\n});''',
    '''const dictationController = createDictationController({\n  api,\n  elements: els,\n  refreshMainStatus: data => renderStatus(data),\n  log,\n  applyVoice: async voice => {\n    const wanted = String(voice || '');\n    if (!wanted) return false;\n    els.dictationVoiceSelect.value = wanted;\n    if (!els.dictationVoiceSelect.value) return false;\n    await changeDictationVoice();\n    return true;\n  }\n});''',
)

# Dictation controller: deterministic queue navigation plus durable project autosave.
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    "const STORAGE_KEY = 'pandafusion.dictation.v1';\n",
    "const STORAGE_KEY = 'pandafusion.dictation.v1'; // legacy emergency snapshot and migration source\n",
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''export function splitSpeechText(value, maxChars = 620) {''',
    '''export function speechNavigationTarget(index, delta, total) {\n  const count = Math.max(0, Number(total || 0));\n  if (!count) return -1;\n  const current = Math.max(0, Math.min(Number(index || 0), count - 1));\n  return Math.max(0, Math.min(current + Number(delta || 0), count - 1));\n}\n\nexport function splitSpeechText(value, maxChars = 620) {''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  log,\n  documentRoot = document,''',
    '''  log,\n  applyVoice = null,\n  documentRoot = document,''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  let chunks = [];\n  const wakeGate = createWakeCommandGate({ now: () => Date.now() });''',
    '''  let chunks = [];\n  let projects = [];\n  let currentProjectId = '';\n  let projectsReady = false;\n  let projectSaveChain = Promise.resolve();\n  let pendingProjectVoice = '';\n  let speechQueue = { parts: [], urls: [], label: 'tramo', index: -1 };\n  const wakeGate = createWakeCommandGate({ now: () => Date.now() });''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  function addActivity(message) {\n    const clean = cleanText(message);\n    if (!clean) return;\n    activity.unshift(clean);\n    activity.splice(20);\n    elements.dictationActivity.innerHTML = '';\n    for (const item of activity) {\n      const row = documentRoot.createElement('div');\n      row.className = 'dictation-activity-row';\n      row.textContent = item;\n      elements.dictationActivity.appendChild(row);\n    }\n  }''',
    '''  function renderActivity() {\n    elements.dictationActivity.innerHTML = '';\n    for (const item of activity) {\n      const row = documentRoot.createElement('div');\n      row.className = 'dictation-activity-row';\n      row.textContent = item;\n      elements.dictationActivity.appendChild(row);\n    }\n  }\n\n  function addActivity(message) {\n    const clean = cleanText(message);\n    if (!clean) return;\n    activity.unshift(clean);\n    activity.splice(50);\n    renderActivity();\n  }''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  async function changeAssistant() {\n    const provider = String(elements.dictationAssistantSelect.value || 'rules');\n    try {\n      const data = await api('/api/dictation/assistant', { provider });\n      renderAssistantStatus(data);\n      const selected = (data.available || []).find(item => String(item.id || '') === data.selected) || {};\n      addActivity(`Asistente: ${selected.label || provider}${selected.model ? ` (${selected.model})` : ''}.`);\n    } catch (error) {\n      addActivity(`No pude cambiar el asistente: ${error.message}.`);\n      await refreshAssistantStatus();\n    }\n  }''',
    '''  async function changeAssistant({ persist = true, announce = true } = {}) {\n    const provider = String(elements.dictationAssistantSelect.value || 'rules');\n    try {\n      const data = await api('/api/dictation/assistant', { provider });\n      renderAssistantStatus(data);\n      const selected = (data.available || []).find(item => String(item.id || '') === data.selected) || {};\n      if (announce) addActivity(`Asistente: ${selected.label || provider}${selected.model ? ` (${selected.model})` : ''}.`);\n      if (persist) schedulePersist();\n    } catch (error) {\n      addActivity(`No pude cambiar el asistente: ${error.message}.`);\n      await refreshAssistantStatus();\n    }\n  }''',
)

old_persistence = '''  function persistNow() {\n    windowRef.clearTimeout(saveTimer);\n    saveTimer = 0;\n    try {\n      storage.setItem(STORAGE_KEY, JSON.stringify({\n        title: elements.dictationTitleInput.value,\n        text: editor.value,\n        updatedAt: Date.now()\n      }));\n    } catch (_) {}\n    updateStats();\n  }\n\n  function schedulePersist() {\n    windowRef.clearTimeout(saveTimer);\n    saveTimer = windowRef.setTimeout(persistNow, 350);\n    updateStats();\n  }\n\n  function restoreDraft() {\n    try {\n      const saved = JSON.parse(storage.getItem(STORAGE_KEY) || '{}');\n      if (saved && typeof saved === 'object') {\n        editor.value = String(saved.text || '');\n        elements.dictationTitleInput.value = String(saved.title || 'Dictado sin título');\n      }\n    } catch (_) {}\n    if (!elements.dictationTitleInput.value) elements.dictationTitleInput.value = 'Dictado sin título';\n    editor.selectionStart = editor.value.length;\n    editor.selectionEnd = editor.value.length;\n    updateStats();\n  }'''
new_persistence = '''  function projectTime(value) {\n    const millis = Number(value || 0) * 1000;\n    if (!millis) return '';\n    try {\n      return new Intl.DateTimeFormat('es-AR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(millis));\n    } catch (_) {\n      return new Date(millis).toLocaleString();\n    }\n  }\n\n  function renderProjects() {\n    elements.dictationProjectList.innerHTML = '';\n    if (!projects.length) {\n      const empty = documentRoot.createElement('div');\n      empty.className = 'dictation-project-empty';\n      empty.textContent = 'Todavía no hay otros dictados guardados.';\n      elements.dictationProjectList.appendChild(empty);\n    }\n    for (const item of projects) {\n      const row = documentRoot.createElement('div');\n      row.className = `dictation-project-row${item.project_id === currentProjectId ? ' active' : ''}`;\n      const openButton = documentRoot.createElement('button');\n      openButton.type = 'button';\n      openButton.className = 'dictation-project-open';\n      const title = documentRoot.createElement('strong');\n      title.textContent = String(item.title || 'Dictado sin título');\n      const meta = documentRoot.createElement('span');\n      meta.textContent = `${Number(item.words || 0)} palabras${item.updated_ts ? ` · ${projectTime(item.updated_ts)}` : ''}`;\n      openButton.append(title, meta);\n      openButton.addEventListener('click', () => loadProject(String(item.project_id || '')));\n      const deleteButton = documentRoot.createElement('button');\n      deleteButton.type = 'button';\n      deleteButton.className = 'compact-btn danger-btn dictation-project-delete';\n      deleteButton.textContent = 'Eliminar';\n      deleteButton.addEventListener('click', () => deleteProject(String(item.project_id || ''), String(item.title || '')));\n      row.append(openButton, deleteButton);\n      elements.dictationProjectList.appendChild(row);\n    }\n    if (!elements.dictationProjectStatus.textContent || /cargando/i.test(elements.dictationProjectStatus.textContent)) {\n      elements.dictationProjectStatus.textContent = projectsReady ? 'Guardado automático activo' : 'Respaldo local';\n    }\n  }\n\n  function upsertProjectSummary(summary) {\n    if (!summary || !summary.project_id) return;\n    projects = projects.filter(item => item.project_id !== summary.project_id);\n    projects.push(summary);\n    projects.sort((left, right) => Number(right.updated_ts || 0) - Number(left.updated_ts || 0));\n    renderProjects();\n  }\n\n  function persistLegacyFallback() {\n    try {\n      storage.setItem(STORAGE_KEY, JSON.stringify({\n        title: elements.dictationTitleInput.value,\n        text: editor.value,\n        updatedAt: Date.now()\n      }));\n    } catch (_) {}\n  }\n\n  function legacyDraft() {\n    try {\n      const saved = JSON.parse(storage.getItem(STORAGE_KEY) || '{}');\n      return saved && typeof saved === 'object' ? saved : {};\n    } catch (_) {\n      return {};\n    }\n  }\n\n  function projectPayload() {\n    return {\n      project_id: currentProjectId,\n      title: elements.dictationTitleInput.value,\n      text: editor.value,\n      voice: String(elements.dictationVoiceSelect.value || ''),\n      assistant: String(elements.dictationAssistantSelect.value || 'rules'),\n      commands_enabled: Boolean(elements.dictationCommandsToggle.checked),\n      pdf_page_numbers: Boolean(elements.dictationPdfPageNumbersToggle.checked),\n      selection_start: Number(editor.selectionStart || 0),\n      selection_end: Number(editor.selectionEnd || 0),\n      activity: [...activity]\n    };\n  }\n\n  function persistNow() {\n    windowRef.clearTimeout(saveTimer);\n    saveTimer = 0;\n    persistLegacyFallback();\n    updateStats();\n    if (!projectsReady) return projectSaveChain;\n    const payload = projectPayload();\n    elements.dictationProjectStatus.textContent = 'Guardando…';\n    projectSaveChain = projectSaveChain.catch(() => {}).then(async () => {\n      try {\n        const data = await api('/api/dictation/projects', { action: 'save', project: payload });\n        currentProjectId = String(data.project && data.project.project_id || currentProjectId);\n        upsertProjectSummary(data.summary);\n        elements.dictationProjectStatus.textContent = 'Guardado';\n        return data;\n      } catch (error) {\n        elements.dictationProjectStatus.textContent = 'Respaldo local; Panda no pudo guardar el proyecto';\n        return null;\n      }\n    });\n    return projectSaveChain;\n  }\n\n  function schedulePersist() {\n    windowRef.clearTimeout(saveTimer);\n    saveTimer = windowRef.setTimeout(persistNow, 450);\n    updateStats();\n  }\n\n  async function applyPendingProjectVoice() {\n    if (!pendingProjectVoice || typeof applyVoice !== 'function') return false;\n    const options = Array.from(elements.dictationVoiceSelect.options || []);\n    if (!options.some(option => String(option.value || '') === pendingProjectVoice)) return false;\n    const wanted = pendingProjectVoice;\n    elements.dictationVoiceSelect.value = wanted;\n    try {\n      const applied = await applyVoice(wanted);\n      if (applied === false) return false;\n      pendingProjectVoice = '';\n      return true;\n    } catch (error) {\n      addActivity(`No pude restaurar la voz ${wanted}: ${error.message}.`);\n      return false;\n    }\n  }\n\n  async function applyLoadedProject(project, { announce = true } = {}) {\n    if (!project || typeof project !== 'object') return;\n    if (active) stopListening();\n    stopSpeech({ clearQueue: true, resumeDictation: false });\n    currentProjectId = String(project.project_id || '');\n    elements.dictationTitleInput.value = String(project.title || 'Dictado sin título');\n    editor.value = String(project.text || '');\n    elements.dictationCommandsToggle.checked = project.commands_enabled !== false;\n    elements.dictationPdfPageNumbersToggle.checked = project.pdf_page_numbers !== false;\n    activity.splice(0, activity.length, ...(Array.isArray(project.activity) ? project.activity.slice(0, 50) : []));\n    renderActivity();\n    undoStack.length = 0;\n    redoStack.length = 0;\n    const start = Math.max(0, Math.min(Number(project.selection_start || 0), editor.value.length));\n    const end = Math.max(start, Math.min(Number(project.selection_end || start), editor.value.length));\n    editor.selectionStart = start;\n    editor.selectionEnd = end;\n    pendingProjectVoice = String(project.voice || '');\n    const wantedAssistant = String(project.assistant || 'rules');\n    if (Array.from(elements.dictationAssistantSelect.options || []).some(option => option.value === wantedAssistant)) {\n      elements.dictationAssistantSelect.value = wantedAssistant;\n      await changeAssistant({ persist: false, announce: false });\n    }\n    await applyPendingProjectVoice();\n    updateStats();\n    renderProjects();\n    if (announce) addActivity(`Proyecto abierto: ${elements.dictationTitleInput.value || 'Dictado sin título'}.`);\n  }\n\n  async function loadProject(projectId) {\n    const wanted = cleanText(projectId);\n    if (!wanted || wanted === currentProjectId) return;\n    if (currentProjectId) await persistNow();\n    try {\n      const data = await api('/api/dictation/projects', { action: 'load', project_id: wanted });\n      await applyLoadedProject(data.project);\n    } catch (error) {\n      addActivity(`No pude abrir ese dictado: ${error.message}.`);\n    }\n  }\n\n  async function createNewProject() {\n    if (currentProjectId) await persistNow();\n    try {\n      const data = await api('/api/dictation/projects', {\n        action: 'save',\n        project: {\n          title: 'Dictado sin título',\n          text: '',\n          voice: String(elements.dictationVoiceSelect.value || ''),\n          assistant: String(elements.dictationAssistantSelect.value || 'rules'),\n          commands_enabled: Boolean(elements.dictationCommandsToggle.checked),\n          pdf_page_numbers: true,\n          activity: []\n        }\n      });\n      projectsReady = true;\n      upsertProjectSummary(data.summary);\n      await applyLoadedProject(data.project, { announce: false });\n      addActivity('Nuevo dictado creado.');\n      schedulePersist();\n      editor.focus();\n      elements.dictationTitleInput.focus();\n      elements.dictationTitleInput.select();\n    } catch (error) {\n      addActivity(`No pude crear el dictado: ${error.message}.`);\n    }\n  }\n\n  async function deleteProject(projectId, label) {\n    const wanted = cleanText(projectId);\n    if (!wanted) return;\n    if (!windowRef.confirm(`¿Eliminar “${label || 'este dictado'}”? Esta acción no se puede deshacer.`)) return;\n    if (wanted === currentProjectId) await persistNow();\n    try {\n      await api('/api/dictation/projects', { action: 'delete', project_id: wanted });\n      projects = projects.filter(item => item.project_id !== wanted);\n      if (wanted === currentProjectId) {\n        currentProjectId = '';\n        if (projects.length) {\n          await loadProject(String(projects[0].project_id || ''));\n        } else {\n          await createNewProject();\n        }\n      } else {\n        renderProjects();\n      }\n    } catch (error) {\n      addActivity(`No pude eliminar ese dictado: ${error.message}.`);\n    }\n  }\n\n  async function initializeProjects() {\n    await refreshAssistantStatus();\n    const fallback = legacyDraft();\n    try {\n      const data = await api('/api/dictation/projects');\n      projects = Array.isArray(data.projects) ? data.projects : [];\n      projectsReady = true;\n      renderProjects();\n      if (projects.length) {\n        const loaded = await api('/api/dictation/projects', { action: 'load', project_id: projects[0].project_id });\n        await applyLoadedProject(loaded.project, { announce: false });\n        return;\n      }\n      if (String(fallback.text || '').trim() || String(fallback.title || '').trim()) {\n        const migrated = await api('/api/dictation/projects', {\n          action: 'save',\n          project: {\n            title: String(fallback.title || 'Dictado sin título'),\n            text: String(fallback.text || ''),\n            voice: String(elements.dictationVoiceSelect.value || ''),\n            assistant: String(elements.dictationAssistantSelect.value || 'rules'),\n            commands_enabled: Boolean(elements.dictationCommandsToggle.checked),\n            pdf_page_numbers: true,\n            activity: ['Borrador anterior migrado al historial de Panda Fusión.']\n          }\n        });\n        upsertProjectSummary(migrated.summary);\n        await applyLoadedProject(migrated.project, { announce: false });\n        schedulePersist();\n        return;\n      }\n      await createNewProject();\n    } catch (error) {\n      projectsReady = false;\n      editor.value = String(fallback.text || '');\n      elements.dictationTitleInput.value = String(fallback.title || 'Dictado sin título');\n      if (!elements.dictationTitleInput.value) elements.dictationTitleInput.value = 'Dictado sin título';\n      editor.selectionStart = editor.value.length;\n      editor.selectionEnd = editor.value.length;\n      elements.dictationProjectStatus.textContent = 'Respaldo local';\n      renderProjects();\n      addActivity(`No pude abrir el historial de Panda: ${error.message}. El borrador local sigue disponible.`);\n      updateStats();\n    }\n  }'''
replace_once("fusion_reader_v2/web/static/js/dictation.mjs", old_persistence, new_persistence)

old_speech = '''  function stopSpeech() {\n    speechSequence += 1;\n    speaking = false;\n    try { elements.dictationPlayer.pause(); } catch (_) {}\n    elements.dictationPlayer.removeAttribute('src');\n    if (active && !processing) startRecorderCycle();\n  }\n\n  async function playAudioUrl(url, sequence) {\n    if (!url || sequence !== speechSequence) return;\n    elements.dictationPlayer.src = url;\n    await new Promise((resolve, reject) => {\n      const cleanup = () => {\n        elements.dictationPlayer.removeEventListener('ended', onEnded);\n        elements.dictationPlayer.removeEventListener('error', onError);\n      };\n      const onEnded = () => { cleanup(); resolve(); };\n      const onError = () => { cleanup(); reject(new Error('audio_playback_failed')); };\n      elements.dictationPlayer.addEventListener('ended', onEnded, { once: true });\n      elements.dictationPlayer.addEventListener('error', onError, { once: true });\n      elements.dictationPlayer.play().catch(error => { cleanup(); reject(error); });\n    });\n  }\n\n  async function speakText(text, label = 'tramo') {\n    const parts = splitSpeechText(text);\n    if (!parts.length) return;\n    stopRecorderCycle(true);\n    speaking = true;\n    speechSequence += 1;\n    const sequence = speechSequence;\n    setStatus(`Leyendo ${label}: 1 de ${parts.length}…`, 'speaking');\n    try {\n      for (let index = 0; index < parts.length && sequence === speechSequence; index += 1) {\n        setStatus(`Leyendo ${label}: ${index + 1} de ${parts.length}…`, 'speaking');\n        const data = await api('/api/dictation/speak', { text: parts[index] });\n        if (!data.audio_url) throw new Error('La voz no devolvió un audio reproducible.');\n        await playAudioUrl(data.audio_url, sequence);\n      }\n      if (sequence === speechSequence) addActivity(`Lectura terminada (${label}).`);\n    } catch (error) {\n      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;\n      addActivity(`No pude leer: ${detail}.`);\n    } finally {\n      if (sequence === speechSequence) {\n        speaking = false;\n        setStatus(active ? 'Escuchando el próximo tramo…' : 'Dictado en pausa.', active ? 'listening' : '');\n        if (active && !processing) startRecorderCycle();\n      }\n    }\n  }'''
new_speech = '''  function renderSpeechNavigation() {\n    const total = speechQueue.parts.length;\n    elements.dictationReadPrevBtn.disabled = !total || speechQueue.index <= 0;\n    elements.dictationReadNextBtn.disabled = !total || speechQueue.index < 0 || speechQueue.index >= total - 1;\n  }\n\n  function stopSpeech({ clearQueue = false, resumeDictation = true } = {}) {\n    speechSequence += 1;\n    speaking = false;\n    try { elements.dictationPlayer.pause(); } catch (_) {}\n    elements.dictationPlayer.removeAttribute('src');\n    if (clearQueue) speechQueue = { parts: [], urls: [], label: 'tramo', index: -1 };\n    renderSpeechNavigation();\n    if (resumeDictation && active && !processing) startRecorderCycle();\n  }\n\n  function invalidateSpeechQueue() {\n    if (!speechQueue.parts.length && !speaking) return;\n    stopSpeech({ clearQueue: true });\n  }\n\n  async function playAudioUrl(url, sequence) {\n    if (!url || sequence !== speechSequence) return;\n    elements.dictationPlayer.src = url;\n    await new Promise((resolve, reject) => {\n      const cleanup = () => {\n        elements.dictationPlayer.removeEventListener('ended', onEnded);\n        elements.dictationPlayer.removeEventListener('error', onError);\n      };\n      const onEnded = () => { cleanup(); resolve(); };\n      const onError = () => { cleanup(); reject(new Error('audio_playback_failed')); };\n      elements.dictationPlayer.addEventListener('ended', onEnded, { once: true });\n      elements.dictationPlayer.addEventListener('error', onError, { once: true });\n      elements.dictationPlayer.play().catch(error => { cleanup(); reject(error); });\n    });\n  }\n\n  async function playSpeechQueueFrom(startIndex) {\n    if (!speechQueue.parts.length) return;\n    const target = speechNavigationTarget(startIndex, 0, speechQueue.parts.length);\n    if (target < 0) return;\n    stopRecorderCycle(true);\n    speaking = true;\n    speechSequence += 1;\n    const sequence = speechSequence;\n    try {\n      for (let index = target; index < speechQueue.parts.length && sequence === speechSequence; index += 1) {\n        speechQueue.index = index;\n        renderSpeechNavigation();\n        setStatus(`Leyendo ${speechQueue.label}: ${index + 1} de ${speechQueue.parts.length}…`, 'speaking');\n        let url = speechQueue.urls[index];\n        if (!url) {\n          const data = await api('/api/dictation/speak', { text: speechQueue.parts[index] });\n          if (sequence !== speechSequence) return;\n          if (!data.audio_url) throw new Error('La voz no devolvió un audio reproducible.');\n          url = data.audio_url;\n          speechQueue.urls[index] = url;\n        }\n        await playAudioUrl(url, sequence);\n      }\n      if (sequence === speechSequence) addActivity(`Lectura terminada (${speechQueue.label}).`);\n    } catch (error) {\n      const detail = error && error.data && error.data.detail ? error.data.detail : error.message;\n      addActivity(`No pude leer: ${detail}.`);\n    } finally {\n      if (sequence === speechSequence) {\n        speaking = false;\n        renderSpeechNavigation();\n        setStatus(active ? 'Escuchando el próximo tramo…' : 'Dictado en pausa.', active ? 'listening' : '');\n        if (active && !processing) startRecorderCycle();\n      }\n    }\n  }\n\n  function speakText(text, label = 'tramo') {\n    const parts = splitSpeechText(text);\n    if (!parts.length) return;\n    stopSpeech({ clearQueue: true, resumeDictation: false });\n    speechQueue = { parts, urls: Array(parts.length).fill(''), label, index: 0 };\n    renderSpeechNavigation();\n    return playSpeechQueueFrom(0);\n  }\n\n  function moveSpeech(delta) {\n    if (!speechQueue.parts.length || speechQueue.index < 0) {\n      addActivity('Primero usá “Leer selección” para crear una lectura navegable.');\n      return;\n    }\n    const target = speechNavigationTarget(speechQueue.index, delta, speechQueue.parts.length);\n    if (target === speechQueue.index) {\n      addActivity(delta < 0 ? 'Ya estás al comienzo de esta lectura.' : 'Ya estás al final de esta lectura.');\n      return;\n    }\n    stopSpeech({ clearQueue: false, resumeDictation: false });\n    speechQueue.index = target;\n    renderSpeechNavigation();\n    return playSpeechQueueFrom(target);\n  }'''
replace_once("fusion_reader_v2/web/static/js/dictation.mjs", old_speech, new_speech)

# Invalidate stale speech after edits/proofreading.
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''    if (result.changed) {\n      pushUndo(before);''',
    '''    if (result.changed) {\n      invalidateSpeechQueue();\n      pushUndo(before);''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''      if (revised !== source) {\n        replaceRange(editor, revised, selection.start, selection.end);''',
    '''      if (revised !== source) {\n        invalidateSpeechQueue();\n        replaceRange(editor, revised, selection.start, selection.end);''',
)

# Open restores pending project voice once the shared catalog exists.
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''    elements.dictationToggleBtn.setAttribute('aria-expanded', 'true');\n    editor.focus();\n  }''',
    '''    elements.dictationToggleBtn.setAttribute('aria-expanded', 'true');\n    applyPendingProjectVoice();\n    editor.focus();\n  }''',
)

# PDF export next to faithful TXT export.
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  function downloadText() {\n    if (!editor.value.trim()) {\n      addActivity('No hay texto para descargar.');\n      return;\n    }\n    const title = cleanText(elements.dictationTitleInput.value) || 'dictado';\n    const filename = `${title.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/[^a-z0-9_-]+/gi, '-').replace(/^-+|-+$/g, '') || 'dictado'}.txt`;\n    const url = windowRef.URL.createObjectURL(new windowRef.Blob([editor.value], { type: 'text/plain;charset=utf-8' }));\n    const anchor = documentRoot.createElement('a');\n    anchor.href = url;\n    anchor.download = filename;\n    documentRoot.body.appendChild(anchor);\n    anchor.click();\n    anchor.remove();\n    windowRef.URL.revokeObjectURL(url);\n    addActivity(`Descargado: ${filename}.`);\n  }''',
    '''  function exportFilename(extension) {\n    const title = cleanText(elements.dictationTitleInput.value) || 'dictado';\n    const stem = title.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/[^a-z0-9_-]+/gi, '-').replace(/^-+|-+$/g, '') || 'dictado';\n    return `${stem}.${extension}`;\n  }\n\n  function downloadText() {\n    if (!editor.value.trim()) {\n      addActivity('No hay texto para descargar.');\n      return;\n    }\n    const filename = exportFilename('txt');\n    const url = windowRef.URL.createObjectURL(new windowRef.Blob([editor.value], { type: 'text/plain;charset=utf-8' }));\n    const anchor = documentRoot.createElement('a');\n    anchor.href = url;\n    anchor.download = filename;\n    documentRoot.body.appendChild(anchor);\n    anchor.click();\n    anchor.remove();\n    windowRef.URL.revokeObjectURL(url);\n    addActivity(`Descargado: ${filename}.`);\n  }\n\n  async function downloadPdf() {\n    if (!editor.value.trim()) {\n      addActivity('No hay texto para descargar.');\n      return;\n    }\n    const filename = exportFilename('pdf');\n    try {\n      const response = await fetchFn('/api/dictation/export/pdf', {\n        method: 'POST',\n        headers: { 'Content-Type': 'application/json' },\n        body: JSON.stringify({\n          title: cleanText(elements.dictationTitleInput.value) || 'Dictado',\n          text: editor.value,\n          page_numbers: Boolean(elements.dictationPdfPageNumbersToggle.checked)\n        })\n      });\n      if (!response.ok) {\n        let detail = `HTTP ${response.status}`;\n        try {\n          const data = await response.json();\n          detail = data.detail || data.error || detail;\n        } catch (_) {}\n        throw new Error(detail);\n      }\n      const blob = await response.blob();\n      const url = windowRef.URL.createObjectURL(blob);\n      const anchor = documentRoot.createElement('a');\n      anchor.href = url;\n      anchor.download = filename;\n      documentRoot.body.appendChild(anchor);\n      anchor.click();\n      anchor.remove();\n      windowRef.URL.revokeObjectURL(url);\n      addActivity(`PDF académico descargado: ${filename}${elements.dictationPdfPageNumbersToggle.checked ? ' · páginas numeradas' : ' · sin numeración'}.`);\n    } catch (error) {\n      addActivity(`No pude generar el PDF: ${error.message}.`);\n    }\n  }''',
)

# Event wiring.
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  elements.dictationStopSpeechBtn.addEventListener('click', stopSpeech);\n  elements.dictationUndoBtn.addEventListener('click', undo);\n  elements.dictationRedoBtn.addEventListener('click', redo);\n  elements.dictationReadBtn.addEventListener('click', () => {''',
    '''  elements.dictationStopSpeechBtn.addEventListener('click', () => stopSpeech());\n  elements.dictationUndoBtn.addEventListener('click', undo);\n  elements.dictationRedoBtn.addEventListener('click', redo);\n  elements.dictationReadPrevBtn.addEventListener('click', () => moveSpeech(-1));\n  elements.dictationReadBtn.addEventListener('click', () => {''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''    speakText(selection.text, 'selección');\n  });\n  elements.dictationUseReaderBtn.addEventListener('click', mountInReader);\n  elements.dictationDownloadBtn.addEventListener('click', downloadText);''',
    '''    speakText(selection.text, 'selección');\n  });\n  elements.dictationReadNextBtn.addEventListener('click', () => moveSpeech(1));\n  elements.dictationUseReaderBtn.addEventListener('click', mountInReader);\n  elements.dictationDownloadBtn.addEventListener('click', downloadText);\n  elements.dictationPdfDownloadBtn.addEventListener('click', downloadPdf);\n  elements.dictationPdfPageNumbersToggle.addEventListener('change', schedulePersist);\n  elements.dictationNewProjectBtn.addEventListener('click', createNewProject);''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    "  elements.dictationAssistantSelect.addEventListener('change', changeAssistant);\n",
    "  elements.dictationAssistantSelect.addEventListener('change', () => changeAssistant());\n",
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  elements.dictationCommandsToggle.addEventListener('change', () => {\n    if (!elements.dictationCommandsToggle.checked) clearWakeCommand();\n  });''',
    '''  elements.dictationCommandsToggle.addEventListener('change', () => {\n    if (!elements.dictationCommandsToggle.checked) clearWakeCommand();\n    schedulePersist();\n  });\n  elements.dictationVoiceSelect.addEventListener('change', schedulePersist);''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  editor.addEventListener('input', () => {\n    windowRef.clearTimeout(manualTimer);''',
    '''  editor.addEventListener('input', () => {\n    invalidateSpeechQueue();\n    windowRef.clearTimeout(manualTimer);''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    '''  restoreDraft();\n  refreshAssistantStatus();''',
    '''  renderSpeechNavigation();\n  initializeProjects();''',
)
replace_once(
    "fusion_reader_v2/web/static/js/dictation.mjs",
    "export { DEFAULT_ASSISTANT_CONTEXT_CHARS, DEFAULT_PAGE_CHARS, STORAGE_KEY, WAKE_COMMAND_WINDOW_MS };\n",
    "export { DEFAULT_ASSISTANT_CONTEXT_CHARS, DEFAULT_PAGE_CHARS, STORAGE_KEY, WAKE_COMMAND_WINDOW_MS };\n",
)

# Styling: history drawer stays compact when closed; project rows are keyboard-friendly and responsive.
append_once(
    "fusion_reader_v2/web/static/styles.css",
    ".dictation-history-panel {",
    r'''
.dictation-pdf-toggle {
  min-height: 30px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.dictation-history-panel {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.08);
  padding: 0;
}

.dictation-history-panel > summary {
  cursor: pointer;
  padding: 8px 10px;
  color: var(--muted);
  font-weight: 600;
}

.dictation-history-panel[open] > summary {
  border-bottom: 1px solid var(--border);
}

.dictation-project-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px;
}

.dictation-project-header > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.dictation-project-status {
  color: var(--muted);
  font-size: 0.78rem;
}

.dictation-project-list {
  display: grid;
  gap: 6px;
  max-height: 220px;
  overflow: auto;
  padding: 0 10px 10px;
}

.dictation-project-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px;
  align-items: stretch;
}

.dictation-project-row.active .dictation-project-open {
  border-color: var(--accent);
  box-shadow: inset 3px 0 0 var(--accent);
}

.dictation-project-open {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  text-align: left;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.02);
  color: var(--text);
  padding: 7px 9px;
  cursor: pointer;
}

.dictation-project-open strong,
.dictation-project-open span {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dictation-project-open span,
.dictation-project-empty {
  color: var(--muted);
  font-size: 0.78rem;
}

.dictation-project-delete {
  align-self: center;
}

.dictation-project-empty {
  padding: 8px 0;
}

.dictation-activity-section {
  border-top: 1px solid var(--border);
  padding: 10px;
}

.dictation-history-panel .dictation-activity {
  max-height: 150px;
  margin-top: 6px;
}
''',
)

# Documentation: persistence is no longer browser-only and export/navigation are first-class.
replace_once(
    "docs/OPERATIONS.md",
    "El borrador se guarda en `localStorage` del origen `127.0.0.1:8010`. El audio se\nescribe en el upload temporal únicamente durante la transcripción y se elimina\nen el `finally` de la ruta. Cerrar el panel detiene pistas de micrófono y lectura.\n",
    "Los dictados se guardan como proyectos versionados dentro del runtime de Panda, con texto, título, voz, asistente, modo de órdenes, preferencia de numeración PDF y actividad reciente. El antiguo `localStorage` se conserva como respaldo de emergencia y fuente de migración para no perder borradores existentes. El audio se escribe en el upload temporal únicamente durante la transcripción y se elimina en el `finally` de la ruta. Cerrar el panel detiene pistas de micrófono y lectura.\n\nLa barra de Dictado permite navegar hacia atrás/adelante entre tramos de la lectura actual, descargar TXT fiel al borrador y generar un PDF académico A4 con numeración opcional. El panel `Dictados guardados y actividad` permanece plegado salvo que el usuario lo abra.\n",
)
replace_once(
    "docs/ARCHITECTURE.md",
    "- `localStorage` conserva el borrador por origen; pasar al lector o descargar TXT\n  siempre requiere una acción explícita;\n",
    "- el runtime de Panda conserva múltiples proyectos de Dictado con `AtomicJSONStore`; el antiguo `localStorage` queda como respaldo/migración de emergencia; pasar al lector o exportar siempre requiere una acción explícita;\n",
)
replace_once(
    "FUSION_READER_V2_STATE.md",
    "  editoriales acotadas cuando la frase invoca a “Lucy” y conserva el borrador en\n  `localStorage`; comparte la voz del lector y elimina el audio temporal después\n  de cada turno.\n",
    "  editoriales acotadas cuando la frase invoca a “Lucy”, conserva varios proyectos de dictado en el runtime (con migración del antiguo `localStorage`), comparte la voz del lector y elimina el audio temporal después de cada turno. La lectura del borrador permite volver/avanzar por tramos y exportar TXT o PDF académico A4 con numeración opcional.\n",
)

# JS unit coverage for navigation clamping.
append_once(
    "tests/dictation_module.test.js",
    "speech navigation clamps at both queue boundaries",
    r'''
test('speech navigation clamps at both queue boundaries', async () => {
  const { speechNavigationTarget } = await import(moduleUrl);
  assert.equal(speechNavigationTarget(0, -1, 5), 0);
  assert.equal(speechNavigationTarget(2, -1, 5), 1);
  assert.equal(speechNavigationTarget(2, 1, 5), 3);
  assert.equal(speechNavigationTarget(4, 1, 5), 4);
  assert.equal(speechNavigationTarget(0, 1, 0), -1);
});
''',
)

# Frontend route contract keeps the central allowlist aligned with the browser module.
replace_once(
    "tests/frontend_contracts.test.js",
    "    '/api/dictation/assist',\n    '/api/dictation/speak',\n",
    "    '/api/dictation/assist',\n    '/api/dictation/proofread',\n    '/api/dictation/projects',\n    '/api/dictation/export/pdf',\n    '/api/dictation/speak',\n",
)

print("Dictation workspace upgrade applied.")
