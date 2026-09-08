#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
desktop_root="$project_root/desktop"
appimage_dir="$desktop_root/src-tauri/target/release/bundle/appimage"

if ! curl --fail --silent --max-time 2 http://127.0.0.1:8010/api/status >/dev/null; then
  fusionctl start
fi

# El uso diario abre el paquete nativo ya compilado. El modo desarrollo queda
# como respaldo: recompilar Tauri en cada click no es un inicio de aplicación.
shopt -s nullglob
appimages=("$appimage_dir"/Panda\ Fusión_*.AppImage)
if (( ${#appimages[@]} )); then
  exec "${appimages[0]}"
fi

if [[ -x "$desktop_root/node_modules/.bin/tauri" ]]; then
  cd "$desktop_root"
  exec npm run dev
fi

echo "No encontré el paquete de escritorio de Panda Fusión." >&2
echo "Compilalo una vez con: cd \"$desktop_root\" && npm run build" >&2
exit 1
