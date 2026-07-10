# Fusion Reader v2 Core Regression Fix 1

## Síntomas

- Layout central demasiado dominante y laterales comprimidos.
- Documento principal confundido visualmente con consulta.
- Lectura por voz degradada con `request_failed` y TTS apagado.
- Preparación sin progreso útil o con mensaje final engañoso.
- Extracción PDF con palabras partidas y headers mecánicos.

## Causas raíz

- El grid y el bloque de lectura no tenían límites de ancho cómodos.
- El panel derecho agrupaba el principal dentro de "Consulta" y la acción de consulta quedaba demasiado pegajosa.
- El guard de ownership del TTS caía por `owner_pid` viejo aunque el servicio real seguía vivo; además la UI escondía el detalle detrás de `request_failed`.
- El backend de preparación siempre cerraba como `done` aunque todo hubiese fallado, y el frontend resumía demasiado el estado.
- La limpieza de `pdftotext` era casi nula: no quitaba headers mecánicos ni reparaba cortes intra-palabra de forma conservadora.

## Soluciones

- Se reequilibró el layout con sidebars más anchas, lectura centrada y ancho máximo por caracteres.
- Se separó visualmente `Lectura activa` de `Consulta` y la carga como consulta vuelve a quedar explícita por archivo.
- El provider TTS operativo puede reconciliar de forma atómica un `owner_pid`
  stale con el proceso vivo validado en `7853`; una falla de escritura de
  metadata no invalida ese listener ya verificado.
- `verify_voice_port_isolation.sh` y el smoke permanecen read-only: reconocen
  y reportan metadata stale, pero no reparan ni modifican runtime.
- La UI muestra errores legibles y desactiva `Leer/Repetir` cuando no corresponde.
- La preparación expone bloque actual, total, porcentaje y terminales correctos para `done`, `error` o `canceled`.
- El pipeline PDF conserva texto bruto y limpio, baja el umbral para confiar en text layer útil y aplica heurísticas conservadoras para headers y palabras partidas.

## Archivos principales

- `fusion_reader_v2/documents.py`
- `fusion_reader_v2/service.py`
- `fusion_reader_v2/tts.py`
- `scripts/fusion_reader_v2_server.py`
- `scripts/verify_voice_port_isolation.sh`
- `scripts/smoke_fusion_reader_v2.sh`

## Tests agregados o actualizados

- TTS: recuperación de `owner_pid` stale.
- Reader: error legible cuando TTS no está disponible.
- Server UI: mensajes amigables de TTS, progreso visible y separación principal/consulta.
- PDF: limpieza de palabras partidas, eliminación de ruido mecánico y preservación de `raw_text`.
- Layout: expectativa actualizada para lectura centrada.

## Resultados

- `python3 -m unittest discover -s tests -p 'test_*.py' -v` → `352 OK`
- `bash scripts/verify_voice_port_isolation.sh` → `OK_WITH_EXTERNAL_WARNINGS`
- `bash scripts/smoke_fusion_reader_v2.sh` → `OK_WITH_WARNINGS`
- Runtime manual: TTS `7853` listo, lectura de bloque correcta, preparación correcta, referencia separada, clearing del principal conserva consultas.

## Warnings conocidos

- Advertencias externas de Doctora en `verify`: faltan algunos archivos documentales y las últimas entradas externas no mencionan ambos puertos actuales.
- Consola del navegador: sólo `404` de `favicon.ico`.

## Fuera de alcance

- No se tocó Doctora ni otros runtimes externos.
- No se hizo rediseño total de UI.
- No se reescribió filosóficamente el contenido PDF.

## Flujo correcto

1. Cargar un archivo desde el dropzone para volverlo documento principal.
2. Activar `Cargar este archivo como consulta` sólo cuando se quiera una referencia explícita.
3. Preparar el documento y observar el progreso por bloque.
4. Leer o repetir con TTS listo; si TTS cae, la UI explica el motivo.
5. Guardar notas o consultar el texto desde Laboratorio.
6. Limpiar documento principal sin borrar consultas independientes.
