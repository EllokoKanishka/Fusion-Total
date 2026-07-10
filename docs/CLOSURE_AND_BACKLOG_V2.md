# Fusion Reader v2 — Cierre de consolidación

Snapshot: 2026-07-10  
Commit de `main`: `ecf70721109f87d1e58c5401f4950b5506917287`

Esta es una fotografía técnica del cierre, no una verdad eterna. Cierra la fase
de reparación y consolidación formada por los PR #2, #3, #4, #5, #6, #7, #9 y
#8. Los trabajos siguientes deben tener objetivos funcionales concretos y estar
guiados por el backlog.

## 1. Alcance del cierre

La fase estabilizó, verificó y documentó el sistema existente. Consolidó
fronteras operativas, dependencias, defaults, diagnóstico, STT, ownership TTS y
regresiones centrales de lectura/documentos. No agregó un producto distinto ni
incorporó sistemas externos al runtime de Fusion.

## 2. Estado verificado de main

Base verificada: `ecf70721109f87d1e58c5401f4950b5506917287`.

- Suite: `python3 -m unittest discover -s tests -p 'test_*.py' -v`.
- Resultado posterior al PR #8: `Ran 362 tests in 26.966s` — `OK`.
- Resultado vigente con los tests estructurales de cierre:
  `Ran 365 tests in 26.903s` — `OK`.
- `py_compile` de documents, service, TTS, dialogue y server: `OK`.
- `bash -n` de smoke, verify, launcher y launcher STT: `OK`.
- Verify: código `0`, `FINAL RESULT: OK_WITH_EXTERNAL_WARNINGS`.
- Smoke: código `0`, `FINAL RESULT: OK_WITH_WARNINGS`.
- Aislamiento: checks estrictos `OK`; `7852` libre.

Esto confirma salud del código y del aislamiento comprobado, no que todos los
servicios locales estén siempre encendidos. La disponibilidad runtime se mide
en cada ejecución. Los warnings documentales de Doctora son externos y no son
fallos propios de Fusion.

## 3. Componentes consolidados

- documentación central, manifiesto de dependencias y requirements core/opcionales;
- defaults locales explícitos, auditables y overrideables;
- smoke no invasivo y verify read-only;
- aislamiento de `7851`, `7852`, `7853` y `7854`;
- owner file TTS y reconciliación runtime atómica y validada;
- UI/API `8010` y STT server `8021`;
- contrato STT `auto`/`server`/`cli` y aliases compatibles;
- PDF con `raw_text` y limpieza conservadora;
- documento principal separado de consultas independientes;
- preparación con progreso y terminales `done`, `error` y `canceled`;
- disponibilidad TTS expresada con mensajes claros y controles coherentes;
- guardas de convivencia GPU y suite de tests vigente.

## 4. Contratos operativos

### Puertos

| Puerto | Contrato |
| --- | --- |
| `8010` | UI/API de Fusion Reader v2 |
| `7853` | TTS GPU de Fusion; exige ownership válido |
| `7851` | fallback TTS CPU |
| `7852` | histórico/no asignado; no usar |
| `7854` | Doctora/externo; Fusion no lo usa |
| `8021` | Faster Whisper Server |
| `11434` | Ollama local para diálogo |
| `8080` | SearXNG local para investigación explícita |

### STT

- `auto`: server sano primero; Whisper CLI como fallback normal.
- `server`: exclusivamente Faster Whisper Server, sin degradación silenciosa.
- `cli`: exclusivamente `FUSION_READER_STT_COMMAND`; no inicia `8021`.
- `FUSION_READER_STT_ENV` selecciona el entorno Python del server.
- `FUSION_READER_STT_URL` configura el cliente HTTP.
- `FUSION_READER_STT_PORT` configura launcher/server, default `8021`.

### Diagnóstico

- tests y `py_compile` validan código sin afirmar disponibilidad de servicios;
- `bash -n` valida launchers y diagnósticos;
- verify es read-only y separa fallos estrictos de warnings externos;
- smoke es no invasivo: no levanta, mata ni repara servicios.

### TTS ownership

El owner file declara a Fusion sobre `7853`. El provider valida proceso real y
puerto antes de confiar en él. Si el PID documental está stale pero el listener
es legítimo, el runtime puede reconciliar metadata mediante escritura atómica.
Verify y smoke solo observan: reportan metadata stale sin modificar runtime.

## 5. Qué queda formalmente cerrado

- auditoría y fronteras del producto v2;
- aislamiento de voz y semántica de warnings;
- manifiesto de dependencias, requirements y defaults locales;
- smoke/verify no invasivos;
- coherencia operativa del camino STT;
- regresiones confirmadas de documento, lectura, TTS, preparación y PDF;
- una base de tests reproducible para iniciar trabajo funcional.

## 6. Backlog priorizado

### P0 — Bloqueos reales

No hay bloqueos P0 confirmados al cierre de esta fase.

### P1 — Próximo trabajo funcional

1. Validar end-to-end el diálogo oral con micrófono real, incluyendo fallback STT.
2. Ajustar VAD, barge-in, eco y ruido con mediciones de escenarios reales.
3. Hacer más visible en UI el provider STT efectivo durante una interacción oral.
4. Probar importación/lectura con PDFs diversos y mejorar la experiencia de referencias.
5. Separar físicamente el entorno STT del entorno histórico compartido, sin romper overrides.

### P2 — Deuda técnica

- reducir responsabilidades concentradas en `service.py` mediante cambios acotados;
- incorporar CI remoto y ampliar tests de integración;
- evaluar `pyproject`/lockfile para reproducibilidad sin modelar entornos GPU externos;
- mejorar portabilidad de defaults locales absolutos;
- reducir documentación histórica redundante;
- eliminar la ambigüedad del status global devuelto al importar referencias;
- documentar o aislar mejor diccionarios del sistema usados por heurísticas PDF.

P2 no bloquea la siguiente fase funcional.

### Fuera de alcance

- Doctora, Antigravity, Telegram y OpenClaw `main`;
- entornos externos, drivers, hardware y servicios ajenos.

## 7. Riesgos conocidos

- Riesgo actual: la disponibilidad de TTS, STT, Ollama y SearXNG depende del runtime local.
- Deuda técnica: existen defaults locales externos y heurísticas PDF dependientes del host.
- Warning externo: faltan documentos informativos de Doctora; no es un fallo de Fusion.
- Comportamiento deliberado: la limpieza PDF es heurística y conservadora para evitar reescritura destructiva.

## 8. Criterio de inicio de la siguiente fase

La consolidación termina con este snapshot. Los próximos PR deben resolver un
objetivo funcional concreto. No se agregan nuevas auditorías salvo evidencia
nueva; P1 guía el siguiente desarrollo.

## 9. Comandos canónicos

```bash
python3 -m pip install -r requirements/fusion-reader-v2.txt
python3 -m pip install -r requirements/fusion-reader-v2-optional.txt
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile fusion_reader_v2/documents.py fusion_reader_v2/service.py fusion_reader_v2/tts.py fusion_reader_v2/dialogue.py scripts/fusion_reader_v2_server.py
bash -n scripts/smoke_fusion_reader_v2.sh
bash -n scripts/verify_voice_port_isolation.sh
./scripts/verify_voice_port_isolation.sh
./scripts/smoke_fusion_reader_v2.sh
./scripts/start_fusion_reader_v2.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/open_fusion_reader.sh
```
