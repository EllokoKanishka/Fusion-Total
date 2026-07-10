# Fusion Reader v2 — Reparación del ciclo de vida de audio

## Garantías completadas en la revisión del PR #11

- `next`, `previous` y `jump` conservan el snapshot público enriquecido, incluida la generación documental y el estado de audio.
- Cada ejecución de preparación usa un `Event` de cancelación nuevo; cancelar y reiniciar en el mismo documento funciona sin workers superpuestos.
- Documento, bloque, texto, voz e idioma forman una única identidad de lectura. Un resultado de una voz anterior queda `stale` y el prefetch solo se comparte por clave exacta.
- Un `Condition` registra lecturas interactivas pendientes y coordina la adquisición del lock del proveedor. Prepare, prefetch y export no pueden iniciar una unidad TTS nueva mientras espera una lectura; un future exacto puede promocionarse por clave y solo esa clave cruza el gate mientras la lectura sigue pendiente.
- La prioridad es cooperativa entre unidades: una inferencia que ya entró al proveedor no se puede interrumpir.
- Cambiar de voz invalida requests, detiene lectura continua y vacía el reproductor antes de publicar el status nuevo.
- Cambiar de voz limpia la cola de prefetch de forma canónica, cancela futures pendientes y no deja estado primario stale.
- El frontend usa leases contables de busy para que clear, promote, load, voice, navigate y read no se liberen entre sí cuando se cruzan abortos y requests nuevos.

## Síntomas y causas verificadas

- La lectura inicial podía quedar sin feedback y el frontend la bloqueaba usando
  un health TTS potencialmente viejo.
- Prefetch se indexaba solo por número de bloque: el índice `0` de un documento
  retirado podía competir con el índice `0` del reemplazo.
- No existía una identidad de instancia documental en read/status.
- Clear y replace no retiraban de forma integral prefetch, prepare, requests y
  reproducción. El evento de cancelación de prepare se reutilizaba y limpiaba
  demasiado pronto.
- Una respuesta HTTP tardía no tenía identidad suficiente para ser rechazada
  por backend o navegador.

## Contrato de identidad

`document_generation` aumenta al cargar, promover, limpiar o reemplazar el
principal. Una lectura captura generación, `doc_id`, índice, texto, voz e
idioma. El resultado expone esa identidad y un `read_request_id`. Si el contexto
cambia durante la síntesis, devuelve `stale/cancelled` sin audio publicable; la
API usa HTTP 409.

Prefetch se identifica mediante generación, índice, voz, idioma y hash del
texto. La cache reusable continúa basada en texto/voz/idioma y no se borra al
limpiar: cache e identidad del reproductor son responsabilidades distintas.

## Cancelación, prioridad y preparación

Una transición documental cancela el evento propio del prepare viejo, retira
su executor/prefetch y crea un evento nuevo. Los workers viejos verifican tanto
generación de prepare como generación documental antes de publicar status.

La lectura interactiva consulta cache primero y comparte síntesis mediante la
cache protegida por lock. Prepare coopera entre bloques y espera trabajo
interactivo/prefetch; una inferencia ya dentro del proveedor puede terminar,
pero su resultado viejo queda descartado. Prioridad efectiva: lectura actual,
prefetch cercano, prepare y exportación.

## Reproductor y solicitudes frontend

Al comenzar clear, carga o promoción del principal, la UI:

1. aborta el request anterior y avanza su secuencia;
2. detiene continuo;
3. ejecuta `pause`, resetea `currentTime`, retira `src` y llama `load()`;
4. valida generación, documento, bloque y secuencia antes de asignar audio.

Las referencias no limpian el audio principal. Navegar invalida lecturas
pendientes y el backend vuelve a comprobar el bloque al finalizar.

## TTS y observabilidad

El backend es la fuente final para Leer. Un hit de cache funciona con TTS down;
un miss consulta el proveedor actual y devuelve error claro. Status distingue
`ready`, `starting` y `temporarily_unavailable`, además de `audio_state`,
`audio_ready` y `audio_cached`. Read informa `ready_ms`, `queue_wait_ms`,
`generation_ms`, `cache_hit` y descarte stale.

Ownership y puertos mantienen las fronteras existentes: Fusion usa `7853` o su
fallback `7851`, nunca `7852` ni `7854`.

## Pruebas

Los tests usan TTS controlado por Events, sin modelos ni sleeps largos. Cubren
reemplazo A/B, prefetch índice cero, clear pendiente, prepare viejo, lecturas
simultáneas, cache con health down, promoción, prioridad exacta del future y
contrato frontend/API.

Prueba manual inicial del primer commit del PR, con Playwright y TTS Fusion
`7853` sano:

- A desde cache: `ready_ms=0`; interacción completa observada ~1.73 s.
- Clear: reproductor verificado con `src=null`, `currentTime=0`, `paused=true`.
- Importación + auto-read de B: ~1.56 s de pared; backend informó
  `ready_ms=791` y `generation_ms=790`.
- El texto visible y el único audio asignado después del reemplazo fueron B;
  nunca se reasignó audio de A.

Validación final:

- la carrera de prioridad exacta se validó con TTS controlado por Events;
- el ownership del busy se validó con pruebas estructurales del frontend;
- no se repitió audio real final porque `8010` y `7853` estaban apagados;
- no inventé un resultado manual que no se haya ejecutado.

## Riesgos restantes

- Una llamada ya iniciada dentro de un proveedor TTS no es físicamente
  abortable; se deja terminar y se descarta por generación.
- El proveedor TTS sigue serializado para proteger motores locales; la prioridad
  es cooperativa entre unidades de síntesis, no preemptiva dentro de una unidad.
- La latencia absoluta depende del modelo y hardware local; el contrato exige
  feedback inmediato e identidad, no un tiempo de inferencia irreal.
