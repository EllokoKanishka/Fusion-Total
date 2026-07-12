# ADR 0007: Bounded content-addressed audio cache

Status: accepted, 2026-07-12.

Cache identity includes schema, text, voice and language. WAV headers are
validated, writes are atomic, symlinks are rejected, and max age/bytes policies
support inspect, dry-run and explicit prune.
