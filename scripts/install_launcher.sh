#!/usr/bin/env bash
# Installer script for the Fusion Reader v2 desktop launcher and environment setup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Default local port for this installation
DEFAULT_PORT="9010"

# Sourcing existing .env if present
if [[ -f ".env" ]]; then
  echo "Encontrado archivo .env existente."
  set -a
  source ".env"
  set +a
fi

verify_python() {
  local py="$1"
  if [[ -x "$py" ]]; then
    if "$py" -c "import reportlab, docx, PIL" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

find_python() {
  # 1. FUSION_READER_PYTHON explícito
  if [[ -n "${FUSION_READER_PYTHON:-}" ]]; then
    if verify_python "$FUSION_READER_PYTHON"; then
      echo "$FUSION_READER_PYTHON"
      return 0
    fi
    echo "ERROR: FUSION_READER_PYTHON=$FUSION_READER_PYTHON no tiene las dependencias requeridas (reportlab, python-docx, Pillow)." >&2
    return 1
  fi

  # 2. .venv/bin/python3 del proyecto
  if verify_python "$ROOT/.venv/bin/python3"; then
    echo "$ROOT/.venv/bin/python3"
    return 0
  fi
  if verify_python "$ROOT/.venv/bin/python"; then
    echo "$ROOT/.venv/bin/python"
    return 0
  fi
  if verify_python "$ROOT/venv/bin/python3"; then
    echo "$ROOT/venv/bin/python3"
    return 0
  fi
  if verify_python "$ROOT/venv/bin/python"; then
    echo "$ROOT/venv/bin/python"
    return 0
  fi

  # 3. Intérpretes en PATH y ubicaciones conocidas (como Miniforge)
  local candidates=(
    "python3"
    "python"
    "${HOME}/Miniforge3/bin/python3"
    "${HOME}/Miniforge3/bin/python"
    "/home/lucy-ubuntu/Miniforge3/bin/python3"
    "/usr/bin/python3"
    "/usr/local/bin/python3"
  )
  for candidate in "${candidates[@]}"; do
    local path=""
    if [[ "$candidate" == /* ]]; then
      path="$candidate"
    else
      path="$(command -v "$candidate" || true)"
    fi
    if [[ -n "$path" ]]; then
      if verify_python "$path"; then
        echo "$path"
        return 0
      fi
    fi
  done

  return 1
}

echo "=== Fusion Reader Launcher Installer ==="

# 1. Resolver python válido
echo "Detectando intérprete de Python compatible..."
if ! PYTHON_BIN="$(find_python)"; then
  echo "ERROR: No se encontró ningún intérprete de Python válido con las dependencias requeridas (reportlab, python-docx, Pillow)." >&2
  echo "Por favor, instala los requerimientos primero (pip install -r requirements/fusion-reader-v2.txt)." >&2
  exit 1
fi
echo "Intérprete detectado: $PYTHON_BIN"

# 2. Resolver puerto
PORT="${FUSION_READER_V2_PORT:-$DEFAULT_PORT}"
echo "Usando puerto: $PORT"

# 3. Escribir configuración local al .env del repo
echo "Escribiendo configuración persistente en .env..."
cat << EOF > .env
# Archivo de configuración local persistente generado por install_launcher.sh
FUSION_READER_PYTHON=$PYTHON_BIN
FUSION_READER_V2_PORT=$PORT
EOF

# 4. Crear launcher en ~/.local/bin/
LAUNCHER_PATH="${HOME}/.local/bin/fusion-reader-launcher"
echo "Generando lanzador en $LAUNCHER_PATH..."
mkdir -p "$(dirname "$LAUNCHER_PATH")"

cat << EOF > "$LAUNCHER_PATH"
#!/usr/bin/env bash
# Lanzador de escritorio para Fusion Reader v2 generado por el instalador.
set -euo pipefail

# Iniciar open_fusion_reader.sh desde el repositorio
exec "$ROOT/scripts/open_fusion_reader.sh"
EOF

chmod +x "$LAUNCHER_PATH"

# 5. Generar accesos directos .desktop
DESKTOP_DIR="${HOME}/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

install_desktop_file() {
  local target="$1"
  cat << EOF > "$target"
[Desktop Entry]
Type=Application
Name=PandaFusion
Comment=Abrir Fusion Reader v2
Exec=$LAUNCHER_PATH
Icon=$ROOT/assets/icons/fusion_red.svg
Terminal=false
Categories=Office;
StartupNotify=true
EOF
  chmod +x "$target"
}

echo "Instalando acceso directo de aplicaciones en $DESKTOP_DIR/fusion.desktop..."
install_desktop_file "$DESKTOP_DIR/fusion.desktop"

# Instalar también en Escritorio / Desktop si existen
for d in "Desktop" "Escritorio"; do
  if [[ -d "${HOME}/$d" ]]; then
    echo "Instalando acceso directo en escritorio ~/ $d/fusion.desktop..."
    install_desktop_file "${HOME}/$d/fusion.desktop"
  fi
done

echo "Instalación completada con éxito."
