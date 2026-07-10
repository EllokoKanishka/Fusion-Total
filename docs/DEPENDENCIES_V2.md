# Fusion Reader v2 Dependencies

## Scope

Este documento describe las dependencias y precondiciones operativas de
`fusion_reader_v2/`.

Para defaults absolutos locales, overrides y deuda de portabilidad asociada,
ver también `docs/LOCAL_DEFAULTS_V2.md`.

El snapshot técnico que cierra la consolidación y prioriza la deuda vive en
`docs/CLOSURE_AND_BACKLOG_V2.md`.

No describe ni administra:

- Doctora Lucy
- Antigravity
- Telegram
- OpenClaw `main`

Esos sistemas quedan fuera del runtime propio de Fusion. Cuando aparecen acá, se
mencionan solo como frontera externa o dependencia opcional de convivencia.

## Required Python runtime

### Base runtime

- `python3` en `PATH` es obligatorio para los launchers y para la suite de
  tests.
- Base instalable mínima del repo:
  `requirements/fusion-reader-v2.txt`
- Dependencias opcionales separadas:
  `requirements/fusion-reader-v2-optional.txt`
- Estos archivos no reemplazan ni modelan binarios del sistema, entornos GPU
  dedicados ni sistemas externos.
- La referencia más explícita de versión aparece en los entornos GPU
  documentados y en los scripts que apuntan a `python3.11`, por lo que Python
  3.11 es la base más confirmada para voz GPU y stacks auxiliares.

### Direct dependencies inferred from code

Dependencias confirmadas por imports o por ejecución directa desde el repo:

- `Pillow`
  - `fusion_reader_v2/documents.py`
  - usada para preprocesado de imágenes/OCR
- `python-docx`
  - `fusion_reader_v2/md_to_docx.py`
  - usada para escribir DOCX editables

Estas quedan cubiertas por `requirements/fusion-reader-v2.txt`.

### Optional Python packages in-repo

- `faster-whisper`
  - `scripts/fusion_reader_v2_stt_server.py`
  - usada por el server STT en `8021`
- `openai-whisper`
  - opcional para obtener el comando `whisper` desde un entorno Python local
  - no cambia el hecho de que Fusion hoy usa el binario/CLI como fallback

Estas quedan agrupadas en `requirements/fusion-reader-v2-optional.txt`.

### Optional Python runtimes / dedicated environments

- AllTalk/XTTS GPU
  - `scripts/start_reader_neural_tts_gpu_5090.sh`
  - entorno separado vía `FUSION_READER_GPU_ENV`
  - default absoluto local auditado en `docs/LOCAL_DEFAULTS_V2.md`
  - el blueprint referencia `alltalk_gpu_5090_py311`
- Docling GPU
  - `fusion_reader_v2/pdf_to_docx.py`
  - entorno separado vía `FUSION_READER_DOCLING_GPU_ENV`
  - default relativo actual:
    `runtime/fusion_reader_v2/pdf_engine_benchmark/venvs/docling_gpu_venv`
- AllTalk CPU legacy
  - `scripts/start_reader_neural_tts.sh`
  - entorno externo legado vía `DIRECT_CHAT_ALLTALK_PYTHON`
  - defaults absolutos locales auditados en `docs/LOCAL_DEFAULTS_V2.md`

### Standard-library-heavy core

La mayor parte del core de lectura, TTS HTTP, diálogo HTTP y bridges locales
usa principalmente biblioteca estándar:

- `fusion_reader_v2/reader.py`
- `fusion_reader_v2/tts.py`
- `fusion_reader_v2/dialogue.py`
- `fusion_reader_v2/conversation.py`
- `fusion_reader_v2/service.py`
- `scripts/fusion_reader_v2_server.py`

## Required system binaries

### Required for normal local operation

- `python3`
  - launchers, server, tests
- `curl`
  - healthchecks y launchers (`start_fusion_reader_v2.sh`,
    `open_fusion_reader.sh`)
- `ss`
  - verificador de puertos y checks de listeners
