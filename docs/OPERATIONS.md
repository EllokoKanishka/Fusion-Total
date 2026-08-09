# Panda Fusión — Operación

La fase de reparación/consolidación está cerrada en
`docs/CLOSURE_AND_BACKLOG_V2.md`; este archivo conserva los procedimientos
operativos diarios sin duplicar el backlog.

El contrato vigente de carga, lectura, cache, cancelación y reproductor está en
`docs/AUDIO_LIFECYCLE_REPAIR_V2.md`.

## Arranque recomendado

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
```

La instalación de escritorio genera `pandafusion.service` con
`scripts/start_pandafusion_systemd.sh` como entrada. Ese proceso inicia primero
el TTS GPU propio de Fusion (`7853`), cae al TTS CPU (`7851`) si hace falta y
recién entonces ejecuta el servidor web. Por eso un reinicio normal conserva la
ruta de voz completa:

```bash
systemctl --user restart pandafusion.service
```

Después de actualizar desde una instalación anterior, ejecutar una sola vez
`./scripts/install_launcher.sh` para regenerar la unidad de usuario con este
contrato; luego los reinicios comunes ya incluyen la voz.

UI:

```text
http://127.0.0.1:8010/
```

Manifiesto único de dependencias:

- [docs/DEPENDENCIES_V2.md](DEPENDENCIES_V2.md)
- [docs/LOCAL_DEFAULTS_V2.md](LOCAL_DEFAULTS_V2.md)
- `requirements/fusion-reader-v2.txt`
- `requirements/fusion-reader-v2-optional.txt` (solo rutas opcionales)

## Healthchecks

```bash
curl -s http://127.0.0.1:7853/api/ready
curl -s http://127.0.0.1:8021/health
curl -s http://127.0.0.1:11434/api/tags
curl -s "http://127.0.0.1:8080/search?q=test&format=json" | head -c 300
curl -s http://127.0.0.1:8010/api/status
curl -s http://127.0.0.1:8010/api/dialogue/status
curl -s http://127.0.0.1:8010/api/media/status
```

Los endpoints de Fusion ahora exponen un bloque `services` para leer rápido:

- `tts.ready`
- `tts.owner_valid`
- `stt.ready`
- `stt.fallback_ready`
- `chat.ready`
- `external_research.ready`
- `dialogue_reasoning.requested_mode`
- `dialogue_reasoning.applied_mode`
- `dialogue_reasoning.degraded`

## Indicador TTS en la UI

La etiqueta superior de voz distingue la ruta de síntesis:

- `TTS GPU 7853 listo`: ruta preferida, baja latencia.
- `TTS CPU 7851 fallback - voz mas lenta`: modo degradado; suele ocurrir con convivencia GPU/juego activo y puede subir mucho la espera de Dialogar.
- `TTS no disponible`: no hay voz utilizable.

Si Dialogar parece lento, mirar primero esa etiqueta o `services.tts.url` en `/api/status`.

Si el TTS está iniciando o caído, los selectores muestran el catálogo conocido
de veinte voces de Fusion como fallback y lo marcan en su ayuda. Eso permite
conservar la elección, pero no declara esas voces listas: el catálogo dinámico
del AllTalk activo vuelve a ser la autoridad apenas responde.

## Verify

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
./scripts/verify_voice_port_isolation.sh
./scripts/smoke_fusion_reader_v2.sh
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

Los tests de exportación de audio deben construirse con un root temporal
inyectado. No deben depender de `~/Descargas` real ni dejar WAVs persistentes
en la carpeta personal del usuario.

Estado histórico auditado el 2026-07-09 (los conteos viven sólo como snapshot
fechado en los documentos de auditoría):

- `./scripts/verify_voice_port_isolation.sh`: ahora separa `FUSION STRICT CHECKS` de `EXTERNAL BOUNDARY / DOCTORA INFO`
- resultado local histórico: `FINAL RESULT: OK_WITH_WARNINGS`
- si faltan bitácoras, memoria o referencias externas de Doctora, eso sale como `WARN` externo y ya no se mezcla con un fallo estricto de aislamiento de Fusion
- el manifiesto Python nuevo cubre solo paquetes `pip` del repo; binarios de sistema y entornos GPU dedicados siguen documentados aparte
- para revisar puertos, servicios, binarios y env vars sin repartir la info entre varios archivos, usar `docs/DEPENDENCIES_V2.md`
- para revisar defaults absolutos locales, overrides y alcance externo, usar `docs/LOCAL_DEFAULTS_V2.md`
- `./scripts/smoke_fusion_reader_v2.sh` sirve como smoke de solo lectura: no levanta servicios, no mata procesos y usa `OK_WITH_WARNINGS` cuando hay componentes opcionales apagados
- el resultado vigente de consolidación está en
  `docs/CONSOLIDATION_FINAL_2026-07-12.md`

## Si 7853 no engancha

1. revisar `runtime/fusion_reader_v2/tts_owner.json`
2. verificar `owner=fusion_reader_v2`
3. verificar `curl -s http://127.0.0.1:7853/api/ready`
4. reiniciar Fusion cuando `7853` ya esté realmente listo

