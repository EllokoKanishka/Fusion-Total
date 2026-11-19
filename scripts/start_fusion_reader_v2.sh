#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$ROOT/scripts/lib/env_helper.sh"
load_env_safe

PORT="${FUSION_READER_V2_PORT:-8010}"
GPU_TTS_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
CPU_TTS_PORT="${FUSION_READER_CPU_TTS_PORT:-${DIRECT_CHAT_ALLTALK_PORT:-7851}}"
GPU_TTS_URL="http://127.0.0.1:${GPU_TTS_PORT}"
CPU_TTS_URL="http://127.0.0.1:${CPU_TTS_PORT}"
STT_PORT="${FUSION_READER_STT_PORT:-8021}"
STT_BASE_URL="${FUSION_READER_STT_URL:-http://127.0.0.1:${STT_PORT}}"
STT_URL="${STT_BASE_URL%/}/health"
RUNTIME_DIR="${FUSION_READER_RUNTIME_ROOT:-${FUSION_READER_RUNTIME_DIR:-$ROOT/runtime/fusion_reader_v2}}"
LOG_DIR="${FUSION_READER_LOG_ROOT:-${FUSION_READER_LOG_DIR:-$RUNTIME_DIR/logs}}"
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$RUNTIME_DIR/tts_owner.json}"
LOG_FILE="${FUSION_READER_LOG_FILE:-$LOG_DIR/fusion_reader_v2_server.log}"
GPU_TTS_LOG_FILE="$LOG_DIR/alltalk_gpu_5090.log"
CPU_TTS_LOG_FILE="$LOG_DIR/alltalk_cpu.log"
STT_LOG_FILE="${FUSION_READER_STT_LOG_FILE:-$LOG_DIR/stt_server.log}"
PID_FILE="${FUSION_READER_PID_FILE:-$RUNTIME_DIR/fusion_reader_v2.pid}"
STARTUP_WAIT_SECONDS="${FUSION_READER_STARTUP_WAIT_SECONDS:-40}"
TTS_GPU_START_WAIT_SECONDS="${FUSION_READER_TTS_GPU_START_WAIT_SECONDS:-${FUSION_READER_GPU_TTS_WAIT_SECONDS:-90}}"
TTS_CPU_START_WAIT_SECONDS="${FUSION_READER_TTS_CPU_START_WAIT_SECONDS:-60}"
STT_GPU_START_WAIT_SECONDS="${FUSION_READER_STT_GPU_START_WAIT_SECONDS:-120}"
TTS_CHILD_PID=""
STT_CHILD_PID=""

if ! PYTHON_BIN="$(find_python)"; then
  echo "[ERROR] No se encontró ningún intérprete de Python válido con las dependencias requeridas (reportlab, python-docx, Pillow)." >&2
  exit 1
fi

export DIRECT_CHAT_ALLTALK_PORT="$CPU_TTS_PORT"
export FUSION_READER_TTS_OWNER_FILE="$OWNER_FILE"
export FUSION_READER_RUNTIME_ROOT="$RUNTIME_DIR"
export FUSION_READER_RUNTIME_DIR="$RUNTIME_DIR"
export FUSION_READER_LOG_ROOT="$LOG_DIR"
export FUSION_READER_LOG_DIR="$LOG_DIR"
export FUSION_READER_LIBRARY_ROOT="${FUSION_READER_LIBRARY_ROOT:-$ROOT/library}"
export FUSION_READER_DOWNLOADS_ROOT="${FUSION_READER_DOWNLOADS_ROOT:-${HOME}/Descargas}"
export FUSION_READER_CACHE_ROOT="${FUSION_READER_CACHE_ROOT:-$RUNTIME_DIR/audio_cache}"

cd "$ROOT"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

timestamp() {
  date +"%Y-%m-%d %H:%M:%S"
}

log_msg() {
  local line
  line="[$(timestamp)] $*"
  echo "$line" | tee -a "$LOG_FILE"
}

