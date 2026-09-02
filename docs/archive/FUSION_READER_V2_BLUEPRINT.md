# Fusion Reader v2 - Blueprint Voice First

Nota 2026-04-24:

```text
Este archivo sigue siendo la fuente de intencion arquitectonica y decisiones de
diseño. El estado operativo vigente vive en FUSION_READER_V2_STATE.md y en
docs/ARCHITECTURE.md.
```

Fecha: 2026-04-16

## Norte

Fusion Reader v2 no es un asistente general. Es un lector conversacional por voz.

La voz neural es el centro del producto. Todo lo demas existe para sostener una experiencia de lectura comoda, entendible y continua.

## Lecciones del prototipo

- La UI anterior carga libros, notas y sesiones, pero no es clara.
- La voz robotica destruye el producto aunque el resto funcione.
- AllTalk/XTTS produjo una voz que al usuario le gusto.
- AllTalk puede correr en `127.0.0.1:7851`.
- Fusion debe hablar con proveedores TTS por contrato HTTP, no copiar SillyTavern entero.
- El fallback `spd-say` solo sirve como emergencia, no como experiencia principal.
- La lectura larga necesita cache y precarga. Generar cada bloque en el momento produce silencios molestos.

## Arquitectura v2

```text
Fusion Reader v2
  ReaderCore
    Document
    Chunker
    Session
    Cursor
    Bookmark

  VoiceCore
    TTSProvider
    AllTalkProvider
    AudioCache
    PrefetchQueue
    PlaybackAdapter

  ConversationCore
    Chat sobre bloque actual
    Resumen/contexto de lectura
    Sin herramientas generales

  API
    load
    read
    next
    previous
    jump
    status
    voice/test
    voice/voices
    chat
```

Actualizacion 2026-04-24:

```text
La arquitectura viva ya incluye una via acotada de investigacion externa:
SearXNG local primero, OpenClaw fusion-research solo como fallback. Esa
capacidad sigue fuera de la ruta critica de lectura.
```

## Separacion lectura / conversacion

Decision del 2026-04-17:

La lectura y la conversacion deben permanecer separadas. El lector por voz no debe
depender de un LLM para leer. El flujo `Documento -> Chunker -> TTS -> Audio` debe
seguir funcionando aunque STT, LLM o chat fallen.

La capa LLM se agrega como `ConversationCore`, acoplada solo por contexto:

```text
ReaderCore expone snapshot:
  documento activo
  chunk actual
  chunks cercanos
  cursor/progreso
  notas/bookmarks

ConversationCore usa ese snapshot:
  responde preguntas sobre el texto
  conversa sobre el fragmento actual
  interpreta comandos permitidos del lector
```

Regla: el LLM puede mirar el estado del lector y pedir acciones controladas, pero
no reemplaza al lector ni se mete en la ruta critica de `/api/read`.

Primer proveedor conversacional:

```text
Ollama
URL: http://127.0.0.1:11434
Modelo inicial/default: qwen3:14b-q8_0
Modo rapido opcional: qwen3:14b
Endpoint Fusion: POST /api/chat
```

## Contrato de voz

Todo motor TTS debe cumplir:

- `health() -> dict`
- `voices() -> list[str]`
- `synthesize(text, voice, language) -> AudioArtifact`

Fusion no debe depender internamente de AllTalk, SillyTavern ni Coqui. Debe depender de un contrato propio.

## Motor inicial

Proveedor principal:

```text
AllTalk XTTS
URL preferida: http://127.0.0.1:7853 (GPU RTX 5090 reservado para Fusion)
Fallback CPU: http://127.0.0.1:7851
Idioma: es
Voz default: female_03.wav
```

