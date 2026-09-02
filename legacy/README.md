# Legacy and compatibility inventory

This directory and the compatibility paths listed below are not the canonical
Fusion Reader v2 product.

| Path | Class | Reason |
|---|---|---|
| `fusion_reader_v2/` | ACTIVE | current product |
| `scripts/fusion_reader_v2_server.py` | COMPATIBILITY | historical wrapper delegates to v2 |
| `scripts/openclaw_direct_chat.py` | COMPATIBILITY/LAB | old reader and voice tests import it |
| `scripts/molbot_direct_chat/` | COMPATIBILITY/LAB | old STT/util/UI tests import it |
| `app/` | LEGACY | previous surface, excluded from active quality gates |
| `tests/manual/` | MANUAL TOOL | human browser/microphone procedures |
| `legacy/config/autonomy_stack/` | ARCHIVE | unrelated browser, n8n and general-agent configuration retained only for traceability |
| `legacy/scripts/verify_reader_mode_v01.sh` | ARCHIVE | superseded verifier |
| `legacy/scripts/chat_voice_es.sh` | LEGACY | general OpenClaw `main` assistant, outside reader boundaries |
| `legacy/scripts/lucy_sensor_client.py` | LEGACY | old sensor/voice prototype with external dependencies |
| `legacy/scripts/x11_file_agent.py` | LEGACY | prohibited desktop file-agent prototype |
| `scripts/benchmark_synthetic_reader.py` | ACTIVE TOOL | hermetic boundedness and latency check |
| `scripts/benchmark_*` | MANUAL TOOL | remaining hardware-dependent measurements |
| `scripts/stress_*` | MANUAL TOOL | historical operational stress tools |
| `docs/archive/` | ARCHIVE | historical snapshots only |

Legacy remains because compatibility callers and regression tests still exist.
Active `fusion_reader_v2` code is forbidden from importing `app`,
`openclaw_direct_chat` or `molbot_direct_chat`; a contract test enforces this.

Future removal requires no repository references, a documented replacement,
passing public/legacy contracts, and a deprecation window for external callers.
