# Fusion Reader v2 — Estado de Continuidad

Fecha: 2026-07-09

Esta es la hoja corta para retomar el proyecto sin perderse. La historia larga
vive en `docs/HISTORY.md` y en los documentos históricos de diseño.

## Norte

Fusion Reader v2 es un lector conversacional por voz neural.

La ruta crítica de lectura debe quedar separada del LLM:

```text
Lectura:
Documento -> Chunker -> TTS -> Audio -> Navegador

Diálogo:
Micrófono -> STT -> ConversationCore/Ollama -> TTS -> Navegador
```

Si STT, Ollama o el diálogo fallan, `Leer` debe seguir funcionando.

## Estado actual

- Camino principal: `fusion_reader_v2/`
- Prototipo legacy: `scripts/openclaw_direct_chat.py`
- UI/API v2: `http://127.0.0.1:8010`
- TTS principal Fusion: `http://127.0.0.1:7853`
- TTS fallback CPU: `http://127.0.0.1:7851`
- TTS Doctora/Antigravity: `http://127.0.0.1:7854`
- STT principal configurado: `http://127.0.0.1:8021`
- STT efectivo actual: `whisper_cli` (fallback visible en UI cuando `8021` no responde)
- LLM local: Ollama `qwen3:14b-q8_0`
- Voz default: `female_03.wav`
- Idioma default: `es`
- Razonamiento activo: `thinking` (default)
- Modos de razonamiento: `normal`, `thinking`, `supreme`, `pensamiento_critico`
- Perfiles de Lucy: `academica` (default), `bohemia`
- Velo activo: `lucy` (default)

## Fronteras de voz

- Fusion no usa `7852`.
- Fusion no usa `7854`.
- Fusion solo confía en `7853` si existe
  `runtime/fusion_reader_v2/tts_owner.json` con `owner=fusion_reader_v2`.
- `verify_voice_port_isolation.sh` es la frontera operativa.

## Investigación externa vigente

Estado actual:

```text
provider default: auto
auto order: SearXNG local -> OpenClaw fusion-research fallback
```

Reglas:

- solo se activa bajo pedido explícito externo;
- la lectura no depende de esa vía;
- `SearXNG` local es el camino preferido;
- `OpenClaw` fallback usa `fusion-research`, nunca `main`;
- no usar Brave/global `web_search` para arreglar Fusion;
- Antigravity/Telegram usa `OpenClaw main` y no debe tocarse.

## Validación vigente

```text
tests.test_fusion_reader_v2: 240 OK
legacy reader safety: 35 tests OK
test discovery completa: 339 tests OK
verify_voice_port_isolation.sh: OK_WITH_WARNINGS en esta auditoría local
  - checks estrictos de Fusion y warnings externos de Doctora quedan separados
  - `7852` seguía libre
  - no hubo evidencia de que Fusion apuntara a `7854`
  - `7853` no estaba escuchando con owner válido al momento de correr el check
  - faltan archivos esperados bajo Doctora (`bitacora_mantenimiento.md` y `memoria/bitacora_mantenimiento.md`)
```

## Manifiesto operativo

- `docs/DEPENDENCIES_V2.md` consolida dependencias Python, binarios,
  servicios locales, puertos, variables de entorno, scripts de arranque y
  fronteras externas del runtime v2.
- `scripts/smoke_fusion_reader_v2.sh` agrega un smoke de solo lectura para ver
  estado operativo sin levantar ni matar servicios.
- la diferencia histórica `335` vs `337` vs `339` en test discovery quedó
  explicada:
  `335` era el estado previo al manifiesto, `337` sumó
  `tests/test_dependencies_manifest.py` y `339` agrega
  `tests/test_smoke_script.py`; las suites modulares además siguen injertando
  algunos casos legacy vía `attach_legacy_tests(...)`.

## Consolidación funcional reciente

Parches recientes confirmados:

