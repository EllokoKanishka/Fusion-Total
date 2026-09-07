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
GPU_TTS_WAIT_SECONDS="${FUSION_READER_GPU_TTS_WAIT_SECONDS:-30}"
RUNTIME_DIR="${FUSION_READER_RUNTIME_ROOT:-${FUSION_READER_RUNTIME_DIR:-$ROOT/runtime/fusion_reader_v2}}"
LOG_DIR="${FUSION_READER_LOG_ROOT:-${FUSION_READER_LOG_DIR:-$RUNTIME_DIR/logs}}"
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$RUNTIME_DIR/tts_owner.json}"
LOG_FILE="${FUSION_READER_LOG_FILE:-$LOG_DIR/fusion_reader_v2_server.log}"
GPU_TTS_LOG_FILE="$LOG_DIR/alltalk_gpu_5090.log"
CPU_TTS_LOG_FILE="$LOG_DIR/alltalk_cpu.log"
PID_FILE="${FUSION_READER_PID_FILE:-$RUNTIME_DIR/fusion_reader_v2.pid}"
STARTUP_WAIT_SECONDS="${FUSION_READER_STARTUP_WAIT_SECONDS:-40}"
TTS_GPU_START_WAIT_SECONDS="${FUSION_READER_TTS_GPU_START_WAIT_SECONDS:-90}"
TTS_CPU_START_WAIT_SECONDS="${FUSION_READER_TTS_CPU_START_WAIT_SECONDS:-60}"
TTS_CHILD_PID=""

if ! PYTHON_BIN="$(find_python)"; then
  echo "[ERROR] No se encontró ningún intérprete de Python válido con las dependencias requeridas (reportlab, python-docx, Pillow)." >&2
  exit 1
fi

export FUSION_READER_RUNTIME_ROOT="$RUNTIME_DIR"
export FUSION_READER_RUNTIME_DIR="$RUNTIME_DIR"
export FUSION_READER_LOG_ROOT="$LOG_DIR"
export FUSION_READER_LOG_DIR="$LOG_DIR"
export FUSION_READER_LIBRARY_ROOT="${FUSION_READER_LIBRARY_ROOT:-$ROOT/library}"
export FUSION_READER_DOWNLOADS_ROOT="${FUSION_READER_DOWNLOADS_ROOT:-${HOME}/Descargas}"
export FUSION_READER_CACHE_ROOT="${FUSION_READER_CACHE_ROOT:-$RUNTIME_DIR/audio_cache}"

cd "$ROOT"

mkdir -p "$LOG_DIR"

timestamp() {
  date +"%Y-%m-%d %H:%M:%S"
}

log_msg() {
  local line
  line="[$(timestamp)] $*"
  echo "$line" | tee -a "$LOG_FILE"
}

listening_pid() {
  local pid=""
  if command -v lsof >/dev/null 2>&1; then
    pid="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
  fi
  if [[ -z "$pid" ]] && command -v ss >/dev/null 2>&1; then
    pid="$(ss -ltnp 2>/dev/null | grep -F ":$PORT" | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1)"
  fi
  [[ -n "$pid" ]] && echo "$pid"
}

startup_status_url="http://127.0.0.1:${PORT}/api/status"

current_commit() {
  git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown"
}

source "$ROOT/scripts/fusion_reader_gpu_guard.sh"
fusion_reader_refuse_when_gpu_conflict "Fusion Reader v2 academic/chat server"
if fusion_reader_apply_game_coexistence_mode; then
  log_msg "Modo convivencia GPU activo: chat sin thinking, num_ctx=${FUSION_READER_CHAT_NUM_CTX}, num_predict=${FUSION_READER_CHAT_NUM_PREDICT}"
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

start_fusion_gpu_tts() {
  nohup "$ROOT/scripts/start_reader_neural_tts_gpu_5090.sh" >>"$GPU_TTS_LOG_FILE" 2>&1 &
  TTS_CHILD_PID="$!"
}

