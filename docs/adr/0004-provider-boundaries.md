# ADR 0004: Provider boundaries

Status: accepted, 2026-07-12.

TTS, STT, chat and research are injected through contracts. Fusion owns TTS
ports 7853/7851 only; explicit research uses SearXNG then isolated OpenClaw
`fusion-research`, never another product's global provider path.
