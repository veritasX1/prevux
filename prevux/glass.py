"""A translucent, blurred toolbar background – Apple's materials / Liquid Glass.

The document scrolls on underneath the toolbar; the bar shows it softly blurred and tinted,
so you keep a sense of what lies above, while the controls stay easy to read. GTK has no
backdrop filter, so the bar paints the scroll area behind it itself (a WidgetPaintable,
blurred). With "Reduce transparency" it is a plain, solid bar."""

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk

BLUR = 26
TINT_LIGHT = (0.985, 0.985, 0.99, 0.74)
TINT_DARK = (0.16, 0.16, 0.17, 0.76)


def rgba(r, g, b, a=1.0):
    color = Gdk.RGBA()
    color.red, color.green, color.blue, color.alpha = r, g, b, a
    return color


class GlassBar(Gtk.Box):
    """Holds the toolbars; `behind` is the widget that scrolls underneath."""

    def __init__(self, on_height=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("glass-bar")
        self.behind = None
        self.on_height = on_height
        self.transparent = True
        self.paintable = Gtk.WidgetPaintable()
        self.paintable.connect("invalidate-contents", lambda *_args: self.queue_draw())
        self.last_height = -1

    def set_behind(self, widget):
        self.behind = widget
        self.paintable.set_widget(widget)

    def set_transparent(self, transparent):
        self.transparent = transparent
        if self.on_height:
            self.on_height(self.get_height() if transparent else 0)
        self.queue_draw()

    def report_height(self, height):
        # A Box is laid out by its layout manager (no size_allocate vfunc), so the height is
        # checked when drawing; it changes only when a bar appears or disappears.
        if height != self.last_height:
            self.last_height = height
            if self.on_height:
                GLib.idle_add(lambda: self.on_height(height if self.transparent else 0) and False)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        self.report_height(height)
        dark = Adw.StyleManager.get_default().get_dark()
        bounds = Graphene.Rect().init(0, 0, width, height)
        if self.transparent and self.behind is not None and self.behind.get_mapped():
            found, origin = self.behind.compute_point(self, Graphene.Point().init(0, 0))
            snapshot.push_clip(bounds)
            snapshot.push_blur(BLUR)
            snapshot.save()
            if found:
                snapshot.translate(origin)
            self.paintable.snapshot(snapshot, self.behind.get_width(), self.behind.get_height())
            snapshot.restore()
            snapshot.pop()
            snapshot.pop()
            snapshot.append_color(rgba(*(TINT_DARK if dark else TINT_LIGHT)), bounds)
            # The glass edge: a fine light line at the top, a soft shade at the bottom.
            snapshot.append_color(rgba(1, 1, 1, 0.06 if dark else 0.55), Graphene.Rect().init(0, 0, width, 1))
        else:
            tint = TINT_DARK if dark else TINT_LIGHT
            snapshot.append_color(rgba(tint[0], tint[1], tint[2]), bounds)
        snapshot.append_color(rgba(0, 0, 0, 0.36 if dark else 0.10),
                              Graphene.Rect().init(0, height - 1, width, 1))
        Gtk.Box.do_snapshot(self, snapshot)