```text
37a073f Prefer Fusion GPU TTS endpoint when ready
104bf5f Improve Fusion Reader startup logging
dd90001 Clarify free mode document status
d97dd52 Show active STT provider in UI
cdef8ab Respect literal document reading requests
Add PDF to Word conversion tool
```

Estado consolidado:

- TTS `7853` corregido y priorizado con owner validation.
- Startup logging `8010` corregido con log persistente y PID file.
- Modo libre/documento clarificado en API/UI.
- STT activo visible en UI; hoy el runtime puede operar con `whisper_cli` si `8021` está offline.
- Lectura literal vs interpretación corregida en modo documento.
- Herramienta auxiliar `PDF -> Word` disponible en la barra izquierda.
- Arquitectura de cinco ejes preservada y validada.
- Smoke de lectura literal validado.
- Smoke de conversión PDF -> DOCX validado con salida real en `~/Descargas`.
- Docling GPU disponible y operativo en runtime.
- Chunking v2 de lectura activo: bloques tipo página, menos fragmentación y navegación más estable.
- UI de lectura ajustada: el viewport del bloque vuelve arriba al cambiar de documento/bloque y la cabecera refleja el documento cargado aunque el anchor venga desfasado.
- Layout visual del lector ensanchado: el texto usa casi todo el panel central con márgenes laterales moderados.
- Exportación de audio v1 activa: bloque actual, bloque N, rango y documento completo con job en background, progreso, cancelación y salida WAV en `~/Descargas`.
- La exportación reutiliza cache/TTS actual y, si AllTalk rechaza un bloque largo por tamaño, lo sintetiza por segmentos y recompone un WAV único sin tocar el chunking.

Último commit relevante:

```text
e2f654a Use wider reader text layout
```

## Historial corto de consolidación

- `ab98a44`: voces mitológicas.
- `2b7024b`: consolidación de estado.
- `37a073f`: corrección de selección TTS `7853` vs fallback `7851`.
- `104bf5f`: lifecycle/logging persistente de `8010`.
- `dd90001`: separación `document` vs `anchor` en modo libre/documento.
- `d97dd52`: proveedor STT activo visible en UI.
- `cdef8ab`: disciplina de lectura literal vs interpretación.
- [x] Background PDF → Word tool.
- [x] **Conversión PDF a Word (Text-First)**:
    *   Motor: **Docling GPU** (RTX 5090).
    *   Política: Sin imágenes, sin base64, sin ruido OCR.
    *   Sanitización v4: Limpieza editorial española conservadora (OCR común, acentos, palabras pegadas, espacios en puntuación, headers repetidos).
    *   Reparación de palabras pegadas: Segmentador local con métrica objetiva, términos protegidos de Ars Magica y reglas de conectores españolas; no usa IA generativa ni reescribe contenido.
    *   Medición real 2026-05-07 sobre `201721562-Roles-Ars-Magica-4a-Ed-1_convertido_8.docx`: 812 tokens sospechosos antes, 17 después en `convertido_9`, reducción 97.91%, sin `base64`, `data:image` ni `<!-- image -->`.
    *   Rendimiento: 15 págs en ~30s (59KB final vs 3.5MB con imágenes).
    *   Tests: incluye validación de reparación v4 con ejemplos reales y preservación de términos protegidos.
- [x] **Consolidación Lifecycle 8010 v2**:
    *   Inyección de metadatos `runtime` (commit, pid, port, logs, python) en `/api/status` y `/api/build`.
    *   Launcher `start_fusion_reader_v2.sh` con verificación de commit: detecta si ya hay una instancia viva con el commit actual para evitar duplicados, y reinicia automáticamente si detecta una versión obsoleta.
    *   Protección contra colisiones: Aborta el inicio si el puerto 8010 está ocupado por una aplicación ajena.
    *   Trazabilidad: Registro de commit actual en el log de inicio y metadata persistente.

## Herramienta auxiliar PDF -> Word

