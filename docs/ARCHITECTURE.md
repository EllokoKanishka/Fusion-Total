# Panda Fusión — Arquitectura Vigente

## Norte

Panda Fusión es un lector conversacional por voz neural. La lectura sigue
siendo el centro del producto.

`fusion_reader_v2` continúa como identificador interno compatible.

## Capas principales

```text
Lectura:
Documento -> Chunker -> TTSProvider -> AudioCache -> Navegador

Diálogo:
Micrófono -> STTProvider -> ConversationCore -> TTSProvider -> Navegador

Investigación externa:
pedido explícito -> provider aislado de Fusion -> respuesta integrada
```

`fusion_reader_v2/facade.py` es la fachada pública que coordina estas capas.
`fusion_reader_v2/service.py` no contiene lógica de producto: conserva
únicamente el import histórico de `FusionReaderV2`.

## Lectura

Componentes principales:

- `fusion_reader_v2/reader.py`
- `fusion_reader_v2/tts.py`
- `fusion_reader_v2/services/reader.py`
- `fusion_reader_v2/services/audio.py`
- `fusion_reader_v2/facade.py`

Propiedades:

- no depende del LLM;
- acepta texto pegado como documento virtual `quick_text` dentro del mismo
  `ReaderSession`; conserva el original sólo en memoria, permite comenzar desde
  el inicio o desde el cursor y nunca lo restaura tras reiniciar;
- cachea audio por texto + voz + idioma;
- usa prefetch alrededor del cursor;
- puede preparar documento completo en background.
- chunking v2 tipo pagina: objetivo ~2200 caracteres, minimo normal ~1200 y maximo duro ~3200;
- empaqueta titulos y parrafos cortos con el contenido siguiente para evitar bloques diminutos;
- divide parrafos enormes por oracion y, si hace falta, por palabras para no romper el maximo duro;
- reduce la fragmentacion de lectura y navegacion a costa de audios por bloque mas largos.
- cuando AllTalk rechaza un bloque largo por tamaño (`http_400`), el servicio lo sintetiza por segmentos y recompone un WAV unico antes de cachearlo para no romper lectura ni exportacion.
- al renderizar un bloque nuevo, la UI resetea el viewport del lector al tope del contenedor scrolleable;
- la cabecera del lector prioriza `document.loaded/title/current/total` para mostrar el documento activo y evita falsos "Ningun documento activo" por desfasajes del anchor.
- el bloque visible del lector usa ahora el ancho completo del panel central, dejando solo padding lateral moderado para no volver a una columna angosta centrada.

## Diálogo

Componentes principales:

- `fusion_reader_v2/dialogue.py`
- `fusion_reader_v2/conversation.py`
- `fusion_reader_v2/services/dialogue.py`
- `fusion_reader_v2/facade.py`

Propiedades:

- usa snapshot del lector, no reemplaza al lector;
- STT principal conceptual en `8021` (`faster_whisper_server`);
- fallback operativo `whisper_cli`;
- TTS neural por defecto para respuesta oral;
- `Dialogar` puede degradar `Supremo -> Pensamiento` para cuidar latencia oral.
- la UI principal expone ahora el provider STT activo y si está en fallback.

## Dictado editable

Componentes principales:

- `fusion_reader_v2/dictation.py`: interpreta órdenes españolas y devuelve
  operaciones acotadas, sin mutar documentos;
- `fusion_reader_v2/dictation_assistant.py`: clasifica órdenes no cubiertas por
  la gramática mediante un modelo opcional y valida una única operación segura;
- `fusion_reader_v2/web/routes/dictation.py`: transcripción efímera y órdenes
  escritas;
- `fusion_reader_v2/web/static/js/dictation.mjs`: captura, editor, historial,
  lectura selectiva y autosalvado local.

```text
Micrófono -> MediaRecorder -> STTProvider -> gramática rápida -> textarea
                                               | noop invocado con Lucy
                                               v
                          asistente opcional -> instrucción acotada -> textarea

Texto seleccionado --------------------------------> TTSProvider -> audio
```

Propiedades:

- vive en un escritorio exclusivo de pantalla completa, separado de Texto rápido
  y Laboratorio; al cerrarlo restaura la interfaz principal;