Regla:

- Fusion no debe caer a `7854`
- Fusion no debe reclamar `7852`

## Si Dialogar no escucha

1. hacer recarga fuerte del navegador
2. revisar `GET /api/dialogue/status`
3. revisar `curl -s http://127.0.0.1:8021/health`
4. confirmar permiso de micrófono del navegador
5. si el navegador niega micrófono, Dialogar debe mostrar el motivo y `Leer` debe seguir sano
6. si hubo barge-in extraño, detener y volver a activar `Dialogar`

La traza de Dialogar muestra diagnóstico de captura:

- `WAV`: tamaño enviado al servidor.
- `RMS` y `pico`: amplitud de la señal capturada.
- `voz sí/no`: si el audio superó el umbral local de voz.
- `corte`: motivo del corte local, normalmente `silence` o `timeout`.
- `Mic`: etiqueta del dispositivo si el navegador la expone.

Si `WAV` existe pero `RMS`/`pico` son casi cero, el navegador está entregando silencio o el micrófono equivocado. Si hay amplitud razonable pero `hallucinated_transcript`, ajustar después umbrales/duración o revisar STT, sin tocar `Leer`.

## Dictado

1. abrir `Dictado` desde la barra superior;
2. pulsar `Iniciar dictado` y aprobar el permiso de micrófono;
3. hacer una pausa breve para cerrar cada tramo;
4. mantener `Órdenes con «Lucy»` activo para corregir o leer por voz: sólo las
   frases que empiezan con “Lucy” se ejecutan como órdenes; o
   desactivarlo cuando todo lo pronunciado deba entrar literalmente;
5. elegir `Reglas instantáneas`, `IA local ligera` u `OpenAI nano`; la gramática
   siempre resuelve primero las órdenes conocidas y el modelo se carga o llama
   sólo ante una orden no reconocida;
6. usar `Pasar al lector` para montar una copia temporal o `Descargar TXT` para
   conservar un archivo.

El borrador se guarda en `localStorage` del origen `127.0.0.1:8010`. El audio se
escribe en el upload temporal únicamente durante la transcripción y se elimina
en el `finally` de la ruta. Cerrar el panel detiene pistas de micrófono y lectura.

Órdenes base: `Lucy, borrá X y escribí Y`, `Lucy, reemplazá X por Y`,
`Lucy, deshacer`, `Lucy, rehacer`, `Lucy, pará acá`,
`Lucy, léeme el último párrafo`, `Lucy, léeme la última hoja` y
`Lucy, léeme desde X`, `Lucy, borrá de X para adelante`.

El default `Reglas instantáneas` no carga ningún LLM. Para instalar el modelo
local ligero opcional:

```bash
ollama pull qwen3:4b
```

OpenAI usa `openai/gpt-5-nano` mediante el mismo agente aislado
`fusion-dialogue`, pero con sesión nueva y prompt editorial. Sólo viajan la
orden y una ventana máxima de 12.000 caracteres alrededor del cursor; audio,
voz y el resto del borrador permanecen locales. Si el asistente falla, la orden
queda en `noop` y el texto no cambia.

## Si STT 8021 está caído

Antes de diagnosticar STT de diálogo, recordar que las conferencias largas
usan el mismo provider con timeout extendido. El panel `Audio y video` normaliza
primero el archivo a FLAC mono de 16 kHz y muestra etapas separadas.

Operación de medios largos:

- la interfaz usa un único flujo para audio o video;
- se puede pedir de forma independiente el PDF en idioma original, el PDF
  traducido al castellano y el audio en castellano;
- sólo se generan las salidas marcadas; se puede elegir una, dos o las tres;
- `Cancelar` detiene cooperativamente un trabajo activo y `Cerrar resultado`
  limpia el panel cuando terminó, sin borrar los PDF/WAV publicados;
- los originales subidos y FLAC temporales se eliminan al terminar o fallar;
- PDF/WAV publicados quedan en Descargas;
- montar copia el texto a `runtime/fusion_reader_v2/imported_texts` para que la
  sesión pueda recuperarlo;
