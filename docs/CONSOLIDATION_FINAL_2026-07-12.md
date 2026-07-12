# Consolidation final: 2026-07-12

Baseline: `19ccaa9bc36b09c008af8245586c82e262ee081a`.
Branch: `refactor/v2-total-consolidation`.

## Result

Fusion Reader v2 remains a voice-first, local reader. Reading does not require
LLM, STT or external research. Configuration/composition, HTTP/static assets,
lifecycle, persistence, notes, audio export, bounded jobs and observability now
have explicit owners. The prototype and non-reader tools are compatibility,
manual or legacy code and cannot be imported by the active package.

Public package imports, `fusion_reader_v2.service.FusionReaderV2`, method
signatures, HTTP routes and deliberate JSON fields remain compatible. Session
schema remains version `1`; legacy state is migrated with backup and corrupt
state is preserved before clean recovery.

## Local verification

Executed from `/tmp/fusion-reader-v2-clean-env`, Python 3.13.12, editable
`.[dev]` install:

| Gate | Result |
|---|---|
| Python suite | 500 passed in 29.062 s under final coverage run; 27.819 s through `fusionctl test` |
| Stress | 3 passed in 6.084 s; required 100/100/50/50/20 repetition matrix |
| Line coverage | 91.27% (`6,765 / 7,412`) |
| Branch coverage | 80.01% (`1,965 / 2,456`, independently thresholded) |
| Ruff check/format | pass |
| mypy | pass, 31 active modules |
| py_compile | pass |
| Node contracts | 4 passed |
| Playwright | 1 Chromium daily-flow E2E passed |
| bash syntax | pass for every active `.sh` |
| ShellCheck | pass with official v0.11.0 temporary binary |
| dependency audit | `pip-audit` and `npm audit`: no known vulnerabilities |
| secret-pattern scan | clean |
| docs consistency | pass |
| synthetic benchmark | pass; bounded cache and job registry |

Coverage is enforced independently at 85% lines and 80% branches. Critical
services meet the documented 95% target: lifecycle 98%, persistence 99%, job
registry 98%, path/config boundary 98% and audio export service 95% in the
dated final report.

## Operational verification

- `fusionctl doctor` returned `ok: true`; after the host restart, 7853 was not
  listening and its temporary owner metadata was absent.
- `fusionctl status` correctly reported unavailable when no default UI process
  was running.
- `fusionctl smoke` returned `OK_WITH_WARNINGS`: optional UI/TTS/STT were not
  running; 7852 was free and 7854 remained external/informational.
- An isolated `fusionctl start` used temporary roots and port 38110 and reached
  `/api/status`. This exposed and then froze a clean-venv launcher regression:
  startup now uses the current interpreter with `-m scripts.fusion_reader_v2_server`.
- No service belonging to another system was started, stopped or signalled.

## Personal-data guard

Immediately before and after the final Python/stress validation:

| Root | Before | After | Result |
|---|---:|---:|---|
| real reader library | 8 files / 2,429 bytes | 8 / 2,429 | unchanged |
| real notes | 7 files / 8,314 bytes | 7 / 8,314 | unchanged |
| real Fusion runtime | 120,417 files / 30,980,666,284 bytes | same | unchanged |
| `~/Descargas` | 967 files / 11,846,413,098 bytes | same | unchanged |

The dated repository baseline recorded an earlier Downloads count of 709.
Downloads grew during the long working session through external/user activity;
the final isolated validation neither added nor removed an item. Tests use
temporary HOME/runtime/library/download roots and synthetic providers.

## Remaining human decisions

- No explicit project license was selected or changed; see
  `LICENSING_DECISION_REQUIRED.md`.
- Branch protection remains a post-merge repository-owner action documented in
  `GITHUB_SETTINGS.md`.
- A real microphone was not claimed; the exact human checklist is in the daily
  use matrix.
- Real GPU/provider performance is informational and was not fabricated from
  synthetic results.

Remote workflow results and PR mergeability are authoritative in the open PR.
The consolidation does not merge, enable auto-merge or delete its branch.
