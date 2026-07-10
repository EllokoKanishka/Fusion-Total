# Fusion Reader v2 Local Defaults

## Scope

Este documento audita los defaults locales y absolutos que siguen existiendo en
Fusion Reader v2.

Objetivo:

- volverlos explícitos;
- dejar claro qué variable permite override;
- separar defaults del repo vs defaults externos a la máquina;
- recordar que estos valores no vuelven portable al repo por sí solos.

Reglas:

- si una variable de entorno ya está seteada, manda la variable;
- este documento no cambia runtime por sí mismo;
- no autoriza tocar Doctora, Antigravity, Telegram ni OpenClaw `main`;
- `verify` y `smoke` no deben interpretarse como rotos solo porque falte un
  sistema externo opcional.

## Active machine-local defaults

| Uso | Variable / selector | Default actual | Alcance | Override |
| --- | --- | --- | --- | --- |
| AllTalk GPU env | `FUSION_READER_GPU_ENV` | `/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311` | externo a este repo | sí |
| AllTalk legacy checkout | `DIRECT_CHAT_ALLTALK_DIR` | `/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts` | externo a este repo | sí |
| AllTalk legacy Python | `DIRECT_CHAT_ALLTALK_PYTHON` | `/home/lucy-ubuntu/ebook2audiobook/python_env/bin/python` | externo a este repo | sí |
| Doctora boundary root | `DOCTORA_LUCY_ROOT` | `/home/lucy-ubuntu/Escritorio/doctora-lucy` | externo; solo frontera informativa de `verify_voice_port_isolation.sh` | sí |
| Whisper CLI fallback | `FUSION_READER_STT_COMMAND` | `whisper` en `PATH`, luego `/home/linuxbrew/.linuxbrew/bin/whisper`, `/usr/local/bin/whisper`, `/usr/bin/whisper` | local del host; fallback STT | sí |

Notas:

- estos defaults existen por compatibilidad con el laboratorio actual;
- no deben borrarse sin fallback compatible;
- no deben asumirse como layout portable del repo.

## Repo-relative defaults

Estos defaults quedan dentro del repo o de su runtime y son más portables:

| Uso | Variable / selector | Default actual |
| --- | --- | --- |
| Docling GPU env | `FUSION_READER_DOCLING_GPU_ENV` | `runtime/fusion_reader_v2/pdf_engine_benchmark/venvs/docling_gpu_venv` |
| TTS owner file | `FUSION_READER_TTS_OWNER_FILE` | `runtime/fusion_reader_v2/tts_owner.json` |
| Runtime dir | `FUSION_READER_RUNTIME_DIR` | `runtime/fusion_reader_v2/` |

`FUSION_READER_DOCLING_GPU_ENV` queda como ejemplo de default preferible:
overrideable y repo-relativo cuando no hace falta salir a `/home`.

## Where each default is used

- `FUSION_READER_GPU_ENV`
  - `scripts/start_reader_neural_tts_gpu_5090.sh`
  - `scripts/bootstrap_alltalk_gpu_5090.sh`
  - `scripts/start_fusion_reader_v2_stt.sh`
  - `scripts/check_gpu_5090_env.py`
- `DIRECT_CHAT_ALLTALK_DIR`
  - `scripts/start_reader_neural_tts.sh`
  - `scripts/start_reader_neural_tts_gpu_5090.sh`
  - `scripts/bootstrap_alltalk_gpu_5090.sh`
  - `scripts/verify_voice_port_isolation.sh`
- `DIRECT_CHAT_ALLTALK_PYTHON`
  - `scripts/start_reader_neural_tts.sh`
- `DOCTORA_LUCY_ROOT`
  - `scripts/verify_voice_port_isolation.sh`
- `FUSION_READER_STT_COMMAND`
  - `fusion_reader_v2/dialogue.py`
  - `scripts/start_fusion_reader_v2.sh` lo refleja en logging operativo

## Operational interpretation

- Si falta `DIRECT_CHAT_ALLTALK_DIR`, `DIRECT_CHAT_ALLTALK_PYTHON` o
  `FUSION_READER_GPU_ENV`, fallan solo los launchers que dependen de esos
  entornos externos.
- Si falta `DOCTORA_LUCY_ROOT`, `verify_voice_port_isolation.sh` puede terminar
  en `OK_WITH_WARNINGS` o `OK_WITH_EXTERNAL_WARNINGS` sin convertir eso en un
  fallo estricto del runtime propio de Fusion.
- Si `FUSION_READER_STT_COMMAND` no está seteada, Fusion intenta primero el
  binario `whisper` resoluble en `PATH` y recién después candidatos locales
  conocidos.
- `scripts/smoke_fusion_reader_v2.sh` es diagnóstico no invasivo: no crea
  rutas externas, no levanta esos sistemas externos y no debe fallar solo por
  su ausencia.

## Known debt

- siguen existiendo defaults absolutos de laboratorio en launchers de voz y
  utilidades GPU;
- la compatibilidad con el AllTalk legacy todavía depende de un checkout y un
  entorno Python fuera del repo;
- el fallback local de `whisper` sigue reflejando rutas típicas del host actual;
- el verificador de puertos mantiene una lectura informativa de Doctora porque
  esa frontera es parte del aislamiento operativo, aunque Doctora siga fuera de
  alcance para cambios.