- el cierre normal solicita cancelación y espera el worker multimedia.

Contrato: `auto` (default) usa el server sano y cae a Whisper CLI ante una
indisponibilidad o fallo normal; no repite por CLI un resultado
`hallucinated_transcript`. `server` (incluidos `faster_whisper` y
`faster-whisper`) usa exclusivamente `8021`. `cli` usa exclusivamente
`FUSION_READER_STT_COMMAND`; el launcher no inicia `8021` y su ausencia es
informativa.

`FUSION_READER_STT_URL` controla el cliente HTTP y `FUSION_READER_STT_PORT` el
launcher/server (default `8021`); al personalizarlos deben señalar el mismo
servicio. El Python del server sigue la cadena `FUSION_READER_STT_ENV` →
`FUSION_READER_GPU_ENV` → default histórico.

`./scripts/smoke_fusion_reader_v2.sh` y `/api/status` permiten verificar el
provider sin iniciar ni cargar modelos pesados.

1. revisar `curl -s http://127.0.0.1:8021/health`
2. revisar `GET /api/dialogue/status` y confirmar `services.stt.ready=false`
3. relanzar `./scripts/start_fusion_reader_v2_stt.sh`
4. confirmar que `services.stt.fallback_ready` no esté ocultando una caída más seria del server principal

## Si Ollama está caído

1. revisar `curl -s http://127.0.0.1:11434/api/tags`
2. revisar `GET /api/dialogue/status` y confirmar `services.chat.ready=false`
3. si `Dialogar` devuelve texto humano de error, no tocar `Leer`
4. relanzar Ollama y reintentar una pregunta corta

## Si OpenAI mediante OpenClaw no responde

1. volver a `IA: Local 14B`; la lectura y la voz siguen disponibles
2. revisar `GET /api/dialogue/status` y el objeto `chat_provider`
3. confirmar que existe el binario indicado por `FUSION_READER_OPENCLAW_BIN`
4. ejecutar `openclaw models list --provider openai`
5. si la sesión OAuth venció, ejecutar personalmente
   `openclaw models auth login --provider openai`
6. verificar el agente sin mutar nada con
   `python3 scripts/setup_fusion_openai_dialogue.py`

La instalación y las fronteras de privacidad están en
`docs/OPENAI_DIALOGUE_PROVIDER.md`.

## Si SearXNG está caído

1. revisar `curl -s "http://127.0.0.1:8080/search?q=test&format=json" | head`
2. revisar `GET /api/dialogue/status` y confirmar `services.external_research`
3. en `auto`, Fusion puede caer a `OpenClaw` si está habilitado
4. si ambas vías externas fallan, la respuesta debe seguir siendo humana y Dialogar no debe quedar mudo

## Investigación externa

Configuración por entorno:

```text
FUSION_READER_EXTERNAL_RESEARCH_PROVIDER=auto|searxng|openclaw
FUSION_READER_SEARXNG_URL=http://127.0.0.1:8080
FUSION_READER_SEARXNG_TIMEOUT=12
```

Regla operativa:

- `auto` prefiere `SearXNG`
- `OpenClaw` queda fallback
- no tocar Brave/global `web_search`

## Modo académico

```bash
./scripts/start_fusion_reader_v2_academic.sh
```

Perfil:

- `qwen3:14b-q8_0`
- thinking activo
- presupuesto de respuesta más alto

## Arranque con Bohemia uncensored

```bash
./scripts/start_fusion_reader_v2_bohemia.sh
```

Fusion opera sobre tres ejes independientes:
- Documento / Modo libre
- Académica / Bohemia
- Normal / Pensar / Supremo / Pensamiento crítico

Advertencia operativa:
Bohemia usa un modelo abliterated/uncensored (`huihui_ai/qwen3-abliterated:14b-v2-q8_0`), útil para exploración privada, charla libre y lectura literaria incómoda; no usar como guía operativa para acciones peligrosas ni como modo docente por defecto.

Aclaración:
- Académica conserva `qwen3:14b-q8_0`.
- Bohemia cambia de modelo solo si la variable `FUSION_READER_BOHEMIA_CHAT_MODEL` está definida.

## Recuperación rápida

- si falla diálogo pero lectura sigue: priorizar no romper `Leer`
- si falla TTS GPU: usar fallback CPU mientras se diagnostica
- si falla investigación externa: responder humano, no exponer errores crudos
- si `Dialogar` devuelve texto pero no audio: mirar `voice_ok`, `audio_available` y `detail`
- si `reasoning_mode_requested=supreme` en voz: esperar `applied_mode=thinking` salvo override explícito
