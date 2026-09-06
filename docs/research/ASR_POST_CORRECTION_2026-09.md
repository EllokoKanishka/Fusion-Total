# ASR post-correction benchmark — September 2026

## Question

After validating `large-v3-turbo` as PandaFusion's production ASR engine, the next question was whether a local LLM should perform a second, conservative lexical correction pass over the Whisper transcript, and whether the larger Qwen 27B model materially outperforms the already-installed 14B model for that narrow task.

## Local model inventory

The target workstation is an RTX 5090 with 32 GB VRAM. The directly comparable operational models already installed were:

- `qwen3:14b-q8_0` — Qwen3 14.8B, Q8_0, about 15.9 GB on disk;
- `qwen3.5:27b` — Qwen3.5 27.8B, Q4_K_M, about 17.4 GB on disk.

A Q6 27B GGUF also exists locally, but the current Ollama build cannot initialize that experimental architecture, and no 14B Q6 counterpart is installed. No new model was downloaded for this decision.

## Measured throughput

On the same machine, sequentially:

| Metric | Qwen3 14B Q8_0 | Qwen3.5 27B Q4_K_M |
| --- | ---: | ---: |
| Model load | 2.72 s | 3.61 s |
| Prompt processing | 4524 tok/s | 1718 tok/s |
| Generation | 84.57 tok/s | 57.48 tok/s |
| Approx. inference VRAM | 17.0 GiB | 25.9 GiB |
| Approx. free VRAM | 15.6 GiB | 6.7 GiB |
| Correction latency, thinking off | ~0.30 s / phrase | ~0.50 s / phrase |

The 14B processes prompt context about 2.6x faster and generates about 1.47x faster while leaving substantially more GPU headroom for Whisper, TTS and the desktop stack.

## Conservative correction task

Both models received the same deterministic instructions:

- correct only evident ASR / spelling / proper-name / technical-term errors;
- do not summarize, explain, add, remove, restyle or paraphrase;
- preserve sentence structure;
- use a supplied glossary only as lexical evidence;
- return only the corrected text.

The benchmark used real Foundation audiobook errors, including variants of `Hari Seldon`, `Gaal Dornick`, `Trantor`, `Terminus`, `psicohistoria`, `psicohistoriador` and `Anacreon`.

Observed result:

- 14B: 12/12 target corrections correct;
- 27B: 12/12 target corrections correct;
- 14B: 6/6 already-correct preservation cases unchanged exactly;
- 27B: 6/6 already-correct preservation cases unchanged exactly;
- neither model added explanations, omitted content or introduced collateral edits in this test set.

This is a focused engineering benchmark, not a general intelligence ranking. The 27B can still be preferable for harder reasoning tasks, but it did not buy any measurable quality improvement for the post-ASR correction contract.

## Thinking mode

Thinking was actively harmful for this task:

- 14B thinking-on average: about 5.65 s per phrase;
- 27B thinking-on average: about 78.3 s per phrase;
- no accuracy gain was observed over thinking-off.

Therefore the post-corrector must force `thinking=false` regardless of the normal chat profile.

## Production decision

Use `qwen3:14b-q8_0` for the optional local ASR post-correction pass.

The pass is deliberately **opt-in per media upload**. Whisper remains the source transcript engine. The LLM receives each bounded transcript paragraph plus the same optional context/glossary used to help STT.

Safety rules for integration:

1. temperature 0;
2. thinking off;
3. local Ollama only;
4. transcript/context/glossary treated as untrusted data, never instructions;
5. paragraphs corrected independently so timestamps remain attached to the same paragraph position;
6. output rejected if word count, character length or similarity suggests a rewrite rather than a lexical correction;
7. rejected paragraphs fall back to the original Whisper text;
8. if Qwen is unavailable, the user is told before a requested corrected job begins;
9. no automatic replacement of the production Whisper engine.

## Implementation status

The feature branch implements this decision without changing the production ASR engine. The existing media context card gains an opt-in `Corregir después con Qwen 14B` checkbox. The request preflight verifies that the configured local model is actually present before starting a corrected job.

The correction pass runs after Whisper and before transcript/PDF publication. Each timestamped paragraph is corrected independently, `thinking` is forced off, temperature is fixed at zero, and a deterministic similarity/length gate rejects outputs that look like rewrites. Rejected paragraphs fall back to the untouched Whisper text and are surfaced in job warnings/telemetry instead of silently replacing the source transcript.

The implementation also records whether correction was requested/completed, the correction model, changed/rejected paragraph counts, and correction timing. Focused repository tests cover request scoping, deterministic Ollama parameters, preservation guards, model availability, and the UI/API contract. The final feature files are formatted with the repository's pinned Ruff formatter before the full merge gates run.

## Why not 27B

For this narrow task, 27B produced the same correction and preservation score while being slower and consuming roughly nine additional GiB of VRAM. The additional reasoning capacity has no demonstrated benefit here, while the reduced GPU margin would make concurrent local services less comfortable.

The 27B remains useful as a general model benchmark target and can be revisited if a materially harder correction corpus exposes failures that the 14B cannot resolve conservatively.
