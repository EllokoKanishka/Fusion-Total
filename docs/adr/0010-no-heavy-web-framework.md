# ADR 0010: No heavy web framework

Status: accepted, 2026-07-12.

The local API uses the standard-library HTTP server, an explicit route table and
static HTML/CSS/JS. Current scope does not justify framework lifecycle,
dependency or build complexity; route/error/body policies remain modular.
