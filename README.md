# Fusion Reader v2

Lector conversacional por voz neural. Fusion Reader v2 prioriza una sola cosa:
que leer en voz alta se sienta humano, claro y continuo.

El proyecto convive con un prototipo legacy, pero la ruta viva de producto está
en `fusion_reader_v2/`.

## Inicio rápido

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
```

UI:

```text
http://127.0.0.1:8010/
```

## Stack actual

- Fusion Reader v2: `fusion_reader_v2/`
- Servidor/UI: `scripts/fusion_reader_v2_server.py`
- TTS principal Fusion: `http://127.0.0.1:7853`
- TTS fallback CPU: `http://127.0.0.1:7851`
- TTS Doctora/Antigravity: `http://127.0.0.1:7854` (reservado, no usar)
- STT principal: `http://127.0.0.1:8021`
- STT provider default: `auto` (server sano primero, Whisper CLI fallback).
  `server` exige `8021`; `cli` usa `FUSION_READER_STT_COMMAND` y el launcher no
  intenta iniciar el server.
- LLM local: Ollama `qwen3:14b-q8_0`
- Investigación externa:
  - default `auto`
  - `SearXNG` local primero
  - `OpenClaw` agente `fusion-research` solo como fallback

## Fronteras críticas

- La lectura no depende del LLM.
- Fusion no usa `7852`.
- Fusion no usa `7854`.
- Fusion no toca `OpenClaw main`.
- Fusion no depende de Brave/web_search global para la búsqueda externa normal.
- `Antigravity/Doctora/Telegram` es otro sistema de la máquina.

## Documentación principal

- Reglas raíz: [AGENTS.md](AGENTS.md)
- Continuidad corta: [FUSION_READER_V2_STATE.md](FUSION_READER_V2_STATE.md)
- Arquitectura vigente: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Operación diaria: [docs/OPERATIONS.md](docs/OPERATIONS.md)
- Dependencias v2: [docs/DEPENDENCIES_V2.md](docs/DEPENDENCIES_V2.md)
- Defaults locales: [docs/LOCAL_DEFAULTS_V2.md](docs/LOCAL_DEFAULTS_V2.md)
- Cierre y backlog vigente: [docs/CLOSURE_AND_BACKLOG_V2.md](docs/CLOSURE_AND_BACKLOG_V2.md)
- Convivencia Fusion/OpenClaw/SearXNG: [docs/OPENCLAW_SEARXNG_COEXISTENCE.md](docs/OPENCLAW_SEARXNG_COEXISTENCE.md)
- Historia: [docs/HISTORY.md](docs/HISTORY.md)
- Personalidad vigente: [docs/PERSONALITY.md](docs/PERSONALITY.md)
- Auditoría de biblioteca: [docs/LIBRARY_AUDIT.md](docs/LIBRARY_AUDIT.md)

Documentos históricos de diseño siguen disponibles, pero ya no son la fuente
canónica de estado operativo:

- `FUSION_READER_V2_BLUEPRINT.md`
- `FUSION_READER_V2_DIALOGUE.md`
- `FUSION_READER_V2_PERFORMANCE.md`
- `FUSION_READER_V2_PERSONALITY_WORKBOOK.md`

## Verify

```bash
python3 -m pip install -r requirements/fusion-reader-v2.txt
python3 -m unittest tests.test_fusion_reader_v2 -v
./scripts/verify_voice_port_isolation.sh
./scripts/smoke_fusion_reader_v2.sh
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

Para dependencias, servicios locales, puertos y variables de entorno, usar
[docs/DEPENDENCIES_V2.md](docs/DEPENDENCIES_V2.md) como manifiesto único.

Para defaults absolutos locales, overrides y deuda operativa asociada, usar
[docs/LOCAL_DEFAULTS_V2.md](docs/LOCAL_DEFAULTS_V2.md).

Base Python instalable del repo:

- `requirements/fusion-reader-v2.txt`: camino core v2
- `requirements/fusion-reader-v2-optional.txt`: STT/fallbacks opcionales

No cubre binarios de sistema ni entornos GPU dedicados, y no cambia el runtime
actual por sí mismo.

Validación vigente al cierre de consolidación:

```text
test discovery completa del PR documental de cierre: 365 tests OK
```

Nota operativa:

- `./scripts/verify_voice_port_isolation.sh` separa ahora los checks estrictos de Fusion de los warnings informativos de frontera con Doctora.
- Si `7853` no está levantado con owner válido, puede devolver `OK_WITH_WARNINGS` sin confundir eso con un uso indebido de `7852` o `7854`.
- Faltantes documentales o de memoria en Doctora aparecen como warnings externos y ya no derriban por sí solos la validación estricta del repo Fusion.
- `./scripts/smoke_fusion_reader_v2.sh` es diagnóstico no invasivo: no levanta ni mata servicios, no reemplaza tests unitarios y puede terminar en `OK_WITH_WARNINGS` si parte del stack no está arriba.
- La referencia operativa del cierre vive en
  [docs/CLOSURE_AND_BACKLOG_V2.md](docs/CLOSURE_AND_BACKLOG_V2.md).
  - `tests/test_local_defaults_v2.py` mantiene auditados los defaults locales y sus overrides
