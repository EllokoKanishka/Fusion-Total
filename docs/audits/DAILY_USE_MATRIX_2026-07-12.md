# Daily use matrix: 2026-07-12

Evidence was produced with temporary HOME/runtime/library/download roots,
synthetic providers and isolated ports. `PASS` means automated evidence;
`CHECKLIST` means intentionally human-only. No real microphone claim is made.

| # | Operation | Result | Evidence |
|---:|---|---|---|
| 1 | clean start | PASS | isolated `fusionctl start` reached health after clean-venv launcher regression fix |
| 2 | status | PASS | API integration and `fusionctl status` unit contracts |
| 3 | TXT | PASS | document and daily API matrices |
| 4 | MD | PASS | document/editorial matrices |
| 5 | PDF | PASS | multipart import integration and PDF matrices |
| 6 | DOCX | PASS | ZIP/document import matrix |
| 7 | ODT | PASS | ZIP/document import matrix |
| 8 | clear | PASS | contract, integration and E2E |
| 9 | new load | PASS | contract and E2E |
| 10 | reference | PASS | public/API matrix |
| 11 | promotion | PASS | public/API matrix |
| 12 | read | PASS | public, audio and E2E |
| 13 | repeat | PASS | reader suite |
| 14 | next | PASS | reader, stress and E2E |
| 15 | previous | PASS | reader and integration |
| 16 | jump | PASS | reader and integration |
| 17 | voice | PASS | TTS matrix and E2E |
| 18 | prepare | PASS | lifecycle/API/E2E |
| 19 | cancel prepare | PASS | lifecycle/stress/E2E |
| 20 | restart prepare | PASS | prepare regressions |
| 21 | export current | PASS | audio export suite |
| 22 | export block | PASS | audio export/API matrix |
| 23 | export range | PASS | audio export/API matrix |
| 24 | export full | PASS | audio export/API matrix |
| 25 | cancel export | PASS | audio export/stress |
| 26 | notes CRUD | PASS | notes/API/E2E |
| 27 | document chat | PASS | conversation/API matrix |
| 28 | free chat | PASS | laboratory/API matrix |
| 29 | reasoning modes | PASS | conversation/server matrices |
| 30 | dialogue text | PASS | dialogue/API/E2E |
| 31 | synthetic audio dialogue | PASS | dialogue suite with synthetic WAV/STT |
| 32 | STT server absent | PASS | STT provider degradation matrix |
| 33 | STT CLI fallback | PASS | STT provider and path matrices |
| 34 | Ollama absent | PASS | conversation health/error matrix |
| 35 | SearXNG absent | PASS | research fallback matrix |
| 36 | PDF to DOCX success | PASS | PDF conversion/API matrix |
| 37 | PDF to DOCX cancel | PASS | PDF job cancellation suite |
| 38 | process restart | PASS | session persistence and launcher contracts |
| 39 | state recovery | PASS | persistence/service matrices |
| 40 | corrupt state | PASS | atomic persistence recovery matrix |
| 41 | cache prune dry-run | PASS | audio cache and fusionctl tests |
| 42 | shutdown | PASS | lifecycle, two-app and stress suites |

The browser E2E verifies one network request per user action for the covered
daily flow. Stress executes 100 load/clear cycles, 100 navigation cycles, 50
export/cancel cycles, 50 prepare/cancel cycles and 20 repeated shutdowns.

## Human microphone checklist

1. Start the isolated Fusion services intended for the session.
2. Confirm the selected physical input and visible input level.
3. Say `leer`, `pausa`, `continuar`, `siguiente`, `anterior` and a note command.
4. Interrupt active speech once and confirm the cursor does not skip text.
5. Confirm no transcript is emitted during silence.
6. Stop Fusion and confirm ports/processes owned by other systems are unchanged.

Status: `CHECKLIST`, not executed during automated consolidation.