# Serialize launchers that share this runtime until readiness is confirmed.
exec 9>"$RUNTIME_DIR/startup.lock"
if ! flock -n 9; then
  log_msg "ERROR: ya hay un arranque de Panda Fusión en curso."
  exit 1
fi

startup_status_url="$(fusion_api_base_url)/health/live"

if startup_probe="$("$PYTHON_BIN" -m scripts.fusionctl _probe-startup)"; then
  log_msg "Panda Fusión ya está activo para esta instalación."
  exit 0
else
  probe_status=$?
  if [[ "$probe_status" != "3" ]]; then
    log_msg "ERROR: no se puede usar el puerto ${PORT}: ${startup_probe}"
    exit 1
  fi
fi

current_commit() {
  git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown"
}

source "$ROOT/scripts/fusion_reader_gpu_guard.sh"
fusion_reader_refuse_when_gpu_conflict "Fusion Reader v2 academic/chat server"
if fusion_reader_apply_game_coexistence_mode; then
  log_msg "Presupuesto GPU ajustado: prefetch mínimo; modo de análisis, contexto y respuesta se conservan."
fi

export FUSION_READER_CHAT_MODEL="${FUSION_READER_CHAT_MODEL:-qwen3:14b-q8_0}"
export FUSION_READER_CHAT_THINK="${FUSION_READER_CHAT_THINK:-1}"
export FUSION_READER_CHAT_NUM_PREDICT="${FUSION_READER_CHAT_NUM_PREDICT:-1536}"
if [[ -z "${FUSION_READER_REASONING_MODE:-}" ]]; then
  if [[ "${FUSION_READER_CHAT_THINK}" == "0" || "${FUSION_READER_CHAT_THINK,,}" == "false" || "${FUSION_READER_CHAT_THINK,,}" == "no" ]]; then
    export FUSION_READER_REASONING_MODE="normal"
  else
    export FUSION_READER_REASONING_MODE="thinking"
  fi
fi