- usa pausas para cerrar cada tramo y no depende de `SpeechRecognition`;
- distingue dictado de órdenes mediante la invocación explícita “Lucy”; el
  control puede desactivarse para que absolutamente todo entre como texto;
- “Lucy” sola arma una ventana acotada para la siguiente frase y el navegador
  no anuncia ese estado hasta que el modelo local, si fue elegido, confirmó su
  precarga y el nuevo grabador ya está activo; una vez detectada voz, reserva la
  invocación hasta obtener una transcripción válida para que el transporte no la
  consuma antes de interpretar la orden;
- comparte el catálogo y la voz TTS seleccionada con el lector principal;
- la transcripción literal usa el STT especializado local (default operativo:
  faster-whisper `small`, idioma `es`); no hace pasar cada frase por el modelo
  conversacional 14B ni por OpenAI;
- el selector de asistente ofrece reglas instantáneas (default), Qwen3 4B local,
  Qwen3 14B Q8 local y OpenAI GPT-5 nano mediante el agente aislado
  `fusion-dialogue`; los modelos sólo se invocan cuando una orden con “Lucy”
  escapa a la gramática;
- si el Qwen seleccionado falta, una acción explícita inicia `ollama pull` como
  proceso poseído y cancelable durante el cierre; 4B y 14B comparten una sola
  ranura serializada para impedir descargas simultáneas y estados cruzados;
- STT, asistente y TTS son procesos separados: cambiar o fallar el asistente no
  reinicia Whisper, AllTalk ni el modelo conversacional 14B;
- soporta insertar, reemplazar, reescribir selección, borrar coincidencias o
  desde un ancla hasta el final, borrar o reemplazar las últimas N palabras, limpiar,
  deshacer, rehacer y leer por
  selección, párrafo, hoja virtual o ancla textual;
- para una escalada opcional el navegador envía como máximo una ventana de
  12.000 caracteres alrededor del cursor; el modelo nunca devuelve un borrador
  completo y toda operación sigue bajo el historial local de deshacer;
- `localStorage` conserva el borrador por origen; pasar al lector o descargar TXT
  siempre requiere una acción explícita;
- una hoja virtual equivale a aproximadamente 1800 caracteres y no pretende
  reproducir la paginación editorial de un PDF o DOCX.

## Modos de razonamiento

Componentes principales:

- `fusion_reader_v2/conversation.py`
- `fusion_reader_v2/services/dialogue.py`
- `fusion_reader_v2/facade.py`

Modos disponibles:

1. **Normal**: 1 pasada, `think=false`. Respuesta directa.
2. **Pensamiento**: 1 pasada, `think=true`. Uso de fase de pensamiento nativa de Ollama.
3. **Supremo**: 3 pasadas (`borrador -> revisión -> final`). Auto-crítica interna.
4. **Pensamiento crítico** (anteriormente Contrapunto): 3 pasadas dialécticas.
   - **Tesis**: Lucy Cunningham genera la respuesta base.
   - **Antítesis**: Un auditor crítico busca fallos y omisiones.
   - **Síntesis**: Lucy integra la tensión en una respuesta final "con cicatriz".

Regla de degradación:

- En modo `Dialogar` (voz), los modos `Supremo` y `Pensamiento crítico` degradan automáticamente a `Pensamiento` para mantener la latencia por debajo de los 3-5 segundos.

## Eje de Anclaje

Fusion Reader v2 permite alternar entre dos modos de anclaje (Laboratory Mode):

1. **Modo Documento** (default):
   - Lucy responde anclada al documento activo y al bloque visible.
   - El contexto incluye el fragmento que el usuario está viendo.
2. **Modo Libre**:
   - Lucy conversa libremente, sin estar anclada al documento activo.
   - **Independencia Documental:** El documento activo NO se inyecta en el contexto por defecto. Esto evita que Lucy interprete todo a través del fragmento visible.
   - **Acceso Explícito:** El documento se incluye en el prompt SOLO si el usuario lo pide explícitamente (ej: "qué dice el texto", "según el fragmento", "mirá el documento").
   - **Claridad Operativa:** La API separa `document` (recurso cargado) de `anchor` (anclaje activo) para evitar confundir disponibilidad con uso.
   - El perfil (Académica/Bohemia) y el Velo siguen activos, tiñendo el tono de la conversación libre.

