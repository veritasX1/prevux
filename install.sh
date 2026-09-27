#!/bin/sh
# Install Prevux for the current user: launcher, icon and a start script.
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
APP_ID=io.github.veritasx1.Prevux
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    python3 -m venv --system-site-packages .venv
    .venv/bin/pip install -q -r requirements.txt
fi

mkdir -p "$HOME/.local/bin" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
cat > "$HOME/.local/bin/prevux" <<SCRIPT
#!/bin/sh
exec "$ROOT/.venv/bin/python" "$ROOT/main.py" "\$@"
SCRIPT
chmod +x "$HOME/.local/bin/prevux"

cp "data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
sed "s|@EXEC@|$HOME/.local/bin/prevux|" "data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop"

update-desktop-database "$DATA/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
echo "Prevux installed. Start it from the app menu or with: prevux FILE"