- `ffmpeg`
  - `scripts/fusion_reader_v2_stt_server.py`
  - fallback de concatenación en `fusion_reader_v2/audio_export.py`
- `pdftotext`
  - `fusion_reader_v2/pdf_to_docx.py`
  - ruta rápida de PDF textual
- `pdfinfo`
  - `fusion_reader_v2/pdf_to_docx.py`
  - conteo de páginas
- `pdftoppm`
  - `fusion_reader_v2/pdf_to_docx.py`
  - render para OCR fallback

### Optional but operationally useful

- `lsof`
  - fallback para detectar listeners en `start_fusion_reader_v2.sh`
- `xdg-open` o `sensible-browser`
  - usados por `scripts/open_fusion_reader.sh`
- `whisper`
  - fallback CLI para STT (`fusion_reader_v2/dialogue.py`)
- `tesseract`
  - OCR fallback para PDFs escaneados
- `sqlite3`
  - solo para chequeo informativo externo en
    `scripts/verify_voice_port_isolation.sh`

### Optional / dedicated external tooling

- `docling`
  - esperado dentro del venv GPU de Docling
- `openclaw`
  - fallback de investigación externa aislada
- `uvicorn`
  - esperado dentro de los entornos de AllTalk/XTTS
- `git`
  - útil para trazabilidad de commit en launchers; si no está, el commit queda
    como `unknown`

## Local services and ports

| Servicio | Puerto | Estado |
| --- | --- | --- |
| Fusion Reader UI/API | `8010` | obligatorio para UI local |
| Fusion GPU TTS | `7853` | preferido; requiere owner válido `fusion_reader_v2` |
| Fusion CPU TTS fallback | `7851` | fallback operativo |
| Histórico/no asignado | `7852` | no usar |
| Doctora/Antigravity TTS | `7854` | externo/reservado; Fusion no debe usarlo |
| STT principal | `8021` | `faster_whisper_server` |
| Ollama | `11434` | dependencia local del diálogo |
| SearXNG local | `8080` | primera vía de investigación externa |
| Memory MCP | `stdio` | opcional; no usa puerto HTTP |

### Port rules

- Fusion no usa `7852`.
- Fusion no usa `7854`.
- Fusion solo confía en `7853` si
  `runtime/fusion_reader_v2/tts_owner.json` declara
  `owner=fusion_reader_v2`.
- Si `7853` no está listo con owner válido, Fusion puede caer a `7851`.
- Warnings externos de Doctora no equivalen a fallo estricto del runtime de
  Fusion.

## Environment variables

### Voice / TTS

- `FUSION_READER_GPU_TTS_PORT`
  - puerto TTS GPU de Fusion; default `7853`
- `FUSION_READER_CPU_TTS_PORT`
  - puerto TTS CPU fallback; default `7851`
- `FUSION_READER_TTS_OWNER_FILE`
  - path al owner file de `7853`
- `FUSION_READER_REQUIRE_TTS_OWNER`
  - exige validar owner antes de confiar en `7853`
- `FUSION_READER_ALLTALK_URL`
  - override manual del endpoint TTS
- `FUSION_READER_TTS_TIMEOUT`
  - timeout de requests TTS
- `FUSION_READER_TTS_MAX_INPUT_CHARS`
  - límite opcional de entrada por request TTS
- `FUSION_READER_TTS_SEGMENT_CHARS`
  - segmentación interna para bloques largos
- `FUSION_READER_VOICE`
  - voz default; hoy `female_03.wav`
- `LUCY_TTS_PORT`
  - puerto externo reservado de Doctora; default `7854`
- `DIRECT_CHAT_ALLTALK_PORT`
  - puerto legacy usado como fallback indirecto para `7851`
- `DIRECT_CHAT_ALLTALK_DIR`
  - checkout externo de AllTalk legacy
- `DIRECT_CHAT_ALLTALK_PYTHON`
  - python del entorno legacy CPU
- `DIRECT_CHAT_ALLTALK_FORCE_CPU`
  - workaround legacy CPU; no pertenece al camino GPU principal
- `FUSION_READER_GPU_ENV`
  - entorno GPU para AllTalk y STT launcher
