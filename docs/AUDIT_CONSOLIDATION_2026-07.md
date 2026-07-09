# Auditoría de Consolidación — Fusion Reader v2

Fecha: 2026-07-09
Rama: `audit/consolidacion-fusion-reader-v2`

## Resumen ejecutivo

Fusion Reader v2 está razonablemente consolidado en su núcleo: la suite principal y la suite completa de tests pasan, no se detectó uso indebido de `7852`, `7854` ni de `OpenClaw main` dentro de los archivos auditados, y los contratos principales de lectura, diálogo, TTS y exportación siguen cubiertos.

Los problemas reales encontrados en esta auditoría se concentran en consolidación y mantenimiento:

- documentación desfasada en conteos y estado de validación;
- una ruta absoluta hardcodeada al entorno local en `fusion_reader_v2/pdf_to_docx.py`;
- verificación de aislamiento de voz demasiado acoplada al estado y artefactos de otro sistema local;
- deuda técnica de tests duplicados entre `tests/test_fusion_reader_v2.py` y la suite modular;
- dependencias operativas importantes no declaradas en un manifiesto único.

Se aplicaron solo parches mínimos y seguros: corrección de la ruta hardcodeada de Docling, agregado de test para esa regresión y actualización de documentación de validación.

## Estado real de tests

Comandos corridos:

```bash
git status --short
git log --oneline -n 20
python3 -m unittest tests.test_fusion_reader_v2 -v
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
./scripts/verify_voice_port_isolation.sh
```

Resultado real inicial:

- `git status --short`: limpio al inicio.
- `git log --oneline -n 20`: historial reciente coherente con consolidaciones previas.
- `tests.test_fusion_reader_v2`: `239 OK`.
- `tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress`: `35 OK`.
- `python3 -m unittest discover -s tests -p 'test_*.py' -v`: `333 OK`.
- `./scripts/verify_voice_port_isolation.sh`: `FAIL`.

Revalidación posterior a los parches:

- `python3 -m unittest tests.test_pdf_to_word.PDFToWordTests.test_docling_gpu_env_prefers_env_and_otherwise_uses_repo_relative_default -v`: `1 OK`.
- `python3 -m unittest tests.test_fusion_reader_v2 -v`: `240 OK`.
- `python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v`: `35 OK`.
- `python3 -m unittest discover -s tests -p 'test_*.py' -v`: `334 OK`.
- `./scripts/verify_voice_port_isolation.sh`: sigue `FAIL`.

Detalle del fallo de aislamiento de voz:

- faltan `/home/lucy-ubuntu/Escritorio/doctora-lucy/bitacora_mantenimiento.md`;
- falta `/home/lucy-ubuntu/Escritorio/doctora-lucy/memoria/bitacora_mantenimiento.md`;
- `7853` no estaba escuchando al momento del check;
- `7854` sí estaba escuchando;
- `7852` estaba libre.

## Inconsistencias encontradas

### Documentación

- `README.md` declaraba `tests.test_fusion_reader_v2: 119 OK`; el valor real inicial fue `239 OK` y el estado final tras el parche quedó en `240 OK`.
- `FUSION_READER_V2_STATE.md` declaraba `216 OK`; el valor real inicial fue `239 OK` y el estado final tras el parche quedó en `240 OK`.
- ninguno de esos documentos reflejaba la suite completa `discover`, primero observada en `333 OK` y luego en `334 OK` tras agregar un test de regresión.
- ambos documentos asumían `verify_voice_port_isolation.sh: OK`, pero el resultado real de esta auditoría fue `FAIL`.

### Tests

- `tests/test_fusion_reader_v2.py` sigue funcionando como contenedor legacy grande.
- la suite modular reutiliza muchos de esos casos vía `attach_legacy_tests(...)`.
- esto no rompe hoy, pero duplica superficie de mantenimiento y hace más difícil saber qué suite es la fuente canónica.

### Operación

- `scripts/verify_voice_port_isolation.sh` mezcla validaciones del repo con chequeos sobre artefactos y memoria de Doctora fuera de este proyecto.
- el script puede fallar aunque Fusion propio esté sano, simplemente por estado externo incompleto o por no tener `7853` levantado en ese instante.

## Errores confirmados

