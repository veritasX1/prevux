"""Prevux' own toolbar symbols, drawn as vectors.

Every symbol is drawn into a 16×16 grid with the widget's current
foreground color, so it follows light/dark mode and scales crisply.
"""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Graphene, Gtk

from .model import rounded_rectangle


LINE = 1.35


def _stroke(cr, width=LINE):
    cr.set_line_width(width)
    cr.stroke()


def _magnifier(cr):
    cr.arc(6.8, 6.8, 4.6, 0, 2 * math.pi)
    _stroke(cr)
    cr.move_to(10.2, 10.2)
    cr.line_to(14.2, 14.2)
    _stroke(cr, 1.8)


def icon_sidebar(cr):
    rounded_rectangle(cr, 1.5, 2.5, 13, 11, 2.2)
    _stroke(cr)
    cr.move_to(6, 2.5)
    cr.line_to(6, 13.5)
    _stroke(cr)
    for y in (5.2, 7.2, 9.2):
        cr.move_to(3, y)
        cr.line_to(4.5, y)
    _stroke(cr, 1.0)


def icon_zoom_in(cr):
    _magnifier(cr)
    cr.move_to(4.6, 6.8)
    cr.line_to(9.0, 6.8)
    cr.move_to(6.8, 4.6)
    cr.line_to(6.8, 9.0)
    _stroke(cr)


def icon_zoom_out(cr):
    _magnifier(cr)
    cr.move_to(4.6, 6.8)
    cr.line_to(9.0, 6.8)
    _stroke(cr)


def icon_search(cr):
    _magnifier(cr)


def icon_info(cr):
    cr.arc(8, 8, 6.5, 0, 2 * math.pi)
    _stroke(cr)
    cr.arc(8, 4.9, 0.95, 0, 2 * math.pi)
    cr.fill()
    cr.move_to(8, 7.2)
    cr.line_to(8, 11.6)
    _stroke(cr, 1.6)


def icon_share(cr):
    cr.move_to(5.5, 6.5)
    cr.line_to(3.5, 6.5)
    cr.line_to(3.5, 14.5)
    cr.line_to(12.5, 14.5)
    cr.line_to(12.5, 6.5)
    cr.line_to(10.5, 6.5)
    _stroke(cr)
    cr.move_to(8, 10)
    cr.line_to(8, 1.5)
    cr.move_to(5.4, 4.1)
    cr.line_to(8, 1.5)
    cr.line_to(10.6, 4.1)
    _stroke(cr)


def icon_markup(cr):
    cr.arc(8, 8, 6.7, 0, 2 * math.pi)
    _stroke(cr)
    # Pen tip.
    cr.move_to(5.2, 11.8)
    cr.line_to(5.8, 9.4)
    cr.line_to(10.4, 4.8)
    cr.line_to(12.2, 6.6)
    cr.line_to(7.6, 11.2)
    cr.close_path()
    cr.set_line_join(1)
    _stroke(cr, 1.1)
    cr.move_to(9.4, 5.8)
    cr.line_to(11.2, 7.6)
    _stroke(cr, 1.0)


def icon_rotate(cr):
    rounded_rectangle(cr, 2, 7, 8, 7, 1.2)
    _stroke(cr)
    cr.arc_negative(8.5, 7.8, 5.2, 0.05, -math.pi * 0.62)
    _stroke(cr)
    # Arrow head at the start of the arc (pointing left/up).
    cr.move_to(4.6, 1.9)
    cr.line_to(6.6, 3.6)
    cr.line_to(4.0, 4.9)
    _stroke(cr)


def icon_rotate_right(cr):
    cr.translate(16, 0)
    cr.scale(-1, 1)
    icon_rotate(cr)


def icon_highlight(cr):
    cr.move_to(3.2, 10.6)
    cr.line_to(9.6, 4.2)
    cr.line_to(12.2, 6.8)
    cr.line_to(5.8, 13.2)
    cr.close_path()
    _stroke(cr, 1.15)
    cr.move_to(9.6, 4.2)
    cr.line_to(11.2, 2.6)
    cr.line_to(13.8, 5.2)
    cr.line_to(12.2, 6.8)
    _stroke(cr, 1.15)
    cr.move_to(3.2, 10.6)
    cr.line_to(1.8, 14.4)
    cr.line_to(5.8, 13.2)
    _stroke(cr, 1.15)


def icon_more(cr):
    cr.arc(8, 8, 6.7, 0, 2 * math.pi)
    _stroke(cr)
    for x in (5, 8, 11):
        cr.arc(x, 8, 0.95, 0, 2 * math.pi)
        cr.fill()


