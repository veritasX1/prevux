"""Prevux settings (like Preview's Settings: General, Images, PDF), stored as JSON in the
user's config folder. Read with get(), changed with put() – saved right away."""

import json
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk

from .i18n import _

PATH = Path(GLib.get_user_config_dir()) / "prevux" / "settings.json"
DEFAULTS = {
    "background": "",              # empty: the theme's colour
    "image_scale": "pixels",       # 100 %: one image pixel per screen point, or "print" size
    "pdf_scale": "pixels",         # 100 %: one PDF point per screen point, or "print" size
    "reopen_last_page": True,
    "pdf_view": "continuous",      # continuous, single, two
    "author": "",                  # name written into notes and markup
}
LAST_PAGES_KEEP = 300

_data = None
_provider = None


def _load():
    global _data
    if _data is None:
        try:
            _data = {**DEFAULTS, **json.loads(PATH.read_text())}
        except (OSError, ValueError):
            _data = dict(DEFAULTS)
    return _data


def get(key):
    return _load().get(key, DEFAULTS.get(key))


def put(key, value):
    data = _load()
    data[key] = value
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=1))


def last_page(path):
    return (_load().get("last_pages") or {}).get(str(path))


def remember_page(path, page):
    pages = dict(_load().get("last_pages") or {})
    pages.pop(str(path), None)
    pages[str(path)] = page          # newest last
    while len(pages) > LAST_PAGES_KEEP:
        pages.pop(next(iter(pages)))
    put("last_pages", pages)


def apply_background():
    """The colour behind the pages (Preview: Settings → General → Window background)."""
    global _provider
    display = Gdk.Display.get_default()
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    color = get("background")
    _provider.load_from_string(f".document-scroller {{ background-color: {color}; }}" if color else "")


def screen_dpi(widget):
    """Logical dots per inch of the monitor the widget is on (96 if unknown)."""
    try:
        surface = widget.get_native().get_surface()
        monitor = widget.get_display().get_monitor_at_surface(surface)
        width_mm = monitor.get_width_mm()
        if width_mm > 0:
            return monitor.get_geometry().width / (width_mm / 25.4)
    except Exception:
        pass
    return 96.0


def actual_size_zoom(widget, doc):
    """The zoom that "Actual Size" means, depending on the 100 % setting."""
    if doc is None:
        return 1.0
    if doc.kind == "image":
        if get("image_scale") != "print":
            return 1.0
        dpi = getattr(doc, "dpi", None) or 72.0
        if isinstance(dpi, (tuple, list)):
            dpi = dpi[0] or 72.0
        return screen_dpi(widget) / float(dpi)
    return screen_dpi(widget) / 72.0 if get("pdf_scale") == "print" else 1.0


class PreferencesDialog(Adw.PreferencesDialog):

    def __init__(self, on_change=None):
        super().__init__(title=_("Settings"))
        self.on_change = on_change or (lambda: None)

        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        group = Adw.PreferencesGroup(title=_("Appearance"))
        row = Adw.ActionRow(title=_("Window background"), subtitle=_("The colour around the pages"))
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        button.set_rgba(self.background_rgba())
        self.background_button = button
        button.connect("notify::rgba", self.on_background)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Default"))
        reset.add_css_class("flat")
        reset.connect("clicked", self.on_background_reset)
        row.add_suffix(button)
        row.add_suffix(reset)
        group.add(row)
        page.add(group)
        group = Adw.PreferencesGroup(title=_("Notes and Markup"))
        author = Adw.EntryRow(title=_("Your name in notes and markup"), text=get("author"))
        author.connect("changed", lambda e: self.set("author", e.get_text().strip()))
        group.add(author)
        page.add(group)
        self.add(page)

        page = Adw.PreferencesPage(title=_("Images"), icon_name="image-x-generic-symbolic")
        group = Adw.PreferencesGroup(title=_("Define 100% scale as"))
        group.add(self.choice("image_scale", [("pixels", _("1 image pixel equals 1 screen point")),
                                              ("print", _("Size on screen equals size on printout"))]))
        page.add(group)
        self.add(page)

        page = Adw.PreferencesPage(title=_("PDF"), icon_name="x-office-document-symbolic")
        group = Adw.PreferencesGroup(title=_("Define 100% scale as"))
        group.add(self.choice("pdf_scale", [("pixels", _("1 point equals 1 screen point")),
                                            ("print", _("Size on screen equals size on printout"))]))
        page.add(group)
        group = Adw.PreferencesGroup(title=_("Opening documents"))
        reopen = Adw.SwitchRow(title=_("Continue where you left off"),
                               subtitle=_("Open a PDF at the page you last viewed"), active=get("reopen_last_page"))
        reopen.connect("notify::active", lambda r, _p: self.set("reopen_last_page", r.get_active()))
        group.add(reopen)
        group.add(self.choice("pdf_view", [("continuous", _("Continuous Scroll")), ("single", _("Single Page")),
                                           ("two", _("Two Pages"))], _("When opening for the first time")))
        page.add(group)
        self.add(page)

    @staticmethod
    def background_rgba():
        """The colour in use: the chosen one, or the theme's default behind the pages."""
        color = Gdk.RGBA()
        if get("background") and color.parse(get("background")):
            return color
        color.parse("#242424" if Adw.StyleManager.get_default().get_dark() else "#ebebed")
        return color

    def on_background(self, button, _param):
        if not getattr(self, "resetting", False):
            self.set("background", button.get_rgba().to_string())

    def on_background_reset(self, _button):
        self.set("background", "")
        self.resetting = True
        self.background_button.set_rgba(self.background_rgba())
        self.resetting = False

    def choice(self, key, options, title=None):
        row = Adw.ComboRow(title=title or "", model=Gtk.StringList.new([label for _value, label in options]))
        values = [value for value, _label in options]
        row.set_selected(values.index(get(key)) if get(key) in values else 0)
        row.connect("notify::selected", lambda r, _p: self.set(key, values[r.get_selected()]))
        return row

    def set(self, key, value):
        put(key, value)
        if key == "background":
            apply_background()
        self.on_change()
