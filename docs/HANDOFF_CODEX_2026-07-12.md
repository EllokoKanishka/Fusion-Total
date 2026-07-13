# Handoff Codex: consolidacion Fusion Reader v2

## Estado exacto

- Repo: `/home/lucy-ubuntu/Escritorio/Fusion Total`
- Branch: `refactor/v2-total-consolidation`
- Head antes de este handoff: `7afc99124c5bc8dd57910898a4b27414c471a24a`
- PR unico: https://github.com/EllokoKanishka/Fusion-Total/pull/13
- Base: `main` en baseline `19ccaa9bc36b09c008af8245586c82e262ee081a`
- PR draft, mergeable, sin auto-merge. No hacer merge ni abrir otro PR.
- Arbol limpio y rama sincronizada antes de agregar este documento.

## Validacion verde

- Python 3.12: 501 tests OK.
- Stress: 3 tests OK, matriz 100/100/50/50/20.
- Cobertura local: 91.63% lineas (`6795/7416`), 80.82% ramas (`1985/2456`).
- CI remoto verde: Python 3.11, Python 3.12, quality/coverage, Shell, JavaScript,
  Playwright y dependency/boundary audit.
- Ruff, format, mypy, py_compile, bash-n, ShellCheck, pip-audit, npm audit,
  secret scan, docs consistency, benchmark, verify y smoke fueron ejecutados.
- `fusionctl doctor`: `ok: true`; UI/TTS/STT apagados tras reinicio degradan con warnings.
- Puerto 7852 libre; 7854 externo y no modificado.

## Unico bloqueo

El check de resultados `CodeQL` sigue rojo con 6 alertas `py/path-injection`, aunque
el workflow `CodeQL Python and JavaScript` termina verde. Check run mas reciente:
`86708388760`.

Alertas actuales:

- `fusion_reader_v2/documents.py`: sinks en lineas aproximadas 123, 145 y 939.
- `fusion_reader_v2/web/server.py`: sinks en lineas aproximadas 267 y 275 (dos flujos).

Ya se hizo:

- allowlist literal de extensiones temporales;
- normalizacion POSIX/Windows de basename;
- pruebas de traversal, absoluto, symlink externo, extension, missing, cache y colisiones;
- 6 de las 12 alertas originales desaparecieron;
- se intento `# lgtm[py/path-injection]` inline y luego
  `# codeql[py/path-injection]` en linea propia; esta configuracion no suprimio las 6 restantes.

No repetir esos dos intentos. Revisar el detalle/dataflow de cada alerta y optar por
una refactorizacion que CodeQL modele, una configuracion CodeQL documentada, o una
clasificacion formal de falso positivo con justificacion auditable. No bajar coverage,
no desactivar CodeQL globalmente y no cerrar alertas sin revisar.

## Comandos para retomar

```bash
cd "/home/lucy-ubuntu/Escritorio/Fusion Total"
git status -sb
git log -6 --oneline
gh pr view 13 --json isDraft,mergeable,mergeStateStatus,headRefOid,url,autoMergeRequest
gh pr checks 13
gh api repos/EllokoKanishka/Fusion-Total/code-scanning/alerts?pr=13\&tool_name=CodeQL\&state=open\&per_page=100
```

Entorno Python 3.12 temporal disponible mientras no se reinicie:
`/tmp/fusion-reader-v2-py312`.

Despues de resolver CodeQL:

1. Ejecutar tests focalizados y suite/cobertura completa.
2. Commit atomico, push a la misma rama y actualizar body del PR.
3. Esperar todos los checks.
4. Solo con todo verde ejecutar `gh pr ready 13`.
5. Confirmar working tree limpio, PR abierto, no auto-merge y ningun PR adicional.

