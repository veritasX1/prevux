"""Markup toolbar: tools, shapes, styles, colors, text style, signatures."""

import json
import math
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gdk, GLib, Graphene, Gtk, PangoCairo

from .i18n import _
from .icons import Icon, Swatch, icon_button, icon_menu_button
from .model import (
    BLACK,
    PALETTE,
    RED,
    InkAnnotation,
    LineAnnotation,
    LoupeAnnotation,
    ShapeAnnotation,
    SignatureAnnotation,
    Style,
    TextAnnotation,
)


SHAPES = [
    ("line", "Line"),
    ("arrow", "Arrow"),
    ("rect", "Rectangle"),
    ("rounded", "Rounded Rectangle"),
    ("oval", "Oval"),
    ("bubble", "Speech Bubble"),
    ("star", "Star"),
    ("polygon", "Polygon"),
    ("spotlight", "Spotlight"),
    ("loupe", "Loupe"),
]

LINE_WIDTHS = [0.5, 1, 2, 3, 5, 8, 12]

SIGNATURE_FILE = Path(GLib.get_user_data_dir()) / "prevux" / "signatures.json"


class MarkupDefaults:
    """The style new markup is created with (like Preview remembers it)."""

    def __init__(self):
        self.stroke = RED
        self.fill = None
        self.width = 2.0
        self.dash = "solid"
        self.shadow = False
        self.scale = 1.0

        self.text_size = 14.0
        self.family = "Sans"
        self.color = BLACK
        self.bold = False
        self.italic = False
        self.underline = False
        self.strike = False
        self.align = "left"

    def set_document(self, doc):
        """Scale sizes to the document (points for PDFs, pixels for images)."""
        if doc is None:
            return
        self.scale = doc.default_line_width() / 2.0
        self.text_size = doc.default_text_size()

    def shape_style(self):
        return Style(
            stroke=self.stroke,
            fill=self.fill,
            width=self.width * self.scale,
            dash=self.dash,
            shadow=self.shadow,
        )

    def apply_text(self, text):
        text.family = self.family
        text.size = self.text_size
        text.color = self.color
        text.bold = self.bold
        text.italic = self.italic
        text.underline = self.underline
        text.strike = self.strike
        text.align = self.align


def popover_box(spacing=8, margin=10):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.set_margin_start(margin)
    box.set_margin_end(margin)
    box.set_margin_top(margin)
    box.set_margin_bottom(margin)
    return box


def heading(text):
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("caption-heading")
    label.add_css_class("dim-label")
    return label


# ============================================================
# MARKUP TOOLBAR
# ============================================================