### 1. Ruta absoluta hardcodeada en PDF -> DOCX

Archivo:

- `fusion_reader_v2/pdf_to_docx.py`

Hallazgo:

- `_get_docling_gpu_env()` devolvía una ruta absoluta fija al workspace actual.

Impacto:

- rompe portabilidad entre clones, usuarios, mounts o cambios de ruta del repo;
- dificulta reproducibilidad y despliegue local limpio;
- contradice la orientación general del proyecto a trabajar desde `runtime/` relativo al repo.

Estado:

- corregido en esta auditoría para usar `FUSION_READER_DOCLING_GPU_ENV` si existe, o un default relativo al repo.

### 2. Verificador de puertos demasiado acoplado a otro sistema

Archivo:

- `scripts/verify_voice_port_isolation.sh`

Hallazgo:

- falla por archivos ausentes en Doctora aunque el problema no pertenezca al código de Fusion.

Impacto:

- falsa señal roja en auditorías de consolidación;
- mezcla salud de Fusion con sincronización documental de otro proyecto;
- vuelve el check menos confiable como smoke local de este repo.

Estado:

- no se cambió semántica en esta pasada para no aflojar una frontera sin discusión explícita;
- queda recomendado desacoplarlo en un parche posterior controlado.

## Deuda técnica

- `tests/test_fusion_reader_v2.py` concentra demasiada historia y sigue siendo la base de varios tests “legacy”.
- faltan manifiestos claros de dependencias Python y binarios de sistema para la ruta v2.
- hay imports/variables sobrantes menores en `fusion_reader_v2/pdf_to_docx.py` y `scripts/fusion_reader_v2_server.py`.
- `FusionReaderV2` en `fusion_reader_v2/service.py` mezcla varias responsabilidades: sesión, prefetch, chat, diálogo, exportación y persistencia.
- hay múltiples artefactos históricos bajo `runtime/fusion_reader_v2/` que no deberían tomarse como estado canónico del producto.

## Riesgos

- riesgo de portabilidad: medio.
  La ruta absoluta de Docling ya era una regresión real.

- riesgo operacional: medio.
  El check de puertos da fallos acoplados a Doctora y puede dificultar diagnósticos.

- riesgo de mantenimiento de tests: medio.
  La duplicación legacy/modular aumenta costo de cambios.

- riesgo documental: bajo/medio.
  Los conteos viejos y comandos incompletos pueden inducir diagnósticos falsos.

## Parches mínimos recomendados

- mantener la corrección aplicada en `fusion_reader_v2/pdf_to_docx.py` para usar env var o ruta relativa al repo;
- conservar el test agregado para cubrir esa regresión;
- mantener actualizados `README.md`, `FUSION_READER_V2_STATE.md` y `docs/OPERATIONS.md` con conteos reales;
- en una siguiente pasada controlada, separar `verify_voice_port_isolation.sh` en:
  - checks estrictos de Fusion;
  - checks informativos de frontera con Doctora.
- empezar a retirar gradualmente `attach_legacy_tests(...)` cuando cada suite modular tenga cobertura propia estable.

## Checklist de smoke local

- correr `python3 -m unittest tests.test_fusion_reader_v2 -v`
- correr `python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v`
- correr `python3 -m unittest discover -s tests -p 'test_*.py' -v`
- correr `./scripts/verify_voice_port_isolation.sh`
- verificar que `7852` siga libre
- verificar que Fusion nunca seleccione `7854`
- verificar que la ruta de Docling pueda resolverse con `FUSION_READER_DOCLING_GPU_ENV` o default relativo
- levantar `./scripts/start_fusion_reader_v2.sh` y consultar `http://127.0.0.1:8010/api/status`

## Próximos pasos

- desacoplar el verificador de puertos del estado documental externo de Doctora;
- definir un manifiesto único de dependencias v2:
  `python-docx`, `Pillow`, `pdftotext`, `pdfinfo`, `tesseract`, `ffmpeg`, `docling`, `torch`;
- seguir moviendo cobertura desde `tests/test_fusion_reader_v2.py` hacia archivos modulares sin duplicación;
- revisar, en una pasada separada, si `FusionReaderV2` debe delegar exportación y diálogo a servicios internos más chicos sin cambiar contratos públicos.
