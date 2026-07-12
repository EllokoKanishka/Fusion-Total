# ADR 0002: Reading is independent of the LLM

Status: accepted, 2026-07-12.

Document load, chunking, navigation, TTS and cached playback do not depend on
STT, Ollama or research providers. Optional provider failures are readiness
degradations, not liveness failures.
