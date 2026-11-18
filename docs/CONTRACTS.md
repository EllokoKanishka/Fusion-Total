# Panda Fusión Public Contracts

This document defines deliberate compatibility promises. Internal modules may
change during consolidation, but these contracts remain stable unless a
documented migration and compatibility layer are added.

## Python Imports

The canonical import is:

```python
from fusion_reader_v2 import FusionReaderV2
```

The historical import remains supported:

```python
from fusion_reader_v2.service import FusionReaderV2
```

Public provider, document, dialogue, notes, metrics, audio export, and
conversion symbols exported by `fusion_reader_v2.__init__` remain available.

## Reader Facade

`FusionReaderV2` remains the application facade. Its public groups are:

- document load, clear, reference, and promotion;
- transient quick-text load without automatic session or library persistence;
- status, current read, repeat through `read_current`, and navigation;
- preparation start/status/cancel;
- audio export start/status/cancel/download;
- media transcription/translation start/status/cancel/mount/download;
- voice catalog, selection, and synthetic test;
- notes CRUD;
- document chat and explicit external research;
- text/audio dialogue and reset;
- dictation text interpretation and ephemeral audio transcription;
- explicit dialogue-provider selection (`local` or `openai`) through
  `POST /api/chat/provider`; the selected value is session-persistent;
- laboratory anchor, profile, veil, and reasoning modes;
- deterministic background shutdown.

The exact parameter names are enforced in
`tests/contracts/test_public_contracts.py` because callers use keyword
arguments.

## JSON Compatibility

`POST /api/read/timings` accepts the current cached `audio_url` and returns
`pending`, `waiting`, `ready`, or `unavailable`. It rejects audio belonging to
another text or voice. Ready results include measured word start/end times,
original-text UTF-16 offsets and alignment coverage. An optional `retry` retries
a previous failure. The reader remains usable while timing data is unavailable.

Existing endpoint routes and successful response fields are preserved. Reader
state always exposes `ok`, `loaded`, `doc_id`, `title`, `current`, `total`,
`chunk`, `document`, and service status. Audio responses retain cache/provider
timing metadata. Job responses retain their existing identifiers, state,
progress, result/output, and error information.

New fields may be added when they are safe for existing clients. Error
responses may be normalized to a stable envelope while preserving the legacy
`error` field:

```json
{
  "ok": false,
  "error": "stable_error_code",
  "detail": "human-readable explanation",
  "request_id": "request correlation id"
}
```

Dynamic values are not snapshot identity: timestamps, process IDs, durations,
UUIDs, temporary paths, and the current Git commit are ignored when comparing
contract snapshots.

Dictation routes:

- `POST /api/dictation/transcribe`: raw browser audio; query parameters
  `filename` and `commands=0|1`; returns `transcript`, STT metadata and one
  bounded `instruction` object;
- `POST /api/dictation/interpret`: JSON `text` plus optional
  `commands_enabled`; useful for typed corrections and accessibility;
- `POST /api/dictation/speak`: JSON `text`; generates interactive TTS with the
  same session voice as the main reader and returns a cache-backed `audio_url`;
- `GET /api/dictation/assistant`: returns the selected bounded assistant and its
  catalog; `POST` to the same route changes the session-persistent selection;
- `POST /api/dictation/assistant/install`: explicitly starts the one-time local
  installation of the configured Ollama dictation model; installation state is
  exposed by `GET /api/dictation/assistant` and never blocks STT or TTS;
- `POST /api/dictation/assistant/warm`: preloads the selected local model with a
  bounded keep-alive before the browser exposes the armed listening state;
- `POST /api/dictation/assist`: accepts one explicit command plus a bounded
  draft excerpt and selection offsets, and returns one validated instruction;
- instruction kinds are additive and currently include `dictate`, `insert`,
  `replace`, `replace_selection`, `replace_last_words`, `delete`, `delete_from`,
  `delete_last_words`, `clear`, `undo`, `redo`, `read`, `stop_listening` and `noop`.