fusion_tts_owner_ok() {
  [[ -f "$OWNER_FILE" ]] || return 1
  grep -q '"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"' "$OWNER_FILE" || return 1
  grep -q "\"port\"[[:space:]]*:[[:space:]]*$GPU_TTS_PORT" "$OWNER_FILE" || return 1

  local owner_pid
  owner_pid="$(sed -n 's/.*"owner_pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$OWNER_FILE" | head -1)"
  if [[ -z "$owner_pid" ]]; then
    return 1
  fi
  [[ -r "/proc/$owner_pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "tts_server:app" || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "--port $GPU_TTS_PORT" || return 1
}

fusion_gpu_ready() {
  curl -fsS --max-time 1 "${GPU_TTS_URL}/api/ready" >/dev/null 2>&1 && fusion_tts_owner_ok
}

cpu_tts_ready() {
  curl -fsS --max-time 2 "${CPU_TTS_URL}/api/ready" >/dev/null 2>&1
}

wait_until_tts_ready() {
  local probe="$1"
  local child_pid="${2:-}"
  local wait_seconds="${3:-60}"
  local deadline
  deadline=$(( $(date +%s) + wait_seconds ))
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

fusion_stt_ready() {
  curl -fsS --max-time 2 "$STT_URL" >/dev/null 2>&1
}

wait_until_stt_ready() {
  local child_pid="${1:-}"
  local deadline
  deadline=$(( $(date +%s) + STT_GPU_START_WAIT_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if fusion_stt_ready; then
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

ensure_fusion_gpu_stt() {
  if fusion_stt_ready; then
    log_msg "Fusion STT GPU listo en ${STT_URL}."
    return 0
  fi

  case "${STT_BASE_URL%/}" in
    "http://127.0.0.1:${STT_PORT}"|"http://localhost:${STT_PORT}") ;;
    *) log_msg "STT externo configurado sin respuesta; no se inicia un motor local distinto."; return 1 ;;
  esac
  if [[ "${FUSION_READER_GAME_COEXISTENCE_ACTIVE:-0}" == "1" ]]; then
    export FUSION_READER_STT_DEVICE="${FUSION_READER_STT_DEVICE:-cpu}"
    export FUSION_READER_STT_COMPUTE_TYPE="${FUSION_READER_STT_COMPUTE_TYPE:-int8}"
  fi
  log_msg "Iniciando STT opcional; dispositivo ${FUSION_READER_STT_DEVICE:-cuda}."
  nohup "$ROOT/scripts/start_fusion_reader_v2_stt.sh" >>"$STT_LOG_FILE" 2>&1 9>&- &
  STT_CHILD_PID="$!"
  if wait_until_stt_ready "$STT_CHILD_PID"; then
    log_msg "Fusion STT GPU listo en ${STT_URL}."
    return 0
  fi

  log_msg "ERROR: Fusion STT GPU no respondió en ${STT_GPU_START_WAIT_SECONDS}s. Revisá ${STT_LOG_FILE}."
  tail -20 "$STT_LOG_FILE" 2>/dev/null || true
  return 1
}

start_fusion_gpu_tts() {
  nohup "$ROOT/scripts/start_reader_neural_tts_gpu_5090.sh" >>"$GPU_TTS_LOG_FILE" 2>&1 9>&- &
  TTS_CHILD_PID="$!"
}

start_fusion_cpu_tts() {
  local fusion_python="${FUSION_READER_GPU_ENV:-${HOME}/fusion_reader_envs/alltalk_gpu_5090_py311}/bin/python"
  if [[ -z "${DIRECT_CHAT_ALLTALK_PYTHON:-}" && -x "$fusion_python" ]]; then
    DIRECT_CHAT_ALLTALK_PYTHON="$fusion_python" DIRECT_CHAT_ALLTALK_FORCE_CPU=1 \
      PYTHONPATH="$ROOT/scripts/gpu_compat${PYTHONPATH:+:$PYTHONPATH}" FUSION_READER_ALLOW_TORCH_PICKLE_LOAD=1 \
      nohup "$ROOT/scripts/start_reader_neural_tts.sh" >>"$CPU_TTS_LOG_FILE" 2>&1 9>&- &
  else
    nohup "$ROOT/scripts/start_reader_neural_tts.sh" >>"$CPU_TTS_LOG_FILE" 2>&1 9>&- &
  fi
  TTS_CHILD_PID="$!"
}

select_gpu_tts() {
  export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"
  log_msg "Fusion TTS URL selected: ${FUSION_READER_ALLTALK_URL}"
}

select_cpu_tts() {
  export FUSION_READER_ALLTALK_URL="$CPU_TTS_URL"
  log_msg "Fusion TTS fallback selected: ${FUSION_READER_ALLTALK_URL}"
}

ensure_fusion_tts_url() {
  local child_pid=""
  if fusion_gpu_ready; then
    select_gpu_tts
    return 0
  fi
  if cpu_tts_ready; then
    select_cpu_tts
    return 0
  fi

  if [[ "${FUSION_READER_GAME_COEXISTENCE_ACTIVE:-0}" == "1" ]]; then
    log_msg "Modo convivencia GPU: TTS CPU no está activo; iniciando fallback propio."
    start_fusion_cpu_tts
    child_pid="$TTS_CHILD_PID"
    if wait_until_tts_ready cpu_tts_ready "$child_pid" "$TTS_CPU_START_WAIT_SECONDS"; then
      select_cpu_tts
      return 0
    fi
    export FUSION_READER_ALLTALK_URL="$CPU_TTS_URL"
    log_msg "WARN: Fusion arrancará sin TTS operativo; falló el fallback CPU. Revisá $CPU_TTS_LOG_FILE"
    return 1
  fi

  log_msg "Fusion TTS GPU no está activo; iniciando servicio propio en ${GPU_TTS_URL}."
  start_fusion_gpu_tts
  child_pid="$TTS_CHILD_PID"
  if wait_until_tts_ready fusion_gpu_ready "$child_pid" "$TTS_GPU_START_WAIT_SECONDS"; then
    select_gpu_tts
    return 0
  fi
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi

  if cpu_tts_ready; then
    select_cpu_tts
    return 0
  fi
  log_msg "Fusion TTS GPU no quedó listo; iniciando fallback CPU propio en ${CPU_TTS_URL}."
  start_fusion_cpu_tts
  child_pid="$TTS_CHILD_PID"
  if wait_until_tts_ready cpu_tts_ready "$child_pid" "$TTS_CPU_START_WAIT_SECONDS"; then
    select_cpu_tts
    return 0
  fi

  export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"
  log_msg "WARN: Fusion arrancará sin TTS operativo; fallaron GPU y CPU. Revisá $GPU_TTS_LOG_FILE y $CPU_TTS_LOG_FILE"
  return 1
}

if [[ "${FUSION_READER_START_ENGINES:-1}" != "0" ]]; then
  ensure_fusion_tts_url || true
else
  export FUSION_READER_ALLTALK_URL="${FUSION_READER_ALLTALK_URL:-$GPU_TTS_URL}"
fi

# The default STT provider is the dedicated Faster-Whisper server.  Start it
# from the same entry point that fusionctl uses so the UI never falls back to
# the CLI merely because the desktop launcher was bypassed.
case "${FUSION_READER_START_ENGINES:-1}:${FUSION_READER_STT_PROVIDER:-auto}" in
  0:*|1:none|1:cli) log_msg "STT server omitido por configuración." ;;
  1:auto|1:server|1:faster_whisper|1:faster-whisper) ensure_fusion_gpu_stt || log_msg "WARN: STT no disponible; lectura y navegación siguen habilitadas." ;;
  *) log_msg "ERROR: proveedor STT inválido"; exit 1 ;;
esac

current_commit="$(current_commit)"
log_msg "==== Fusion Reader v2 startup ===="
log_msg "Commit: ${current_commit}"
log_msg "API/UI port: ${PORT}"
log_msg "TTS URL selected: ${FUSION_READER_ALLTALK_URL}"
log_msg "STT GPU URL: ${STT_URL}"
log_msg "STT command: ${FUSION_READER_STT_COMMAND:-whisper}"
log_msg "Chat model: ${FUSION_READER_CHAT_MODEL}"
log_msg "Reasoning mode env: ${FUSION_READER_REASONING_MODE}"
log_msg "Voice env: ${FUSION_READER_VOICE:-female_03.wav}"
log_msg "Profile env: ${FUSION_READER_PROFILE:-default}"
log_msg "PID file: ${PID_FILE}"
log_msg "Persistent log: ${LOG_FILE}"

if [[ "${FUSION_READER_FOREGROUND:-0}" == "1" ]]; then
  printf '%s\n' "$$" >"$PID_FILE"
  exec "$PYTHON_BIN" -u -m scripts.fusion_reader_v2_server >>"$LOG_FILE" 2>&1 9>&-
fi

nohup "$PYTHON_BIN" -m scripts.fusion_reader_v2_server >>"$LOG_FILE" 2>&1 9>&- &
server_pid=$!

if ! printf '%s\n' "$server_pid" >"$PID_FILE" 2>/dev/null; then
  log_msg "WARN: no pude escribir PID file en ${PID_FILE}"
fi

log_msg "Fusion Reader v2 server spawned with PID ${server_pid}"

deadline=$(( $(date +%s) + STARTUP_WAIT_SECONDS ))
while (( $(date +%s) < deadline )); do
  if health="$(curl --noproxy '*' -fsS --max-time 2 "$startup_status_url" 2>/dev/null)" &&
    "$PYTHON_BIN" -c 'import json, sys; sys.exit(json.load(sys.stdin) != {"ok": True, "status": "live", "pid": int(sys.argv[1])})' "$server_pid" <<<"$health" 2>/dev/null; then
    log_msg "Fusion Reader v2 health OK: ${startup_status_url}"
    exit 0
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    log_msg "Fusion Reader v2 server PID ${server_pid} terminó antes del health check."
    tail -20 "$LOG_FILE" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done

log_msg "Fusion Reader v2 no respondió health dentro de ${STARTUP_WAIT_SECONDS}s: ${startup_status_url}"
tail -20 "$LOG_FILE" 2>/dev/null || true
exit 1