- `FUSION_READER_GPU_TTS_HOST`
  - host del TTS GPU; default `127.0.0.1`
- `FUSION_READER_GPU_TTS_WAIT_SECONDS`
  - espera antes de caer a CPU fallback

### STT

- `FUSION_READER_STT_PROVIDER`
  - default `auto`; valores canónicos `auto`, `server` o `cli`
  - aliases de server: `faster_whisper`, `faster-whisper`; valores inválidos se
    normalizan conservadoramente a `auto`
- `FUSION_READER_STT_ENV`
  - entorno Python del server; prioridad sobre `FUSION_READER_GPU_ENV`, que se
    mantiene como fallback compatible
- `FUSION_READER_STT_URL`
  - base URL del server STT; default `http://127.0.0.1:8021`
- `FUSION_READER_STT_PORT`
  - puerto del server STT; default `8021`
- `FUSION_READER_STT_HOST`
  - host del server STT
- `FUSION_READER_STT_MODEL`
  - modelo STT; default `small`
- `FUSION_READER_STT_DEVICE`
  - `cuda` o `cpu`
- `FUSION_READER_STT_COMPUTE_TYPE`
  - `float16`, `int8`, etc.
- `FUSION_READER_STT_LANGUAGE`
  - idioma STT; default `es`
- `FUSION_READER_STT_TIMEOUT`
  - timeout del fallback CLI
- `FUSION_READER_STT_SERVER_TIMEOUT`
  - timeout del server STT HTTP
- `FUSION_READER_STT_THREADS`
  - threads para `whisper` CLI
- `FUSION_READER_STT_COMMAND`
  - comando del fallback CLI
- `FUSION_READER_STT_BEAM_SIZE`
  - beam principal del server STT
- `FUSION_READER_STT_RECOVERY_BEAM_SIZE`
  - beam de recuperación para transcripción vacía

### Chat / reasoning

- `FUSION_READER_OLLAMA_URL`
  - URL base de Ollama; default `http://127.0.0.1:11434`
- `FUSION_READER_CHAT_MODEL`
  - modelo local principal
- `FUSION_READER_BOHEMIA_CHAT_MODEL`
  - override del perfil Bohemia
- `FUSION_READER_CHAT_TIMEOUT`
  - timeout de requests al chat
- `FUSION_READER_CHAT_THINK`
  - activa thinking nativo
- `FUSION_READER_REASONING_MODE`
  - `normal`, `thinking`, `supreme`, `pensamiento_critico`
- `FUSION_READER_CHAT_NUM_PREDICT`
  - presupuesto general de respuesta
- `FUSION_READER_CHAT_NUM_PREDICT_NORMAL`
- `FUSION_READER_CHAT_NUM_PREDICT_THINKING`
- `FUSION_READER_CHAT_NUM_PREDICT_SUPREME`
- `FUSION_READER_CHAT_NUM_PREDICT_SUPREME_REVIEW`
- `FUSION_READER_CHAT_NUM_PREDICT_SUPREME_FINAL`
- `FUSION_READER_CHAT_NUM_CTX`
- `FUSION_READER_CHAT_TEMPERATURE`
- `FUSION_READER_CHAT_MAX_DOCUMENT_CHARS`
- `FUSION_READER_CHAT_MAX_REFERENCE_CHARS`
- `FUSION_READER_CHAT_MAX_DOCUMENT_EXCERPT_CHARS`
- `FUSION_READER_CHAT_MAX_CHUNKS_PER_DOCUMENT`
- `FUSION_READER_CHAT_REFERENCE_INTRO_CHUNKS`

### External research

- `FUSION_READER_EXTERNAL_RESEARCH_PROVIDER`
  - `auto`, `searxng`, `openclaw`
- `FUSION_READER_SEARXNG_URL`
  - default `http://127.0.0.1:8080`
- `FUSION_READER_SEARXNG_TIMEOUT`
- `FUSION_READER_SEARXNG_ENABLED`
- `FUSION_READER_OPENCLAW_BIN`
  - binario de OpenClaw
