#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$ROOT/scripts/lib/env_helper.sh"
load_env_safe

URL="$(fusion_api_base_url)/"
LOG_DIR="${FUSION_READER_LOG_ROOT:-${FUSION_READER_LOG_DIR:-${FUSION_READER_RUNTIME_ROOT:-${FUSION_READER_RUNTIME_DIR:-$ROOT/runtime/fusion_reader_v2}}/logs}}"
LOG_FILE="$LOG_DIR/desktop_launcher.log"

cd "$ROOT"
mkdir -p "$LOG_DIR"

wait_for_url() {
  local url="$1"
  local attempts="${2:-30}"
  for _ in $(seq 1 "$attempts"); do
    if curl --noproxy '*' -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

if ! wait_for_url "${URL}health/live" 1; then
  echo "Iniciando servidor de Fusion Reader v2 en $URL..." >>"$LOG_FILE"

  if command -v systemctl >/dev/null 2>&1 && systemctl --user show >/dev/null 2>&1; then
    echo "Usando servicio systemd (pandafusion.service)..." >>"$LOG_FILE"
    if ! systemctl --user start pandafusion.service >>"$LOG_FILE" 2>&1; then
      echo "ERROR: Falló systemctl --user start pandafusion.service." >>"$LOG_FILE"
      START_SUCCESS=1
    else
      START_SUCCESS=0
      wait_for_url "${URL}health/live" 40 || START_SUCCESS=1
    fi
  else
    echo "Fallback: Usando script tradicional..." >>"$LOG_FILE"
    if ! ./scripts/start_fusion_reader_v2.sh >>"$LOG_FILE" 2>&1; then
      START_SUCCESS=1
    else
      START_SUCCESS=0
    fi
  fi

  if [[ "${START_SUCCESS:-1}" != "0" ]]; then
    echo "ERROR: El servidor de Fusion Reader v2 no pudo iniciarse o no respondió a tiempo." >>"$LOG_FILE"

    # Calcular la ruta del log respetando overrides
    RUN_DIR="${FUSION_READER_RUNTIME_ROOT:-${FUSION_READER_RUNTIME_DIR:-$ROOT/runtime/fusion_reader_v2}}"
    L_DIR="${FUSION_READER_LOG_ROOT:-${FUSION_READER_LOG_DIR:-$RUN_DIR/logs}}"
    EFFECTIVE_LOG_FILE="${FUSION_READER_LOG_FILE:-$L_DIR/fusion_reader_v2_server.log}"

    if command -v zenity >/dev/null 2>&1; then
      zenity --error --title="Error de PandaFusion" --text="No se pudo iniciar el servidor de Fusion Reader.\n\nPor favor, revisa el archivo de log en:\n$EFFECTIVE_LOG_FILE" &
    elif command -v notify-send >/dev/null 2>&1; then
      notify-send -u critical "Error de PandaFusion" "No se pudo iniciar el servidor de Fusion Reader. Revisa el log en $EFFECTIVE_LOG_FILE."
    fi
    exit 1
  fi
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
else
  sensible-browser "$URL" >/dev/null 2>&1 &
fi

