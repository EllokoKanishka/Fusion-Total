# Fusion Reader v2 - Modo Dialogo de Laboratorio

Fecha base: 2026-04-17
Actualizado: 2026-04-21

Nota 2026-04-24:

```text
Este archivo queda como documento de diseño e implementación histórica del modo
Dialogar. El estado operativo vigente vive en docs/ARCHITECTURE.md,
docs/OPERATIONS.md y FUSION_READER_V2_STATE.md.
```

Este documento nacio como diseno del modo `Dialogar` y hoy funciona como hoja
de continuidad de su implementacion real.

Nota actual 2026-04-21:

```text
La captura principal del navegador ya no usa MediaRecorder/WebM.
Fusion arma PCM en el navegador y sube audio/wav para evitar errores
intermitentes de tipo "EBML header parsing failed".
```

Nota de convivencia 2026-04-18:

```text
Las referencias historicas a AllTalk 7852 quedan como registro.
Fusion Reader v2 ya no autodetecta 7852 y reserva su GPU TTS en 7853.
```

## Objetivo

Agregar un unico boton en el laboratorio:

```text
Dialogar
```

Al activarlo, el usuario conversa por voz con la IA de laboratorio sobre el
documento activo. La IA puede hablar con voz neural y el usuario puede
interrumpirla hablando encima.

## Separacion obligatoria

El modo dialogo no reemplaza ni modifica la ruta critica de lectura:

```text
Lectura:
Documento -> Chunker -> TTS -> Audio -> navegador

Dialogo:
Microfono -> STT -> ConversationCore/Ollama -> TTS de respuesta -> navegador
```

Si STT, LLM o dialogo fallan, el boton `Leer` debe seguir funcionando.

## Comportamiento deseado

1. El usuario toca `Dialogar`.
2. El navegador pide permiso de microfono.
3. El sistema escucha una frase del usuario.
4. El servidor transcribe la frase a texto.
5. `ConversationCore` responde usando el snapshot del lector:
   - documento activo;
   - bloque visible;
   - bloque anterior/siguiente;
   - texto del documento dentro del limite de contexto.
6. La respuesta se sintetiza con AllTalk GPU y se reproduce en el navegador.
7. Cuando termina la respuesta, el navegador vuelve a escuchar.
8. Si el usuario empieza a hablar mientras la IA esta hablando:
   - se corta el audio de la IA en el navegador;
   - se graba la intervencion del usuario;
   - se transcribe;
   - se envia como siguiente turno;
   - la IA continua la charla.

## Implementacion actual aceptable

La implementacion actual es semi-duplex con barge-in del lado navegador:

- `AudioContext` + `ScriptProcessor` capturan PCM del microfono.
- La UI conserva un pre-roll corto para no comerse el arranque de la frase.
- Al finalizar el turno, el navegador empaqueta el audio como `audio/wav`.
- Un detector simple de energia de microfono decide inicio/fin de habla.
- Mientras la IA habla, si se detecta voz del usuario, se pausa/corta el audio
  de respuesta y se empieza a grabar la nueva frase.
- La transcripcion puede hacerse por turnos completos, no por streaming.
- La respuesta TTS puede generarse completa antes de reproducir, no por streaming.

Esto ya permite una conversacion natural por turnos y una interrupcion usable.

## Futuro no obligatorio en la primera version

- STT streaming real.
- TTS streaming por frases.
- Cancelacion de eco.
- VAD avanzado con WebRTC VAD en backend.
- Deteccion robusta de wake/silence en ambientes ruidosos.

## Contratos nuevos propuestos

### STTProvider

```text
transcribe_file(path, mime, language) -> TranscriptResult
```

Resultado:

```text
ok
text
provider
detail
duration_ms
```

Proveedor inicial de fallback:

```text
Whisper CLI
Comando: whisper
Idioma: es
Modelo configurable: FUSION_READER_STT_MODEL
```

Motivo: en esta maquina hay `ffmpeg` y comando `whisper`; en el Python actual no
estan instalados `faster_whisper`, `whisper`, `speech_recognition`, `soundfile`
ni `pydub`.

Proveedor recomendado para fluidez:

```text
FasterWhisper STT Server
URL: http://127.0.0.1:8021
Script: ./scripts/start_fusion_reader_v2_stt.sh
Entorno: /home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311
Modelo default: small
Device: cuda
Compute: float16
```

Motivo: el CLI carga proceso/modelo en cada turno. El servidor STT persistente
carga faster-whisper una vez y reutiliza el modelo, eliminando el arranque por
frase.

### DialogueTurn

Entrada:

```text
audio del navegador o texto ya transcripto
```

Salida:

```text
transcript
answer
audio_url
model
stt_ms
chat_ms
tts_ms
```

## Endpoints propuestos

```text
GET  /api/dialogue/status
POST /api/dialogue/turn
POST /api/dialogue/reset
```

`POST /api/dialogue/turn` acepta audio crudo como cuerpo HTTP. La UI actual
envia `audio/wav`, aunque el backend sigue tolerando otros mimes de audio por
compatibilidad y pruebas.

