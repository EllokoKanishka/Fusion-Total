# Qwen3-ASR benchmark gate — September 2026

## Purpose

This experiment answers one narrow question: does `Qwen/Qwen3-ASR-1.7B` plus
`Qwen/Qwen3-ForcedAligner-0.6B` improve real PandaFusion transcription quality
enough to justify replacing the production `large-v3-turbo` stack?

It is deliberately **not** a production provider migration. The current
faster-whisper path already has working cancellation, long-form processing,
request-scoped context, PDF generation, and a validated desktop deployment.
Qwen must earn a migration through the same real corpus before production code
changes.

## Research summary

Qwen3-ASR's official package exposes a local `Qwen3ASRModel` with:

- Spanish support;
- request-scoped `context`;
- automatic splitting/merging of long audio;
- optional timestamps through `Qwen3-ForcedAligner`;
- local Transformers and vLLM backends.

The official package recommends a fresh isolated Python environment to avoid
dependency conflicts. Its current package release used for this benchmark is
`qwen-asr==0.0.6`, which pins the Transformers compatibility set required by the
model. We therefore keep the experiment out of PandaFusion's production venv.

Primary sources:

- https://github.com/QwenLM/Qwen3-ASR
- https://huggingface.co/Qwen/Qwen3-ASR-1.7B
- https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B
- https://huggingface.co/docs/transformers/main/model_doc/qwen3_asr
- https://arxiv.org/abs/2601.21337

The 1.7B model is Apache-2.0 and the published model files are roughly 4.7 GB.
The forced aligner is a separate model because timestamp generation is not the
same inference path as transcription.

The official Qwen interface exposes `context` as free-form request guidance. It
is useful for domain hints, but it should not be treated as an exact spelling
lexicon or as equivalent to faster-whisper's native weighted hotword biasing.
That distinction became important in the real PandaFusion benchmark below.

## Container compatibility learned from the real desktop run

The first Foundation benchmark exposed a concrete input-contract mismatch in
`qwen-asr==0.0.6`: passing the `.mp4` audiobook path directly reached the
package's `librosa`/`soundfile` loader, where `libsndfile` rejected the video
container with `Format not recognised` before ASR inference began.

That is not a model-quality failure and it is not specific to Foundation. The
official Qwen examples use decoder-friendly audio inputs such as WAV or arrays,
while PandaFusion's user-facing media surface intentionally accepts containers
such as MP4, MKV, MOV and WebM.

The benchmark runner therefore normalizes **every** source through local FFmpeg
before calling Qwen:

- audio only (`-vn`);
- 16 kHz;
- mono;
- FLAC;
- temporary file deleted automatically after inference.

This is the same stable audio profile already used by PandaFusion's production
media normalization path. It keeps the experiment representative of the real
application without patching `qwen-asr`, and it handles both audio files and
video containers consistently.

The JSON report records `preprocess_seconds` separately. `inference_seconds`
continues to measure Qwen inference only, while `total_seconds` includes local
normalization, model loading and inference.

## Why an isolated harness

Do not install `qwen-asr` into PandaFusion's normal virtual environment. The
benchmark setup script creates an ignored environment below
`runtime/qwen3_asr_benchmark/venv`. This preserves the validated production
stack and makes rollback trivial: delete the benchmark runtime directory.

The experiment is local. It does not require DashScope, a paid API, a card, or
sending the audiobook to a remote transcription service. The first run does
need internet access to download the public model checkpoints.

## Repository tools

- `scripts/setup_qwen3_asr_benchmark.sh`: creates the isolated benchmark venv.
- `scripts/benchmark_qwen3_asr.py`: normalizes local media with FFmpeg, runs
  local Qwen3-ASR + optional ForcedAligner, records timings/resources, and
  writes transcript/timestamp/JSON artifacts.
- `benchmarks/asr/foundation_entities.txt`: stable entity vocabulary used for
  the Foundation audiobook comparison.
- `requirements/qwen3-asr-benchmark.txt`: isolated benchmark dependency pin.

The runner deliberately imports Torch and Qwen lazily so normal PandaFusion CI
and runtime do not acquire these heavyweight experimental dependencies.

## Foundation protocol

Use the same audiobook already validated with PandaFusion:

`La FUNDACION - Isaac Asimov Audiolibro Parte 1.mp4`

Run Qwen with:

- model: `Qwen/Qwen3-ASR-1.7B`;
- aligner: `Qwen/Qwen3-ForcedAligner-0.6B`;
- dtype: BF16;
- device: CUDA 0;
- language: Spanish;
- timestamps: enabled;
- context: `Audiolibro de Fundación de Isaac Asimov en castellano.`;
- entities: `benchmarks/asr/foundation_entities.txt`.

Example after creating the isolated environment:

```bash
runtime/qwen3_asr_benchmark/venv/bin/python \
  scripts/benchmark_qwen3_asr.py \
  "/ruta/La FUNDACION - Isaac Asimov Audiolibro Parte 1.mp4" \
  --output-dir runtime/qwen3_asr_benchmark/foundation \
  --language Spanish \
  --context "Audiolibro de Fundación de Isaac Asimov en castellano." \
  --entities-file benchmarks/asr/foundation_entities.txt
```

The runner writes:

