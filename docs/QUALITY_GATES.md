# Quality gates

The PR is ready only when these gates are green:

| Gate | Command/check |
|---|---|
| Python 3.11/3.12 | editable install and unittest discovery |
| Formatting/lint | `ruff check .`, `ruff format --check .` |
| Types | `mypy fusion_reader_v2` |
| Coverage | active line >=85%, branch >=80%; critical services >=95% |
| Compile | `python -m py_compile` over active Python |
| Shell | `bash -n` and `shellcheck` |
| JavaScript | `npm test`, `npm run check:ui` |
| Browser | synthetic-provider Playwright flow |
| Security | pip-audit, secret scan, path tests, CodeQL |
| Stress | repetition, registry and leak suite |
| Boundaries | voice-port verify and read-only smoke |
| Repository | docs/dependency consistency, tracked-tree hygiene and `git diff --check` |

External Doctora warnings are informational and cannot fail Fusion's strict
checks. Real microphone validation and the licensing decision are human-only.
Branch protection is intentionally deferred; see `GITHUB_SETTINGS.md`.
