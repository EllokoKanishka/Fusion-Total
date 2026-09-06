# STT quality research — September 2026

## Problem

The current local pipeline is reliable, but the `small` Whisper checkpoint makes avoidable lexical errors on long Spanish material, especially proper nouns, fictional names, places and technical vocabulary. This is a general ASR problem rather than a PandaFusion-specific defect: rare/entity words remain a major target of contextual-biasing research.

## Findings

### 1. Qwen3-ASR-1.7B is the strongest replacement candidate

Qwen3-ASR-1.7B is Apache-2.0, supports Spanish, long audio and prompt/context biasing. Its 2026 technical report describes the 1.7B model as state of the art among open-source ASR systems and competitive with strong proprietary APIs. The native Transformers model card explicitly supports free-form context/hotwords for names and domain vocabulary.

Sources:
- https://arxiv.org/abs/2601.21337
- https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf

Why it is not the immediate default: PandaFusion currently depends on Whisper segment timestamps throughout the PDF/media pipeline. Qwen3-ASR timestamping uses a separate Qwen3-ForcedAligner model, so replacing Whisper correctly requires an A/B integration with chunking/alignment rather than a blind model swap.

### 2. Voxtral is another serious open-weight candidate

Mistral's Voxtral Mini 3B and Small 24B support Spanish and outperform Whisper large-v3 on Mistral's published multilingual transcription benchmarks. Voxtral also has strong semantic/audio-understanding capabilities.

Source:
- https://mistral.ai/news/voxtral/

Why it is not the immediate default: the current local pipeline already has a mature `faster-whisper` server with cancellation, segment timestamps, long-form retries and GPU isolation. Migrating to Voxtral should be benchmark-driven and preserve these contracts.

### 3. The existing Whisper stack can improve materially without an architecture migration

`faster-whisper` supports `large-v3-turbo`, `initial_prompt` and native `hotwords`. The large-v3-turbo checkpoint keeps most large-v3 quality while decoding substantially faster, and contextual hints directly address names and rare/domain terms.

Sources:
- https://github.com/SYSTRAN/faster-whisper
- https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
- https://huggingface.co/openai/whisper-large-v3-turbo

### 4. Contextual biasing is the correct treatment for rare names

Recent ASR research consistently treats proper nouns/domain vocabulary as a contextual-biasing problem. Prompt/hotword methods can reduce entity error substantially without retraining the entire recognizer.

A 2026 LREC study is especially relevant to PandaFusion: it separates specialized-vocabulary error from ordinary WER and shows that normal WER can hide severe jargon failures. On its hardest sets, providing the correct jargon through prompting reduced specialized-vocabulary B-WER by roughly 0.50–0.70 absolute in the oracle experiment. That supports measuring names/technical terms separately instead of relying only on overall transcript readability.

Sources:
- https://aclanthology.org/2025.findings-emnlp.203/
- https://www.isca-archive.org/interspeech_2025/nakagome25_interspeech.html
- https://aclanthology.org/2026.lrec-1.32/
- https://www.nature.com/articles/s41598-025-12121-4

### 5. LLM post-correction is useful, but must be conservative

2025–2026 work shows that LLM post-processing can improve ASR text, including Spanish workflows, but unconstrained rewriting can damage lexical fidelity. PandaFusion should only add this as a verified/selective correction stage, never as an opaque rewrite of the transcript.

Sources:
- https://doi.org/10.1016/j.csl.2026.101966
- https://ieeexplore.ieee.org/document/10930744/

## Real PandaFusion A/B — Fundación audiobook

The same 1 h 06 min Spanish audiobook was processed in three conditions:

- A: historical Whisper `small` baseline.
- B: `large-v3-turbo`, CUDA float16, beam 5, no extra context.
- C: the same `large-v3-turbo` profile plus a short work description and a vocabulary list containing names such as `Hari Seldon`, `Gaal Dornick`, `Trantor`, `Terminus` and `psicohistoria`.

Observed results:

- `large-v3-turbo` removed the repeated `psicostoria` hallucination seen with `small` and increased exact `psicohistoria` recognition from 4 to 16 occurrences.
- Context biasing changed `Hari Seldon` from zero exact recognitions in A/B to repeated correct recognition in C.
- `Gaal Dornick` likewise changed from the phonetic `Gal Dornick` form to correct exact forms when context was supplied.
- The context run was slower than the no-context control (about 174 s versus 124 s for the 3975 s file), but still remained far faster than real time on the target RTX 5090 workstation.
- Context does not improve every entity monotonically, so PandaFusion must not treat hotwords as a blanket post-correction rule. The vocabulary is a decoding hint, not a replacement dictionary.

This experiment is not a formal WER benchmark because it does not use a human-aligned reference transcript. It is sufficient, however, to demonstrate a large named-entity benefit and justify an opt-in contextual-biasing interface.

## Decision implemented

1. Dedicated CUDA STT defaults to `large-v3-turbo` instead of `small`.
2. GPU beam search defaults to 5 for quality; CPU/game-coexistence remains lightweight (`small`, beam 2).
3. Long-form Whisper keeps previous-text context enabled.
4. The STT server accepts bounded global contextual biasing through:
   - `FUSION_READER_STT_INITIAL_PROMPT`
   - `FUSION_READER_STT_HOTWORDS`
   - `FUSION_READER_STT_HOTWORDS_FILE`
5. Native faster-whisper hotwords are used when supported; older versions degrade safely to `initial_prompt` vocabulary biasing.
6. Media jobs can also carry a bounded **per-request** prompt and hotword list. That context exists only for that upload and is not written to `.env`, manifests or the next transcription.
7. The media UI exposes these per-file hints under **Ayudar con nombres y términos (opcional)**.
8. User overrides always win.

## Why per-request context instead of global hotwords

Global vocabulary is appropriate only for a stable domain. A vocabulary for an Asimov audiobook should not make `Trantor` or `Hari Seldon` more probable when the next upload is a university class, an interview or a medical recording. The application therefore treats user-supplied media context as ephemeral job input and keeps environment-level context as an advanced persistent override.

The per-job values are bounded before background processing, percent-encoded across the local HTTP hop and merged with any administrator-configured global context only inside the STT request. The global server state itself is not mutated. Validation must include a second, context-free transcription without restarting STT to prove that the previous job's vocabulary does not leak into the next request.

## Next model migration gate

Do not replace Whisper solely from leaderboard claims. Benchmark at least these engines against the same real PandaFusion corpus:

- historical `small` baseline
- `large-v3-turbo`
- `large-v3`
- Qwen3-ASR-1.7B + Qwen3-ForcedAligner
- Voxtral Mini 3B if local runtime integration is practical

Measure:

- normalized WER
- named-entity WER / proper-noun accuracy
- timestamp quality
- hallucinations on silence/music
- one-hour audiobook throughput
- peak VRAM/RAM
- cancellation behavior
- startup/model-load time

For PandaFusion, named-entity accuracy and semantic fidelity are more important than shaving a few seconds from a one-hour offline transcription.
