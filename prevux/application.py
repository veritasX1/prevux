import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .i18n import _
from .window import PrevuxWindow


# Shortcuts follow Preview, with Cmd mapped to Ctrl and Option to Alt.
# Editing keys (Ctrl+C/X/V/Z/A, Delete) are handled by the window so that
# text fields keep working.
ACCELERATORS = {
    "app.new-window": ["<Control><Alt>n"],
    "app.quit": ["<Control>q"],
    "app.shortcuts": ["<Control>question"],
    "app.preferences": ["<Control>comma"],
    "win.new-from-clipboard": ["<Control>n"],
    "win.open": ["<Control>o"],
    "win.close": ["<Control>w"],
    "win.save": ["<Control>s"],
    "win.export": ["<Control><Shift>s"],
    "win.print": ["<Control>p"],
    "win.find": ["<Control>f"],
    "win.hide-sidebar": ["<Control><Alt>1"],
    "win.show-sidebar": ["<Control><Alt>2"],
    "win.show-contents": ["<Control><Alt>3"],
    "win.show-sheet": ["<Control><Alt>4"],
    "win.show-bookmarks": ["<Control><Alt>5"],
    "win.bookmark": ["<Control>d"],
    "win.slideshow": ["<Control><Shift>f"],
    "win.next-tab": ["<Control>Tab", "<Control>Page_Down"],
    "win.previous-tab": ["<Control><Shift>Tab", "<Control><Shift>ISO_Left_Tab", "<Control>Page_Up"],
    "win.display-mode::continuous": ["<Control>1"],
    "win.display-mode::single": ["<Control>2"],
    "win.display-mode::two": ["<Control>3"],
    "win.zoom-selection": ["<Control>asterisk"],
    "win.remove-background": ["<Control><Shift>k"],
    "win.actual-size": ["<Control>0"],
    "win.zoom-fit": ["<Control>9"],
    "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
    "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
    "win.markup": ["<Control><Shift>a"],
    "win.fullscreen": ["F11"],
    "win.previous-page": ["<Alt>Up"],
    "win.next-page": ["<Alt>Down"],
    "win.go-to-page": ["<Control><Alt>g"],
    "win.previous-document": ["<Alt>Page_Up"],
    "win.next-document": ["<Alt>Page_Down"],
    "win.inspector": ["<Control>i"],
    "win.rotate-left": ["<Control>l"],
    "win.rotate-right": ["<Control>r"],
    "win.highlight": ["<Control><Shift>h"],
    "win.crop": ["<Control>k"],
    "win.adjust-color": ["<Control><Alt>c"],
}


class PrevuxApplication(Adw.Application):

    def __init__(self):
        super().__init__(
            application_id="io.github.veritasx1.Prevux",
            flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.clipboard_annotation = None
        self.clipboard_marker = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Prevux")

        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(
            str(Path(__file__).resolve().parent.parent / "data")
        )
        Gtk.Window.set_default_icon_name(self.get_application_id())

        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        from . import settings
        settings.apply_background()

        for name, callback in (
            ("new-window", lambda: self.new_window().present()),
            ("quit", self.quit_all),
            ("about", self.show_about),
            ("shortcuts", self.show_shortcuts),
            ("preferences", self.show_preferences),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _param, function=callback: function())
            self.add_action(action)

        for action, accels in ACCELERATORS.items():
            self.set_accels_for_action(action, accels)

    def do_activate(self):
        window = self.get_active_window() or self.new_window()
        window.present()

    def do_open(self, files, n_files, hint):
        paths = [file.get_path() for file in files if file.get_path()]
        from . import settings
        window = self.get_active_window()
        if settings.get("open_in_tabs") and window is not None and getattr(window, "documents", None):
            window.present()
            window.load_paths(paths)          # as new tabs of the open window
            return
        self.open_paths(paths)

    def new_window(self):
        return PrevuxWindow(self)

    def open_paths(self, paths, window=None):
        window = window or self.new_window()
        window.present()
        window.load_paths(paths)
        return window

    def note_recent(self, path):
        try:
            Gtk.RecentManager.get_default().add_item(Gio.File.new_for_path(path).get_uri())
        except Exception:
            pass

    def quit_all(self):
        for window in list(self.get_windows()):
            window.close()

    def show_preferences(self, page=None):
        from .settings import PreferencesDialog

        def changed():
            for window in self.get_windows():
                if hasattr(window, "settings_changed"):
                    window.settings_changed()
        dialog = PreferencesDialog(changed)
        if page:
            dialog.set_visible_page_name(page)
        dialog.present(self.get_active_window())

    def show_about(self):
        about = Adw.AboutDialog(
            application_name="Prevux",
            application_icon=self.get_application_id(),
            developer_name="Olaf Winkler",
            version="0.9 Beta",
            website="https://github.com/veritasX1/prevux",
            comments=_("A lightweight image and PDF viewer for Linux, inspired by macOS Preview."),
            license_type=Gtk.License.CUSTOM,
            license=_(
                "Prevux is dedicated to the public domain under CC0 1.0 Universal. "
                "You may copy, modify and distribute it without asking permission."
            ),
        )
        about.present(self.get_active_window())

    def show_shortcuts(self):
        from .shortcuts import show_shortcuts
        show_shortcuts(self.get_active_window())


def main():
    return PrevuxApplication().run(sys.argv)