Scripts actuales:

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_reader_neural_tts.sh
```

`scripts/start_fusion_reader_v2.sh` detecta automaticamente si el AllTalk GPU
reservado para Fusion esta listo en `127.0.0.1:7853` y lo usa como proveedor
primario. Si no esta listo, vuelve al fallback CPU `127.0.0.1:7851`.

Decision del 2026-04-18:

```text
El puerto 7852 no se autodetecta como proveedor de Fusion.
Motivo: otros agentes locales pueden levantar AllTalk en 7852 y responder
Ready sin pertenecer al entorno aislado de Fusion.
```

Decision del 2026-04-19:

```text
Fusion Reader v2 reserva AllTalk GPU en 7853 y Doctora Lucy/Antigravity reserva
su TTS en 7854. Fusion solo acepta 7853 como propio si existe
runtime/fusion_reader_v2/tts_owner.json con owner=fusion_reader_v2; un simple
Ready HTTP ya no alcanza para evitar contaminacion cruzada.
AllTalkProvider rechaza explicitamente 7854 (Doctora Lucy) y 7852
(historico/no asignado), incluso si esos puertos llegan por variable de entorno.
La frontera se verifica con scripts/verify_voice_port_isolation.sh.
```

## RTX 5090

Objetivo tecnico:

```text
AllTalk/XTTS o proveedor equivalente debe correr en GPU sobre RTX 5090.
```

Problema observado inicialmente:

```text
PyTorch actual no soporta bien sm_120.
AllTalk en CUDA carga el modelo pero falla generando audio con HTTP 500.
```

Regla:

No tocar el entorno existente de `ebook2audiobook`. Crear un entorno aislado para pruebas GPU.

Estado al 2026-04-17:

```text
Entorno GPU: /home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311
Python: 3.11.15
Torch: 2.11.0+cu128
CUDA runtime: 12.8
GPU: RTX 5090, sm_120
AllTalk GPU Fusion: http://127.0.0.1:7853
```

Scripts GPU:

```bash
./scripts/bootstrap_alltalk_gpu_5090.sh
./scripts/check_gpu_5090_env.py
./scripts/start_reader_neural_tts_gpu_5090.sh
```

Compatibilidad necesaria para AllTalk viejo sobre Torch nuevo:

- shim local en `scripts/gpu_compat/sitecustomize.py`;
- `FastAPI.route` agregado si falta;
- `torch.load(..., weights_only=False)` solo cuando el lanzador exporta `FUSION_READER_ALLOW_TORCH_PICKLE_LOAD=1`;
- `LD_LIBRARY_PATH` apuntando a librerias `nvidia/*/lib` del entorno;
- dependencias extra: `torchcodec`, `nvidia-npp-cu12`, `aiofiles`;
- pines compatibles: `transformers==4.39.1`, `tokenizers==0.15.2`, `huggingface-hub==0.22.1`, `fastapi==0.135.1`.

Validacion GPU historica inicial, antes de reservar el puerto definitivo 7853:

```text
check_gpu_5090_env.py: matmul CUDA OK
AllTalk cargo XTTSv2 en cuda
Generacion directa: WAV OK, ~1.99 s en frase corta
AllTalkProvider contra puerto temporal 7852: ok=True, WAV 189518 bytes
Bloque 420 caracteres: CPU 7851 ~54.5 s, GPU temporal 7852 frio ~9.3 s, GPU caliente ~4.7 s
```

Nota actual: 7852 fue solo una medicion de laboratorio. Fusion ya no debe
arrancar ni autodetectar 7852; el puerto GPU estable y protegido es 7853.

## Estrategia de lectura

Para que el lector se sienta fluido:

1. Dividir texto en chunks chicos y naturales. Default actual: maximo 420 caracteres.
2. Generar audio del chunk actual.
3. Mantener una ventana de prefetch alrededor del cursor (`FUSION_READER_PREFETCH_AHEAD=3`).
4. Cachear audio por hash de texto + voz + idioma.
5. Reusar cache al repetir, volver o retomar.
6. Al saltar a un bloque, preparar ese bloque y los siguientes antes de tocar `Leer`.
7. Permitir `Preparar documento` para cachear todos los chunks en background.

## Reescritura

No borrar todavia el prototipo viejo. Queda como laboratorio.

La v2 se escribe en paralelo bajo `fusion_reader_v2/`, con tests propios, y cuando supere al prototipo se migra la UI.

## Primer milestone

- Paquete `fusion_reader_v2`.
- ReaderCore testeado.
- TTSProvider + AllTalkProvider.
- AudioCache.
- Prefetch del siguiente chunk.
- API minima para probar voz y lectura.

Estado: completado.

## Milestone actual

La v2 ya tiene una primera experiencia usable en navegador:

- Servidor HTTP propio en `scripts/fusion_reader_v2_server.py`.
- Puerto default: `8010`.
- UI en `http://127.0.0.1:8010/`.
- Zona de carga/conversion de documentos desde el navegador.
- Biblioteca TXT/MD desde `library/` queda como API interna/laboratorio.
- Carga por `book_id`, sin exponer el filesystem completo, y carga por documento subido.
- Importacion de TXT/MD/PDF/DOCX/ODT/RTF/HTML.
- Reproductor HTML de audio generado por AllTalk.
- Prueba de voz neural desde la UI.
- Lectura por bloque: anterior, leer, repetir, siguiente, ir.
- Modo continuo experimental: al terminar un audio avanza y lee el siguiente bloque.
- Audio cacheado servido desde `/audio/<archivo.wav>`.
- Metricas basicas de audio: `ready_ms`, `synthesis_ms`, `cached`, `provider`.
- Progreso visible para importacion/OCR por job.
- Metricas agrupadas por documento y por chunk lento.

Endpoints v2 actuales:

```text
GET  /
GET  /health
GET  /api/status
GET  /api/library
GET  /api/voice/voices
GET  /api/voice/metrics
GET  /api/voice/metrics/summary
GET  /api/voice/metrics/documents
GET  /api/voice/metrics/chunks
GET  /api/import-status
GET  /api/prepare/status
GET  /api/notes
GET  /api/dialogue/status
GET  /audio/<archivo.wav>
POST /api/load
POST /api/import
POST /api/import-file
POST /api/import-file/start
POST /api/read
POST /api/next
POST /api/previous
POST /api/jump
POST /api/prepare/start
POST /api/prepare/cancel
POST /api/notes/create
POST /api/notes/update
POST /api/notes/rename
POST /api/notes/delete
POST /api/dialogue/turn
POST /api/dialogue/reset
POST /api/voice/test
POST /api/chat
```

Servicio STT recomendado para dialogo:

```text
GET  http://127.0.0.1:8021/health
POST http://127.0.0.1:8021/transcribe
```

Arranque:

```bash
./scripts/start_fusion_reader_v2_stt.sh
```

Fusion usa este servidor con `AutoSTTProvider` y cae a `whisper` CLI solo como
fallback.

Contrato web de audio:

```text
POST /api/read       -> devuelve audio_url
POST /api/voice/test -> devuelve audio_url
GET  /audio/*.wav    -> sirve audio desde runtime/fusion_reader_v2/audio_cache
```

La reproduccion ocurre en el navegador, no en el servidor. El parametro `play=true`
queda solo como compatibilidad local para pruebas por consola.

Cada respuesta de audio debe permitir distinguir:

- si vino de cache;
- cuanto espero el cliente hasta tener audio listo;
- cuanto tardo la sintesis real cuando no hubo cache;
- que proveedor entrego el audio.
- si fallo el proveedor TTS o un prefetch quedo colgado, sin taparlo con el `ok` del estado de lectura.

Timeouts actuales:

```text
prefetch_wait_seconds=25
FUSION_READER_TTS_TIMEOUT=60
```

Contrato de importacion:

```text
POST /api/import
{
  "filename": "documento.pdf",
  "mime": "application/pdf",
  "data_b64": "..."
}
```

El servidor extrae texto, crea una sesion de lectura y devuelve el mismo estado
que `/api/load`, mas `source_type` e `import_detail`.

Para documentos grandes la UI debe usar `POST /api/import-file`, enviando el
archivo crudo como cuerpo HTTP. Eso evita inflar PDFs grandes en base64/JSON.
El servidor escribe el upload a un temporal por chunks y luego convierte.

La ruta recomendada actual para documentos grandes es asincrona:

```text
POST /api/import-file/start
GET  /api/import-status?id=<job_id>
```

`/api/import-file/start` devuelve `202` con un job. El servidor convierte en
segundo plano y reporta `stage`, `current`, `total`, `percent`, `message`,
`result` y `error`. La UI usa esto para mostrar progreso durante OCR/conversion
de PDFs grandes.

Conversores actuales:

- texto plano y markdown: decodificacion directa;
- HTML/RTF: limpieza basica a texto;
- DOCX/ODT: extraccion interna desde ZIP/XML;
- PDF: `pdftotext`; si no hay capa de texto suficiente, OCR con Tesseract (`spa+eng`);
- otros documentos de oficina: LibreOffice headless a TXT cuando sea posible.

Los textos convertidos se guardan como `.txt` en
`runtime/fusion_reader_v2/imported_texts/`. Los PDFs incluyen marcas `[Pagina N]`
para preservar referencia interna.

Regla de calidad OCR:

- No aceptar OCR crudo como libro si destruye estructura.
- En PDF escaneado, preservar paginas, detectar capitulos/encabezados y separar columnas.
- Saltar portadas/imagenes con baja senal de texto antes que leer basura.
- Saltar indices ruidosos si solo agregan encabezados rotos y no lectura util.
- Preprocesar OCR con contraste/nitidez/escala y ejecutar paginas en paralelo moderado.
- Verificar una muestra convertida antes de entregar un cambio de OCR.

Parametros OCR actuales:

```text
FUSION_READER_OCR_DPI=170
FUSION_READER_OCR_WORKERS=4
```

Muestra de control actual:

```text
runtime/fusion_reader_v2/imported_texts/ars_magica_structured_sample_1_6_v9.txt
paginas utiles: 4-5
estructura: # Capitulo 1, ## Introduccion
chunks: 52
max chunk: 280 caracteres
```

Contrato de metricas:

```text
GET /api/voice/metrics/documents
```

Agrupa lecturas por documento: cantidad, aciertos, cache ratio, `ready_ms`
promedio/maximo, `synthesis_ms` promedio/maximo, longitud media y ultimo chunk.

```text
GET /api/voice/metrics/chunks?doc_id=<id>&limit=<n>
```

Agrupa por documento + chunk y devuelve los chunks mas lentos. Sirve para saber
si la demora viene de un bloque largo, falta de cache, prefetch lento o sintesis
real lenta.

## Proximo trabajo recomendado

1. Afinar la primera sintesis API GPU y mantener el modelo caliente.
2. Mejorar OCR fino de PDF escaneado: letras confundidas, separacion de parrafos y deteccion de capitulos.
3. Mejorar chunking para voz: cortes por frase, dialogos y longitud real hablada.
4. Agregar pausa/reanudar como estado de lectura, no solo control del `<audio>`.
5. Migrar notas/bookmarks del prototipo viejo cuando la v2 ya lea fluido.
6. Agregar STT de comandos cuando la lectura continua sea estable.
