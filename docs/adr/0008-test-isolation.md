# ADR 0008: Test isolation

Status: accepted, 2026-07-12.

Tests inject temporary runtime, library, downloads, notes, cache and provider
roots. CI uses synthetic providers and no GPU/network services. Real microphone
checks remain an explicit human procedure.