## Disciplina de Lectura Documental

En modo documento, `ConversationCore` distingue entre intención literal e intención interpretativa:

- **Pedido literal:** "qué dice", "leeme", "qué hay en pantalla", "repetí", etc.
  - la respuesta debe empezar reproduciendo o parafraseando fielmente el bloque visible;
  - la interpretación, si existe, queda como capa secundaria.
- **Pedido interpretativo:** "qué significa", "interpretá", "analizá", "explicá", etc.
  - puede pasar directamente a lectura analítica.
- **Pedido mixto:**
  - primero literal;
  - después interpretación breve.

Esto no altera perfiles, velos ni modos de razonamiento; agrega una disciplina de orden y fidelidad documental.

## Perfiles de personalidad

Fusion Reader v2 permite alternar entre dos perfiles de Lucy:

1. **Académica** (default):
   - personalidad sobria, exigente y filosófico-técnica;
   - inspirada en una presencia borgiana, contemplativa y precisa;
   - usa `FUSION_READER_CHAT_MODEL` (default `qwen3:14b-q8_0`).
2. **Bohemia**:
   - personalidad más libre, literaria y directa;
   - menos escolar, rechaza el "humanismo barato" y busca la tensión latente;
   - usa `FUSION_READER_BOHEMIA_CHAT_MODEL` si está definido, o el default.

## Velos Conversacionales

Fusion Reader v2 introduce un cuarto eje liviano de modulación tonal: el **Velo**.
- Es ortogonal a Anclaje, Perfil y Razonamiento.
- Inyecta una micro-instrucción dinámica (ej: "Nocturna", "Desarme", "Crítica") al final de la capa de personalidad.
- No requiere recargar modelos ni reescribir prompts completos.
- *Default:* "Lucy" (vacío/neutro).

## Selección de Voz TTS

Fusion Reader v2 introduce un quinto eje de personalización: la **Voz**.
- Permite cambiar la voz neural (AllTalk/XTTS) en tiempo de ejecución.
- **Catálogo Dinámico:** Se nutre de las voces disponibles en el servidor AllTalk activo.
- **Etiquetas Mitológicas:** Los nombres de archivo se mapean a códigos mitológicos (ej: `female_03.wav` -> "● M03 — Hera", `Morgan_Freeman CC3.wav` -> "● V06 — Hermes") y se agrupan en "Voces M" y "Voces V" mediante `<optgroup>`. Se aplica un código de colores distintivo por voz para facilitar la identificación visual.
- **Persistencia:** La voz seleccionada se guarda en el estado de la sesión y persiste entre reinicios.
- **Sincronización:** Al cambiar de voz, se cancelan los prefetches y preparaciones activas para evitar la mezcla de voces en la cola de reproducción.

## Notas

Componentes:

- `fusion_reader_v2/notes.py`
- `fusion_reader_v2/services/notes.py`
- `fusion_reader_v2/web/routes/notes.py`

Propiedades:

- por documento y bloque;
- editables, renombrables y borrables;
- accesibles por texto y por voz.

## Herramienta auxiliar PDF -> Word

Componentes:

- `fusion_reader_v2/pdf_to_docx.py`
- `fusion_reader_v2/web/routes/tools.py`

Propiedades:

- es una utilidad lateral, no parte del core conversacional del lector;
- no carga el resultado en Fusion ni reemplaza el documento activo;
- convierte PDF (digital o escaneado) a DOCX estructurado y editable;
- motor principal: **Docling GPU** (aceleración NVIDIA RTX 5090) para máxima calidad editorial;
- modo: **text-first** (omite imágenes, base64 y ruido OCR por defecto);
- motor secundario: `pdftotext` (rápido) para PDFs digitales simples;
- fallback legado: `ocr_tesseract` (deprecado para documentos largos);
- flujo: PDF → Docling GPU (placeholder mode) → Markdown Sanitizado v4 → DOCX editable → Descargas;
- sanitización v4: elimina automáticamente `data:image`, `base64`, bloques de imágenes y caracteres basura; normaliza OCR español conservador y repara palabras pegadas antes de escribir el DOCX;
- reparación de palabras pegadas: capa editorial medible en `md_to_docx.py` con detector `detect_suspicious_glued_tokens`, segmentador español local, correcciones exactas seguras y protección de términos de Ars Magica (por ejemplo Bonisagus, Bjornaer, Intellego, Ex Miscellanea);
- medición real v4: contra `convertido_8` del libro de Ars Magica se redujo de 812 a 17 tokens sospechosos en `convertido_9` (97.91%); no usa IA generativa, no inventa contenido y no reintroduce imágenes/base64/placeholders;
- no usa CPU fallback para Docling para evitar bloqueos de UI (mínimo 60 min en CPU vs 7 min en GPU);
- guarda el resultado en `~/Descargas` o `~/Downloads`;
- ofrece un enlace HTTP de descarga efímero para el DOCX recién generado;
- omite imágenes por defecto para asegurar un Word limpio y editable;
- no usa LibreOffice;
- alcance v1: funciona bien para textos académicos lineales y escaneos simples/medios;
- no garantiza reconstrucción editorial perfecta en manuales complejos, libros con muchas imágenes, tablas, columnas o maquetación pesada;
- cualquier capa de pulido adicional con IA/Qwen queda postergada como mejora futura opcional y no está implementada en esta etapa.

## Herramienta auxiliar Audio Export v1

Componentes:

- `fusion_reader_v2/audio_export.py`
- `fusion_reader_v2/services/audio_export.py`
- `fusion_reader_v2/facade.py`
- `fusion_reader_v2/web/routes/audio.py`

Propiedades:

- exporta audio del documento principal cargado en cuatro modos: bloque actual, bloque especifico, rango y documento completo;
- toma snapshot del documento, voz, idioma y chunks al iniciar para no depender del estado mutable de la UI;
- reutiliza `AudioCache` y el flujo TTS existente antes de regenerar audio;
- corre en background con un solo job activo a la vez, progreso visible y cancelacion cooperativa;
- concatena WAV localmente con `wave` cuando los parametros coinciden y usa `ffmpeg` como fallback si hace falta;
- guarda el WAV final en `~/Descargas` o `~/Downloads` y ofrece descarga HTTP efimera desde `/api/audio-export/download/<job_id>`;
- no cambia la reproduccion normal, el chunking, las voces ni los puertos;
- la seleccion irregular de bloques queda fuera de alcance en esta v1.

## Procesamiento local de audio y video

Componentes:

- `fusion_reader_v2/media.py`: formatos, inspección, normalización, jobs y PDF;
- `fusion_reader_v2/services/media.py`: STT, traducción, TTS, cancelación y artefactos;
- `fusion_reader_v2/web/routes/media.py`: carga, estado, montaje y descargas;
- `fusion_reader_v2/web/static/js/media.mjs`: panel derecho y un único poller.

```text
audio/video -> ffprobe -> FFmpeg FLAC 16 kHz mono -> Whisper
  -> transcripción con tiempos -> PDF
  -> [opcional] Ollama por fragmentos -> castellano -> TTS actual -> WAV
```

Propiedades:

- acepta formatos comunes decodificables por FFmpeg y diagnostica archivos ilegibles;
- captura la voz seleccionada al iniciar para evitar mezclas si la UI cambia;
- ejecuta un solo job multimedia por vez y permite cancelación cooperativa;
- publica PDF/WAV en Descargas mediante reserva y reemplazo seguro;
- conserva el texto interno en runtime hasta que el usuario decide montarlo;
- montar publica un TXT durable en `imported_texts` y lo carga en el
  `ReaderSession` como `media_transcript` o `media_translation`;
- no clona la voz del conferencista: usa una voz elegida de Fusion;
- no atribuye hablantes: hay tiempos Whisper, no diarización.

## Investigación externa

Componentes:

- `fusion_reader_v2/local_web_bridge.py`
- `fusion_reader_v2/openclaw_bridge.py`

Contrato:

- `ExternalResearchResult`
- activación solo ante pedido explícito

Proveedor vigente:

```text
default: auto
auto: SearXNG local -> OpenClaw fusion-research fallback
```

