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
- `POST /api/dictation/assist`: accepts one explicit command plus a bounded
  draft excerpt and selection offsets, and returns one validated instruction;
- instruction kinds are additive and currently include `dictate`, `insert`,
  `replace`, `replace_selection`, `delete`, `delete_from`, `clear`, `undo`,
  `redo`, `read`, `stop_listening` and `noop`.

Audio dictation treats an utterance as a command only when it begins with the
wake word `Lucy`; otherwise it remains literal dictation. Typed commands sent
through `/api/dictation/interpret` remain explicit and do not need the wake
word.

The draft itself is browser-owned. Optional model assistance accepts at most a
12,000-character excerpt around the caret. It cannot return a complete draft or
an unbounded operation; the browser validates and applies the operation while
retaining undo ownership. Selecting OpenAI is explicit because that excerpt is
sent to the isolated cloud provider.

## HTTP Routes

The v2 routes listed in `docs/ARCHITECTURE.md` remain supported. Health is
split into liveness and readiness without removing `/health`, `/api/status`, or
`/api/build`. Static assets may move out of Python, but `/` continues serving
the same reader UI.

`POST /api/quick-text` accepts `text`, optional `title`, and optional
`start_offset`. It loads a transient main document through the normal reader
pipeline and exposes `document.transient=true`; the source text is deliberately
excluded from the recoverable session snapshot.

Media routes:

- `POST /api/media/transcribe`: multipart `file`; transcripción y PDF (compatibilidad).
- `POST /api/media/translate`: multipart `file`; acepta los flags de query
  `original_pdf`, `translated_pdf` y `spanish_audio`. Por compatibilidad,
  si se omiten genera las tres salidas. Al menos una debe estar activa.
- `GET /api/media/status[/<job_id>]`: último job o job específico.
- `POST /api/media/cancel/<job_id>`: cancelación cooperativa.
- `POST /api/media/mount/<job_id>`: monta el texto terminado como documento principal.
- `GET /api/media/download/<job_id>/{pdf|translated-pdf|audio}`: artefacto validado.

Los jobs usan estados `queued`, `running`, `canceling`, `done`, `cancelled` y
`error`; devuelven sólo preview y conteos, no la transcripción completa. Hay un
único job multimedia activo. La carga default máxima es 2 GiB
(`FUSION_READER_MEDIA_MAX_BYTES`) y el timeout default es 2 horas
(`FUSION_READER_MEDIA_TIMEOUT_SECONDS`).

## System Boundaries

Reading never requires STT, an LLM, or research. External research activates
only on an explicit request and uses SearXNG first, then the isolated OpenClaw
`fusion-research` agent. Fusion never routes through OpenClaw `main`, port
7852, or port 7854.

OpenAI dialogue is a separate, explicit capability and may only use the
`fusion-dialogue` agent. It changes the conversation `ChatProvider` while STT,
TTS, reading and multimedia processing remain local. A provider failure must
not interrupt reader controls and must not silently fall back to another model.