## UI propuesta

En el panel `Laboratorio`:

```text
[Dialogar]
```

Estados visibles:

```text
inactivo
escuchando
procesando
hablando
interrumpido
error
```

La UI mantiene el chat textual visible para depuracion, pero el flujo principal
del usuario es el boton unico.

## Riesgos

- La primera carga del modelo Whisper puede tardar.
- Si Whisper CLI no tiene el modelo descargado, puede intentar descargarlo.
- AllTalk GPU no tolera bien multiples generaciones simultaneas; las llamadas TTS
  ya estan serializadas en `FusionReaderV2`.
- La deteccion de interrupcion por energia puede cortar por ruido ambiente; debe
  tener umbral y silencio minimo configurables.
- El microfono puede captar la voz de la IA reproducida por parlantes. La primera
  version recomienda auriculares o volumen moderado hasta agregar cancelacion de
  eco.

## Validacion inicial esperada

1. `GET /api/dialogue/status` devuelve disponibilidad STT/TTS.
2. `POST /api/dialogue/turn` con texto directo en tests devuelve respuesta y WAV.
3. `POST /api/dialogue/turn` con audio real transcribe y responde.
4. La UI reproduce el audio de la IA.
5. Si el usuario habla durante la reproduccion, el navegador corta el audio y
   envia el nuevo turno.

## Regla de producto

El laboratorio conversa sobre el documento. No se convierte en asistente general,
no automatiza escritorio y no ejecuta herramientas externas no relacionadas con
la lectura. La única excepción vigente es la investigación externa explícita por
provider aislado de Fusion, documentada fuera de este archivo.

## Estado implementado 2026-04-17

Primera version implementada y activada tras reiniciar Fusion Reader v2. Queda
pendiente la prueba manual fina con microfono real para ajustar VAD/barge-in.

## Ajuste VAD 2026-04-17

Problemas observados en prueba real:

- el sistema empezaba a grabar tarde y se comia palabras iniciales;
- la transcripcion deformaba palabras porque recibia audio incompleto;
- el detector cortaba la frase antes de que el usuario terminara.

Ajuste aplicado:

- la UI abre captura PCM apenas se activa `Dialogar`;
- mantiene un pre-roll de audio de 900 ms y lo incluye cuando detecta voz;
- baja el tiempo de confirmacion de inicio de voz a 80 ms;
- usa umbral adaptativo basado en ruido ambiente;
- espera 1700 ms de silencio antes de cortar;
- sube el minimo de grabacion a 900 ms y el maximo a 24 s;
- `faster-whisper` server usa `beam_size=5` por defecto para priorizar
  precision de transcripcion en español.

Resultado esperado: no perder el inicio de la frase, tolerar pausas naturales y
mandar al STT un turno mas completo.

Correccion historica: un intento anterior con `MediaRecorder` podia producir
WebM sin cabecera y causar `transcription_failed`. Esa ruta quedo reemplazada
por la captura PCM + `audio/wav` del 2026-04-21.

## Prioridad del dialogo 2026-04-17

Problema observado: si `Preparar documento` estaba activo, AllTalk podia quedar
ocupado generando/cacheando audio de lectura mientras el usuario dialogaba. En
CPU fallback esto dejaba la UI en `Procesando tu frase...` durante mas de 20 s.

Ajuste aplicado:

- al activar `Dialogar`, la UI pide cancelar `/api/prepare/cancel`;
- cada turno de dialogo cancela preparacion y limpia cola de prefetch antes de
  STT/chat/TTS;
- las respuestas orales se fuerzan a ser breves y se recortan antes del TTS si
  superan `FUSION_READER_DIALOGUE_TTS_MAX_CHARS` (default 320);
- Fusion usa AllTalk GPU propio en `7853` cuando esta disponible, con fallback
  CPU en `7851`.

Estado verificado actual: `dialogue/status` muestra STT
`faster_whisper_server`, TTS AllTalk por contrato, preparacion `idle`, y los
turnos de notas/dialogo pasan por la ruta corta de baja latencia.

## Reparacion 2026-04-21

Problema confirmado:

- Fusion no escuchaba bien aunque otros asistentes locales si;
- los logs del STT mostraban `convert_failed` con `EBML header parsing failed`;
- la causa era audio WebM invalido/intermitente desde el navegador.

Reparacion aplicada:

- se elimino la dependencia de `MediaRecorder` para la captura principal;
- la UI ahora toma PCM directo, conserva pre-roll, y arma `dialogue.wav`;
- el backend/STT recibe `audio/wav` como formato principal;
- el servidor STT deja trazas explicitas para `convert_failed` y
  `empty_transcript`.

Continuidad operativa:

- tras cambios del front de `Dialogar`, hacer recarga fuerte de la pestana
  (`Ctrl+Shift+R` en Chromium) antes de volver a probar;
- si sigue fallando, revisar primero logs vivos del proceso STT.

Archivos:

```text
fusion_reader_v2/dialogue.py
fusion_reader_v2/conversation.py
fusion_reader_v2/service.py
scripts/fusion_reader_v2_server.py
tests/test_fusion_reader_v2.py
```

Endpoints implementados:

```text
GET  /api/dialogue/status
POST /api/dialogue/turn
POST /api/dialogue/reset
```

UI implementada:

```text
Boton: Dialogar
Estado: diálogo apagado / escuchando / procesando / hablando
Audio: reproductor propio del laboratorio
```

Comportamiento implementado:

- captura microfono con PCM directo del navegador;
- VAD simple por energia de microfono;
- grabacion de frase completa;
- envio de audio crudo al servidor;
- STT automatico: `FasterWhisperServerSTTProvider` si `8021` esta vivo, fallback
  `WhisperCliSTTProvider`;
- respuesta de `ConversationCore.ask_dialogue`;
- TTS de respuesta con cache;
- reproduccion en navegador;
- barge-in inicial: si el usuario habla mientras la IA responde, el navegador
  corta el audio y graba el nuevo turno.

Validacion automatica:

```text
python3 -m unittest tests.test_fusion_reader_v2 -v
Resultado historico en esa pasada: 49 tests OK
```

Validacion de sintaxis:

```text
python3 -m py_compile fusion_reader_v2/dialogue.py fusion_reader_v2/conversation.py fusion_reader_v2/service.py scripts/fusion_reader_v2_server.py
Resultado: OK
```

Nota operativa:

No se reinicio el servidor vivo inmediatamente porque estaba corriendo una
preparacion real de audio sobre el DOCX grande. El modo dialogo queda disponible
en el proximo reinicio de `scripts/start_fusion_reader_v2.sh`.

## Activacion y smoke test

Luego de terminar la preparacion grande (`387/387`, un chunk fallido), se
reinicio Fusion Reader v2 y se verifico:

```text
GET /api/dialogue/status
stt.provider = whisper_cli
stt.command = /home/linuxbrew/.linuxbrew/bin/whisper
stt.model = small
tts.url = http://127.0.0.1:7853/api/ready
```

Smoke test sin microfono:

```text
POST /api/dialogue/turn
Entrada: Respondé en una frase breve: ¿sobre qué podemos dialogar acá?
Modelo: qwen3:14b
chat_ms: 3959
tts_ms: 989
audio_url: /audio/f1a458557bc48066d2becf00bcda1360.wav
```

Respuesta observada:

```text
Podemos dialogar sobre el color, el pigmento y el contexto visual del plato descrito.
```

Pendiente manual:

```text
Probar el boton Dialogar con microfono real en el navegador.
```

## Optimizacion STT persistente 2026-04-17

Problema observado:

```text
Whisper CLI tardaba ~4-5 s por frase corta.
```

Causa:

```text
El comando whisper abre un proceso y carga el modelo en cada turno.
```

Solucion aplicada:

```text
scripts/fusion_reader_v2_stt_server.py
scripts/start_fusion_reader_v2_stt.sh
FasterWhisperServerSTTProvider
AutoSTTProvider
```

Estado vivo:

```text
GET /api/dialogue/status
stt.provider = faster_whisper_server
stt.model = small
stt.device = cuda
stt.compute_type = float16
fallback = whisper_cli
```

Medicion con el mismo audio WAV generado por AllTalk:

```text
Whisper CLI fallback: ~5154 ms
Servidor faster-whisper GPU, primera llamada: ~796 ms
Servidor faster-whisper GPU, caliente: ~105-174 ms
```

Turno completo con audio, Qwen y TTS:

```text
stt_provider = faster_whisper_server
stt_ms = 848
chat_ms = 2960
tts_ms = 3068
duration_ms = 6880
```

Conclusion:

```text
El cuello STT queda resuelto de forma estructural. La latencia restante del
dialogo completo viene sobre todo de Qwen y TTS, no de voz-a-texto.
```

## Notas, barge-in y filtro anti-alucinaciones 2026-04-19

Problemas observados en uso real:

- las notas por voz podian confundirse de bloque si la UI enviaba contexto viejo;
- algunas confirmaciones prometian guardar sin crear una entrada visible;
- al hablar encima de Fusion, la respuesta podia cortarse y volver a arrancar;
- Whisper podia alucinar frases de cierre de video como "suscribete" cuando
  recibia ruido, silencio o audio muy corto.

Ajustes aplicados:

- la UI manda el bloque visible en cada turno de laboratorio;
- los comandos de nota se detectan antes del LLM y guardan de forma determinista;
- las confirmaciones de nota y dialogo usan AllTalk/XTTS `female_03.wav` por
  defecto; `text_ack` queda solo como opt-in con `FUSION_READER_FAST_*_ACK=1`;
- el navegador cancela la voz local/audio al detectar barge-in;
- las transcripciones espurias comunes de Whisper se filtran como
  `hallucinated_transcript` y no llegan al chat, notas ni UI como mensaje del
  usuario.

Validacion automatica:

```text
python3 -m unittest tests.test_fusion_reader_v2 -v
Resultado historico en esa pasada: 49 tests OK
```
