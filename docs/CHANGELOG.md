# Changelog

## 2026-07-17: post-consolidation hardening

- retained ownership of every preparation worker across document resets and shutdown;
- made preparation workers non-daemon and added deterministic cancellation, exception and multi-generation tests;
- added cross-filesystem output publication through copy, `fsync` and same-directory atomic replacement;
- included nested ES modules in packaged builds and added a non-editable wheel CI gate;
- raised the Pillow floor and Python 3.11/3.12 pins to the patched `12.3.0` release;
- preserved machine-readable `pip-audit` evidence when dependency auditing fails;
- aligned the continuity document with the loopback-only HTTP contract.

## 2026-07-12: total structural consolidation

- captured a repository/data-safety baseline and public contract tests;
- introduced installable packaging, central settings and a composition root;
- separated HTTP/static assets from the compatibility wrapper;
- extracted lifecycle, atomic persistence, notes and audio export services;
- added common bounded job registries and cache retention controls;
- hardened uploads, remote mutation guards, errors, health and server isolation;
- added `fusionctl`, clean-environment quality gates, Node and Playwright tests;
- added CI, security, nightly stress, canonical docs and ADRs;
- preserved legacy reader compatibility and external system boundaries.

Historical development notes remain in `docs/HISTORY.md` and `docs/archive/`.
