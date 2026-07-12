# ADR 0006: Versioned atomic persistence

Status: accepted, 2026-07-12.

JSON state carries `schema_version` and is written via private temporary file,
flush, fsync and atomic replace. Legacy input is backed up before migration;
corruption is quarantined and degrades to defaults.
