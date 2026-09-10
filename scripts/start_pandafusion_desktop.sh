#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
desktop_root="$project_root/desktop"
appimage_dir="$desktop_root/src-tauri/target/release/bundle/appimage"
server_url="${PANDA_FUSION_URL:-http://127.0.0.1:8010/}"

export PATH="$HOME/.local/bin:$HOME/Miniforge3/bin:$PATH"

# La carcasa Tauri/WebKitGTK todavía no ofrece getUserMedia estable en Linux:
# puede perder el micrófono y renderizar una superficie gris al redimensionar.
# Por eso el acceso de uso diario abre Chromium en modo aplicación: conserva una
# ventana separada sin barra de navegación y usa el motor que sí pasó el flujo
# completo de dictado. Tauri permanece disponible sólo para diagnóstico explícito.

# Evitar procesos duplicados únicamente al diagnosticar la AppImage nativa.
is_appimage_running() {
  local pid
  for pid in $(pgrep -f "panda-fusion-desktop|Panda Fusión.*AppImage" 2>/dev/null || true); do
    if [[ "$pid" != "$$" && "$pid" != "$PPID" ]]; then
      return 0
    fi
  done
  return 1
}

if [[ "${PANDA_FUSION_NATIVE_EXPERIMENTAL:-0}" == "1" ]] && is_appimage_running; then
  echo "Panda Fusión ya está en ejecución."
  if command -v wmctrl >/dev/null 2>&1; then
    wmctrl -a "Panda Fusión" 2>/dev/null || true
  fi
  exit 0
fi

ensure_server_ready() {
  if curl --fail --silent --max-time 2 http://127.0.0.1:8010/api/status >/dev/null 2>&1; then
    echo "Servidor de Panda Fusión activo en http://127.0.0.1:8010."
    return 0
  fi

  echo "Iniciando servidor de Panda Fusión..."
  fusionctl start || true

  local deadline=$(( $(date +%s) + 40 ))
  while (( $(date +%s) < deadline )); do
    if curl --fail --silent --max-time 2 http://127.0.0.1:8010/api/status >/dev/null 2>&1; then
      echo "Servidor de Panda Fusión listo."
      return 0
    fi
    sleep 1
  done

  echo "ERROR: El servidor de Panda Fusión no respondió a tiempo." >&2
  return 1
}

ensure_server_ready

# El acceso principal abre el motor compatible en modo app, no una pestaña.
if [[ "${PANDA_FUSION_NATIVE_EXPERIMENTAL:-0}" != "1" ]]; then
  for browser in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$browser" >/dev/null 2>&1; then
      echo "Abriendo Panda Fusión en modo aplicación con $browser."
      exec "$browser" --app="$server_url" --class=PandaFusion --name=PandaFusion "$@"
    fi
  done

  echo "ERROR: No encontré Google Chrome ni Chromium para abrir Panda Fusión." >&2
  echo "Instalá uno de esos navegadores o usá PANDA_FUSION_NATIVE_EXPERIMENTAL=1 sólo para diagnosticar Tauri." >&2
  exit 1
fi

# La AppImage nativa queda disponible sólo bajo opt-in experimental.
shopt -s nullglob
appimages=("$appimage_dir"/Panda\ Fusión_*.AppImage)
if (( ${#appimages[@]} )); then
  target_appimage="${appimages[0]}"
  if [[ ! -x "$target_appimage" ]]; then
    chmod +x "$target_appimage"
  fi
  echo "Abriendo ventana experimental de Panda Fusión: $target_appimage"
  exec "$target_appimage" "$@"
fi

if [[ -x "$desktop_root/node_modules/.bin/tauri" ]]; then
  cd "$desktop_root"
  exec npm run dev
fi

echo "No encontré el paquete experimental de escritorio de Panda Fusión." >&2
echo "Compilalo una vez con: cd \"$desktop_root\" && npm run build" >&2
exit 1