class MarkupToolbar(Gtk.Box):

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.window = window
        self.add_css_class("markup-toolbar")
        self.tool_buttons = {}
        self.updating = False

        # --- selection and drawing tools ----------------------
        self.tool_box = Gtk.Box(spacing=2)
        for tool, icon, tooltip in (
            ("text-select", "text-select", _("Text Selection")),
            ("rect-select", "rect-select", _("Rectangular Selection")),
            ("redact", "redact", _("Redact")),
            ("sketch", "sketch", _("Sketch")),
            ("note", "note", _("Note")),
        ):
            button = icon_button(icon, tooltip, toggle=True)
            button.connect("toggled", self.on_tool_toggled, tool)
            self.tool_buttons[tool] = button
            self.tool_box.append(button)
        self.append(self.tool_box)

        # --- insert -------------------------------------------
        self.shapes_button = icon_menu_button("shapes", _("Shapes"), self.build_shapes())
        self.shapes_button.set_always_show_arrow(True)
        self.append(self.shapes_button)

        text_button = icon_button("text", _("Text"))
        text_button.connect("clicked", lambda _button: self.window.view.insert_text())
        self.append(text_button)

        self.signature_popover = Gtk.Popover()
        self.signature_popover.connect("show", lambda _popover: self.fill_signatures())
        self.signature_button = icon_menu_button("signature", _("Sign"), self.signature_popover)
        self.signature_button.set_always_show_arrow(True)
        self.append(self.signature_button)

        self.append(self.separator())

        # --- style --------------------------------------------
        self.shape_style_popover = self.build_shape_style()
        button = icon_menu_button("shape-style", _("Shape Style"), self.shape_style_popover)
        button.set_always_show_arrow(True)
        self.append(button)

        self.border_icon = Icon("border-color")
        self.border_popover = ColorPopover(self, "stroke", allow_none=True)
        button = Gtk.MenuButton(child=self.border_icon, popover=self.border_popover)
        button.set_tooltip_text(_("Border Color"))
        button.set_always_show_arrow(True)
        button.add_css_class("flat")
        self.append(button)

        self.fill_icon = Icon("fill-color")
        self.fill_popover = ColorPopover(self, "fill", allow_none=True)
        button = Gtk.MenuButton(child=self.fill_icon, popover=self.fill_popover)
        button.set_tooltip_text(_("Fill Color"))
        button.set_always_show_arrow(True)
        button.add_css_class("flat")
        self.append(button)

        self.text_style_popover = TextStylePopover(self)
        button = icon_menu_button("text-style", _("Text Style"), self.text_style_popover)
        button.set_always_show_arrow(True)
        self.append(button)

        spacer = Gtk.Box(hexpand=True)
        self.append(spacer)

        # --- image / page tools (right side) ------------------
        self.image_box = Gtk.Box(spacing=2)
        button = icon_button("adjust-color", _("Adjust Color…"))
        button.set_action_name("win.adjust-color")
        self.image_box.append(button)
        button = icon_button("adjust-size", _("Adjust Size…"))
        button.set_action_name("win.adjust-size")
        self.image_box.append(button)
        self.append(self.image_box)

        self.crop_button = icon_button("crop", _("Crop"))
        self.crop_button.set_action_name("win.crop")
        self.append(self.crop_button)

        self.update_color_icons()

    def separator(self):
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.set_margin_start(6)
        separator.set_margin_end(6)
        separator.set_margin_top(6)
        separator.set_margin_bottom(6)
        return separator

    @property
    def view(self):
        return self.window.view

    @property
    def defaults(self):
        return self.window.defaults

    def set_document_kind(self, kind):
        pdf = kind == "pdf"
        self.tool_buttons["text-select"].set_visible(pdf)
        self.tool_buttons["note"].set_visible(pdf)
        self.tool_buttons["redact"].set_visible(pdf)
        self.image_box.set_visible(kind == "image")

    def sync_tool(self, tool):
        self.updating = True
        for name, button in self.tool_buttons.items():
            button.set_active(name == tool)
        self.updating = False

    def on_tool_toggled(self, button, tool):
        if self.updating:
            return
        if button.get_active():
            self.view.set_tool(tool)
        else:
            self.view.set_tool("select_default")
        self.sync_tool(self.view.tool)

    # --- shapes -----------------------------------------------

    def build_shapes(self):
        popover = Gtk.Popover()
        grid = Gtk.Grid(row_spacing=4, column_spacing=4)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        for index, (kind, name) in enumerate(SHAPES):
            button = Gtk.Button(child=Icon("shape-" + kind, 20))
            button.set_tooltip_text(_(name))
            button.add_css_class("flat")
            button.set_size_request(40, 36)
            button.connect("clicked", self.on_shape, kind, popover)
            grid.attach(button, index % 5, index // 5, 1, 1)
        popover.set_child(grid)
        return popover

    def on_shape(self, button, kind, popover):
        popover.popdown()
        self.view.insert_shape(kind)

    # --- shape style ------------------------------------------

    def build_shape_style(self):
        popover = Gtk.Popover()
        box = popover_box(spacing=4)

        box.append(heading(_("Line Width")))
        self.width_buttons = {}
        for width in LINE_WIDTHS:
            button = Gtk.ToggleButton(child=LineSample(width))
            button.add_css_class("flat")
            button.connect("toggled", self.on_width, width)
            self.width_buttons[width] = button
            box.append(button)

        box.append(heading(_("Line Style")))
        row = Gtk.Box(spacing=2, homogeneous=True)
        row.add_css_class("linked")
        self.dash_buttons = {}
        for dash in ("solid", "dashed", "dotted"):
            button = Gtk.ToggleButton(child=LineSample(2, dash, 48))
            button.connect("toggled", self.on_dash, dash)
            self.dash_buttons[dash] = button
            row.append(button)
        box.append(row)

        self.shadow_check = Gtk.CheckButton(label=_("Shadow"))
        self.shadow_check.connect("toggled", self.on_shadow)
        box.append(self.shadow_check)

        popover.set_child(box)
        popover.connect("show", lambda _popover: self.sync_shape_style())
        return popover

    def sync_shape_style(self):
        style = self.selected_style()
        width = self.defaults.width
        dash = self.defaults.dash
        shadow = self.defaults.shadow
        if style is not None:
            width = style.width / self.defaults.scale
            dash = style.dash
            shadow = style.shadow

        self.updating = True
        closest = min(LINE_WIDTHS, key=lambda value: abs(value - width))
        for value, button in self.width_buttons.items():
            button.set_active(value == closest)
        for value, button in self.dash_buttons.items():
            button.set_active(value == dash)
        self.shadow_check.set_active(shadow)
        self.updating = False

    def selected_style(self):
        annotation = self.view.selected
        if annotation is not None and annotation.uses_style:
            return annotation.style
        return None

    def styled_types(self):
        return (ShapeAnnotation, LineAnnotation, InkAnnotation, TextAnnotation, LoupeAnnotation)

    def on_width(self, button, width):
        if self.updating or not button.get_active():
            return
        self.defaults.width = width
        scaled = width * self.defaults.scale
        self.view.modify_selected(
            lambda annotation: setattr(annotation.style, "width", scaled),
            self.styled_types(),
        )
        self.sync_shape_style()

    def on_dash(self, button, dash):
        if self.updating or not button.get_active():
            return
        self.defaults.dash = dash
        self.view.modify_selected(
            lambda annotation: setattr(annotation.style, "dash", dash),
            self.styled_types(),
        )
        self.sync_shape_style()

    def on_shadow(self, check):
        if self.updating:
            return
        value = check.get_active()
        self.defaults.shadow = value
        self.view.modify_selected(
            lambda annotation: setattr(annotation.style, "shadow", value),
            self.styled_types(),
        )

    # --- colors -----------------------------------------------

    def apply_color(self, target, color):
        if target == "stroke":
            self.defaults.stroke = color
        elif target == "fill":
            self.defaults.fill = color
        else:
            self.defaults.color = color

        if target == "text":
            self.view.modify_selected(
                lambda annotation: setattr(annotation, "color", color or BLACK),
                TextAnnotation,
            )
        else:
            self.view.modify_selected(
                lambda annotation: setattr(annotation.style, target, color),
                self.styled_types(),
            )
        self.update_color_icons()

    def current_color(self, target):
        annotation = self.view.selected if self.window.view else None
        if target == "text":
            if isinstance(annotation, TextAnnotation):
                return annotation.color
            return self.defaults.color
        style = self.selected_style() if annotation else None
        if style is not None:
            return getattr(style, target)
        return getattr(self.defaults, target)

    def update_color_icons(self):
        if getattr(self.window, "view", None) is None:
            self.border_icon.set_accent(self.defaults.stroke)
            self.fill_icon.set_accent(self.defaults.fill)
            return
        self.border_icon.set_accent(self.current_color("stroke"))
        self.fill_icon.set_accent(self.current_color("fill"))

    # --- signatures -------------------------------------------

    def fill_signatures(self):
        box = popover_box(spacing=6)
        signatures = load_signatures()

        if not signatures:
            label = Gtk.Label(label=_("No saved signatures yet."))
            label.add_css_class("dim-label")
            box.append(label)

        for index, strokes in enumerate(signatures):
            row = Gtk.Box(spacing=4)
            button = Gtk.Button(child=SignaturePreview(strokes))
            button.add_css_class("flat")
            button.connect("clicked", self.on_signature, strokes)
            button.set_hexpand(True)
            row.append(button)
            remove = Gtk.Button(icon_name="edit-delete-symbolic")
            remove.add_css_class("flat")
            remove.add_css_class("circular")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_tooltip_text(_("Delete Signature"))
            remove.connect("clicked", self.on_remove_signature, index)
            row.append(remove)
            box.append(row)

        box.append(Gtk.Separator())
        create = Gtk.Button(label=_("Create Signature…"))
        create.add_css_class("flat")
        create.connect("clicked", self.on_create_signature)
        box.append(create)
        self.signature_popover.set_child(box)

    def on_signature(self, button, strokes):
        self.signature_popover.popdown()
        signature = SignatureAnnotation(
            [list(map(tuple, stroke)) for stroke in strokes],
            Style(stroke=BLACK, width=2.2),
        )
        self.view.insert_ink(signature)

    def on_remove_signature(self, button, index):
        signatures = load_signatures()
        del signatures[index]
        save_signatures(signatures)
        self.fill_signatures()

    def on_create_signature(self, button):
        self.signature_popover.popdown()
        SignatureDialog(self).present(self.window)


# ============================================================
# COLOR POPOVER
# ============================================================

class ColorPopover(Gtk.Popover):

    def __init__(self, toolbar, target, allow_none=False):
        super().__init__()
        self.toolbar = toolbar
        self.target = target

        box = popover_box(spacing=8)
        grid = Gtk.Grid(row_spacing=4, column_spacing=4)
        colors = list(PALETTE)
        if allow_none:
            colors = [("None", None)] + colors
        for index, (name, color) in enumerate(colors):
            button = Gtk.Button(child=Swatch(color))
            button.set_tooltip_text(_(name))
            button.add_css_class("flat")
            button.add_css_class("swatch-button")
            button.connect("clicked", self.on_color, color)
            grid.attach(button, index % 7, index // 7, 1, 1)
        box.append(grid)

        more = Gtk.Button(label=_("More Colors…"))
        more.add_css_class("flat")
        more.connect("clicked", self.on_more)
        box.append(more)
        self.set_child(box)

    def on_color(self, button, color):
        self.popdown()
        self.toolbar.apply_color(self.target, color)

    def on_more(self, button):
        self.popdown()
        dialog = Gtk.ColorDialog(with_alpha=True)
        current = self.toolbar.current_color(self.target) or RED
        initial = Gdk.RGBA()
        initial.red, initial.green, initial.blue, initial.alpha = current
        dialog.choose_rgba(self.toolbar.window, initial, None, self.on_chosen)

    def on_chosen(self, dialog, result):
        try:
            color = dialog.choose_rgba_finish(result)
        except GLib.Error:
            return
        self.toolbar.apply_color(
            self.target, (color.red, color.green, color.blue, color.alpha)
        )


# ============================================================
# TEXT STYLE
# ============================================================

class TextStylePopover(Gtk.Popover):

    def __init__(self, toolbar):
        super().__init__()
        self.toolbar = toolbar
        self.updating = False

        box = popover_box(spacing=8, margin=12)
        box.set_size_request(250, -1)

        families = sorted(
            {family.get_name() for family in PangoCairo.FontMap.get_default().list_families()},
            key=str.lower,
        )
        self.families = families
        self.font = Gtk.DropDown.new_from_strings(families)
        self.font.set_enable_search(True)
        expression = Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        self.font.set_expression(expression)
        self.font.connect("notify::selected", self.on_font)
        box.append(heading(_("Font")))
        box.append(self.font)

        row = Gtk.Box(spacing=6)
        self.color_icon = Icon("text-color", 16)
        self.color_popover = ColorPopover(toolbar, "text")
        color_button = Gtk.MenuButton(child=self.color_icon, popover=self.color_popover)
        color_button.set_tooltip_text(_("Text Color"))
        row.append(color_button)

        self.size = Gtk.SpinButton.new_with_range(4, 999, 1)
        self.size.set_digits(0)
        self.size.set_hexpand(True)
        self.size.set_tooltip_text(_("Font Size"))
        self.size.connect("value-changed", self.on_size)
        row.append(self.size)
        box.append(row)

        styles = Gtk.Box(homogeneous=True)
        styles.add_css_class("linked")
        self.style_buttons = {}
        for key, label, css in (
            ("bold", "B", "text-bold"),
            ("italic", "I", "text-italic"),
            ("underline", "U", "text-underline"),
            ("strike", "S", "text-strike"),
        ):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class(css)
            button.connect("toggled", self.on_style, key)
            self.style_buttons[key] = button
            styles.append(button)
        box.append(styles)

        aligns = Gtk.Box(homogeneous=True)
        aligns.add_css_class("linked")
        self.align_buttons = {}
        group = None
        for key, icon in (
            ("left", "format-justify-left-symbolic"),
            ("center", "format-justify-center-symbolic"),
            ("right", "format-justify-right-symbolic"),
        ):
            button = Gtk.ToggleButton(icon_name=icon)
            if group is not None:
                button.set_group(group)
            group = group or button
            button.connect("toggled", self.on_align, key)
            self.align_buttons[key] = button
            aligns.append(button)
        box.append(aligns)

        self.set_child(box)
        self.connect("show", lambda _popover: self.sync())

    @property
    def view(self):
        return self.toolbar.view

    @property
    def defaults(self):
        return self.toolbar.defaults

    def source(self):
        annotation = self.view.selected
        if isinstance(annotation, TextAnnotation):
            return annotation
        return self.defaults

    def sync(self):
        source = self.source()
        self.updating = True
        family = source.family
        lowered = [name.lower() for name in self.families]
        if family.lower() in lowered:
            self.font.set_selected(lowered.index(family.lower()))
        else:
            self.font.set_selected(Gtk.INVALID_LIST_POSITION)
        size = source.size if isinstance(source, TextAnnotation) else source.text_size
        self.size.set_value(round(size))
        for key, button in self.style_buttons.items():
            button.set_active(getattr(source, key))
        self.align_buttons[source.align].set_active(True)
        self.color_icon.set_accent(source.color)
        self.updating = False

    def apply(self, attribute, value, default_attribute=None):
        setattr(self.defaults, default_attribute or attribute, value)
        self.view.modify_selected(
            lambda annotation: setattr(annotation, attribute, value),
            TextAnnotation,
        )

    def on_font(self, dropdown, _param):
        if self.updating:
            return
        item = dropdown.get_selected_item()
        if item is not None:
            self.apply("family", item.get_string())

    def on_size(self, spin):
        if not self.updating:
            self.apply("size", spin.get_value(), "text_size")

    def on_style(self, button, key):
        if not self.updating:
            self.apply(key, button.get_active())

    def on_align(self, button, key):
        if not self.updating and button.get_active():
            self.apply("align", key)


# ============================================================
# SMALL DRAWING WIDGETS
# ============================================================

class LineSample(Gtk.Widget):

    def __init__(self, width, dash="solid", length=120):
        super().__init__()
        self.width = width
        self.dash = dash
        self.length = length

    def do_measure(self, orientation, for_size):
        size = self.length if orientation == Gtk.Orientation.HORIZONTAL else 14
        return size, size, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
        color = self.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        style = Style(width=self.width, dash=self.dash)
        style.apply_line(cr)
        cr.move_to(4, height / 2)
        cr.line_to(width - 4, height / 2)
        cr.stroke()


class SignaturePreview(Gtk.Widget):

    def __init__(self, strokes, width=180, height=56):
        super().__init__()
        self.strokes = strokes
        self.width = width
        self.height = height

    def do_measure(self, orientation, for_size):
        size = self.width if orientation == Gtk.Orientation.HORIZONTAL else self.height
        return size, size, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
        color = self.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, 1)
        draw_strokes(cr, self.strokes, width, height, 1.6)


def draw_strokes(cr, strokes, width, height, line_width):
    points = [point for stroke in strokes for point in stroke]
    if not points:
        return
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    w = max(1.0, max(xs) - min(xs))
    h = max(1.0, max(ys) - min(ys))
    scale = min((width - 8) / w, (height - 8) / h)
    ox = (width - w * scale) / 2 - min(xs) * scale
    oy = (height - h * scale) / 2 - min(ys) * scale
    ink = InkAnnotation(
        [[(ox + x * scale, oy + y * scale) for x, y in stroke] for stroke in strokes],
        Style(stroke=None, width=line_width),
    )
    cr.set_line_width(line_width)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    ink.path(cr)
    cr.stroke()


def load_signatures():
    try:
        return json.loads(SIGNATURE_FILE.read_text())
    except (OSError, ValueError):
        return []


def save_signatures(signatures):
    SIGNATURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIGNATURE_FILE.write_text(json.dumps(signatures))


class SignatureDialog(Adw.Dialog):
    """Draw a signature with mouse, touchpad or pen."""

    def __init__(self, toolbar):
        super().__init__()
        self.toolbar = toolbar
        self.strokes = []
        self.set_title(_("Create Signature"))
        self.set_content_width(520)

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)

        box = popover_box(spacing=12, margin=18)
        label = Gtk.Label(
            label=_("Sign on the line using your mouse, touchpad or pen."),
            wrap=True,
        )
        label.add_css_class("dim-label")
        box.append(label)

        self.area = Gtk.DrawingArea(content_height=200, hexpand=True)
        self.area.add_css_class("signature-pad")
        self.area.set_draw_func(self.draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        self.area.add_controller(drag)
        box.append(self.area)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        clear = Gtk.Button(label=_("Clear"))
        clear.connect("clicked", self.on_clear)
        buttons.append(clear)
        done = Gtk.Button(label=_("Done"))
        done.add_css_class("suggested-action")
        done.connect("clicked", self.on_done)
        buttons.append(done)
        box.append(buttons)

        view.set_content(box)
        self.set_child(view)

    def draw(self, area, cr, width, height):
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
        cr.set_line_width(1)
        cr.move_to(24, height * 0.72)
        cr.line_to(width - 24, height * 0.72)
        cr.stroke()

        cr.set_source_rgb(0.05, 0.05, 0.1)
        ink = InkAnnotation(self.strokes, Style(stroke=None, width=2.4))
        cr.set_line_width(2.4)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        ink.path(cr)
        cr.stroke()

    def on_begin(self, gesture, x, y):
        self.start = (x, y)
        self.strokes.append([(x, y)])
        self.area.queue_draw()

    def on_update(self, gesture, dx, dy):
        x, y = self.start[0] + dx, self.start[1] + dy
        stroke = self.strokes[-1]
        if math.hypot(x - stroke[-1][0], y - stroke[-1][1]) >= 1.2:
            stroke.append((x, y))
            self.area.queue_draw()

    def on_clear(self, button):
        self.strokes = []
        self.area.queue_draw()

    def on_done(self, button):
        if self.strokes:
            signatures = load_signatures()
            signatures.insert(0, self.strokes)
            save_signatures(signatures)
            self.toolbar.on_signature(None, self.strokes)
        self.close()