Reglas:

- `SearXNG` trae fuentes, snippets y URLs;
- no inventar fuentes;
- no presentar profundidad académica falsa cuando solo hay snippets;
- `spoken_answer` no debe leer URLs largas;
- `OpenClaw main` no se toca.

## TTS / STT

TTS:

- GPU Fusion: `7853`
- fallback CPU: `7851`
- Doctora/Antigravity: `7854` reservado, no usar

STT:

- primario configurado: `8021`
- provider primario: `faster_whisper_server`
- fallback CLI: `whisper`
- provider de fallback: `whisper_cli`
- estado actual típico de runtime: si `8021` no responde, `AutoSTTProvider` cae a `whisper_cli` sin romper `Leer` ni `Dialogar`

## Puertos y frontera

```text
8010 Fusion Reader v2
7853 TTS Fusion
7851 TTS CPU fallback
7854 TTS Doctora/Antigravity
8021 STT faster-whisper
11434 Ollama
stdio fusion_memory_mcp_server.py
```

## Proveedor seleccionable de diálogo

`ConversationCore` conserva la construcción de contexto, perfiles, velos,
historial y modos de razonamiento. `SelectableChatProvider` cambia únicamente
el último adaptador:

```text
ConversationCore -> Local: Ollama qwen3 14B
                 -> Cloud: OpenClaw fusion-dialogue -> OpenAI
```

El adaptador OpenClaw ofrece dos ejecuciones explícitas. `agent` usa una sesión
nueva y prompt por archivo en cada turno; `infer` usa inferencia local one-shot,
sin sesión ni herramientas, y transporta el prompt por argv. `infer` sólo se
habilita por configuración y usa el directorio de autenticación aislado de
`fusion-dialogue`; una falla no conmuta silenciosamente a otro provider.

La selección persiste en la sesión y nunca cambia STT, TTS, navegación,
lectura ni caché. El servicio multimedia recibe expresamente el provider local:
seleccionar OpenAI en Dialogar no sube audios, videos o libros. Tampoco existe
fallback silencioso entre nube y local.

## Lifecycle operativo

- `scripts/start_fusion_reader_v2.sh`
  - detecta commit actual y verifica instancias existentes en el puerto `8010`;
  - si existe una instancia con el mismo commit, evita lanzamientos duplicados;
  - si existe una instancia vieja (commit diferente), la reinicia automáticamente;
  - aborta con error si el puerto está ocupado por un proceso ajeno (no-Fusion);
  - selecciona TTS `7853` con validación de owner;
  - deja log persistente en `runtime/fusion_reader_v2/logs/fusion_reader_v2_server.log`;
  - guarda PID en `runtime/fusion_reader_v2/fusion_reader_v2.pid`;
  - valida health post-start de `8010`.
- `fusion_reader_v2/web/server.py` (implementación)
- `scripts/fusion_reader_v2_server.py` (wrapper compatible)
  - inyecta metadata de runtime (commit, pid, port, logs) en `/api/status` y `/api/build` para facilitar la gestión del lifecycle.
- `scripts/start_fusion_reader_v2_stt.sh`
  - levanta el server `8021` cuando se invoca explícitamente;
  - hoy no forma parte obligatoria del launcher principal.

## Memoria MCP (Read-Only)

Fusion Reader v2 implementa un servidor MCP de solo lectura (`scripts/fusion_memory_mcp_server.py`) que expone el estado curado del proyecto ubicado en `runtime/fusion_reader_v2/memory/`.

**Herramientas disponibles:**
- `memory.list`: Lista archivos permitidos.
- `memory.read`: Lee contenido de archivos `.md`.
- `memory.search`: Búsqueda de texto en la memoria.
- `memory.state`, `memory.boundaries`, `memory.next_steps`: Accesos directos a archivos clave.

**Seguridad:**
- No permite escritura ni borrado.
- Validación estricta de rutas mediante `pathlib.resolve()`.
- Restringido a una whitelist de archivos Markdown.

## Documento canónico de convivencia

Para OpenClaw/SearXNG/Antigravity:

- `docs/OPENCLAW_SEARXNG_COEXISTENCE.md`