def icon_text_select(cr):
    # A small "A" next to an I-beam cursor.
    cr.move_to(1.5, 12.5)
    cr.line_to(4.5, 3.5)
    cr.line_to(7.5, 12.5)
    cr.move_to(2.6, 9.4)
    cr.line_to(6.4, 9.4)
    _stroke(cr)
    cr.move_to(11.5, 2.5)
    cr.line_to(11.5, 13.5)
    cr.move_to(9.8, 2.2)
    cr.curve_to(10.8, 2.2, 11.5, 2.5, 11.5, 3.3)
    cr.curve_to(11.5, 2.5, 12.2, 2.2, 13.2, 2.2)
    cr.move_to(9.8, 13.8)
    cr.curve_to(10.8, 13.8, 11.5, 13.5, 11.5, 12.7)
    cr.curve_to(11.5, 13.5, 12.2, 13.8, 13.2, 13.8)
    _stroke(cr, 1.1)


def icon_rect_select(cr):
    cr.set_dash([2.0, 1.6])
    cr.rectangle(2, 3, 12, 10)
    _stroke(cr)
    cr.set_dash([])


def icon_sketch(cr):
    cr.move_to(1.5, 11.5)
    cr.curve_to(3.5, 5, 5.5, 5, 6, 9)
    cr.curve_to(6.5, 12.5, 8.5, 12.5, 9.5, 8)
    _stroke(cr)
    cr.move_to(9.5, 12.8)
    cr.line_to(10, 10.8)
    cr.line_to(14, 6.8)
    cr.line_to(15.2, 8)
    cr.line_to(11.2, 12)
    cr.close_path()
    _stroke(cr, 1.0)


def icon_shapes(cr):
    rounded_rectangle(cr, 1.5, 1.5, 8.5, 8.5, 1.2)
    _stroke(cr)
    cr.arc(10, 10, 4.5, 0, 2 * math.pi)
    _stroke(cr)


def icon_text(cr):
    rounded_rectangle(cr, 1.5, 1.5, 13, 13, 2.2)
    _stroke(cr)
    cr.move_to(4.8, 4.8)
    cr.line_to(11.2, 4.8)
    cr.move_to(8, 4.8)
    cr.line_to(8, 11.6)
    _stroke(cr, 1.5)


def icon_signature(cr):
    cr.move_to(1.5, 10)
    cr.curve_to(3, 3, 5.5, 2, 5, 6)
    cr.curve_to(4.5, 10, 3, 12, 4, 11)
    cr.curve_to(6, 8, 7.5, 7, 7.5, 9)
    cr.curve_to(7.5, 10.5, 9, 10.5, 10, 8.5)
    cr.curve_to(10.5, 7.8, 11.5, 8.5, 12, 9.5)
    cr.curve_to(12.5, 10, 13.5, 9.5, 14.5, 8.5)
    _stroke(cr, 1.15)
    cr.move_to(1.5, 13.8)
    cr.line_to(14.5, 13.8)
    _stroke(cr, 1.0)


def icon_note(cr):
    cr.move_to(2, 2)
    cr.line_to(14, 2)
    cr.line_to(14, 10)
    cr.line_to(10, 14)
    cr.line_to(2, 14)
    cr.close_path()
    _stroke(cr)
    cr.move_to(14, 10)
    cr.line_to(10, 10)
    cr.line_to(10, 14)
    _stroke(cr, 1.0)
    for y in (5, 7.5):
        cr.move_to(4.5, y)
        cr.line_to(11.5, y)
    _stroke(cr, 1.0)


def icon_shape_style(cr):
    for y, width in ((3.5, 0.8), (7.5, 1.6), (12, 2.8)):
        cr.move_to(2, y)
        cr.line_to(14, y)
        _stroke(cr, width)


def icon_text_style(cr):
    cr.move_to(1.2, 13)
    cr.line_to(5.2, 2.8)
    cr.line_to(9.2, 13)
    cr.move_to(2.6, 9.6)
    cr.line_to(7.8, 9.6)
    _stroke(cr, 1.3)
    cr.arc(12.2, 10.6, 2.3, 0, 2 * math.pi)
    _stroke(cr, 1.2)
    cr.move_to(14.5, 7.8)
    cr.line_to(14.5, 13)
    _stroke(cr, 1.2)


