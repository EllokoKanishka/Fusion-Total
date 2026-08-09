#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$ROOT/scripts/lib/env_helper.sh"
load_env_safe

GPU_TTS_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
CPU_TTS_PORT="${FUSION_READER_CPU_TTS_PORT:-${DIRECT_CHAT_ALLTALK_PORT:-7851}}"
GPU_TTS_URL="http://127.0.0.1:${GPU_TTS_PORT}"
CPU_TTS_URL="http://127.0.0.1:${CPU_TTS_PORT}"
RUNTIME_DIR="${FUSION_READER_RUNTIME_ROOT:-${FUSION_READER_RUNTIME_DIR:-$ROOT/runtime/fusion_reader_v2}}"
LOG_DIR="${FUSION_READER_LOG_ROOT:-${FUSION_READER_LOG_DIR:-$RUNTIME_DIR/logs}}"
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$RUNTIME_DIR/tts_owner.json}"
GPU_LOG="$LOG_DIR/alltalk_gpu_5090.log"
CPU_LOG="$LOG_DIR/alltalk_cpu.log"
SERVER_LOG="${FUSION_READER_LOG_FILE:-$LOG_DIR/fusion_reader_v2_server.log}"
TTS_WAIT_SECONDS="${FUSION_READER_TTS_STARTUP_WAIT_SECONDS:-120}"
TTS_CHILD_PID=""

mkdir -p "$LOG_DIR"

fusion_tts_owner_ok() {
  [[ -f "$OWNER_FILE" ]] || return 1
  grep -q '"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"' "$OWNER_FILE" || return 1
  grep -q "\"port\"[[:space:]]*:[[:space:]]*$GPU_TTS_PORT" "$OWNER_FILE" || return 1

  local owner_pid
  owner_pid="$(sed -n 's/.*"owner_pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$OWNER_FILE" | head -1)"
  [[ -n "$owner_pid" && -r "/proc/$owner_pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "tts_server:app" || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "--port $GPU_TTS_PORT" || return 1
}

gpu_ready() {
  curl -fsS --max-time 2 "$GPU_TTS_URL/api/ready" >/dev/null 2>&1 && fusion_tts_owner_ok
}

cpu_ready() {
  curl -fsS --max-time 2 "$CPU_TTS_URL/api/ready" >/dev/null 2>&1
}

wait_until_ready() {
  local probe="$1"
  local child_pid="${2:-}"
  local deadline
  deadline=$(( $(date +%s) + TTS_WAIT_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if "$probe"; then
      return 0
    fi
    if [[ -n "$child_pid" ]] && ! kill -0 "$child_pid" 2>/dev/null; then
      wait "$child_pid" 2>/dev/null || true
      return 1
    fi
    sleep 1
  done
  return 1
}

start_gpu_tts() {
  "$ROOT/scripts/start_reader_neural_tts_gpu_5090.sh" >>"$GPU_LOG" 2>&1 &
  TTS_CHILD_PID="$!"
}

start_cpu_tts() {
  "$ROOT/scripts/start_reader_neural_tts.sh" >>"$CPU_LOG" 2>&1 &
  TTS_CHILD_PID="$!"
}

select_tts() {
  local child_pid=""
  if gpu_ready; then
    export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"
    echo "[INFO] Fusion TTS listo en $FUSION_READER_ALLTALK_URL" >>"$SERVER_LOG"
    return 0
  fi
  if cpu_ready; then
    export FUSION_READER_ALLTALK_URL="$CPU_TTS_URL"
    echo "[INFO] Fusion TTS fallback listo en $FUSION_READER_ALLTALK_URL" >>"$SERVER_LOG"
    return 0
  fi

  start_gpu_tts
  child_pid="$TTS_CHILD_PID"
  if wait_until_ready gpu_ready "$child_pid"; then
    export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"
    echo "[INFO] Fusion TTS iniciado en $FUSION_READER_ALLTALK_URL" >>"$SERVER_LOG"
    return 0
  fi
  if kill -0 "$child_pid" 2>/dev/null; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi

  start_cpu_tts
  child_pid="$TTS_CHILD_PID"
  if wait_until_ready cpu_ready "$child_pid"; then
    export FUSION_READER_ALLTALK_URL="$CPU_TTS_URL"
    echo "[INFO] Fusion TTS fallback iniciado en $FUSION_READER_ALLTALK_URL" >>"$SERVER_LOG"
    return 0
  fi
  return 1
}

if ! select_tts; then
  export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"
  echo "[WARN] Fusion arrancará sin TTS; revisá $GPU_LOG y $CPU_LOG" >>"$SERVER_LOG"
fi

if [[ -n "${FUSION_READER_PYTHON:-}" && -x "${FUSION_READER_PYTHON}" ]]; then
  PYTHON_BIN="$FUSION_READER_PYTHON"
elif ! PYTHON_BIN="$(find_python)"; then
  echo "[ERROR] No se encontró un Python compatible para Panda Fusion." >&2
  exit 1
fi

cd "$ROOT"
exec "$PYTHON_BIN" -u -m scripts.fusion_reader_v2_server >>"$SERVER_LOG" 2>&1
