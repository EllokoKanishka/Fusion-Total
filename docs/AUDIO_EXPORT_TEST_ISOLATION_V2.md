# Fusion Reader v2 — Aislamiento de tests de exportación de audio

## Síntoma

Después de ejecutar repetidamente la suite del proyecto aparecieron WAVs
pequeños y numerados en la carpeta real de Descargas del usuario. Los nombres
coincidían con los escenarios de `tests/test_audio_export.py` y
`tests/test_fusion_reader_v2.py`.

La exportación manual real seguía produciendo un solo archivo final; el daño lo
causaban las pruebas, no el botón de la UI.

## Causa raíz

La ruta de exportación de audio resolvía su destino directamente con
`find_downloads_dir()`. Eso era correcto para runtime, pero no para tests.

Antes de esta reparación:

- `unique_audio_download_target()` siempre apuntaba a la carpeta global de
  Descargas;
- `FusionReaderV2._audio_export_worker()` siempre usaba esa ruta global;
- `get_audio_export_download()` validaba contra la carpeta global;
- los tests creaban jobs de exportación sin inyectar un root propio;
- muchos tests dependían de `sleep()` en vez de esperar al thread real;
- por eso la suite dejaba artefactos persistentes en `~/Descargas`.

## Reparación aplicada

La exportación ahora acepta un root explícito:

- `FusionReaderV2(..., audio_export_root=...)`
- `unique_audio_download_target(filename, root=...)`

Comportamiento:

- en runtime, si no se inyecta nada, el root por defecto sigue saliendo de
  `find_downloads_dir()`;
- en tests, `test_app()` crea un sandbox temporal y pasa un root propio;
- `get_audio_export_download()` valida contra el root efectivo de esa
  instancia, no contra una ruta global fija.

## Ciclo de vida de los tests

Se agregó `wait_for_audio_export(app, job_id, timeout=...)` para evitar polling
frágil con `sleep()`.

Ese helper:

- espera estado terminal;
- falla con un mensaje útil si se agota el timeout;
- no deja que el test termine mientras el thread sigue vivo;
- permite los estados `done`, `cancelled` y `error` según el caso.

Además, `test_app()` usa un `TemporaryDirectory` por defecto y `close_test_app()`
elimina ese sandbox al cerrar el test. Los proveedores sintéticos de TTS ahora
escriben dentro de ese root temporal, no en la carpeta personal.

## Cierre de trabajo en background

El cleanup de `FusionReaderV2` pasó a usar un ciclo de vida explícito:

- `open`
- `closing`
- `closed`

Ese estado se coordina con un lock/condition compartido para que la transición
`open → closing` sea atómica.

Consecuencias prácticas:

- `start_audio_export()` y `prepare_document()` revalidan el estado dentro de
  su sección crítica antes de registrar jobs o threads;
- `_synthesize_cached_with_settings()` registra cada síntesis TTS activa antes
  de llamar al proveedor, y el shutdown espera a que ese contador llegue a
  cero;
- `shutdown_background_work()` captura una vez los threads, futures y
  executors aceptados, y si vence el timeout deja la instancia en `closing`
  para poder reintentar el cierre más tarde;
- una segunda llamada a shutdown reutiliza el cierre en curso en vez de
  arrancar otro;
- `closed` es terminal y `close_test_app()` solo borra el tempdir después de que
  el cierre haya terminado de verdad.

## Pruebas de hermeticidad

La cobertura reforzada valida que:

- las exportaciones de tests quedan dentro del sandbox temporal;
- una carpeta externa simulada de “Descargas reales” conserva su sentinel;
- los modos `current`, `block`, `range` y `full` generan un solo archivo final;
- cada exportación legítima deja exactamente un WAV nuevo, o un único `_2.wav`
  cuando corresponde una segunda exportación real con el mismo nombre;
- la cancelación no deja artefactos parciales;
- el error de TTS no deja archivos parciales;
- los archivos temporales `.audio_export_*.txt` se limpian incluso cuando se
  usa ffmpeg para concatenar;
- las descargas se rechazan si intentan salir del root efectivo o resolver por
  symlink fuera de él;
- dos exportaciones legítimas reutilizan caché sin multiplicar jobs;
- el root runtime por defecto sigue siendo Descargas;
- la protección contra path traversal se mantiene.

Además, ahora se cubren explícitamente estas carreras y garantías:

- una exportación de audio no puede colarse después de que el shutdown empezó;
- `prepare_document()` tampoco puede registrar su thread tarde;
- una lectura TTS interactiva no puede sintetizar después del cierre;
- un timeout de shutdown se puede reintentar hasta completar el cleanup;
- dos shutdowns concurrentes reutilizan el mismo cierre;
- cerrar una instancia no cancela el trabajo en background de otra instancia;
- una excepción dentro de `managed_test_app()` sigue dejando correr el cleanup
  sin ocultar la excepción original.

También se añadió una verificación estructural del frontend para asegurar que:

- el botón de exportación registra una sola acción;
- el polling de estado no dispara nuevas exportaciones;
- el render de estado no llama al inicio del job.

## Contrato de runtime

En ejecución normal:

- el audio exportado sigue guardándose en Descargas;
- los nombres siguen siendo seguros y numerados solo cuando hace falta;
- la descarga HTTP sigue funcionando;
- un request crea como máximo un job;
- un job crea como máximo un archivo final.

## Archivos existentes

No se borró ningún archivo previo de `~/Descargas`.

La reparación solo evita que los tests vuelvan a sembrar WAVs nuevos en la
carpeta real del usuario.

## Riesgos restantes

- siguen existiendo archivos históricos ya creados por ejecuciones anteriores;
- si se agregan nuevos helpers de test que construyan `FusionReaderV2`, deben
  pasar un `audio_export_root` temporal o usar `test_app()`;
- cualquier nueva ruta de exportación debe respetar el mismo patrón de
  inyección explícita.
