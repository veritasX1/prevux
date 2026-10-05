#!/usr/bin/env python3
"""Pictures of Prevux in one language with a test document – never touches real settings:

    python3 tools/shots-language.py <folder> <de|en|fr> <document>"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

SCRATCH = tempfile.mkdtemp(prefix="prevux-shots-")
os.environ["XDG_DATA_HOME"] = os.environ["XDG_CONFIG_HOME"] = os.environ["XDG_CACHE_HOME"] = SCRATCH
OUT, LANGUAGE, DOCUMENT = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
(Path(SCRATCH) / "prevux").mkdir()
(Path(SCRATCH) / "prevux" / "settings.json").write_text(json.dumps({"language": LANGUAGE}))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from prevux.application import PrevuxApplication  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)


def settle(seconds=1.0):
    context = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        context.iteration(False)
        time.sleep(0.005)


def shot(widget, name):
    width, height = widget.get_width(), widget.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, width, height)
    texture = widget.get_native().get_renderer().render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(str(OUT / name))
    print("Bild", name, flush=True)


class Shots(PrevuxApplication):
    def do_activate(self):
        try:
            window = self.new_window()
            window.set_default_size(1200, 800)
            window.present()
            window.load_paths([DOCUMENT])
            settle(1.5)
            shot(window, f"fenster-{LANGUAGE}.png")
            self.show_preferences()
            settle()
            for w in Gtk.Window.list_toplevels():
                if w is not window and w.get_visible() and w.get_width() > 100:
                    shot(w, f"einstellungen-{LANGUAGE}.png")
            dialog = window.get_visible_dialog() if hasattr(window, "get_visible_dialog") else None
            if dialog is not None:
                shot(dialog, f"einstellungen-{LANGUAGE}.png")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.stdout.flush()
        os._exit(0)


Shots().run([])