def icon_crop(cr):
    cr.move_to(4, 1)
    cr.line_to(4, 12)
    cr.line_to(15, 12)
    cr.move_to(1, 4)
    cr.line_to(12, 4)
    cr.line_to(12, 15)
    _stroke(cr, 1.4)


def icon_adjust_size(cr):
    rounded_rectangle(cr, 1.5, 1.5, 13, 13, 2)
    _stroke(cr)
    cr.move_to(4.5, 11.5)
    cr.line_to(11.5, 4.5)
    cr.move_to(4.5, 8)
    cr.line_to(4.5, 11.5)
    cr.line_to(8, 11.5)
    cr.move_to(8, 4.5)
    cr.line_to(11.5, 4.5)
    cr.line_to(11.5, 8)
    _stroke(cr, 1.2)


def icon_adjust_color(cr):
    cr.move_to(8, 1.8)
    cr.line_to(14.6, 13.6)
    cr.line_to(1.4, 13.6)
    cr.close_path()
    _stroke(cr)
    cr.move_to(1.4, 9)
    cr.line_to(6.5, 8)
    _stroke(cr, 1.1)
    for index, y in enumerate((5.8, 7.8, 9.8)):
        cr.move_to(9.5, 8)
        cr.line_to(15.5, y)
    _stroke(cr, 0.9)


def icon_undo(cr):
    cr.move_to(3, 6)
    cr.line_to(10, 6)
    cr.curve_to(13, 6, 14, 8, 14, 9.5)
    cr.curve_to(14, 11.5, 12.5, 13, 10, 13)
    cr.line_to(6, 13)
    _stroke(cr)
    cr.move_to(5.8, 3)
    cr.line_to(2.8, 6)
    cr.line_to(5.8, 9)
    _stroke(cr)


def icon_redo(cr):
    cr.translate(16, 0)
    cr.scale(-1, 1)
    icon_undo(cr)


def icon_document(cr):
    cr.move_to(3, 1.5)
    cr.line_to(9.5, 1.5)
    cr.line_to(13, 5)
    cr.line_to(13, 14.5)
    cr.line_to(3, 14.5)
    cr.close_path()
    _stroke(cr)
    cr.move_to(9.5, 1.5)
    cr.line_to(9.5, 5)
    cr.line_to(13, 5)
    _stroke(cr, 1.0)


def icon_redact(cr):
    rounded_rectangle(cr, 1.5, 4.5, 13, 7, 1.2)
    cr.fill()


# --- shapes (for the shapes popover) --------------------------

def shape_line(cr):
    cr.move_to(2, 13)
    cr.line_to(14, 3)
    _stroke(cr, 1.5)


def shape_arrow(cr):
    shape_line(cr)
    cr.move_to(14, 3)
    cr.line_to(8.6, 3.9)
    cr.line_to(12.8, 8.5)
    cr.close_path()
    cr.fill()


def shape_rect(cr):
    cr.rectangle(1.5, 3.5, 13, 9)
    _stroke(cr, 1.5)


def shape_rounded(cr):
    rounded_rectangle(cr, 1.5, 3.5, 13, 9, 2.6)
    _stroke(cr, 1.5)


def shape_oval(cr):
    cr.save()
    cr.translate(8, 8)
    cr.scale(6.5, 4.8)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    _stroke(cr, 1.5)


def shape_bubble(cr):
    rounded_rectangle(cr, 1.5, 2, 13, 9, 2.6)
    _stroke(cr, 1.5)
    cr.move_to(4.2, 11)
    cr.line_to(3.2, 14.5)
    cr.line_to(7, 11)
    _stroke(cr, 1.5)


def shape_star(cr):
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        factor = 6.8 if index % 2 == 0 else 2.9
        x = 8 + math.cos(angle) * factor
        y = 8.6 + math.sin(angle) * factor
        if index == 0:
            cr.move_to(x, y)
        else:
            cr.line_to(x, y)
    cr.close_path()
    _stroke(cr, 1.3)


def shape_polygon(cr):
    for index in range(6):
        angle = index * math.pi / 3
        x = 8 + math.cos(angle) * 6.6
        y = 8 + math.sin(angle) * 6
        if index == 0:
            cr.move_to(x, y)
        else:
            cr.line_to(x, y)
    cr.close_path()
    _stroke(cr, 1.5)


def shape_spotlight(cr):
    cr.rectangle(0.5, 1.5, 15, 13)
    rounded_rectangle(cr, 4, 5, 8, 6, 1.2)
    cr.set_fill_rule(1)
    cr.fill()
    cr.set_fill_rule(0)


