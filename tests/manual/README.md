# Manual checks

These scripts are not automated CI evidence. Run them only with explicit local
consent and temporary Fusion roots. Record browser/microphone permissions,
hardware, providers and output honestly; synthetic audio is not a real
microphone test.

- `reader_flow_3runs.js`: repeated browser reader flow;
- `reader_human_sample.js`: human-observed sample flow;
- real microphone checklist: grant browser permission, select the physical
  input, activate dialogue, speak a reader command, interrupt playback, inspect
  RMS/peak/cut reason, then verify normal reading still works.
