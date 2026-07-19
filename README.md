# Fusion Reader v2

Fusion Reader v2 es un lector conversacional local, centrado en lectura continua
con voz neural. La lectura de documentos funciona sin LLM, STT ni investigación
externa; esos servicios sólo enriquecen la experiencia cuando están disponibles.

## Inicio rápido

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/fusionctl doctor
./scripts/start_reader_neural_tts_gpu_5090.sh
.venv/bin/fusionctl start
```

La UI queda disponible en `http://127.0.0.1:8010/`. Fusion escucha sólo en
loopback por defecto.

## Capacidades

- carga TXT, MD, PDF, DOCX y ODT;
- lectura inmediata de texto pegado, temporal y sin persistencia automática;
- transcripción local de audio/video a PDF montable;
- traducción local al castellano y exportación WAV con la voz elegida;
- segmentación natural, lectura, repetición y navegación;
- TTS con cache acotada y prefetch;
- preparación y exportación WAV cancelables;
- referencias, promoción, notas y recuperación de sesión;
- diálogo sobre la lectura mediante STT/LLM opcionales;
- selector de inteligencia para diálogo: `Local 14B` por defecto u OpenAI
  mediante el agente OpenClaw aislado `fusion-dialogue`;
- investigación externa sólo ante pedido explícito;
- conversión auxiliar PDF a DOCX.

## Fronteras

- `fusion_reader_v2/` es el producto activo;
- `scripts/fusion_reader_v2_server.py` es sólo un wrapper compatible;
- TTS Fusion usa `7853` con owner válido y fallback CPU `7851`;
- `7852` está sin asignar y `7854` pertenece a Doctora/Antigravity;
- investigación automática: SearXNG local, luego OpenClaw `fusion-research`;
- Fusion nunca usa OpenClaw `main` ni modifica sistemas externos.

La integración opcional con OpenAI no reemplaza la voz ni la lectura local.
Consultá [docs/OPENAI_DIALOGUE_PROVIDER.md](docs/OPENAI_DIALOGUE_PROVIDER.md)
para la autenticación OAuth, la frontera de privacidad y la instalación.

## Operación

```bash
fusionctl start
fusionctl status
fusionctl doctor
fusionctl smoke
fusionctl cache inspect
fusionctl cache prune --dry-run
fusionctl stop
```

`fusionctl doctor`, `status` y los modos dry-run no crean roots ni cambian
servicios ajenos.

## Validación

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python -m unittest discover -s tests/stress -p 'stress_*.py' -v
coverage run --branch -m unittest discover -s tests -p 'test_*.py'
ruff check .
ruff format --check .
mypy fusion_reader_v2
npm test
npm run test:e2e
```

Los conteos y resultados fechados viven en documentos de auditoría, no en este
README.

## Documentación

Leer en este orden:

1. [AGENTS.md](AGENTS.md)
2. [FUSION_READER_V2_STATE.md](FUSION_READER_V2_STATE.md)
3. [Arquitectura](docs/ARCHITECTURE.md)
4. [Operaciones](docs/OPERATIONS.md)
5. [Contratos](docs/CONTRACTS.md)

También: [configuración](docs/CONFIGURATION.md),
[testing](docs/TESTING.md), [seguridad](SECURITY.md),
[portabilidad](docs/PORTABILITY.md), [defaults locales auditados](docs/LOCAL_DEFAULTS_V2.md),
[cierre/backlog histórico](docs/CLOSURE_AND_BACKLOG_V2.md) y
[gates](docs/QUALITY_GATES.md).

Los blueprints y reportes previos son referencias históricas, no estado
operativo vigente.
