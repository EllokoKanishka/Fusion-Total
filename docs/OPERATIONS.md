# Fusion Reader v2 — Operación

## Arranque recomendado

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
```

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

## Verify

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
./scripts/verify_voice_port_isolation.sh
./scripts/smoke_fusion_reader_v2.sh
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

Estado auditado el 2026-07-09:

- `tests.test_fusion_reader_v2`: `240 OK`
- `tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress`: `35 OK`
- `python3 -m unittest discover -s tests -p 'test_*.py' -v`: `345 OK`
- `./scripts/verify_voice_port_isolation.sh`: ahora separa `FUSION STRICT CHECKS` de `EXTERNAL BOUNDARY / DOCTORA INFO`
- resultado local actual: `FINAL RESULT: OK_WITH_WARNINGS`
- si faltan bitácoras, memoria o referencias externas de Doctora, eso sale como `WARN` externo y ya no se mezcla con un fallo estricto de aislamiento de Fusion
- el manifiesto Python nuevo cubre solo paquetes `pip` del repo; binarios de sistema y entornos GPU dedicados siguen documentados aparte
- para revisar puertos, servicios, binarios y env vars sin repartir la info entre varios archivos, usar `docs/DEPENDENCIES_V2.md`
- para revisar defaults absolutos locales, overrides y alcance externo, usar `docs/LOCAL_DEFAULTS_V2.md`
- `./scripts/smoke_fusion_reader_v2.sh` sirve como smoke de solo lectura: no levanta servicios, no mata procesos y usa `OK_WITH_WARNINGS` cuando hay componentes opcionales apagados
- el total operativo actual del branch es `345` y ahora incluye además `tests/test_local_defaults_v2.py` para vigilar defaults locales y overrides

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

## Si STT 8021 está caído

1. revisar `curl -s http://127.0.0.1:8021/health`
2. revisar `GET /api/dialogue/status` y confirmar `services.stt.ready=false`
3. relanzar `./scripts/start_fusion_reader_v2_stt.sh`
4. confirmar que `services.stt.fallback_ready` no esté ocultando una caída más seria del server principal

## Si Ollama está caído

1. revisar `curl -s http://127.0.0.1:11434/api/tags`
2. revisar `GET /api/dialogue/status` y confirmar `services.chat.ready=false`
3. si `Dialogar` devuelve texto humano de error, no tocar `Leer`
4. relanzar Ollama y reintentar una pregunta corta

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