def shape_loupe(cr):
    cr.arc(7, 7, 5.3, 0, 2 * math.pi)
    _stroke(cr, 1.5)
    cr.move_to(10.9, 10.9)
    cr.line_to(14.5, 14.5)
    _stroke(cr, 2.2)


ICONS = {
    name[5:].replace("_", "-"): function
    for name, function in globals().items()
    if name.startswith("icon_")
}
ICONS.update({
    "shape-" + name[6:]: function
    for name, function in globals().items()
    if name.startswith("shape_")
})


class Icon(Gtk.Widget):
    """A symbol drawn with the widget's foreground color."""

    def __init__(self, name, size=16):
        super().__init__()
        self.name = name
        self.size = size
        self.accent = None
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)

    def set_accent(self, color):
        """Optional color bar/fill, used by the color buttons."""
        self.accent = color
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        width = self.get_width()
        height = self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))

        color = self.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.translate((width - self.size) / 2, (height - self.size) / 2)
        cr.scale(self.size / 16, self.size / 16)
        cr.set_line_cap(1)
        cr.set_line_join(1)

        if self.name == "border-color":
            self.draw_border_color(cr, color)
        elif self.name == "fill-color":
            self.draw_fill_color(cr, color)
        elif self.name == "text-color":
            self.draw_text_color(cr, color)
        else:
            ICONS[self.name](cr)

    def draw_border_color(self, cr, color):
        rounded_rectangle(cr, 2, 2, 12, 12, 2)
        if self.accent:
            cr.set_source_rgba(*self.accent)
            cr.set_line_width(3)
            cr.stroke()
        else:
            cr.set_line_width(1.2)
            cr.stroke()
            self.draw_none_slash(cr)

    def draw_fill_color(self, cr, color):
        rounded_rectangle(cr, 2, 2, 12, 12, 2)
        if self.accent:
            cr.set_source_rgba(*self.accent)
            cr.fill_preserve()
            cr.set_source_rgba(color.red, color.green, color.blue, 0.35)
            cr.set_line_width(1)
            cr.stroke()
        else:
            cr.set_line_width(1.2)
            cr.stroke()
            self.draw_none_slash(cr)

    def draw_text_color(self, cr, color):
        cr.move_to(3.5, 10.5)
        cr.line_to(8, 1.5)
        cr.line_to(12.5, 10.5)
        cr.move_to(5, 7.5)
        cr.line_to(11, 7.5)
        cr.set_line_width(1.4)
        cr.stroke()
        cr.rectangle(2, 12.5, 12, 2.5)
        cr.set_source_rgba(*(self.accent or (0, 0, 0, 1)))
        cr.fill()

    def draw_none_slash(self, cr):
        cr.move_to(3, 13)
        cr.line_to(13, 3)
        cr.set_source_rgba(1.0, 0.23, 0.19, 1)
        cr.set_line_width(1.5)
        cr.stroke()


class Swatch(Gtk.Widget):
    """A round color swatch for color popovers."""

    def __init__(self, color, size=22):
        super().__init__()
        self.color = color
        self.size = size

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        size = self.size
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, size, size))
        cr.arc(size / 2, size / 2, size / 2 - 1, 0, 2 * math.pi)
        if self.color is None:
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(0, 0, 0, 0.25)
            cr.set_line_width(1)
            cr.stroke()
            cr.move_to(size * 0.25, size * 0.75)
            cr.line_to(size * 0.75, size * 0.25)
            cr.set_source_rgb(1.0, 0.23, 0.19)
            cr.set_line_width(1.6)
            cr.stroke()
            return
        cr.set_source_rgba(*self.color)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(1)
        cr.stroke()


# Toolbar symbols at 20 px with 34 × 32 px buttons (Apple HIG: toolbar glyphs around
# 18–20 pt, hit targets at least 28 pt). Drawn on a 16 grid, scaled, so lines get a
# little heavier too – like the "medium" weight of toolbar symbols on the Mac.
TOOLBAR = 20


def icon_button(name, tooltip, toggle=False, size=TOOLBAR):
    button = Gtk.ToggleButton() if toggle else Gtk.Button()
    button.set_child(Icon(name, size))
    button.set_tooltip_text(tooltip)
    button.add_css_class("flat")
    return button


def icon_menu_button(name, tooltip, popover=None, size=TOOLBAR):
    button = Gtk.MenuButton()
    button.set_child(Icon(name, size))
    button.set_tooltip_text(tooltip)
    button.add_css_class("flat")
    if popover is not None:
        button.set_popover(popover)
    return button