Audio dictation requires the wake word `Lucy` for commands. In the browser,
a mixed utterance may contain dictated text followed by `Lucy` and an imperative:
the prefix is inserted literally, then only the invoked tail is interpreted.
Quoted dialogue and inline mentions of the name stay literal. `Lucy` alone arms
the following utterance for twenty seconds. Typed commands sent
through `/api/dictation/interpret` remain explicit and do not need the wake
word.

A bare `Lucy` utterance arms the next recorded utterance as its command for a
bounded time window. When the local assistant is selected, the browser first
waits for Ollama to confirm the model preload. It starts the new recorder before
announcing the armed state, so neither model load nor recorder startup can be
mistaken for active command listening. Common accented STT spellings such as
`Lúci` use the same wake contract. Once speech begins, the browser reserves the
wake state through transcription and consumes it only after receiving a valid
transcript; a failed upload or transcription keeps the bounded command window
available for one retry. An invoked command never degrades to a literal
`dictate` instruction.

The draft itself is browser-owned. Optional model assistance accepts at most a
12,000-character excerpt around the caret. It cannot return a complete draft or
an unbounded operation. Ollama receives the schema through its native `format`
field. OpenAI runs behind the isolated OpenClaw CLI, which receives the same
serialized JSON schema in its prompt; PandaFusion validates the resulting
object strictly before returning any editor instruction because that CLI does
not expose OpenAI's native response-format parameter. CLI diagnostics outside
the JSON transport envelope are ignored, but the embedded OpenClaw result and
the assistant instruction must both retain their expected object shapes.
The browser applies validated operations while retaining undo ownership.
Selecting OpenAI is explicit because that excerpt is sent to the isolated cloud
provider.

## Local Notes and Session Recovery

Notes are stored under `notes/by-id/<sha256-of-complete-doc-id>.json` so
different document IDs cannot collide through filename sanitization. Historical
flat JSON files remain readable when their document or per-note identity is
explicit. Reading them never rewrites them; subsequent edits use the new path.
Notes with unknown ownership remain in their original files and produce a
review warning instead of being assigned to the currently selected document.
Malformed canonical notes block edits until reviewed. Optional `warnings` in
notes responses expose codes; the reader displays a human-readable notice.

Code rollback must preserve both storage layouts: older versions do not read
new `by-id` files. Callers must still supply distinct document IDs; this storage
contract cannot distinguish two sources assigned the same ID.

Session recovery validates field types before restoration. Invalid snapshots
are preserved as `.corrupt.*` files and startup uses the default state. If a
migration backup fails, the migrated value is available only in memory; the
original file and subsequent writes remain protected until backup succeeds.
Persistence warning buffers retain at most 64 entries per store.

Laboratory notes choose the newer written or spoken exchange using recorded
timestamps. Untimed historical entries retain the dialogue-first fallback.

## HTTP Routes

The v2 routes listed in `docs/ARCHITECTURE.md` remain supported. Health is
split into liveness and readiness without removing `/health`, `/api/status`, or
`/api/build`. Static assets may move out of Python, but `/` continues serving
the same reader UI.

`POST /api/quick-text` accepts `text`, optional `title`, and optional
`start_offset`. It loads a transient main document through the normal reader
pipeline and exposes `document.transient=true`; the source text is deliberately
excluded from the recoverable session snapshot.

`start_offset` uses browser UTF-16 units. A split surrogate pair clamps left;
the response reports the applied UTF-16 offset. The Python helper
`chunk_index_for_offset` retains codepoint units. Chunk mapping aligns against
the original source, accounting for normalized whitespace and skipped noise
paragraphs. A single word longer than the hard chunk limit is split without
dropping characters; the original document text remains available unchanged.

Text extraction accepts safe directory entries in DOCX/ODT archives while
retaining traversal, encryption and archive size checks. RTF text extraction
decodes declared ANSI code pages, hexadecimal escapes, scoped Unicode fallback
counts and surrogate pairs, skipping metadata destinations. This extracts text;
it does not promise reconstruction of RTF layout or all font-specific encodings.

OCR uses automatic page segmentation unless a single persistent gutter and at
least three supporting text lines justify two-column order. Full-width text
above/below that region remains included. It does not force two columns on every
page or discard the upper body as heading-only. Raw recognition is preserved
without editorial rewriting in the imported `.raw.txt`; this is the recognizer's
output, not a replacement for retaining the original scanned PDF.

