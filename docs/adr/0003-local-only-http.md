# ADR 0003: Local-only HTTP by default

Status: accepted, 2026-07-12.

The server binds to loopback. Non-loopback binding requires explicit remote
opt-in and an API token for mutations. This preserves the local document trust
boundary without adding a heavy authentication framework.
