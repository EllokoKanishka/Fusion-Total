# OpenAI en el diálogo de Fusion Reader v2

## Alcance

Esta integración cambia solamente el `ChatProvider`. El lector, los bloques,
STT, TTS, cache, navegación y notas siguen siendo locales. La respuesta de
OpenAI vuelve como texto y la pronuncia la voz AllTalk/XTTS ya seleccionada.
El procesamiento de audio, video y documentos también queda fijado al provider
local aunque el diálogo esté usando OpenAI.

Dictado puede seleccionar por separado `OpenAI nano` para clasificar una orden
editorial no reconocida. Reutiliza el agente aislado `fusion-dialogue`, pero no
el historial conversacional: abre una sesión nueva, envía sólo la orden y una
ventana máxima de 12.000 caracteres, y acepta únicamente una operación
editorial validada. No sube audio ni cambia el selector de Dialogar.

El default de instalación permanece en `Local 14B`. La interfaz permite elegir:

- `Local 14B`;
- `OpenAI vía OpenClaw`.

Cuando se elige OpenAI, el contexto conversacional necesario sale de la PC. El
selector lo indica de manera explícita y nunca hace fallback silencioso entre
local y cloud.

## Frontera OpenClaw

El diálogo usa exclusivamente el agente `fusion-dialogue`. No usa ni modifica:

- `main`;
- `fusion-research`;
- canales, bindings, Telegram o gateway;
- búsqueda web.

Fusion ofrece dos modos de ejecución explícitos:

- `agent` (predeterminado): ejecuta `openclaw agent --local` con un
  `--session-id` nuevo en cada turno;
- `infer` (experimental): ejecuta `openclaw infer model run --local` sin
  sesión, herramientas, memoria ni archivos de bootstrap.

Fusion ya envía la conversación completa serializada por `ConversationCore`.
En modo `infer`, `OPENCLAW_AGENT_DIR` apunta al almacén exclusivo de
`fusion-dialogue`, por lo que conserva su OAuth y su orden de autenticación sin
usar `main`. Los errores fallan cerrados: nunca se vuelve silenciosamente a
`agent` ni al modelo local.

OpenClaw no ofrece un argumento de archivo para el prompt de `infer model run`.
Por eso, en ese modo el texto aparece transitoriamente en los argumentos del
proceso local y puede ser visible para procesos del mismo usuario. El modo
`agent` conserva el transporte mediante archivo temporal.

Referencias oficiales de OpenClaw:

- [proveedor OpenAI y autenticación](https://docs.openclaw.ai/providers/openai);
- [administración de agentes](https://docs.openclaw.ai/cli/agents);
- [ejecución de un agente](https://docs.openclaw.ai/cli/agent);
- [inferencia liviana y stateless](https://docs.openclaw.ai/cli/infer).

## Preparación en la PC

Primero autenticar OpenAI en OpenClaw, tomando control personal del navegador:

```bash
openclaw models auth login --provider openai
openclaw models list --provider openai
```

Después inspeccionar y aplicar el configurador versionado:

```bash
python3 scripts/setup_fusion_openai_dialogue.py
python3 scripts/setup_fusion_openai_dialogue.py --apply --model openai/gpt-5.6-sol
```

Si la cuenta no expone GPT-5.6, usar el identificador que devuelva `models
list`, por ejemplo:

```bash
python3 scripts/setup_fusion_openai_dialogue.py --apply --model openai/gpt-5.5
```

El script hace backup atómico de `~/.openclaw/openclaw.json`, crea solamente
`fusion-dialogue` y le aplica un perfil mínimo con herramientas de archivos,
shell, red, navegador, mensajería y subagentes denegadas. No reinicia el gateway
compartido.

Configurar el mismo modelo en el `.env` local de Fusion:

```dotenv
FUSION_READER_CHAT_PROVIDER=local
FUSION_READER_OPENAI_CHAT_ENABLED=1
FUSION_READER_OPENAI_CHAT_MODEL=openai/gpt-5.6-sol
FUSION_READER_OPENAI_CHAT_AGENT=fusion-dialogue
FUSION_READER_OPENAI_DICTATION_MODEL=openai/gpt-5-nano
FUSION_READER_OPENAI_EXECUTION_MODE=agent
```

Para activar la ruta liviana explícitamente:

```dotenv
FUSION_READER_OPENAI_EXECUTION_MODE=infer
```

`FUSION_READER_OPENAI_CHAT_AGENT_DIR` es opcional. Si no se define, Fusion usa
`$OPENCLAW_STATE_DIR/agents/fusion-dialogue/agent` o
`~/.openclaw/agents/fusion-dialogue/agent`. La aplicación falla cerrada si ese
directorio no existe o pertenece a otro agente.

## Rendimiento validado

La corrección de sesiones se validó en una instalación real con OpenClaw
`2026.7.1-2`, OAuth de ChatGPT y `openai/gpt-5.6-sol`. Cinco turnos escritos
consecutivos tardaron 10.22 s, 10.27 s, 11.22 s, 9.52 s y 10.02 s. El contexto
se conservó y la latencia dejó de crecer entre turnos. Estos valores son una
referencia de esa instalación, no un límite garantizado para otras redes o
cuentas.

La ruta `infer` también completó una prueba directa y una prueba integrada de
cinco turnos en la instalación real. Como esta consolidación reconstruye el
cambio sobre un `main` posterior, la comprobación integrada debe repetirse con
el nuevo head antes de fusionarlo o activarlo de forma permanente.

## Diagnóstico

`GET /api/status` y `GET /api/dialogue/status` publican `chat_provider`, que
incluye selección, catálogo, modelo y disponibilidad. Si OpenClaw o la
autenticación fallan, Dialogar devuelve un error humano y la lectura permanece
sana. El usuario puede volver manualmente a `Local 14B`.
