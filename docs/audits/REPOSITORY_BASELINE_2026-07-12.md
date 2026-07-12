# Repository Baseline - 2026-07-12

This snapshot was captured before the total consolidation branch was created.
It contains repository and machine metadata only. Personal filenames, document
contents, environment values, tokens, and secrets are intentionally excluded.

## Checkpoint

| Item | Value |
| --- | --- |
| Repository | `EllokoKanishka/Fusion-Total` |
| Baseline branch | `main` |
| Baseline commit | `19ccaa9bc36b09c008af8245586c82e262ee081a` |
| Working tree | clean |
| Open pull requests | 0 |
| Open issues | `#1 Auditoria y consolidacion de Fusion Reader v2 antes de nuevas features` |
| Tracked files | 146 |
| Tracked bytes | 1,610,879 |
| Tracked-tree digest | `1fa031191a12aee6be3b8a289a58371940b516c1a4929b72ae874a58f3d51d7a` |

The tree digest is the SHA-256 of `git ls-tree -r --long HEAD` and makes the
complete baseline file tree reproducible without copying generated or ignored
files into this document.

## Tracked Tree

| Root | Files | Lines | Bytes |
| --- | ---: | ---: | ---: |
| `fusion_reader_v2/` | 14 | 9,602 | 429,124 |
| `scripts/` | 48 | 12,098 | 464,259 |
| `tests/` | 30 | 9,912 | 469,941 |
| `docs/` | 16 | 2,196 | 88,594 |
| `app/` | 8 | 1,508 | 63,265 |
| `requirements/` | 2 | 17 | 594 |
| `config/` | 7 | 232 | 5,971 |
| `library/` | 4 | 42 | 1,366 |
| `runtime/` | 1 | 50 | 2,123 |

Other tracked roots are `agente/` (3 files), `assets/` (2), and 12 root-level
files. The only tracked runtime file is
`runtime/fusion_reader_v2/audit_consolidation_ab98a44.md`; all live runtime
artifacts are ignored.

### Active package sizes

| File | Lines | Bytes |
| --- | ---: | ---: |
| `fusion_reader_v2/service.py` | 3,774 | 182,422 |
| `fusion_reader_v2/conversation.py` | 1,058 | 55,658 |
| `fusion_reader_v2/documents.py` | 923 | 35,626 |
| `fusion_reader_v2/pdf_to_docx.py` | 742 | 27,829 |
| `fusion_reader_v2/md_to_docx.py` | 722 | 32,200 |
| `fusion_reader_v2/tts.py` | 406 | 16,817 |
| `fusion_reader_v2/openclaw_bridge.py` | 386 | 18,041 |
| `fusion_reader_v2/dialogue.py` | 293 | 12,718 |
| `fusion_reader_v2/reader.py` | 288 | 9,810 |
| `fusion_reader_v2/local_web_bridge.py` | 288 | 12,048 |
| `fusion_reader_v2/notes.py` | 273 | 9,052 |
| `fusion_reader_v2/metrics.py` | 228 | 9,559 |
| `fusion_reader_v2/audio_export.py` | 169 | 5,423 |
| `fusion_reader_v2/__init__.py` | 52 | 1,921 |

The two largest active concentration points are
`scripts/fusion_reader_v2_server.py` (4,108 lines) and
`fusion_reader_v2/service.py` (3,774 lines).

## Imports And Public Surface

Current consumers import the package root and these public modules:

```text
fusion_reader_v2
fusion_reader_v2.audio_export
fusion_reader_v2.conversation
fusion_reader_v2.dialogue
fusion_reader_v2.documents
fusion_reader_v2.local_web_bridge
fusion_reader_v2.md_to_docx
fusion_reader_v2.openclaw_bridge
fusion_reader_v2.pdf_to_docx
fusion_reader_v2.reader
```

The server imports `AudioCache`, `FusionReaderV2`, `VoiceMetricsStore`, document
import helpers, and PDF-to-DOCX contracts from these paths. These imports are
compatibility constraints for the consolidation.

## Tests And Automation

- Python test modules: 24 plus shared helpers and fixtures.
- Python test methods discovered by static inventory: 365.
- Baseline discovery run: 406 tests passed in 30.266 seconds on Python 3.13.12.
- The difference between 365 static methods and 406 executed cases comes from
  `attach_legacy_tests(...)`, which dynamically copies legacy methods into eight
  canonical classes. Removing this duplication is a consolidation requirement.
- JavaScript assets/tests: `scripts/fusion_reader_v2_busy_controls.js` and
  `tests/busy_controls.test.js` under the existing minimal `package.json`.
