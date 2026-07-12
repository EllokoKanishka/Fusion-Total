# Fusion Reader v2 — Root Agent Rules

## Identity

This repo is a **voice-first conversational reader**.

The active product is not a general assistant, browser agent, desktop
automation tool, YouTube integrator, or workflow orchestrator. It is a
specialist in building a neural-voice reading product.

## North Star

The product succeeds only if reading by voice feels human, clear, comfortable,
and continuous.

Everything else exists to support that:

- library
- document ingestion
- chunking
- reading state
- bookmarks
- notes
- conversation about the current reading
- TTS/STT

If voice quality or voice latency is bad, fix that before polishing secondary
features.

## Current Architecture Direction

The old prototype remains as lab and compatibility reference.

The active product lives under:

```text
fusion_reader_v2/
```

Read these first before architectural changes:

1. `AGENTS.md`
2. `FUSION_READER_V2_STATE.md`
3. `docs/ARCHITECTURE.md`
4. `docs/OPERATIONS.md`
5. `docs/CONTRACTS.md`
6. Historical documents only as reference.

## Allowed Product Operations

- Load TXT/MD/PDF/DOCX/ODT reading documents.
- Split text into natural voice chunks.
- Read, pause, resume, repeat, navigate, bookmark.
- Generate neural TTS.
- Cache generated audio.
- Prefetch upcoming chunks.
- List and test voices.
- Accept voice/STT commands for reader control.
- Chat only about the active document, current chunk, recent reading context,
  notes, or explicit laboratory context.
- Route local/cloud models only for reader conversations.
- Run explicit external research only when the user clearly asks to search
  outside the reader, using Fusion's isolated provider path.

## Prohibited Product Operations

- No browser automation as a product feature.
- No YouTube integration as a product feature.
- No n8n workflows.
- No desktop file operations as product features.
- No general assistant tasks.
- No unrelated tools or browsing inside the product.
- No SillyTavern full transplant.
- No fixes to Fusion by mutating Antigravity, Doctora, Telegram, or OpenClaw
  `main`.
- No Brave/global `web_search` changes as a shortcut for Fusion.

## External Research Boundary

Fusion has a narrow exception for web research:

```text
default external provider: auto
auto order: SearXNG local -> OpenClaw agent fusion-research fallback
```

Rules:

- External research only activates under explicit requests like
  `busca en internet`, `investiga afuera`, `busca fuentes`, `busca tesis`.
- Reading must remain isolated from that lane.
- `SearXNG` local is the preferred path.
- `OpenClaw` fallback must use `fusion-research`, never `main`.
- Never fix Fusion by changing Brave or global OpenClaw web search.
- Antigravity/Doctora/Telegram is another system and must not be touched.

## Voice Engine

Primary TTS direction:

```text
AllTalk/XTTS
Fusion GPU URL: http://127.0.0.1:7853
Fusion CPU fallback: http://127.0.0.1:7851
Default voice: female_03.wav
Language: es
```

Startup helper:

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
```

Port isolation:

```text
Fusion Reader v2 owns 7853 and requires runtime/fusion_reader_v2/tts_owner.json
with owner=fusion_reader_v2 before trusting a Ready response.
Doctora Lucy/Antigravity owns 7854.
7852 is historical/unassigned and must not be used for new automatic starts.
```

The old CPU workaround `DIRECT_CHAT_ALLTALK_FORCE_CPU=1` belongs only to legacy
fallbacks. Do not mutate the existing `ebook2audiobook` Python environment while
fixing GPU voice. Use the isolated GPU environment documented in the blueprint.

## v2 Engineering Rules

- Prefer new code in `fusion_reader_v2/`.
- Keep the monolith/prototype working until v2 surpasses it.
- Use provider contracts for TTS. Fusion must depend on its own `TTSProvider`,
  not on SillyTavern internals.
- Cache audio by text + voice + language.
- Prefetch next chunk while current audio plays.
- Keep chunks natural for spoken reading, not arbitrary byte slices.
- Keep UI and API reader-focused and minimal.
- Add tests for core behavior before expanding UI.

## Recovery Procedure

When resuming work:

1. Read this file.
2. Read `FUSION_READER_V2_STATE.md`.
3. Read `docs/ARCHITECTURE.md`.
4. Read `docs/OPERATIONS.md` and `docs/CONTRACTS.md`.
5. Run `git status --short`.
6. Do not revert user changes.
7. Continue v2 under `fusion_reader_v2/` unless the user explicitly asks to fix
   the old prototype.

## Testing

v2 tests:

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
```

Legacy reader safety tests:

```bash
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```
