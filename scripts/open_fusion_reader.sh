#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${FUSION_READER_V2_PORT:-8010}"
STT_PORT="${FUSION_READER_STT_PORT:-8021}"
STT_PROVIDER_RAW="${FUSION_READER_STT_PROVIDER:-auto}"
case "${STT_PROVIDER_RAW,,}" in
  cli) STT_PROVIDER="cli" ;;
  server|faster_whisper|faster-whisper) STT_PROVIDER="server" ;;
  *) STT_PROVIDER="auto" ;;
esac
GPU_TTS_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
CPU_TTS_PORT="${FUSION_READER_CPU_TTS_PORT:-${DIRECT_CHAT_ALLTALK_PORT:-7851}}"
URL="http://127.0.0.1:${PORT}/"
STT_URL="http://127.0.0.1:${STT_PORT}/health"
GPU_TTS_URL="http://127.0.0.1:${GPU_TTS_PORT}/api/ready"
CPU_TTS_URL="http://127.0.0.1:${CPU_TTS_PORT}/api/ready"
LOG_DIR="$ROOT/runtime/fusion_reader_v2"
LOG_FILE="$LOG_DIR/desktop_launcher.log"
STT_LOG_FILE="$LOG_DIR/stt_server.log"
GPU_TTS_LOG_FILE="$LOG_DIR/alltalk_gpu_5090.log"
CPU_TTS_LOG_FILE="$LOG_DIR/alltalk_cpu.log"
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$LOG_DIR/tts_owner.json}"

mkdir -p "$LOG_DIR"

source "$ROOT/scripts/fusion_reader_gpu_guard.sh"
fusion_reader_refuse_when_gpu_conflict "Fusion Reader v2 launcher"
if fusion_reader_apply_game_coexistence_mode; then
  echo "Modo convivencia GPU activo: se prioriza CPU para STT/TTS automaticos." >>"$LOG_FILE"
fi

export FUSION_READER_CHAT_MODEL="${FUSION_READER_CHAT_MODEL:-qwen3:14b-q8_0}"

wait_for_url() {
  local url="$1"
  local attempts="${2:-30}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

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
  curl -fsS --max-time 2 "$GPU_TTS_URL" >/dev/null 2>&1 && fusion_tts_owner_ok
}

wait_for_fusion_gpu() {
  local attempts="${1:-30}"
  for _ in $(seq 1 "$attempts"); do
    if fusion_gpu_ready; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

echo "STT provider solicitado: ${STT_PROVIDER} (valor: ${STT_PROVIDER_RAW})" >>"$LOG_FILE"
if [[ "$STT_PROVIDER" == "cli" ]]; then
  echo "STT server ${STT_PORT}: inicio omitido porque el provider es cli." >>"$LOG_FILE"
elif ! curl -fsS "$STT_URL" >/dev/null 2>&1; then
  echo "STT server ${STT_PORT}: intentando iniciar para provider ${STT_PROVIDER}." >>"$LOG_FILE"
  (
    cd "$ROOT"
    if [[ "${FUSION_READER_GAME_COEXISTENCE_ACTIVE:-0}" == "1" ]]; then
      FUSION_READER_STT_DEVICE=cpu FUSION_READER_STT_COMPUTE_TYPE=int8 \
        nohup ./scripts/start_fusion_reader_v2_stt.sh >>"$STT_LOG_FILE" 2>&1 &
    else
      nohup ./scripts/start_fusion_reader_v2_stt.sh >>"$STT_LOG_FILE" 2>&1 &
    fi
  )
fi

if [[ "${FUSION_READER_GAME_COEXISTENCE_ACTIVE:-0}" == "1" ]]; then
  if ! curl -fsS --max-time 2 "$CPU_TTS_URL" >/dev/null 2>&1; then
    (
      cd "$ROOT"
      nohup ./scripts/start_reader_neural_tts.sh >>"$CPU_TTS_LOG_FILE" 2>&1 &
    )
    wait_for_url "$CPU_TTS_URL" 90 || true
  fi
elif ! fusion_gpu_ready \
  && ! curl -fsS --max-time 2 "$CPU_TTS_URL" >/dev/null 2>&1; then
  (
    cd "$ROOT"
    nohup ./scripts/start_reader_neural_tts_gpu_5090.sh >>"$GPU_TTS_LOG_FILE" 2>&1 &
  )
  if ! wait_for_fusion_gpu 90; then
    (
      cd "$ROOT"
      nohup ./scripts/start_reader_neural_tts.sh >>"$CPU_TTS_LOG_FILE" 2>&1 &
    )
    wait_for_url "$CPU_TTS_URL" 90 || true
  fi
fi

if ! curl -fsS "$URL" >/dev/null 2>&1; then
  echo "Iniciando servidor de Fusion Reader v2 en $URL..." >>"$LOG_FILE"
  if ! ./scripts/start_fusion_reader_v2.sh >>"$LOG_FILE" 2>&1; then
    echo "ERROR: El servidor de Fusion Reader v2 no pudo iniciarse correctamente." >>"$LOG_FILE"
    if command -v zenity >/dev/null 2>&1; then
      zenity --error --title="Error de PandaFusion" --text="No se pudo iniciar el servidor de Fusion Reader.\n\nPor favor, revisa el archivo de log en:\n$ROOT/runtime/fusion_reader_v2/logs/fusion_reader_v2_server.log" &
    elif command -v notify-send >/dev/null 2>&1; then
      notify-send -u critical "Error de PandaFusion" "No se pudo iniciar el servidor de Fusion Reader. Revisa el log."
    fi
    exit 1
  fi
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
else
  sensible-browser "$URL" >/dev/null 2>&1 &
fi