- vive como utilidad compacta de UI, separada del flujo de lectura;
- no carga el DOCX resultante en Fusion;
- no cambia el documento activo ni el modo libre/documento;
- acepta PDF por click o mini drag/drop sobre el control lateral;
- genera un DOCX editable y estructurado;
- guarda la salida en `~/Descargas` o `~/Downloads`;
- ofrece además una descarga HTTP efímera desde `/api/tools/pdf-to-docx/download/<id>`;
- omite imágenes por defecto;
- no usa LibreOffice en esta v1;
- usa Docling GPU para PDFs digitales y escaneos simples/medios;
- no usa CPU fallback silencioso para PDFs escaneados largos o pesados.

## Alcance de PDF -> Word v1

- funcional para textos académicos lineales y escaneos simples/medios;
- validado operativamente con `sample_a` y con conversiones reales como `Platon El banquete` y `Ars Magica` para uso académico editable;
- no garantiza reconstrucción editorial perfecta en manuales complejos, libros con muchas imágenes, tablas, columnas o maquetación pesada;
- el pulido adicional con IA/Qwen queda postergado como mejora futura opcional y no forma parte de esta v1.

## Arranque recomendado

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
./scripts/fusion_memory_mcp_server.py (para memoria)
```

Notas:

- `start_fusion_reader_v2.sh` no levanta `8021` automáticamente.
- `open_fusion_reader.sh` sí contempla el arranque del STT dedicado.
- Si `8021` no está arriba, Fusion sigue operativo por `whisper_cli`.

## Chunking v2 de lectura

- objetivo por bloque: ~2200 caracteres;
- mínimo normal: ~1200 caracteres;
- máximo duro: ~3200 caracteres;
- títulos y párrafos cortos se empaquetan con el contenido siguiente;
- párrafos largos se dividen por oración y, si hace falta, por palabras;
- mejora principal: menos bloques diminutos, mejor lectura continua y mejor navegación;
- impacto esperado: audios por bloque más largos, pero mucha menos fragmentación entre avances.

## Audio Export v1

- modos soportados: bloque actual, bloque específico, rango y documento completo;
- corre como job en background con estado `queued/running/done/error/cancelled`;
- muestra progreso por bloques, cacheados y generados;
- reutiliza `AudioCache` y el TTS actual sin cambiar motor, voces ni puertos;
- concatena WAV localmente y guarda el resultado final en `~/Descargas` o `~/Downloads`;
- ofrece descarga HTTP efímera desde `/api/audio-export/download/<job_id>`;
- selección irregular de bloques queda postergada para una versión futura;
- smoke real validado en `8010` con `Platon El Banquete`: bloque actual, bloque 2 y rango 1-2.

## Memory MCP read-only v1

- Expone memoria markdown local en `runtime/fusion_reader_v2/memory/`.
- Transporte: `stdio`.
- Herramientas: `memory.list`, `memory.read`, `memory.search`, `memory.state`, `memory.boundaries`, `memory.next_steps`.
- Seguridad: solo lectura, validación estricta de path, restringido a archivos `.md`.

## Pendientes reales

- decidir si `8021` debe ser parte del launcher principal o si `whisper_cli` queda como camino operativo aceptado;
- exponer aún mejor en UI el provider STT efectivo durante diálogo oral y fallback real;
- probar `Dialogar` con micrófono real en más escenarios;
- ajustar fino VAD/barge-in según ruido ambiente y eco;
- afinar warmup/keep-hot de AllTalk GPU;
- mejorar OCR fino para PDFs escaneados largos;
- subir calidad del filtrado/ranking académico en la ruta `SearXNG` de Fusion.

## Fuentes vivas

- Reglas raíz: `AGENTS.md`
- Arquitectura: `docs/ARCHITECTURE.md`
- Operación: `docs/OPERATIONS.md`
- Convivencia OpenClaw/SearXNG: `docs/OPENCLAW_SEARXNG_COEXISTENCE.md`
- Personalidad: `docs/PERSONALITY.md`
- Historia: `docs/HISTORY.md`
