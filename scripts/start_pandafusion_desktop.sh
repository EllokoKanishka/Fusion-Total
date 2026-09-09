#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
desktop_root="$project_root/desktop"
appimage_dir="$desktop_root/src-tauri/target/release/bundle/appimage"

export PATH="$HOME/.local/bin:$HOME/Miniforge3/bin:$PATH"

# 1. Evitar procesos duplicados si la ventana nativa de AppImage ya está corriendo
is_appimage_running() {
  local pid
  for pid in $(pgrep -f "panda-fusion-desktop|Panda Fusión.*AppImage" 2>/dev/null || true); do
    if [[ "$pid" != "$$" && "$pid" != "$PPID" ]]; then
      return 0
    fi
  done
  return 1
}

if is_appimage_running; then
  echo "Panda Fusión ya está en ejecución."
  if command -v wmctrl >/dev/null 2>&1; then
    wmctrl -a "Panda Fusión" 2>/dev/null || true
  fi
  exit 0
fi

# 2. Verificar y esperar a que el servidor backend (http://127.0.0.1:8010/api/status) esté listo
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

# 3. Lanzar la AppImage nativa compilada
shopt -s nullglob
appimages=("$appimage_dir"/Panda\ Fusión_*.AppImage)
if (( ${#appimages[@]} )); then
  target_appimage="${appimages[0]}"
  if [[ ! -x "$target_appimage" ]]; then
    chmod +x "$target_appimage"
  fi
  echo "Abriendo ventana nativa de Panda Fusión: $target_appimage"
  exec "$target_appimage" "$@"
fi

if [[ -x "$desktop_root/node_modules/.bin/tauri" ]]; then
  cd "$desktop_root"
  exec npm run dev
fi

echo "No encontré el paquete de escritorio de Panda Fusión." >&2
echo "Compilalo una vez con: cd \"$desktop_root\" && npm run build" >&2
exit 1