Editorial splitting preserves dictionary words and requires dictionary-backed
pieces when splitting connector spans; known explicit OCR repairs remain.
Unknown vocabulary may be left unsplit rather than guessed.

Document imports retain their starting document generation, including time
spent receiving a raw upload. Main imports also retain request order: the newer
accepted main import supersedes an older one. A failed registration or worker
start does not supersede a running import. Reference imports do not supersede
each other. Publication checks identity and cancellation while holding the same
lock as main-document replacement, before writing converted text or loading it.
Synchronous raw/base64 imports reject superseded publication with JSON 409.

`POST /api/import-cancel/<job_id>` requests cooperative cancellation. Status
remains queryable through `GET /api/import-status?id=<job_id>`, transitioning
through `canceling` to `cancelled`; progress cannot resurrect a cancelled job.
A queued cancelled job skips conversion. An active converter may finish its
current operation, but cannot publish after cancellation. The UI stops polling
at `cancelled`. A job already terminal retains its result/state.

Failed registration or worker start removes the owned input for imports and
PDF-to-Word jobs. A full job registry returns JSON 503 (`job_registry_full`).
Invalid numeric GET parameters return JSON 400 with request ID; unexpected
GET/HEAD failures use the same sanitized error envelope as POST. HEAD errors
carry JSON content headers without a response body.

Media routes:

The media UI captures `stt_prompt`/`stt_hotwords` when an upload starts. Once the
server accepts that upload with a job ID, it clears those inputs for the next
file. Failed/cancelled uploads retain hints for retry. New values entered during
an outstanding upload are preserved. The post-correction preference persists.

- `GET /api/media/capabilities`: preflight local de FFmpeg/FFprobe, STT y, según
  las salidas pedidas, traducción/TTS; acepta `operation`, flags de salida y
  `file_bytes` para verificar espacio libre antes de subir.
- `POST /api/media/transcribe`: multipart `file`; transcripción y PDF
  (compatibilidad). Puede recibir `stt_prompt` y `stt_hotwords` como query
  opcional para contextual biasing de ese único archivo.
- `POST /api/media/translate`: multipart `file`; acepta los flags de query
  `original_pdf`, `translated_pdf` y `spanish_audio`, además de los mismos
  `stt_prompt` y `stt_hotwords` opcionales. Por compatibilidad, si se omiten los
  flags de salida genera las tres salidas; la UI nueva envía flags explícitos y
  deja sólo el PDF traducido activo por defecto. Al menos una debe estar activa.
- `GET /api/media/status[/<job_id>]`: último job o job específico.
- `POST /api/media/cancel/<job_id>`: cancelación cooperativa.
- `POST /api/media/mount/<job_id>`: monta el texto terminado como documento principal.
- `GET /api/media/download/<job_id>/{pdf|translated-pdf|audio}`: artefacto validado.

`stt_prompt` y `stt_hotwords` son hints de decodificación, no reglas de
reescritura. Se normalizan y limitan antes de iniciar el worker, viajan sólo en
esa llamada al proveedor STT y no se escriben en `.env`, en el manifiesto del
job ni en la siguiente transcripción. Los overrides persistentes del servidor
siguen existiendo mediante las variables `FUSION_READER_STT_*` documentadas.

Los jobs usan estados `queued`, `running`, `canceling`, `done`, `partial`,
`cancelled` y `error`; `partial` conserva y permite montar las salidas que sí
terminaron. Devuelven sólo preview, conteos, proveedor y tiempos, no la
transcripción completa. Hay un
único job multimedia activo. La carga default máxima es 2 GiB
(`FUSION_READER_MEDIA_MAX_BYTES`) y el timeout default es 2 horas
(`FUSION_READER_MEDIA_TIMEOUT_SECONDS`) para el pipeline completo. La duración
del medio tiene un máximo independiente de 6 horas
(`FUSION_READER_MEDIA_MAX_DURATION_SECONDS`).

## System Boundaries

Reading never requires STT, an LLM, or research. External research activates
only on an explicit request and uses SearXNG first, then the isolated OpenClaw
`fusion-research` agent. Fusion never routes through OpenClaw `main`, port
7852, or port 7854.

