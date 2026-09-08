# Dictation workspace consolidation — September 2026

## Scope

This note records the design decisions behind the Dictation workspace consolidation after real desktop use exposed three friction points: speech playback could only move forward automatically, the draft had only raw TXT export, and the technical activity console consumed permanent vertical space while only one browser-local draft could be resumed.

## Current-state findings

- Dictation speech is split into bounded TTS chunks and played sequentially through one `<audio>` element. The browser player can seek inside the current chunk, but the workspace had no controls for moving to the previous or next generated chunk.
- The only durable draft was `pandafusion.dictation.v1` in browser `localStorage`, containing `title`, `text` and `updatedAt`. It did not preserve voice, assistant, command mode, PDF preferences, activity history, or multiple projects.
- The console is diagnostic/user activity, not part of the editable draft, so it does not need permanent layout space.
- ReportLab is already a production dependency and Panda already generates A4 PDFs for multimedia transcripts, so PDF export does not require another renderer or an external service.
- `POST /api/dictation/proofread` existed in the dictation handler and documentation but was absent from the central POST route table. The workspace consolidation closes that routing gap.

## Persistence decision

Projects move to Panda-owned versioned JSON under the runtime directory using the existing `AtomicJSONStore`. Each project stores the draft, title, voice, bounded assistant choice, Lucy-command toggle, PDF page-number preference, caret selection and a bounded activity history.

The old `localStorage` draft is retained as an emergency browser-local snapshot and is automatically imported when no Panda-side project exists. This preserves the user's current draft during the upgrade instead of treating the persistence change as a destructive reset.

MDN documents that `localStorage` is scoped to an origin and persists across browser sessions, but browser storage remains subject to browser storage policy, quotas and user clearing. A Panda-owned runtime store is therefore a better fit for named work that should be recoverable independently of one browser profile.

References:
- https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage
- https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria

## PDF decision

The PDF preset is deliberately described as **academic clean / APA-inspired**, not as a claim that an arbitrary dictated text is a complete APA paper.

APA 7 student-paper guidance uses 1-inch margins, a legible font, double spacing, a 0.5-inch first-line paragraph indent and page numbers in the upper-right corner. Panda adapts those typographic conventions to A4, which is the natural paper size for this installation, while keeping page numbers optional.

Reference:
- https://www.apa.org/ed/precollege/psn/2020/09/apa-style-student-papers

## Interaction decisions

- `Atrás` and `Adelante` navigate the current Dictation TTS queue across chunk boundaries. The native audio control remains available for seeking inside the current chunk.
- A finished or manually stopped reading keeps its queue so the user can go back and replay earlier chunks. Editing the source invalidates the old queue so stale text is never read as if it were current.
- `Deshacer` reverts the last edit; `Rehacer` reapplies an edit that was just undone. These remain editor-history operations and are intentionally separate from reading navigation.
- The technical console becomes a collapsed history/activity drawer. The same drawer lists saved Dictation projects; opening a project restores its draft and workspace settings.
- TXT remains a faithful plain-text export. PDF is the presentation export with title, margins, readable typography and optional page numbers.

## Boundaries

No change is made to production Whisper, Qwen ASR experiments, Doctora Lucy, OpenClaw, Telegram, or the main reader's document persistence. The feature remains local and does not introduce paid APIs or external storage.