- `FUSION_READER_OPENCLAW_AGENT`
  - debe resolver a `fusion-research`
- `FUSION_READER_OPENCLAW_TIMEOUT`
- `FUSION_READER_OPENCLAW_RETRIES`
- `FUSION_READER_OPENCLAW_ENABLED`

### Reader/session/runtime

- `FUSION_READER_V2_PORT`
  - puerto UI/API; default `8010`
- `FUSION_READER_PREFETCH_AHEAD`
- `FUSION_READER_PREFETCH_WORKERS`
- `FUSION_READER_DIALOGUE_TTS_MAX_CHARS`
- `FUSION_READER_FAST_NOTE_ACK`
- `FUSION_READER_FAST_DIALOGUE_ACK`
- `FUSION_READER_DIALOGUE_ALLOW_SUPREME`
- `FUSION_READER_RUNTIME_DIR`
- `FUSION_READER_LOG_DIR`
- `FUSION_READER_LOG_FILE`
- `FUSION_READER_PID_FILE`
- `FUSION_READER_STARTUP_WAIT_SECONDS`
- `FUSION_READER_AUDIO_CACHE_VERSION`
- `FUSION_READER_PROFILE`

### PDF / OCR

- `FUSION_READER_DOCLING_GPU_ENV`
  - override del venv Docling GPU
- `FUSION_READER_OCR_DPI`
- `FUSION_READER_OCR_WORKERS`

### GPU coexistence

- `FUSION_READER_GPU_GUARD`
- `FUSION_READER_ALLOW_GPU_WITH_GAMES`
- `FUSION_READER_GPU_CONFLICT_POLICY`
- `FUSION_READER_GAME_COEXISTENCE`
- `FUSION_READER_CHAT_NUM_PREDICT_WITH_GAME`
- `FUSION_READER_CHAT_NUM_CTX_WITH_GAME`
- `FUSION_READER_STT_COMPUTE_TYPE_WITH_GAME`

### External boundary helpers

- `DOCTORA_LUCY_ROOT`
  - root externo que el verificador consulta solo como frontera informativa

Defaults absolutos locales y repo-relativos relevantes:

- `FUSION_READER_GPU_ENV`
- `DIRECT_CHAT_ALLTALK_DIR`
- `DIRECT_CHAT_ALLTALK_PYTHON`
- `DOCTORA_LUCY_ROOT`
- `FUSION_READER_STT_COMMAND`
- `FUSION_READER_DOCLING_GPU_ENV`

Su auditoría consolidada vive en `docs/LOCAL_DEFAULTS_V2.md`.

## Startup scripts

### `scripts/open_fusion_reader.sh`

- wrapper de escritorio;
- intenta levantar STT, TTS y server si faltan;
- abre `http://127.0.0.1:8010/` en navegador;
- usa GPU TTS si `7853` está listo con owner válido; si no, puede arrancar
  fallback CPU.

### `scripts/start_fusion_reader_v2.sh`

- levanta solo la UI/API de Fusion en `8010`;
- selecciona `FUSION_READER_ALLTALK_URL` entre `7853` y `7851`;
- valida owner de `7853`;
- registra commit, PID y log persistente;
- no toca `7852` ni debe reclamar `7854`.

### `scripts/start_reader_neural_tts_gpu_5090.sh`

- levanta AllTalk/XTTS GPU reservado para Fusion;
- escribe `runtime/fusion_reader_v2/tts_owner.json`;
- se niega a reclamar `7853` si ya pertenece a otro proceso;
- depende de un checkout externo de AllTalk y de un venv GPU específico.

### `scripts/start_fusion_reader_v2_stt.sh`

- levanta el server STT local en `8021`;
- usa `scripts/fusion_reader_v2_stt_server.py`;
- resuelve Python como `FUSION_READER_STT_ENV` → `FUSION_READER_GPU_ENV` →
  default local histórico, sin crear ni mover entornos;
- puede degradar a CPU/int8 si la guardia de convivencia GPU lo exige.

### `scripts/verify_voice_port_isolation.sh`