OpenAI dialogue is a separate, explicit capability and may only use the
`fusion-dialogue` agent. It changes the conversation `ChatProvider` while STT,
TTS, reading and multimedia processing remain local. A provider failure must
not interrupt reader controls and must not silently fall back to another model.


### Dictation proofreading

- `POST /api/dictation/proofread`: request `{text}`; bounded to 12,000 characters. Returns corrected `text`, model/timing counters and accepted/rejected-part telemetry. Operational failures are atomic and preserve the supplied text.
- `proofread` is a bounded dictation instruction with scopes `all`, `selection`, `current_paragraph`, `previous_paragraph`, or `last_paragraph`. It does not permit a model to return a whole-document editor operation.
- Native Ollama structured classification runs with thinking disabled and temperature `0`; final instructions are still schema-validated before any editor mutation.
- Correction similarity alone is insufficient: protected Spanish negation
  context, ordered numeric values/number words and literal quotations must
  remain intact. Rejections preserve the source and count as safe rejected parts
  in Dictation. These lexical barriers do not prove arbitrary semantic equivalence.
- Empty/non-text input returns HTTP 400, oversized input 413, an unavailable
  corrector 503 and an operational correction failure 502. A failure after an
  earlier paragraph was corrected still returns the complete original text.

### Dictation draft safety

- STT, typed interpretation, optional assistance and proofreading retain the
  draft session, revision, text, selection, lifecycle and request order. A stale
  result cannot mutate the editor. Closing/stopping dictation invalidates pending
  work; late microphone permission releases its tracks. A recording captured
  before a newer command cannot supersede that command.
- Exact and accent/punctuation-tolerant destructive searches preserve word
  boundaries and original UTF-16 ranges. Selection rewrites remain limited to
  the selected range, with undo owned by the browser.
- Confirmation remains visible inside Dictation. Cancellation preserves the
  draft; acceptance after a revision change cannot clear the newer text.
- The save indicator distinguishes pending, saved and error states. Storage
  read/parse errors preserve the unreadable stored value; write failures retain
  the in-memory draft, keep TXT export available, prevent switching away and
  request browser confirmation before leaving. Successful persistence clears
  the warning. This is browser-local storage, not an external backup.
- Stopping/replacing speech settles its pending playback promise, removes media
  listeners and prevents a late TTS response from starting cancelled playback.

### Conversation intent and context (consolidation 9.6)

A reflective question about a numbered block continues to the language model;
chat and spoken dialogue both consume the selected laboratory focus. Only an
explicit navigation command moves the reading cursor. Literal block requests
can return an excerpt without moving playback. A reference to recent speech
alone does not save a note: an explicit save/note command is required. Existing
STT compatibility forms (`Tomando a esto que acabo de decir`) remain supported.

External research requires an initial affirmative request clause (including
polite/modal forms). Negated instructions and discussion of quoted search
phrases do not invoke the external bridge. This is a conservative grammar,
not an unrestricted natural-language intent classifier.

In free mode, new reader notes and laboratory focus are supplied only when
explicitly requested (`analizá mis notas`, `analizá el foco del laboratorio`,
`qué significa el bloque 2`). Asking for the document does not opt into notes
or focus. Without a document request, no title/ID/reference catalog is injected.
Previous conversation remains available and may contain earlier cited material.
Document mode retains its current anchoring behavior.

`FUSION_READER_CHAT_MAX_INPUT_CHARS` sets a common input-content character budget
(default 64000, minimum 8192). It applies to local/cloud requests through
`ConversationCore`, including every Supreme/Critical reasoning pass. It preserves
system instructions and the latest question ahead of historical/context material;
oversized material is explicitly marked as clipped and retains both ends. An
oversized question can itself be clipped. This is a character bound, not a token
count or a guarantee that any provider's token window/VRAM allocation will fit.
Per-source limits remain, notes are individually bounded to 2000 characters,
and repeated laboratory user material is not injected twice. Later reasoning
passes retain the latest question as a separate prioritized message.

