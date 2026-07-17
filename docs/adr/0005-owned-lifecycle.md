# ADR 0005: Owned deterministic lifecycle

Status: accepted, 2026-07-12.

Every background thread, future, executor and cancellation event has an owner.
Shutdown rejects new work, cancels cooperatively, joins with deadlines and is
idempotent/retryable. Daemon threads are not a cleanup strategy.
