# Testing

All automated tests use synthetic providers and temporary roots. They must not
write to the user's real downloads, library, notes or runtime.

## Suites

```bash
python scripts/run_isolated_tests.py
python scripts/run_isolated_tests.py stress
npm test
npm run test:e2e
```

- `tests/unit`: isolated policies and data structures;
- `tests/integration`: in-process HTTP boundaries;
- `tests/contracts`: public imports, signatures and JSON shape;
- `tests/e2e`: Chromium against a synthetic server on a free port;
- `tests/stress`: bounded repetition and leak checks, run nightly;
- `tests/manual`: human-only checks, including real microphone work.

## Coverage

```bash
coverage erase
coverage run --branch scripts/run_isolated_tests.py
coverage run --branch --append scripts/run_isolated_tests.py stress
coverage json -o coverage.json
python scripts/check_coverage_thresholds.py coverage.json
coverage report --fail-under=0
coverage xml
```

The repository target is at least 90% line coverage and 80% branch coverage for
active Python code. Lifecycle, persistence, path security, audio export and job
registry target 95%. Archived legacy and minimal `__main__` wrappers are the
only documented exclusions.

Real TTS, STT, Ollama, external search and microphones are not CI requirements.
Never claim a real microphone validation from synthetic audio.

## Ejecución aislada de la consolidación

Ejecutar `python scripts/run_isolated_tests.py` para la suite completa y `python scripts/run_isolated_tests.py stress` para stress. Crea HOME, estado legacy y temporales privados antes de importar pruebas. Permite conexiones sólo a sockets efímeros creados por la propia suite y bloquea motores/hijos no previstos. FUSION_TEST_EVIDENCE_ROOT puede seleccionar la carpeta de resultados. CI usa este recorrido, también bajo coverage. Un bloqueo del arnés no se cuenta como prueba satisfecha.

Los 174 métodos legacy fueron comparados con sus candidatos; sólo 41 cuerpos AST idénticos se retiraron del módulo compatible. Los otros 133 permanecen como referencia inactiva porque hay diferencias de fixtures y aserciones; no se afirma equivalencia de cobertura por nombre. Se conservan las 42 pruebas activas y el entrypoint compatible. La matriz de comparación completa se conserva en la auditoría local.
