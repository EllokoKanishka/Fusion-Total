#!/usr/bin/env bash
# Admission follows observed VRAM and a configurable reserve, never a game name.
# 0 sufficient; 2 pressured; 3 unknown. No engine is stopped by this probe.
fusion_reader_gpu_refresh_budget() {
  local project_root python output status
  project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  python="${FUSION_READER_PYTHON:-python3}"
  if output=$(PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}" "$python" -m fusion_reader_v2.gpu_budget 2>/dev/null); then
    status=0
  else
    status=$?
  fi
  FUSION_READER_GPU_BUDGET_STATUS="$status"
  FUSION_READER_GPU_BUDGET_DETAIL="$output"
}

fusion_reader_gpu_conflict_processes() {
  # Compatibility name for callers: report a budget, not a guessed process list.
  fusion_reader_gpu_refresh_budget
  printf '%s\n' "$FUSION_READER_GPU_BUDGET_DETAIL"
}

fusion_reader_gpu_conflict_active() {
  [[ "${FUSION_READER_GPU_GUARD:-1}" != "0" ]] || return 1
  [[ "${FUSION_READER_ALLOW_GPU_WITH_GAMES:-0}" != "1" ]] || return 1
  fusion_reader_gpu_refresh_budget
  [[ "$FUSION_READER_GPU_BUDGET_STATUS" != "0" ]]
}

fusion_reader_gpu_conflict_summary() {
  printf '%s\n' "${FUSION_READER_GPU_BUDGET_DETAIL:-GPU budget not sampled}"
}

fusion_reader_refuse_when_gpu_conflict() {
  local component="${1:-Fusion Reader GPU service}"
  fusion_reader_gpu_conflict_active || return 0
  if [[ "${FUSION_READER_GPU_CONFLICT_POLICY:-warn}" == "block" ]]; then
    echo "Refusing to start $component: GPU budget insufficient or unavailable." >&2
    fusion_reader_gpu_conflict_summary >&2
    exit 3
  fi
  echo "Warning: $component: GPU budget insufficient or unavailable; using conservative coexistence." >&2
  fusion_reader_gpu_conflict_summary >&2
}

fusion_reader_apply_game_coexistence_mode() {
  fusion_reader_gpu_conflict_active || return 1
  [[ "${FUSION_READER_GAME_COEXISTENCE:-1}" != "0" ]] || return 1
  export FUSION_READER_GAME_COEXISTENCE_ACTIVE=1
  export FUSION_READER_PREFETCH_AHEAD=0
  export FUSION_READER_PREFETCH_WORKERS=1
  # Keep user-selected analysis mode, context and response budget intact.
  return 0
}