start_fusion_cpu_tts() {
  nohup "$ROOT/scripts/start_reader_neural_tts.sh" >>"$CPU_TTS_LOG_FILE" 2>&1 &
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

ensure_fusion_tts_url || true

current_commit="$(current_commit)"
existing_pid="$(listening_pid || true)"

if [[ -n "$existing_pid" ]]; then
  log_msg "Puerto ${PORT} ocupado por PID ${existing_pid}. Verificando instancia..."
  
  status_raw=$(curl -fsS --max-time 2 "$startup_status_url" 2>/dev/null || echo "")
  
  if [[ -n "$status_raw" ]]; then
    # Parse metadata using python inline
    runtime_data=$(python3 -c "import json, sys; data=json.load(sys.stdin); rt=data.get('runtime', {}); print('|'.join([str(rt.get(k, '')) for k in ['app', 'commit', 'pid']]))" <<< "$status_raw")
    IFS='|' read -r rt_app rt_commit _rt_pid <<< "$runtime_data"
    
    if [[ "$rt_app" == "fusion_reader_v2" ]]; then
      if [[ "$rt_commit" == "$current_commit" ]]; then
        log_msg "Fusion Reader v2 ya está vivo con commit actual (${current_commit}). No se relanza."
        # Update PID file just in case
        echo "$existing_pid" > "$PID_FILE"
        exit 0
      else
        log_msg "Instancia vieja detectada (commit: ${rt_commit:-unknown}, actual: ${current_commit}). Reiniciando..."
        kill "$existing_pid" 2>/dev/null || true
        sleep 2
        if listening_pid >/dev/null 2>&1; then
           log_msg "Esperando que el puerto ${PORT} se libere..."
           sleep 3
        fi
      fi
    else
      log_msg "ERROR: El puerto ${PORT} está ocupado por otra aplicación que no parece ser Fusion Reader v2."
      exit 1
    fi
  else
    # Status doesn't respond but port is busy. Check if it looks like our server script.
    if [[ -r "/proc/${existing_pid}/cmdline" ]]; then
      cmdline=$(tr '\0' ' ' <"/proc/${existing_pid}/cmdline")
      if [[ "$cmdline" == *"fusion_reader_v2_server.py"* ]]; then
        log_msg "Instancia de Fusion detectada (sin metadatos runtime). Reiniciando..."
        kill "$existing_pid" 2>/dev/null || true
        sleep 3
      else
        log_msg "ERROR: El puerto ${PORT} está ocupado por un proceso desconocido (PID ${existing_pid})."
        exit 1
      fi
    else
      log_msg "ERROR: El puerto ${PORT} está ocupado y no se puede identificar el proceso."
      exit 1
    fi
  fi
fi

log_msg "==== Fusion Reader v2 startup ===="
log_msg "Commit: $(current_commit)"
log_msg "API/UI port: ${PORT}"
log_msg "TTS URL selected: ${FUSION_READER_ALLTALK_URL}"
log_msg "STT command: ${FUSION_READER_STT_COMMAND:-whisper}"
log_msg "Chat model: ${FUSION_READER_CHAT_MODEL}"
log_msg "Reasoning mode env: ${FUSION_READER_REASONING_MODE}"
log_msg "Voice env: ${FUSION_READER_VOICE:-female_03.wav}"
log_msg "Profile env: ${FUSION_READER_PROFILE:-default}"
log_msg "PID file: ${PID_FILE}"
log_msg "Persistent log: ${LOG_FILE}"

nohup "$PYTHON_BIN" -m scripts.fusion_reader_v2_server >>"$LOG_FILE" 2>&1 &
server_pid=$!

if ! printf '%s\n' "$server_pid" >"$PID_FILE" 2>/dev/null; then
  log_msg "WARN: no pude escribir PID file en ${PID_FILE}"
fi

log_msg "Fusion Reader v2 server spawned with PID ${server_pid}"

deadline=$(( $(date +%s) + STARTUP_WAIT_SECONDS ))
while (( $(date +%s) < deadline )); do
  if curl -fsS --max-time 2 "$startup_status_url" >/dev/null 2>&1; then
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