Retrieved block excerpts and a search-derived focus window retain the source
passage around matching terms, including late matches, with original Unicode
and explicit clipping markers. The full imported source is not rewritten.
Environment reasoning mode accepts `pensamiento_critico`; legacy `contrapunto`
resolves to that mode. No model, personality, voice or default mode is changed.

### Owned cancellation and cleanup (consolidation 9.7)

`run_owned` starts its deadline before child launch/input transfer. On the Linux
runtime, stdin/stdout/stderr use nonblocking pipes and a selector: a child that
never reads stdin cannot prevent timeout or cancellation. Output is drained
while running, retaining at most `output_limit` bytes per output stream and
discarding overflow; there are no growing output temporary files. Timeout and
cancellation terminate/reap the owned process and close all pipes. Captured
output limits do not constrain the child's own memory or arbitrary files it
writes.

Web shutdown first requests job cancellation and starts app background shutdown,
then closes the listening socket and waits for workers. HTTP joins are bounded;
pending workers are reported as errors. Job, app, HTTP and main cleanup phases
are attempted independently after errors. A failed/incomplete shutdown is not
reported as closed. Provider calls without cooperative cancellation can remain
pending until their own timeout; frontend abort is not proof that inference
has stopped. Resource ownership/VRAM policy is a separate concern.

Prefetch executor tracking discards finished history. Idle queue clearing reuses
the current pool; replacements are capped at `max(2, prefetch_workers + 1)` live
tracked pools, retaining those with work for shutdown. At capacity, new work
uses the current pool rather than spawning another replacement.

`AudioArtifact.owned_temporary` defaults to false. Only explicitly owned outputs
may be consumed/deleted after cache publication or segmented synthesis. Cache
artifacts and external/persistent provider paths are not inferred disposable
from their name. Failed cache publication cleans owned provider temporaries in
the facade; failed concatenation cleans its owned output. The desktop listener
probe uses the shared owned-subprocess helper, with architecture checks intact.

Dialogue requests carry an abort controller and generation/turn ownership.
Stopping, restarting or superseding a turn invalidates its late responses,
including when transport ignores abort. Old callbacks cannot clear a newer
turn's processing state or restart its audio. Microphone setup is single-flight;
late permission and partial AudioContext failures release acquired resources.
Capture deadlines have independent timers, PCM has a sample bound, and dictation
also rejects a recording exceeding 4 MiB. Visibility loss stops dialogue/dictation
capture and invalidates pending work, with no automatic restart. Main document
playback remains separate. This is not a microphone test against real hardware.

### Panda resource ownership and GPU admission (consolidation 9.8)

Diego selected a dedicated Ollama process for Panda on 2026-11-17. The desktop
server entrypoint derives `127.0.0.1:11435` (configurable private port) from
`ResourceSettings`, leaving the shared Ollama service and its configuration
untouched. Constructors remain side-effect free; `main` explicitly starts and
owns the daemon. Library callers that do not attach a managed resource controller
have no authority to unload a shared daemon's models.

