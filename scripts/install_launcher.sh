#!/usr/bin/env bash
# Installer script for the Fusion Reader v2 desktop launcher and environment setup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Default local port for this installation
DEFAULT_PORT="9010"

source "$ROOT/scripts/lib/env_helper.sh"

if [[ -f "$ROOT/.env" ]]; then
  echo "Encontrado archivo .env existente. Cargando de forma segura..."
  load_env_safe
fi

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

# 4.5 Configurar servicio systemd de usuario
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
echo "Generando servicio systemd en $SYSTEMD_USER_DIR/pandafusion.service..."
mkdir -p "$SYSTEMD_USER_DIR"
SERVICE_PATH="$SYSTEMD_USER_DIR/pandafusion.service"

cat << EOF > "$SERVICE_PATH"
[Unit]
Description=PandaFusion Server
After=network.target

[Service]
Type=simple
EnvironmentFile="$ROOT/.env"
WorkingDirectory="$ROOT"
ExecStart="$ROOT/scripts/start_pandafusion_systemd.sh"
Restart=on-failure
RestartSec=3
KillMode=control-group

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload || echo "ADVERTENCIA: No se pudo recargar systemd. Si estás en un entorno sin systemd, el servicio podría no funcionar."

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
