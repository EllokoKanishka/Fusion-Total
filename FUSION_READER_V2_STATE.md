# Fusion Reader v2: estado de continuidad

Fecha: 2026-07-12

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
- fachada compatible: `fusion_reader_v2/facade.py` -> `service.py`;
- lifecycle: `fusion_reader_v2/services/lifecycle.py`;
- persistencia: `fusion_reader_v2/services/persistence.py` y
  `session_persistence.py`;
- exportación: `fusion_reader_v2/services/audio_export.py`;
- notas: `fusion_reader_v2/services/notes.py`;
- jobs acotados: `fusion_reader_v2/domain/jobs.py`;
- HTTP: `fusion_reader_v2/web/server.py`;
- assets: `fusion_reader_v2/web/static/`;
- operación: `fusionctl`.

`scripts/fusion_reader_v2_server.py` conserva el arranque histórico sin construir
la aplicación al importarse. El prototipo anterior permanece como compatibilidad
y laboratorio, no como dependencia del producto v2.

## Contratos operativos

- API/UI local: `127.0.0.1:8010`;
- TTS GPU: `127.0.0.1:7853`, sólo con owner `fusion_reader_v2` válido;
- TTS CPU: `127.0.0.1:7851`;
- `7852` no se usa; `7854` está reservado a Doctora/Antigravity;
- STT: `auto` prefiere server `8021` y cae a CLI;
- investigación: activación explícita, SearXNG primero, OpenClaw
  `fusion-research` después, nunca `main`;
- HTTP remoto requiere opt-in y token;
- cache, uploads, cuerpos y registros de jobs tienen límites explícitos;
- shutdown espera threads/futures propios y se puede reintentar.

## Persistencia

El estado JSON usa `schema_version`, escritura temporal, `fsync` y reemplazo
atómico. Los estados legacy se respaldan antes de migrar; los corruptos se
preservan con sufijo `.corrupt.<timestamp>` y el lector degrada a defaults.

## Validación vigente

La consolidación se valida desde un venv Python 3.12 limpio con unittest, branch
coverage, Ruff, mypy, py_compile, Node, Playwright, stress, auditoría de
dependencias, scripts de aislamiento y smoke read-only. Los resultados exactos
están en `docs/CONSOLIDATION_FINAL_2026-07-12.md` y `docs/audits/`.

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