- Active shell scripts: 21 tracked `.sh` files.
- Existing primary suites: v2, legacy reader safety, server API, audio export,
  lifecycle, conversation/dialogue, TTS/STT, notes/metrics, research, and PDF
  conversion.

Installed baseline tools:

| Tool | Version/state |
| --- | --- |
| Git | 2.43.0 |
| GitHub CLI | 2.87.3, authenticated as repository owner |
| Python | 3.13.12 |
| Node | 25.8.0 |
| npm | 11.11.0 |
| shellcheck | missing |
| ruff | missing |
| mypy | missing |
| coverage | missing |

## Configuration Inventory

The active tree contains more than 80 `FUSION_READER_*` names spread across
Python, shell, JavaScript, and documentation. Major groups are roots/runtime,
TTS, STT, Ollama/chat, external research, GPU setup, OCR, prefetch, profile,
reasoning, and lifecycle metadata. There is no central typed settings object.

Active absolute local defaults remain in launchers and diagnostics for the GPU
environment, historical AllTalk checkout, CPU Python, and the read-only Doctora
boundary. A launcher compatibility test also reads
`/home/lucy-ubuntu/.local/bin/fusion-reader-launcher`. Historical documents
contain additional dated absolute paths. These are portability debt, not
authorization to mutate external environments.

## Runtime And Ports (Read-Only)

Observed listeners at snapshot time:

| Port | State | Boundary |
| ---: | --- | --- |
| 8010 | listening on loopback | Fusion UI/API |
| 7851 | not listening | Fusion CPU fallback |
| 7852 | not listening | forbidden/unassigned |
| 7853 | listening on loopback | Fusion GPU TTS |
| 7854 | listening on loopback | Doctora, external and untouched |
| 8021 | listening on loopback | Fusion STT |
| 11434 | listening on loopback | Ollama |
| 8080 | listening on loopback | SearXNG |

Relevant processes were inspected read-only. Fusion UI, Fusion TTS, Fusion STT,
Ollama, SearXNG, and the external 7854 owner were already running. No process
was started, stopped, signalled, or otherwise changed while taking this
snapshot.

## Personal Data Guards

Each digest below hashes a sorted stream of relative filename, byte size, and
mtime. The stream itself is not committed, so the digest can detect accidental
changes while keeping personal names out of Git.

| Root | Files | Bytes | Metadata SHA-256 |
| --- | ---: | ---: | --- |
| `~/Descargas` | 709 | 9,974,301,498 | `e13044097f3a5786f03df4ea80b914499b6ecbd5f8d6405de119b1408d9e707a` |
| `~/Downloads` | missing | 0 | n/a |
| live Fusion runtime | 120,417 | 30,980,734,139 | `890716189b93746d9a7d2d789324400f36617ce15911e52e53238abab6ec381f` |
| reader library | 8 | 2,429 | `883baada80e806b5e0114c03aef0c179efb2c5e1dec8bbf651a435a75e260f11` |
| live notes | 7 | 8,314 | `5e7e4fdca8558e4aa408c0386800eac988bbc9d8d9ef6c09fc7a1ecd864ce661` |

The live runtime is large because it includes ignored model/tool outputs and
cache data. Consolidation tests must never use these roots.

The baseline suite left Downloads, library, and notes hashes unchanged. The
live runtime digest changed because `stt_server.log` was written by the already
running STT process and `session_state.json` received a new mtime during test
module import/shutdown. This is baseline evidence of the server module's global
application side effect; the file was not read, deleted, or restored. Future
suites must override `HOME` and all Fusion roots before importing the server.

## Branches And Recent History

Local branches at baseline:

```text
main                                      19ccaa9 (origin/main)
chore/document-local-default-overrides   43c0579 (origin tracking branch)
```

Additional remote branch:

```text
origin/codex/fix-inconsistencies-and-apply-improvements  44bb17b
```

The last 30 commits run from `19ccaa9` (2026-07-11) through `05dfcf4`
(2026-05-06). Recent merged work covers test isolation, deterministic audio
lifecycle, v2 closure documentation, reader flow repair, STT consolidation,
requirements, smoke checks, dependency documentation, and port isolation.

## Baseline Risks

- `FusionReaderV2` and the HTTP server are monolithic concentration points.
- Importing the current server module constructs global application state.
- Configuration and roots are distributed across modules and launchers.
- Live services and a very large ignored runtime make hermetic roots mandatory.
- The active Python is newer than the requested support matrix; clean Python
  3.11/3.12 validation must be established separately.
- Static-quality and coverage tools are not installed in the current global
  environment and must be supplied through project development dependencies.
- Historical and active artifacts are mixed across `scripts/`, root documents,
  `app/`, `scratch/`, and ignored runtime directories.