The private instance reuses the readable model store (`FUSION_READER_OLLAMA_MODELS_DIR`;
standard Linux store when present, otherwise the user store). It does not copy
weights or install models on startup. The child environment sets `OLLAMA_HOST`,
`OLLAMA_MODELS`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`,
`OLLAMA_NOPRUNE=true`, `OLLAMA_NO_CLOUD=true`, and a configurable byte-valued
`OLLAMA_GPU_OVERHEAD`. Existing shared environment/services are not modified.
Explicit future installs still need write permission to the chosen store;
Panda does not grant permissions or change ownership automatically.

An occupied private endpoint is rejected, never adopted. Readiness requires the
live `Popen` child to own the listening socket, checked by PID/file-descriptor
inode and `/proc/net/tcp`, not merely an HTTP response or model name. Failed
readiness disables private-model health, inference, correction and installation
requests; no silent fallback to the shared daemon occurs. Shutdown requests cancellation of that owned child before waiting for HTTP
workers; final cleanup joins/reaps it. A failed API bind does not claim or stop existing
TTS/STT services. Cleanup reports its scope, retained/pending resources and
`all_vram_released=false`; it never infers global GPU freedom from an empty
Ollama model list. Abrupt process death and physical VRAM residency still require
integrated verification; this is a graceful-lifecycle ownership contract.

Chat, dictation models, dictation/media correction, local media translation,
reader voice, dialogue/dictation/media STT, STT fallback and word alignment appear
in the resource inventory. Cloud/research consumers are external, not owned GPU
models. All local Ollama users in the managed application use the private endpoint
and the same priority gate. Media jobs enter as background work; dialogue has
priority at native-request boundaries. The CLI STT fallback is explicitly CPU in
the managed application, including its long-form clone, so it cannot allocate an
unbudgeted second GPU model. Main GPU STT remains available when capacity permits.

GPU policy uses measured device-0 occupancy and a reserve, not a game name.
Telemetry has a two-second child deadline and bounded output. A missing/invalid
sample is unknown, never free memory. Admission estimates are configurable:
`FUSION_READER_GPU_RESERVE_MIB=4096`, `FUSION_READER_LLM_WORKING_SET_MIB=18000`,
`FUSION_READER_SMALL_LLM_WORKING_SET_MIB=6000`. Estimates are not calibrated peak
VRAM sizes or proof of full GPU placement. Memory wait releases the priority gate;
request cancellation/deadlines remain visible. Only idle, verified private models
may be evicted for admission; shared models are never eligible. Status returns
cached observations and does not spawn a hardware probe on every poll.

Startup coexistence uses this budget for any competing application. It reduces
prefetch to its minimum and allows existing automatic CPU voice/STT fallbacks
when needed. It does not change the chosen analysis mode, context or answer limit.
No game or unrelated application is stopped.

STT serialization is cooperative between iterator segments: dialogue priority 0,
word alignment 1, long-form media 2, FIFO within a priority. Segment text/timestamps
remain intact. A running native step is not forcibly preempted. Requests carry
server deadlines (long-form maximum defaults to the media timeout, 7200 seconds;
`FUSION_READER_STT_REQUEST_TIMEOUT_SECONDS` overrides it). Upload reading and audio
conversion share that remaining deadline. Expiry returns explicit HTTP 504 with
`deadline_exceeded` and cleans the request registry, never a falsely complete
partial transcription. The normal client forwards its configured timeout.

Reserved voice ports 7852/7854 are rejected even through numeric loopback aliases,
IPv4-mapped IPv6, wildcard/local spellings, disabled owner checking, or returned
absolute audio URLs. Endpoint recognition does not relax API bind validation.

## Consolidación 9.11: fronteras

HTTP exige Host local único; Origin, cuando existe, debe corresponder al origen local y puerto efectivo. JSON mutador requiere application/json; se rechaza framing ambiguo. Los textos de foco documental se crean como nodos de texto. El MCP curado aplica pertenencia por ruta resuelta tanto al listar/leer como al buscar. Cabeceras de descarga usan fallback ASCII y filename UTF-8. El workflow de higiene es sólo auditoría GET: compara SHA actuales y no borra refs por PR histórico. Tauri experimental admite únicamente micrófono sin cámara desde el origen actual del lector.

## Lucy dictation command completion

- `DictationInstruction` has an additive `replacements` array (empty by default).
  The rule parser can return `kind=replace_many`, with 2–8 `[target, replacement]`
  pairs, `all_matches=true`. Each target is bounded to 500 characters and each
  replacement to 2000; replacement commands over 20000 characters are rejected.
- Multiple replacements match the original draft and apply together. Missing or
  overlapping targets reject the entire batch; one undo restores the full edit.
  A comma/semicolon or `y` separates pairs; quote text containing those separators.
  A single replacement keeps the existing nearest-occurrence behavior.
- An ambiguous rule returns `noop` with `scope=ambiguous_replacement`; the browser
  does not pass it to an LLM for a partial reinterpretation. Model assistants
  continue to use their existing single-operation schema.
- A completed explicit editing command requests one short confirmation through
  `/api/dictation/speak`. It reports an actual edit, no match or refusal truthfully.
  Plain dictation does not request confirmation. The recording cycle is suspended
  during synthesis/playback and resumes afterwards, including TTS/playback errors.
  Stop/close/new requests invalidate late confirmation audio; no system TTS or
  Doctora port is used by the product.
