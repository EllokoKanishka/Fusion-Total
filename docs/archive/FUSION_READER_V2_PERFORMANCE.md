# Fusion Reader v2 - Rendimiento de Voz y Latencia

Fecha: 2026-04-17

Nota 2026-04-24:

```text
Este archivo conserva mediciones y decisiones de rendimiento históricas.
La validación operativa vigente ya no se registra acá: ver
FUSION_READER_V2_STATE.md y docs/OPERATIONS.md.
```

Este documento resume el diagnostico y la solucion aplicada para reducir el
tiempo entre navegar a un bloque y escucharlo con voz neural.

Actualizacion de convivencia 2026-04-18:

```text
Los datos historicos de este documento mencionan 7852.
Desde ahora Fusion reserva su GPU TTS en 7853 y no autodetecta 7852, porque ese
puerto puede pertenecer a otros agentes locales.

Desde el 2026-04-19, Doctora Lucy/Antigravity reserva su TTS en 7854. Fusion
solo trata 7853 como propio si `runtime/fusion_reader_v2/tts_owner.json`
declara `owner=fusion_reader_v2`; responder `Ready` ya no es suficiente.
```

## Problema

Al saltar a un bloque, por ejemplo el bloque 5, Fusion tardaba demasiado en
generar unas pocas lineas de audio. La demora no era esperable para esta
maquina.

## Maquina verificada

```text
CPU: AMD Ryzen 9 7950X, 16 cores / 32 hilos
RAM: 124 GiB
GPU: NVIDIA GeForce RTX 5090, 32 GiB VRAM
Driver NVIDIA: 570.211.01
```

Entorno GPU aislado:

```text
Ruta: /home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311
Python: 3.11.15
Torch: 2.11.0+cu128
CUDA runtime: 12.8
GPU capability: sm_120
```

Verificacion:

```bash
python3 scripts/check_gpu_5090_env.py
```

Resultado observado: CUDA disponible, RTX 5090 detectada y matmul en GPU OK.

## Diagnostico

La ruta estable estaba usando AllTalk CPU en:

```text
http://127.0.0.1:7851
```

El proceso estaba forzado a CPU por compatibilidad:

```text
DIRECT_CHAT_ALLTALK_FORCE_CPU=1
```

Esto era correcto como fallback, pero no como ruta principal para una RTX 5090.

Medicion historica con el mismo bloque de 420 caracteres:

```text
AllTalk CPU 7851:       ~54.5 s
AllTalk GPU 7852 frio:   ~9.3 s
AllTalk GPU 7852 caliente: ~4.7 s
```

Ademas, Fusion solo tenia un prefetch de un bloque. Al saltar a un bloque que no
estaba preparado, la UI debia esperar la sintesis completa antes de poder
reproducir.

## Solucion aplicada

### 1. GPU como proveedor preferido

`scripts/start_fusion_reader_v2.sh` ahora detecta AllTalk GPU reservado para
Fusion:

```text
http://127.0.0.1:7853
```

Si responde `/api/ready`, Fusion exporta automaticamente:

```bash
FUSION_READER_ALLTALK_URL=http://127.0.0.1:7853
```

Si GPU no esta disponible, vuelve al fallback CPU:

```text
http://127.0.0.1:7851
```

### 2. Ventana de prefetch alrededor del cursor

`FusionReaderV2` ya no prepara solo un bloque. Mantiene una ventana configurable:

```bash
FUSION_READER_PREFETCH_AHEAD=3
```

Comportamiento:

- al cargar o navegar, prepara el bloque actual;
- al saltar a un bloque, prepara ese bloque y los siguientes;
- al leer, si el WAV ya esta preparado, responde desde cache;
- la reproduccion sigue separada del LLM/chat.

Ejemplo observado:

```text
Saltar a bloque 5 -> cola [5, 6, 7, 8]
Prefetch GPU bloque 5 -> ~2.5 s
POST /api/read despues del prefetch -> ready_ms=13
```

### 3. Correccion de URLs de audio en AllTalk GPU

AllTalk GPU puede devolver URLs absolutas apuntando al puerto viejo:

```text
http://127.0.0.1:7851/...
```

Aunque Fusion este usando:

```text
http://127.0.0.1:7853
```

`AllTalkProvider` ahora normaliza URLs locales al `base_url` configurado. Esto
evita que Fusion intente descargar el WAV desde el puerto CPU apagado.

### 4. Estado `busy` en vez de falso TTS caido

Durante sintesis, AllTalk puede tardar en responder `/api/ready`. Fusion ahora
trata ese timeout como:

```json
{"ok": true, "detail": "busy"}
```

No como TTS caido.

## Archivos cambiados

```text
fusion_reader_v2/service.py
fusion_reader_v2/tts.py
scripts/start_fusion_reader_v2.sh
tests/test_fusion_reader_v2.py
FUSION_READER_V2_STATE.md
```

## Comandos operativos

Arranque GPU:

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2.sh
```

Estado esperado:

```bash
curl -s http://127.0.0.1:7853/api/ready
curl -s http://127.0.0.1:8010/api/status
```

`/api/status` debe mostrar:

```text
tts.url = http://127.0.0.1:7853/api/ready
prefetch_ahead = 3
```

Fallback CPU:

```bash
./scripts/start_reader_neural_tts.sh
./scripts/start_fusion_reader_v2.sh
```

## Estado recomendado

Para lectura diaria en esta maquina:

```text
Fusion Reader v2: http://127.0.0.1:8010
AllTalk GPU:      http://127.0.0.1:7853
Ollama chat:      http://127.0.0.1:11434
AllTalk CPU:      apagado salvo fallback
```

## Preparar documento

Implementado el 2026-04-17.

La UI ahora incluye `Preparar documento`, que crea un job en background para
cachear todos los chunks del documento activo. Tambien incluye
`Cancelar preparacion`.

Endpoints:

```text
POST /api/prepare/start
POST /api/prepare/cancel
GET  /api/prepare/status
```

`/api/status` incluye:

```text
prepare.status
prepare.current
prepare.total
prepare.percent
prepare.cached
prepare.generated
prepare.failed
prepare.message
```

La preparacion usa la misma cache de audio del lector. Si un bloque ya esta
cacheado, no vuelve a pasar por AllTalk. Si el usuario navega o lee, la
preparacion cede entre chunks para no competir con la lectura interactiva.

Prueba real:

```text
Documento de 2 bloques
POST /api/prepare/start -> generated=2, percent=100
POST /api/read -> provider=cache, cached=true, ready_ms=0
```

Validacion historica: este documento conserva resultados de la etapa en la que
la suite v2 todavía estaba en crecimiento. No usar sus conteos como fuente
canónica actual.

## Proximo paso de rendimiento fino

Agregar calentamiento fino del modelo:

```text
warmup TTS / mantener modelo caliente
```

Ese modo deberia reducir la primera llamada GPU despues de estar inactivo.
