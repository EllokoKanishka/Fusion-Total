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

- Reglas raíz: [AGENTS.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/AGENTS.md:1)
- Continuidad corta: [FUSION_READER_V2_STATE.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/FUSION_READER_V2_STATE.md:1)
- Arquitectura vigente: [docs/ARCHITECTURE.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/docs/ARCHITECTURE.md:1)
- Operación diaria: [docs/OPERATIONS.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/docs/OPERATIONS.md:1)
- Convivencia Fusion/OpenClaw/SearXNG: [docs/OPENCLAW_SEARXNG_COEXISTENCE.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/docs/OPENCLAW_SEARXNG_COEXISTENCE.md:1)
- Historia: [docs/HISTORY.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/docs/HISTORY.md:1)
- Personalidad vigente: [docs/PERSONALITY.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/docs/PERSONALITY.md:1)
- Auditoría de biblioteca: [docs/LIBRARY_AUDIT.md](/home/lucy-ubuntu/Escritorio/Fusion%20Total/docs/LIBRARY_AUDIT.md:1)

Documentos históricos de diseño siguen disponibles, pero ya no son la fuente
canónica de estado operativo:

- `FUSION_READER_V2_BLUEPRINT.md`
- `FUSION_READER_V2_DIALOGUE.md`
- `FUSION_READER_V2_PERFORMANCE.md`
- `FUSION_READER_V2_PERSONALITY_WORKBOOK.md`

## Verify

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
./scripts/verify_voice_port_isolation.sh
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

Validación vigente:

```text
tests.test_fusion_reader_v2: 240 OK
legacy reader safety: 35 tests OK
test discovery completa: 334 tests OK
```

Nota operativa:

- `./scripts/verify_voice_port_isolation.sh` depende del estado real de `7853` con `tts_owner.json` válido y de la frontera local con Doctora; no siempre da `OK` en una máquina parcialmente levantada o con artefactos externos incompletos.
