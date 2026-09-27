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
# PNG versions for places that do not render SVG icons.
python3 - "$DATA" "data/$APP_ID.svg" "$APP_ID" <<'PY' || true
import sys, gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
data, svg, app_id = sys.argv[1:]
import os
for size in (32, 48, 64, 128, 256, 512):
    folder = f"{data}/icons/hicolor/{size}x{size}/apps"
    os.makedirs(folder, exist_ok=True)
    GdkPixbuf.Pixbuf.new_from_file_at_size(svg, size, size).savev(f"{folder}/{app_id}.png", "png", [], [])
PY
# -t: the user icon folder has no index.theme; without it the cache is not rebuilt.
gtk-update-icon-cache -q -f -t "$DATA/icons/hicolor" 2>/dev/null || true
echo "Prevux installed. Start it from the app menu or with: prevux FILE"
