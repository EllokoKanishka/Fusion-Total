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

El agente se ejecuta con `openclaw agent --local` y un `--session-id` nuevo en
cada turno. Fusion ya envía la conversación completa serializada por
`ConversationCore`; por eso no se reutiliza la sesión interna de OpenClaw, lo
que evita duplicar y acumular el mismo historial en cada respuesta. OpenClaw
aporta el runtime y la autenticación OpenAI/Codex.

Referencias oficiales de OpenClaw:

- [proveedor OpenAI y autenticación](https://docs.openclaw.ai/providers/openai);
- [administración de agentes](https://docs.openclaw.ai/cli/agents);
- [ejecución de un agente](https://docs.openclaw.ai/cli/agent).

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
```

## Rendimiento validado

La corrección de sesiones se validó en una instalación real con OpenClaw
`2026.7.1-2`, OAuth de ChatGPT y `openai/gpt-5.6-sol`. Cinco turnos escritos
consecutivos tardaron 10.22 s, 10.27 s, 11.22 s, 9.52 s y 10.02 s. El contexto
se conservó y la latencia dejó de crecer entre turnos. Estos valores son una
referencia de esa instalación, no un límite garantizado para otras redes o
cuentas.

## Diagnóstico

`GET /api/status` y `GET /api/dialogue/status` publican `chat_provider`, que
incluye selección, catálogo, modelo y disponibilidad. Si OpenClaw o la
autenticación fallan, Dialogar devuelve un error humano y la lectura permanece
sana. El usuario puede volver manualmente a `Local 14B`.