- `qwen3_asr_transcript.txt`;
- `qwen3_asr_timestamps.json`;
- `qwen3_asr_report.json`.

The JSON report includes model/package versions, detected language,
normalization/load/inference times, real-time factor, GPU/RAM peaks, timestamp
count, word/character counts, and accent-insensitive exact counts for the stable
entity vocabulary.

## Comparison against current PandaFusion

The current production reference is:

- `large-v3-turbo`;
- CUDA float16;
- beam 5;
- request-scoped Foundation context/hotwords;
- approximately 118 seconds for the 1 h 06 min file in the latest validated
  desktop run;
- correct repeated recognition of difficult entities such as `Hari Seldon`,
  `Gaal Dornick`, `Trantor`/`Trántor`, and `psicohistoriador`.

That reference is not a formal WER ground truth. Do **not** claim a WER win from
this audiobook alone. A formal WER requires a human reference transcript.

For this migration gate compare:

1. exact/normalized entity coverage;
2. manual spot checks around the known difficult names;
3. omissions/repetitions/hallucinations;
4. timestamp usability and ordering;
5. one-hour throughput;
6. peak VRAM/RAM;
7. model load time;
8. behavior on silence/music;
9. practical cancellation feasibility before production integration.

## Completed desktop benchmark — 2026-09-06

The gate was executed on the target RTX 5090 workstation against the same
3975.563-second Foundation audiobook used for the Whisper validation.

Qwen3-ASR result:

- model: `Qwen/Qwen3-ASR-1.7B`;
- aligner: `Qwen/Qwen3-ForcedAligner-0.6B`;
- `qwen-asr==0.0.6`;
- Torch `2.14.0+cu130`, CUDA 13.0;
- preprocessing: 1.634 s;
- model load: 8.050 s;
- inference: 109.564 s;
- end-to-end total: 119.248 s;
- speed: 36.28x real time;
- inference peak GPU allocated: 10.855 GiB;
- inference peak GPU reserved: 13.547 GiB;
- process max RSS: 5.217 GiB;
- 10,703 word-level aligned timestamp items;
- final aligned timestamp at 3968.32 s, about 99.8% temporal coverage.

The ForcedAligner result was excellent: timestamps were monotonic, dense and
covered essentially the full audiobook without severe inversions or large gaps.
Qwen also recognized common in-domain names such as `Trantor` very reliably.

The migration gate nevertheless failed on the lexical criterion that matters
most to PandaFusion. With only the request `context`, exact entity coverage was
0.3. Notable results were:

- `Trantor`: 45 exact occurrences;
- `Terminus`: 4;
- `Imperio Galáctico`: 3;
- `Enciclopedia Galáctica`: 4;
- `Hari Seldon`: 0 exact occurrences (`Harry Seldon` and other variants);
- `Gaal Dornick`: 0 exact occurrences (`Gal Dornick`);
- `psicohistoria`: 0 exact occurrences;
- `psicohistoriador`: 0 exact occurrences;
- `Anacreon`: 0 exact occurrences.

The strongest failure was domain normalization: fictional/rare terminology such
as `psicohistoria` was repeatedly pushed toward common Spanish words such as
`psicología`, while the production faster-whisper path with native contextual
biasing had already produced 16 exact `psicohistoria` occurrences, 7 exact
`Hari Seldon` occurrences and 5 exact `Gaal Dornick` occurrences on the same
material.

A short music/no-speech file produced only one spurious word (`No.`) near the
end rather than a long hallucinated passage. That is acceptable experimental
behavior but not a reason to migrate.

## Migration decision

**Keep `large-v3-turbo` as PandaFusion's production STT default.**

Qwen3-ASR is extremely competitive on throughput and clearly superior in the
density/quality of forced word alignment, but it does not provide a material
fidelity advantage on the real corpus. For PandaFusion's use case, the opposite
is true on the most important hard vocabulary: faster-whisper's native
request-scoped hotword/context biasing currently preserves proper nouns and rare
technical/fictional terms more reliably.

The speed difference is not material: Qwen's 119.248 s end-to-end result is
essentially tied with the approximately 118 s production Whisper run for a
66-minute file. Qwen also used substantially more benchmark GPU/RAM resources.

No production provider swap is justified from this result. Preserve the Qwen
harness for future releases because its ForcedAligner is strong and later Qwen
versions may improve domain-term control.

A future Qwen retest should require at least one of:

- a newer model/package with materially improved domain vocabulary steering;
- an official hotword/lexicon mechanism stronger than free-form context;
- a demonstrated lexical-fidelity gain on the same Foundation corpus without
  sacrificing the current alignment quality.

## Migration decision rule

Do not replace faster-whisper merely because Qwen is newer or because a public
leaderboard is favorable.

A production migration becomes justified only if Qwen shows a material fidelity
advantage on the real corpus while:

- producing usable aligned timestamps;
- fitting comfortably in the RTX 5090 32 GB budget;
- remaining far faster than real time for long-form offline work;
- avoiding a meaningful increase in hallucinations/omissions;
- preserving a plausible route to cooperative cancellation and the existing
  media/PDF contracts.

The September 2026 Qwen3-ASR-1.7B benchmark did **not** meet that gate. Keep
`large-v3-turbo` as production default and retain the Qwen harness for future
model releases.
