# Panda Fusión: estado de continuidad

Fecha: 2026-07-17
Versión del paquete: `2.0.0`

## Norte

El producto activo es un lector conversacional voice-first. La ruta crítica es:

```text
Documento -> chunks naturales -> TTS -> cache/prefetch -> navegador
```

STT, Ollama, SearXNG y OpenClaw son degradables. Su ausencia no debe impedir
cargar, navegar o leer.

## Arquitectura vigente

- paquete activo: `fusion_reader_v2/`;
- composition root: `fusion_reader_v2/composition.py`;
- configuración: `fusion_reader_v2/config.py`;
- fachada pública real: `fusion_reader_v2/facade.py`; `service.py` conserva solo el import compatible;
- lifecycle: `fusion_reader_v2/services/lifecycle.py`;
- persistencia: `fusion_reader_v2/services/persistence.py` y
  `session_persistence.py`;
- preparación: `fusion_reader_v2/services/preparation.py` conserva ownership de todos sus workers hasta que terminan;
- exportación: `fusion_reader_v2/services/audio_export.py`;
- notas: `fusion_reader_v2/services/notes.py`;
- jobs acotados: `fusion_reader_v2/domain/jobs.py`;
- HTTP: `fusion_reader_v2/web/server.py`;
- assets: `fusion_reader_v2/web/static/`, incluidos los módulos ES dentro de la wheel;
- operación: `fusionctl`.

`scripts/fusion_reader_v2_server.py` conserva el arranque histórico sin construir
la aplicación al importarse. El prototipo anterior permanece como compatibilidad
y laboratorio, no como dependencia del producto v2.

## Contratos operativos

- API/UI local: `127.0.0.1:8010`;
- HTTP es exclusivamente loopback: `localhost`, `127.0.0.1` y `::1`; cualquier bind remoto se rechaza;
- TTS GPU: `127.0.0.1:7853`, sólo con owner `fusion_reader_v2` válido;
- TTS CPU: `127.0.0.1:7851`;
- `7852` no se usa; `7854` está reservado a Doctora/Antigravity;
- STT: `auto` prefiere server `8021` y cae a CLI;
- investigación: activación explícita, SearXNG primero, OpenClaw
  `fusion-research` después, nunca `main`;
- cache, uploads, cuerpos y registros de jobs tienen límites explícitos;
- shutdown espera threads/futures propios y se puede reintentar.
- `Texto rápido` carga texto pegado en el `ReaderSession` normal, permite iniciar
  desde el cursor y no persiste el contenido ni lo agrega a la biblioteca.
- `Audio y video` vive en el panel derecho y comparte un pipeline local: FFmpeg
  normaliza a FLAC, Whisper transcribe con idioma y tiempos, ReportLab genera
  PDF y, opcionalmente, Ollama traduce al castellano antes de reutilizar el TTS
  y la voz seleccionada para exportar WAV.
- `Dictado` abre un escritorio exclusivo de pantalla completa: captura audio en
  el navegador, reutiliza el STT local en castellano, aplica sólo operaciones
  editoriales acotadas cuando la frase invoca a “Lucy” y conserva el borrador en
  `localStorage`; comparte la voz del lector y elimina el audio temporal después
  de cada turno.
- las órdenes conocidas de Dictado son instantáneas; las desconocidas pueden
  escalar, por elección explícita, a Qwen3 4B local o GPT-5 nano mediante
  `fusion-dialogue`. Sólo reciben una ventana acotada del borrador y devuelven
  una operación validada y reversible; STT y TTS permanecen aislados.
- la unidad de usuario instalada arranca TTS GPU/CPU antes del servidor web; si
  el motor aún no responde, ambos selectores conservan el catálogo conocido de
  veinte voces con estado degradado hasta recuperar el catálogo dinámico real.
- diálogo: `Local 14B` sigue siendo el default; el usuario puede seleccionar
  OpenAI explícitamente mediante OpenClaw `fusion-dialogue`. La voz, la lectura
  y multimedia permanecen locales y no hay fallback silencioso.

## Persistencia

El estado JSON usa `schema_version`, escritura temporal, `fsync` y reemplazo
atómico. Los estados legacy se respaldan antes de migrar; los corruptos se
preservan con sufijo `.corrupt.<timestamp>` y el lector degrada a defaults.

## Validación vigente

La consolidación se valida con Python 3.11 y 3.12 mediante unittest, branch
coverage, Ruff, mypy, py_compile, Node, Playwright, stress, auditoría de
dependencias, scripts de aislamiento y smoke read-only. CI también construye e
inspecciona una wheel no editable para comprobar que la interfaz y sus módulos
ES estén empaquetados. Los resultados exactos viven en el PR y en los documentos
de auditoría fechados.

Los defaults locales heredados y sus overrides permanecen auditados en
`docs/LOCAL_DEFAULTS_V2.md`; el cierre/backlog anterior se conserva en
`docs/CLOSURE_AND_BACKLOG_V2.md` como referencia histórica.

## Retomar trabajo

1. Leer `AGENTS.md`.
2. Leer este archivo.
3. Leer `docs/ARCHITECTURE.md`.
4. Leer `docs/OPERATIONS.md`.
5. Leer `docs/CONTRACTS.md`.
6. Tratar blueprints y documentos archivados sólo como referencia.
7. Ejecutar `git status --short` y no revertir cambios ajenos.