- verifica la frontera de puertos de voz;
- separa:
  - `FUSION STRICT CHECKS`
  - `EXTERNAL BOUNDARY / DOCTORA INFO`
  - `FINAL RESULT`
- devuelve código distinto de cero solo ante fallo estricto de Fusion.

## Optional / external boundaries

- Doctora Lucy es externa a este repo.
- Antigravity es externo a este repo.
- Telegram es externo a este repo.
- OpenClaw `main` no debe tocarse para resolver Fusion.
- OpenClaw `fusion-research` puede usarse solo en la ruta aislada de
  investigación externa ya documentada.
- El checkout de AllTalk legacy (`DIRECT_CHAT_ALLTALK_DIR`) es externo a este
  repo.
- Los defaults absolutos locales no son parte portable del repo y deben leerse
  como compatibilidad del laboratorio actual, no como contrato universal.
- Warnings documentales o de memoria de Doctora no implican que Fusion esté
  roto.

## Verification commands

Requirements mínimos del repo:

```bash
python3 -m pip install -r requirements/fusion-reader-v2.txt
python3 -m pip install -r requirements/fusion-reader-v2-optional.txt  # opcional
```

Límites del manifiesto:

- no instala `curl`, `ffmpeg`, `pdftotext`, `pdfinfo`, `pdftoppm`, `tesseract`,
  `ss` ni otros binarios del sistema;
- no instala AllTalk GPU, Docling GPU ni otros entornos dedicados;
- no reemplaza `verify` ni `smoke`;
- no cambia el runtime actual del repo por sí mismo.

Validación mínima actual:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
./scripts/verify_voice_port_isolation.sh
./scripts/smoke_fusion_reader_v2.sh
```

El smoke nuevo es diagnóstico no invasivo:

- no mata procesos;
- no inicia servicios pesados;
- no escribe owner files;
- no reemplaza tests unitarios;
- reporta `OK_WITH_WARNINGS` cuando parte del stack local no está levantado.

Validación histórica aún útil:

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

Smoke local breve:

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
curl -s http://127.0.0.1:8010/api/status
./scripts/verify_voice_port_isolation.sh
```

Healthchecks útiles:

```bash
curl -s http://127.0.0.1:7853/api/ready
curl -s http://127.0.0.1:7851/api/ready
curl -s http://127.0.0.1:8021/health
curl -s http://127.0.0.1:11434/api/tags
curl -s "http://127.0.0.1:8080/search?q=test&format=json" | head -c 300
```

## Known gaps

- ahora existe un manifiesto mínimo instalable en `requirements/`, pero sigue
  faltando un lockfile o empaquetado más fuerte tipo `pyproject.toml` para toda
  la ruta v2;
- varios scripts todavía traen defaults absolutos locales para entornos externos
  (`FUSION_READER_GPU_ENV`, `DIRECT_CHAT_ALLTALK_DIR`,
  `DIRECT_CHAT_ALLTALK_PYTHON`);
- el camino STT aceptado sigue siendo híbrido:
  `8021` principal, `whisper_cli` como fallback;
- `fusion_reader_v2/service.py` sigue concentrando muchas responsabilidades;
- la ruta PDF/OCR mezcla camino textual rápido, OCR fallback y Docling GPU; el
  manifiesto nuevo cubre solo el tramo Python del repo y no automatiza todavía
  los binarios ni el entorno dedicado de Docling;
- `verify_voice_port_isolation.sh` sigue leyendo frontera externa de Doctora por
  diseño informativo;
- el runtime GPU de AllTalk y el runtime GPU de Docling siguen siendo entornos
  dedicados y no vendorizados por el repo.
- el discovery actual de este branch cuenta `345` tests;
- el conteo histórico previo del tramo de consolidación había quedado en
  `339`, pero ya no es la referencia operativa actual;
- `tests/test_local_defaults_v2.py` agrega una guardia estructural para
  defaults locales, overrides y fronteras documentales;
- la diferencia de conteo responde al estado del branch y a nuevas guardias de
  documentación, no a un bug nuevo del loader.
