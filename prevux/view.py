"""The document view: continuous pages, zoom, markup interaction."""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango

from .i18n import _
from .model import (
    InkAnnotation,
    LoupeAnnotation,
    LineAnnotation,
    MarkupAnnotation,
    NoteAnnotation,
    RedactAnnotation,
    ShapeAnnotation,
    TextAnnotation,
    new_shape,
)
from .recognize import recognize


MARGIN = 28
GAP = 18
HANDLE = 4.5
HANDLE_HIT = 8
MIN_ZOOM = 0.05
MAX_ZOOM = 16.0
ZOOM_STEPS = [
    0.1, 0.125, 0.25, 0.33, 0.5, 0.67, 0.75, 0.85, 1.0, 1.25, 1.5, 2.0,
    3.0, 4.0, 6.0, 8.0, 12.0, 16.0,
]

RESIZE_CURSORS = {
    "nw": "nwse-resize", "se": "nwse-resize",
    "ne": "nesw-resize", "sw": "nesw-resize",
    "n": "ns-resize", "s": "ns-resize",
    "w": "ew-resize", "e": "ew-resize",
    "start": "crosshair", "end": "crosshair", "magnify": "pointer",
}


def rgba(red, green, blue, alpha=1.0):
    color = Gdk.RGBA()
    color.red, color.green, color.blue, color.alpha = red, green, blue, alpha
    return color


def is_highlight(annotation):
    return isinstance(annotation, MarkupAnnotation) and annotation.kind == "highlight"


class DocumentView(Gtk.Widget):

    __gsignals__ = {
        "zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "page-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "modified": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "notice": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, scroller):
        super().__init__()
        self.scroller = scroller
        self.set_focusable(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("document-view")

        self.doc = None
        self.zoom = 1.0
        self.fit_mode = "page"
        self.current_page = 0
        self.tool = "select"
        self.highlight_mode = None

        self.selected = None
        self.selected_page = 0
        self.text_selection = None
        self.rect_selection = None
        self.search_hits = []
        self.search_current = -1

        self.editor = None
        self.field_editor = None
        self.field_editing = None
        self.choice_popover = None
        self.retired_editors = []
        self.editing = None
        self.editing_page = 0
        self.edit_text_before = None

        self.action = None
        self.pending_anchor = None
        self.scroll_top_pending = False
        self.last_viewport = (0, 0)
        self.layout_cache = None
        self.render_queue = []
        self.render_source = None
        self.modified_source = None

        # Defaults the markup popovers write into.
        self.defaults = None

        self.setup_controllers()

        vadjustment = scroller.get_vadjustment()
        vadjustment.connect("value-changed", self.on_scrolled)
        scroller.get_hadjustment().connect("value-changed", lambda *_args: self.queue_draw())

    # ========================================================
    # DOCUMENT
    # ========================================================

    def set_document(self, doc, fit=True):
        self.finish_editing()
        self.doc = doc
        self.selected = None
        self.text_selection = None
        self.rect_selection = None
        self.search_hits = []
        self.current_page = 0
        self.layout_cache = None
        self.render_queue = []
        if fit:
            self.fit_mode = "page" if doc is None or doc.kind == "image" else "width"
        self.scroll_top_pending = True
        self.queue_resize()
        GLib.idle_add(self.refit)
        self.emit("selection-changed")

    def document_structure_changed(self):
        """Pages were rotated, cropped, added, removed or reordered."""
        self.layout_cache = None
        self.render_queue = []
        self.current_page = min(self.current_page, max(0, self.doc.page_count - 1))
        if self.fit_mode:
            GLib.idle_add(self.refit)
        self.queue_resize()
        self.queue_draw()

    def refresh(self):
        self.layout_cache = None
        if self.selected is not None and not self.find_annotation(self.selected.id):
            self.selected = None
        elif self.selected is not None:
            self.selected_page, self.selected = self.find_annotation(self.selected.id)
        self.queue_resize()
        self.queue_draw()
        self.emit("selection-changed")

    def find_annotation(self, annotation_id):
        for page, annotations in enumerate(self.doc.annotations):
            for annotation in annotations:
                if annotation.id == annotation_id:
                    return page, annotation
        return None

    def notify_modified(self):
        self.queue_draw()
        # Thumbnails and the window title are updated with a short delay.
        if self.modified_source is None:
            self.modified_source = GLib.timeout_add(300, self._emit_modified)

    def _emit_modified(self):
        self.modified_source = None
        self.emit("modified")
        return False

    # ========================================================
    # LAYOUT
    # ========================================================

    def content_size(self):
        if self.doc is None or self.doc.page_count == 0:
            return 1, 1
        sizes = [self.doc.page_size(index) for index in range(self.doc.page_count)]
        width = max(size[0] for size in sizes) * self.zoom + 2 * MARGIN
        height = (
            sum(size[1] for size in sizes) * self.zoom
            + GAP * (len(sizes) - 1)
            + 2 * MARGIN
        )
        return int(math.ceil(width)), int(math.ceil(height))

    def do_measure(self, orientation, for_size):
        width, height = self.content_size()
        size = width if orientation == Gtk.Orientation.HORIZONTAL else height
        return size, size, -1, -1

    def page_rects(self):
        if self.doc is None:
            return []
        key = (self.get_width(), self.get_height(), self.zoom, self.doc.page_count, self.doc.version)
        if self.layout_cache and self.layout_cache[0] == key:
            return self.layout_cache[1]

        width = self.get_width()
        rects = []
        y = MARGIN
        for index in range(self.doc.page_count):
            page_width, page_height = self.doc.page_size(index)
            w = page_width * self.zoom
            h = page_height * self.zoom
            x = max(MARGIN, (width - w) / 2)
            rects.append((x, y, w, h))
            y += h + GAP

        total = y - GAP + MARGIN
        if total < self.get_height():
            offset = (self.get_height() - total) / 2
            rects = [(x, y + offset, w, h) for x, y, w, h in rects]

        self.layout_cache = (key, rects)
        return rects

    def viewport_size(self):
        return self.scroller.get_width(), self.scroller.get_height()

    def visible_range(self):
        vadjustment = self.scroller.get_vadjustment()
        top = vadjustment.get_value()
        return top, top + vadjustment.get_page_size()

    def do_size_allocate(self, width, height, baseline):
        self.layout_cache = None

        if self.pending_anchor is not None:
            self.apply_anchor()

        viewport = self.viewport_size()
        if self.fit_mode and viewport != self.last_viewport:
            self.last_viewport = viewport
            GLib.idle_add(self.refit)

        if self.editor is not None:
            self.allocate_editor()

        if self.field_editor is not None:
            self.allocate_field_editor()

        if self.choice_popover is not None:
            self.choice_popover.present()

    # ========================================================
    # COORDINATES
    # ========================================================

    def page_at(self, x, y, clamp=True):
        """Return (page, page_x, page_y) for a widget point."""
        rects = self.page_rects()
        if not rects:
            return None

        best = None
        best_distance = None
        for index, (px, py, pw, ph) in enumerate(rects):
            if px <= x <= px + pw and py <= y <= py + ph:
                best = index
                break
            distance = max(py - y, y - (py + ph), 0)
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance

        if best is None or (not clamp and best_distance is not None and best_distance > 0):
            return None

        px, py, pw, ph = rects[best]
        return best, (x - px) / self.zoom, (y - py) / self.zoom

    def to_widget(self, page, x, y):
        px, py, _pw, _ph = self.page_rects()[page]
        return px + x * self.zoom, py + y * self.zoom

    def clamp_to_page(self, page, x, y):
        width, height = self.doc.page_size(page)
        return max(0.0, min(width, x)), max(0.0, min(height, y))

    # ========================================================
    # ZOOM
    # ========================================================

    def set_zoom(self, zoom, anchor=None, keep_fit=False):
        if self.doc is None:
            return
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if not keep_fit:
            self.fit_mode = None
        if abs(zoom - self.zoom) < 1e-6:
            self.emit("zoom-changed")
            return

        # Remember which document point is under the anchor.
        vadjustment = self.scroller.get_vadjustment()
        hadjustment = self.scroller.get_hadjustment()
        if anchor is None:
            anchor = (
                hadjustment.get_value() + hadjustment.get_page_size() / 2,
                vadjustment.get_value() + vadjustment.get_page_size() / 2,
            )
        located = self.page_at(*anchor)
        if located is not None:
            self.pending_anchor = (
                located,
                anchor[0] - hadjustment.get_value(),
                anchor[1] - vadjustment.get_value(),
            )

        self.zoom = zoom
        self.layout_cache = None
        self.queue_resize()
        self.queue_draw()
        self.emit("zoom-changed")

    def apply_anchor(self):
        (page, x, y), view_x, view_y = self.pending_anchor
        self.pending_anchor = None
        if self.scroll_top_pending:
            # A freshly opened document starts at the top of the first page.
            self.scroll_top_pending = False
            GLib.idle_add(self._scroll_to, 0, 0)
            return
        if page >= len(self.page_rects()):
            return
        widget_x, widget_y = self.to_widget(page, x, y)
        GLib.idle_add(self._scroll_to, widget_x - view_x, widget_y - view_y)

    def _scroll_to(self, x, y):
        self.scroller.get_hadjustment().set_value(x)
        self.scroller.get_vadjustment().set_value(y)
        return False

    def zoom_step(self, direction, anchor=None):
        if direction > 0:
            steps = [step for step in ZOOM_STEPS if step > self.zoom * 1.01]
            zoom = steps[0] if steps else MAX_ZOOM
        else:
            steps = [step for step in ZOOM_STEPS if step < self.zoom * 0.99]
            zoom = steps[-1] if steps else MIN_ZOOM
        self.set_zoom(zoom, anchor)

    def zoom_to_fit(self, mode="page"):
        self.fit_mode = mode
        self.refit()

    def refit(self):
        if self.doc is None or not self.fit_mode or self.doc.page_count == 0:
            return False
        viewport_width, viewport_height = self.viewport_size()
        if viewport_width <= 1 or viewport_height <= 1:
            return False

        page_width, page_height = self.doc.page_size(self.current_page)
        max_width = max(
            self.doc.page_size(index)[0] for index in range(self.doc.page_count)
        )
        available_width = viewport_width - 2 * MARGIN
        available_height = viewport_height - 2 * MARGIN

        if self.fit_mode == "width":
            zoom = min(available_width / max_width, 2.0)
        else:
            zoom = min(available_width / page_width, available_height / page_height)
            if self.doc.kind == "image":
                zoom = min(zoom, 1.0)

        self.last_viewport = (viewport_width, viewport_height)
        self.set_zoom(zoom, keep_fit=True)
        return False

    # ========================================================
    # SCROLLING / PAGES
    # ========================================================

    def on_scrolled(self, adjustment):
        self.queue_draw()
        rects = self.page_rects()
        if not rects:
            return
        top, bottom = self.visible_range()
        middle = top + (bottom - top) * 0.35
        page = self.current_page
        for index, (_x, y, _w, h) in enumerate(rects):
            if y - GAP / 2 <= middle <= y + h + GAP / 2:
                page = index
                break
        if page != self.current_page:
            self.current_page = page
            self.emit("page-changed", page)

    def scroll_to_page(self, page, y=None):
        rects = self.page_rects()
        if not 0 <= page < len(rects):
            return
        _x, top, _w, _h = rects[page]
        value = top - MARGIN / 2 if y is None else top + y * self.zoom - 60
        self.current_page = page
        self.scroller.get_vadjustment().set_value(value)
        self.emit("page-changed", page)

    # ========================================================
    # RENDERING
    # ========================================================

    def do_snapshot(self, snapshot):
        if self.doc is None:
            return

        top, bottom = self.visible_range()
        scale = self.zoom * self.get_scale_factor()
        dark = self.is_dark()
        visible = []

        for index, (x, y, w, h) in enumerate(self.page_rects()):
            if y + h < top - 200 or y > bottom + 200:
                continue
            visible.append(index)

            rect = Graphene.Rect().init(x, y, w, h)
            rounded = Gsk.RoundedRect()
            rounded.init_from_rect(rect, 0)
            snapshot.append_outset_shadow(
                rounded, rgba(0, 0, 0, 0.45 if dark else 0.22), 0, 1, 0, 6,
            )

            texture, rendered = self.doc.texture(index, scale)
            highlights = [
                annotation for annotation in self.doc.annotations[index]
                if is_highlight(annotation)
            ]

            # Highlights are multiplied onto the page like a real marker, so
            # the text underneath stays readable.
            if highlights:
                snapshot.push_blend(Gsk.BlendMode.MULTIPLY)

            if texture is None:
                snapshot.append_color(rgba(1, 1, 1), rect)
            else:
                ratio = texture.get_width() / max(1.0, w * self.get_scale_factor())
                snapshot.append_scaled_texture(
                    texture,
                    Gsk.ScalingFilter.TRILINEAR if ratio > 1.5 else Gsk.ScalingFilter.LINEAR,
                    rect,
                )

            if highlights:
                snapshot.pop()
                cr = snapshot.append_cairo(rect)
                cr.translate(x, y)
                cr.scale(self.zoom, self.zoom)
                for annotation in highlights:
                    cr.save()
                    annotation.draw(cr)
                    cr.restore()
                snapshot.pop()

            if texture is not None:
                self.snapshot_loupes(snapshot, index, texture, x, y, w, h)

            if self.doc.kind == "pdf" and (
                texture is None or abs(rendered - scale) / scale > 0.12
            ):
                self.schedule_render(index, scale)

            cr = snapshot.append_cairo(rect)
            cr.rectangle(x, y, w, h)
            cr.clip()
            cr.translate(x, y)
            cr.scale(self.zoom, self.zoom)
            self.draw_page_overlay(cr, index)

        if self.doc.kind == "pdf":
            keep = set(range(min(visible, default=0) - 2, max(visible, default=0) + 3))
            self.doc.evict(keep)

        self.draw_selection(snapshot)

        if self.editor is not None:
            self.snapshot_child(self.editor, snapshot)

        if self.field_editor is not None:
            self.snapshot_child(self.field_editor, snapshot)

    def snapshot_loupes(self, snapshot, index, texture, x, y, w, h):
        """Magnify the page texture inside each loupe."""
        for annotation in self.doc.annotations[index]:
            if not isinstance(annotation, LoupeAnnotation):
                continue
            cx = x + annotation.cx * self.zoom
            cy = y + annotation.cy * self.zoom
            radius = annotation.radius * self.zoom
            factor = annotation.magnification
            circle = Gsk.RoundedRect()
            circle.init_from_rect(
                Graphene.Rect().init(cx - radius, cy - radius, radius * 2, radius * 2),
                radius,
            )
            snapshot.push_rounded_clip(circle)
            snapshot.append_color(rgba(1, 1, 1), Graphene.Rect().init(cx - radius, cy - radius, radius * 2, radius * 2))
            snapshot.append_scaled_texture(
                texture,
                Gsk.ScalingFilter.LINEAR,
                Graphene.Rect().init(
                    cx + (x - cx) * factor, cy + (y - cy) * factor, w * factor, h * factor,
                ),
            )
            snapshot.pop()

    def is_dark(self):
        color = self.get_color()
        return color.red + color.green + color.blue > 1.5

    def draw_page_overlay(self, cr, index):
        if self.doc.kind == "pdf" and self.doc.has_forms:
            # Fillable fields are tinted, as in Preview.
            accent = self.accent()
            for field in self.doc.form_fields(index):
                if field["readonly"]:
                    continue
                x0, y0, x1, y1 = field["rect"]
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.12)
                cr.rectangle(x0, y0, x1 - x0, y1 - y0)
                cr.fill()

        for annotation in self.doc.annotations[index]:
            if is_highlight(annotation):
                continue
            cr.save()
            if annotation is self.editing and isinstance(annotation, TextAnnotation):
                annotation.draw(cr, with_text=False)
            else:
                annotation.draw(cr)
            cr.restore()

        accent = self.accent()

        for number, (page, (x0, y0, x1, y1)) in enumerate(self.search_hits):
            if page != index:
                continue
            if number == self.search_current:
                cr.set_source_rgba(1.0, 0.75, 0.0, 0.55)
            else:
                cr.set_source_rgba(1.0, 0.9, 0.2, 0.35)
            cr.rectangle(x0 - 1, y0 - 1, x1 - x0 + 2, y1 - y0 + 2)
            cr.fill()

        if self.text_selection and self.text_selection["page"] == index:
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.30)
            for x0, y0, x1, y1 in self.text_selection["rects"]:
                cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.fill()

        if self.rect_selection and self.rect_selection[0] == index:
            _page, x0, y0, x1, y1 = self.rect_selection
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            width, height = self.doc.page_size(index)

            # Dim the area outside the selection.
            cr.set_source_rgba(0, 0, 0, 0.25)
            cr.rectangle(0, 0, width, height)
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.set_fill_rule(1)
            cr.fill()
            cr.set_fill_rule(0)

            line = 1 / self.zoom
            cr.set_line_width(line)
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.set_source_rgb(1, 1, 1)
            cr.stroke_preserve()
            cr.set_dash([4 * line, 4 * line])
            cr.set_source_rgb(0, 0, 0)
            cr.stroke()
            cr.set_dash([])

    def accent(self):
        # libadwaita exposes the accent as a named color in its stylesheet.
        try:
            from gi.repository import Adw
            return Adw.StyleManager.get_default().get_accent_color_rgba()
        except Exception:
            return rgba(0.0, 0.48, 1.0)

    def draw_selection(self, snapshot):
        if self.selected is None or self.editing is self.selected:
            if self.rect_selection:
                self.draw_rect_selection_label(snapshot)
            return

        page = self.selected_page
        rects = self.page_rects()
        if page >= len(rects):
            return

        accent = self.accent()
        x0, y0, x1, y1 = self.selected.bounds()
        ax, ay = self.to_widget(page, x0, y0)
        bx, by = self.to_widget(page, x1, y1)

        cr = snapshot.append_cairo(
            Graphene.Rect().init(0, 0, self.get_width(), self.get_height())
        )

        if not isinstance(self.selected, LineAnnotation):
            cr.rectangle(ax - 2.5, ay - 2.5, bx - ax + 5, by - ay + 5)
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.9)
            cr.set_line_width(1)
            cr.stroke()

        for _name, hx, hy in self.selected.handles():
            wx, wy = self.to_widget(page, hx, hy)
            cr.arc(wx, wy, HANDLE, 0, 2 * math.pi)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
            cr.set_line_width(1.5)
            cr.stroke()

    def draw_rect_selection_label(self, snapshot):
        page, x0, y0, x1, y1 = self.rect_selection
        width = abs(x1 - x0)
        height = abs(y1 - y0)
        if width < 1 or height < 1:
            return
        unit = "px" if self.doc.kind == "image" else "pt"
        text = f"{width:.0f} × {height:.0f} {unit}"

        wx, wy = self.to_widget(page, max(x0, x1), max(y0, y1))
        layout = self.create_pango_layout(text)
        _ink, logical = layout.get_pixel_extents()

        box = Graphene.Rect().init(
            wx - logical.width - 14, wy + 6, logical.width + 12, logical.height + 6
        )
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(box, 5)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(rgba(0, 0, 0, 0.7), box)
        snapshot.pop()

        snapshot.save()
        snapshot.translate(Graphene.Point().init(box.get_x() + 6, box.get_y() + 3))
        snapshot.append_layout(layout, rgba(1, 1, 1))
        snapshot.restore()

    def schedule_render(self, index, scale):
        entry = (index, scale)
        if any(item[0] == index for item in self.render_queue):
            self.render_queue = [item for item in self.render_queue if item[0] != index]
        self.render_queue.append(entry)
        if self.render_source is None:
            self.render_source = GLib.idle_add(self.render_next, priority=GLib.PRIORITY_LOW)

    def render_next(self):
        if not self.render_queue or self.doc is None or self.doc.kind != "pdf":
            self.render_queue = []
            self.render_source = None
            return False

        index, scale = self.render_queue.pop(0)
        top, bottom = self.visible_range()
        rects = self.page_rects()
        if index < len(rects):
            _x, y, _w, h = rects[index]
            if not (y + h < top - 400 or y > bottom + 400):
                self.doc.render(index, scale)
                self.queue_draw()

        if not self.render_queue:
            self.render_source = None
            return False
        return True

    # ========================================================
    # INPUT
    # ========================================================

    def setup_controllers(self):
        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)
        self.drag_gesture = drag

        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self.on_pressed)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self.on_scroll)
        self.scroller.add_controller(scroll)
        self.scroll_controller = scroll

        pinch = Gtk.GestureZoom()
        pinch.connect("begin", self.on_pinch_begin)
        pinch.connect("scale-changed", self.on_pinch)
        self.scroller.add_controller(pinch)
        self.pinch_start = 1.0

    def on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        event = controller.get_current_event()
        found, x, y = event.get_position()
        anchor = None
        if found:
            point = self.scroller.compute_point(self, Graphene.Point().init(x, y))
            if point[0]:
                anchor = (point[1].x, point[1].y)
        factor = math.pow(1.0015, -dy * 40) if dy else 1
        self.set_zoom(self.zoom * factor, anchor)
        return True

    def on_pinch_begin(self, gesture, sequence):
        self.pinch_start = self.zoom

    def on_pinch(self, gesture, scale):
        found, x, y = gesture.get_bounding_box_center()
        anchor = None
        if found:
            point = self.scroller.compute_point(self, Graphene.Point().init(x, y))
            if point[0]:
                anchor = (point[1].x, point[1].y)
        self.set_zoom(self.pinch_start * scale, anchor)

    def tolerance(self):
        return 5 / self.zoom

    def hit_handle(self, x, y):
        if self.selected is None:
            return None
        for name, hx, hy in self.selected.handles():
            wx, wy = self.to_widget(self.selected_page, hx, hy)
            if math.hypot(x - wx, y - wy) <= HANDLE_HIT:
                return name
        return None

    def hit_annotation(self, page, x, y):
        tolerance = self.tolerance()
        for annotation in reversed(self.doc.annotations[page]):
            if annotation.hit(x, y, tolerance):
                return annotation
        return None

    def select(self, annotation, page=None):
        if annotation is not self.selected:
            self.selected = annotation
            if page is not None:
                self.selected_page = page
            self.queue_draw()
            self.emit("selection-changed")

    def on_pressed(self, gesture, n_press, x, y):
        if self.doc is None or n_press != 2:
            return
        located = self.page_at(x, y, clamp=False)
        if located is None:
            return
        page, px, py = located
        annotation = self.hit_annotation(page, px, py)
        if isinstance(annotation, (TextAnnotation, NoteAnnotation)):
            self.start_editing(annotation, page)
        elif annotation is None and self.tool == "text-select":
            self.select_word(page, px, py)

    def on_drag_begin(self, gesture, x, y):
        self.action = None
        if self.doc is None:
            return
        self.grab_focus()

        if self.editor is not None:
            self.finish_editing()
        if self.field_editor is not None:
            self.finish_field_edit()

        handle = self.hit_handle(x, y)
        if handle:
            self.action = {
                "type": "resize",
                "handle": handle,
                "original": self.selected.clone(),
                "changed": False,
            }
            return

        located = self.page_at(x, y)
        if located is None:
            return
        page, px, py = located

        if self.tool == "sketch":
            px, py = self.clamp_to_page(page, px, py)
            self.doc.checkpoint()
            ink = InkAnnotation([[(px, py)]], self.defaults.shape_style())
            self.doc.annotations[page].append(ink)
            self.select(None)
            self.action = {"type": "sketch", "annotation": ink, "page": page}
            self.queue_draw()
            return

        if self.tool == "note" and self.doc.kind == "pdf":
            self.doc.checkpoint()
            note = NoteAnnotation((px - 4, py - 4))
            self.doc.annotations[page].append(note)
            self.select(note, page)
            self.set_tool("select_default")
            self.start_editing(note, page, checkpoint=False)
            return

        if self.tool in ("text-select", "rect-select"):
            field = self.hit_field(page, px, py)
            if field is not None:
                self.select(None)
                self.activate_field(page, field)
                return

        annotation = self.hit_annotation(page, px, py)
        if annotation is not None:
            self.clear_text_selection()
            self.rect_selection = None
            self.select(annotation, page)
            self.action = {
                "type": "move",
                "page": page,
                "last": (px, py),
                "changed": False,
            }
            return

        self.select(None)
        self.clear_text_selection()
        self.rect_selection = None

        if self.tool == "redact" and self.doc.kind == "pdf":
            # On text: redact whole words; elsewhere: redact an area.
            on_word = any(
                x0 - 2 <= px <= x1 + 2 and y0 - 2 <= py <= y1 + 2
                for x0, y0, x1, y1, *_rest in self.doc.page_words(page)
            )
            if on_word:
                self.action = {"type": "text", "page": page, "start": (px, py)}
            else:
                px, py = self.clamp_to_page(page, px, py)
                self.action = {"type": "rect", "page": page, "start": (px, py)}
        elif self.tool == "text-select" and self.doc.kind == "pdf":
            self.action = {"type": "text", "page": page, "start": (px, py)}
        else:
            px, py = self.clamp_to_page(page, px, py)
            self.action = {"type": "rect", "page": page, "start": (px, py)}
        self.queue_draw()

    def on_drag_update(self, gesture, offset_x, offset_y):
        if self.action is None:
            return
        found, start_x, start_y = gesture.get_start_point()
        x, y = start_x + offset_x, start_y + offset_y
        kind = self.action["type"]

        if kind == "resize":
            page = self.selected_page
            px, py = self.widget_to_page(page, x, y)
            if not self.action["changed"]:
                self.doc.checkpoint()
                self.action["changed"] = True
            self.selected.resize(self.action["handle"], px, py, self.action["original"])
            self.notify_modified()
            return

        page = self.action.get("page", 0)
        px, py = self.widget_to_page(page, x, y)

        if kind == "move":
            if not self.action["changed"]:
                if math.hypot(offset_x, offset_y) < 3:
                    return
                self.doc.checkpoint()
                self.action["changed"] = True
            last_x, last_y = self.action["last"]
            self.selected.move(px - last_x, py - last_y)
            self.action["last"] = (px, py)
            self.notify_modified()

        elif kind == "sketch":
            px, py = self.clamp_to_page(page, px, py)
            stroke = self.action["annotation"].strokes[-1]
            last_x, last_y = stroke[-1]
            if math.hypot(px - last_x, py - last_y) * self.zoom >= 1.5:
                stroke.append((px, py))
                self.queue_draw()

        elif kind == "text":
            self.update_text_selection(page, self.action["start"], (px, py))

        elif kind == "rect":
            px, py = self.clamp_to_page(page, px, py)
            sx, sy = self.action["start"]
            if gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK:
                side = max(abs(px - sx), abs(py - sy))
                px = sx + math.copysign(side, px - sx)
                py = sy + math.copysign(side, py - sy)
            self.rect_selection = (page, sx, sy, px, py)
            self.queue_draw()
            self.emit("selection-changed")

    def on_drag_end(self, gesture, offset_x, offset_y):
        action = self.action
        self.action = None
        if action is None:
            return
        kind = action["type"]

        if kind == "sketch":
            self.recognize_sketch(action["annotation"], action["page"])
            self.notify_modified()

        elif kind == "move" and not action["changed"]:
            if isinstance(self.selected, NoteAnnotation):
                self.start_editing(self.selected, action["page"])

        elif kind == "text" and self.tool == "redact":
            if self.text_selection:
                self.add_redaction(self.text_selection["page"], self.text_selection["rects"])

        elif kind == "rect" and self.tool == "redact":
            if self.rect_selection:
                page, x0, y0, x1, y1 = self.rect_selection
                self.rect_selection = None
                if abs(x1 - x0) * self.zoom >= 4 and abs(y1 - y0) * self.zoom >= 4:
                    rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                    self.add_redaction(page, [rect])
                self.queue_draw()

        elif kind == "text":
            if self.text_selection and self.highlight_mode:
                self.add_markup(self.highlight_mode[0], self.highlight_mode[1])

        elif kind == "rect" and self.rect_selection:
            _page, x0, y0, x1, y1 = self.rect_selection
            if abs(x1 - x0) * self.zoom < 4 or abs(y1 - y0) * self.zoom < 4:
                self.rect_selection = None
                self.queue_draw()
            self.emit("selection-changed")

    def recognize_sketch(self, ink, page):
        result = recognize(ink.strokes[-1])
        if result is None:
            return
        # A separate undo step, so Ctrl+Z brings back the freehand stroke.
        self.doc.checkpoint()
        annotations = self.doc.annotations[page]
        index = annotations.index(ink)
        if result[0] == "line":
            shape = LineAnnotation("line", result[1], result[2], ink.style)
        else:
            shape = ShapeAnnotation(result[0], result[1], ink.style)
        annotations[index] = shape
        self.emit("notice", _("Shape recognized. Undo to keep your drawing."))

    def widget_to_page(self, page, x, y):
        px, py, _pw, _ph = self.page_rects()[page]
        return (x - px) / self.zoom, (y - py) / self.zoom

    def on_motion(self, controller, x, y):
        if self.doc is None or self.action is not None:
            return
        name = "default"
        handle = self.hit_handle(x, y)
        if handle:
            name = RESIZE_CURSORS.get(handle, "default")
        else:
            located = self.page_at(x, y, clamp=False)
            if located is not None:
                page, px, py = located
                if self.tool == "sketch":
                    name = "crosshair"
                elif self.tool == "note":
                    name = "copy"
                elif self.hit_field(page, px, py) is not None:
                    field = self.hit_field(page, px, py)
                    name = "text" if field["kind"] == "text" else "pointer"
                elif self.hit_annotation(page, px, py) is not None:
                    name = "move"
                elif self.tool == "redact":
                    name = "crosshair"
                elif self.tool == "text-select" and self.doc.kind == "pdf":
                    name = "text"
                elif self.tool == "rect-select" or self.doc.kind == "image":
                    name = "crosshair"
        self.set_cursor_from_name(name)

    # ========================================================
    # TOOLS
    # ========================================================

    def set_tool(self, tool):
        if tool == "select_default":
            tool = "text-select" if self.doc and self.doc.kind == "pdf" else "rect-select"
        self.tool = tool
        self.set_cursor_from_name("default")
        self.emit("selection-changed")

    def insertion_point(self):
        """The center of the visible part of the current page."""
        page = self.current_page
        rects = self.page_rects()
        if not rects:
            return 0, 0, 0
        hadjustment = self.scroller.get_hadjustment()
        top, bottom = self.visible_range()
        x = hadjustment.get_value() + hadjustment.get_page_size() / 2
        _px, py, _pw, ph = rects[page]
        y = (max(top, py) + min(bottom, py + ph)) / 2
        _page, px, py = self.page_at(x, y)
        px, py = self.clamp_to_page(page, px, py)
        return page, px, py

    def insert(self, annotation, page):
        self.finish_editing()
        self.doc.checkpoint()
        self.doc.annotations[page].append(annotation)
        self.clear_text_selection()
        self.rect_selection = None
        self.select(annotation, page)
        self.notify_modified()

    def insert_shape(self, kind):
        if self.doc is None:
            return
        page, x, y = self.insertion_point()
        width, height = self.doc.page_size(page)
        # Like Preview: sized to the document, not to the zoom – but never larger than half of
        # what is visible, so it stays usable when zoomed in.
        visible = min(self.viewport_size()) / self.zoom
        size = min(min(width, height) * 0.18, visible * 0.5)
        shape = new_shape(kind, (x, y), size, self.defaults.shape_style())
        self.insert(shape, page)

    def insert_text(self):
        if self.doc is None:
            return
        page, x, y = self.insertion_point()
        size = self.defaults.text_size
        width, _height = self.doc.page_size(page)
        text = TextAnnotation(
            (x - size * 1.5, y - size, x + size * 1.5, y + size), _("Text"), size,
        )
        self.defaults.apply_text(text)
        text.max_width = max(size * 2, width - text.x0)
        text.normalize()
        self.insert(text, page)
        self.start_editing(text, page, checkpoint=False, select_all=True)

    def insert_ink(self, annotation):
        if self.doc is None:
            return
        page, x, y = self.insertion_point()
        x0, y0, x1, y1 = annotation.bounds()
        width, height = self.doc.page_size(page)
        target = min(width, height) * 0.3
        factor = target / max(1.0, x1 - x0)
        annotation.transform(
            lambda px, py: (
                x + (px - (x0 + x1) / 2) * factor,
                y + (py - (y0 + y1) / 2) * factor,
            )
        )
        annotation.style.width = max(1.0, annotation.style.width * factor)
        self.insert(annotation, page)

    def add_markup(self, kind, color):
        if not self.text_selection:
            return
        selection = self.text_selection
        self.doc.checkpoint()
        markup = MarkupAnnotation(kind, selection["rects"], color, selection["text"])
        self.doc.annotations[selection["page"]].append(markup)
        self.clear_text_selection()
        self.notify_modified()

    def add_redaction(self, page, rects):
        self.doc.checkpoint()
        self.doc.annotations[page].append(RedactAnnotation(rects))
        self.clear_text_selection()
        if not getattr(self, "redact_hint_shown", False):
            self.redact_hint_shown = True
            self.emit("notice", _("Redacted content is removed permanently when you save."))
        self.notify_modified()

    def delete_selected(self):
        if self.selected is None:
            return False
        self.doc.checkpoint()
        self.doc.annotations[self.selected_page].remove(self.selected)
        self.select(None)
        self.notify_modified()
        return True

    def nudge(self, dx, dy):
        if self.selected is None:
            return False
        self.doc.checkpoint()
        self.selected.move(dx / self.zoom, dy / self.zoom)
        self.notify_modified()
        return True

    def modify_selected(self, function, only=None):
        """Apply a style change to the selection; returns True if applied."""
        annotation = self.selected
        if annotation is None or (only and not isinstance(annotation, only)):
            return False
        self.doc.checkpoint()
        function(annotation)
        if isinstance(annotation, TextAnnotation):
            annotation.fit_height()
        if self.editing is annotation:
            self.update_editor_style()
        self.notify_modified()
        return True

    # ========================================================
    # FORMS (PDF)
    # ========================================================

    def hit_field(self, page, x, y):
        if self.doc.kind != "pdf" or not self.doc.has_forms:
            return None
        for field in self.doc.form_fields(page):
            x0, y0, x1, y1 = field["rect"]
            if x0 <= x <= x1 and y0 <= y <= y1 and not field["readonly"]:
                return field
        return None

    def activate_field(self, page, field):
        kind = field["kind"]
        if kind == "checkbox":
            self.set_field(page, field, not field["checked"])
        elif kind == "radio":
            if not field["checked"]:
                self.set_field(page, field, True)
        elif kind == "choice":
            self.open_choices(page, field)
        else:
            self.start_field_edit(page, field)

    def set_field(self, page, field, value):
        self.doc.checkpoint(structure=True)
        self.doc.set_field(page, field["xref"], value)
        self.notify_modified()

    def open_choices(self, page, field):
        self.close_choices()
        popover = Gtk.Popover()
        popover.set_has_arrow(False)
        listbox = Gtk.ListBox()
        listbox.add_css_class("navigation-sidebar")
        for choice in field["choices"]:
            label = Gtk.Label(label=choice, xalign=0)
            listbox.append(label)
            if choice == field["value"]:
                listbox.select_row(label.get_parent())
        listbox.connect(
            "row-activated",
            lambda _box, row: self.choose(page, field, field["choices"][row.get_index()]),
        )
        popover.set_child(listbox)
        popover.set_parent(self)
        x0, y0, x1, y1 = field["rect"]
        ax, ay = self.to_widget(page, x0, y0)
        bx, by = self.to_widget(page, x1, y1)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(ax), int(ay), int(bx - ax), int(by - ay)
        popover.set_pointing_to(rect)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.connect("closed", lambda _popover: GLib.idle_add(self.close_choices))
        self.choice_popover = popover
        popover.popup()

    def choose(self, page, field, value):
        self.close_choices()
        if value != field["value"]:
            self.set_field(page, field, value)

    def close_choices(self):
        popover = self.choice_popover
        if popover is not None:
            self.choice_popover = None
            popover.popdown()
            popover.unparent()
        return False

    def start_field_edit(self, page, field):
        self.finish_field_edit()
        self.field_editing = (page, field)

        if field["multiline"]:
            editor = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
            editor.get_buffer().set_text(field["value"] or "")
        else:
            editor = Gtk.Entry(has_frame=False)
            editor.set_text(field["value"] or "")
            if field["max_length"]:
                editor.set_max_length(field["max_length"])
            editor.connect("activate", lambda _entry: self.next_field(1))
        editor.add_css_class("form-editor")

        x0, y0, x1, y1 = field["rect"]
        size = field["font_size"] or min(12.0, (y1 - y0) * 0.65)
        provider = Gtk.CssProvider()
        provider.load_from_string(
            f".form-editor, .form-editor text {{ font-size: {max(6.0, size * self.zoom):.1f}px; }}"
        )
        editor.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_field_key)
        editor.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_args: GLib.idle_add(self.finish_field_if_unfocused))
        editor.add_controller(focus)

        self.field_editor = editor
        editor.set_parent(self)
        editor.grab_focus()
        self.queue_resize()

    def field_editor_text(self):
        editor = self.field_editor
        if isinstance(editor, Gtk.TextView):
            buffer = editor.get_buffer()
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        return editor.get_text()

    def on_field_key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.finish_field_edit(commit=False)
            self.grab_focus()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            backwards = keyval == Gdk.KEY_ISO_Left_Tab or state & Gdk.ModifierType.SHIFT_MASK
            self.next_field(-1 if backwards else 1)
            return True
        return False

    def next_field(self, direction):
        """Commit and move to the next text field, like Tab in Preview."""
        if self.field_editing is None:
            return
        page, field = self.field_editing
        self.finish_field_edit()
        order = [
            (index, item)
            for index in range(self.doc.page_count)
            for item in self.doc.form_fields(index)
            if item["kind"] == "text" and not item["readonly"]
        ]
        keys = [(index, item["xref"]) for index, item in order]
        if not order or (page, field["xref"]) not in keys:
            return
        position = (keys.index((page, field["xref"])) + direction) % len(order)
        next_page, next_field = order[position]
        if next_page != self.current_page:
            self.scroll_to_page(next_page, next_field["rect"][1])
        self.start_field_edit(next_page, next_field)

    def finish_field_if_unfocused(self):
        editor = self.field_editor
        if editor is not None and not editor.has_focus() and editor.get_focus_child() is None:
            self.finish_field_edit()
        return False

    def finish_field_edit(self, commit=True):
        editor = self.field_editor
        if editor is None:
            return
        page, field = self.field_editing
        text = self.field_editor_text()
        self.field_editor = None
        self.field_editing = None
        if editor.has_focus() or editor.get_focus_child() is not None:
            self.grab_focus()
        editor.set_visible(False)
        self.retired_editors.append(editor)
        GLib.timeout_add(500, self.release_editors)
        if commit and text != (field["value"] or ""):
            self.set_field(page, field, text)
        self.queue_draw()

    def allocate_field_editor(self):
        page, field = self.field_editing
        if page >= len(self.page_rects()):
            return
        x0, y0, x1, y1 = field["rect"]
        ax, ay = self.to_widget(page, x0, y0)
        bx, by = self.to_widget(page, x1, y1)
        width = max(20, int(bx - ax))
        _minimum, natural, _mb, _nb = self.field_editor.measure(Gtk.Orientation.VERTICAL, width)
        height = max(int(by - ay), natural if not field["multiline"] else 0)
        top = ay + (by - ay - height) / 2 if not field["multiline"] else ay
        transform = Gsk.Transform().translate(Graphene.Point().init(ax, top))
        self.field_editor.allocate(width, int(height), -1, transform)

    # ========================================================
    # TEXT SELECTION (PDF)
    # ========================================================

    def clear_text_selection(self):
        if self.text_selection is not None:
            self.text_selection = None
            self.queue_draw()
            self.emit("selection-changed")

    def nearest_word(self, words, x, y):
        best = None
        best_distance = None
        for index, (x0, y0, x1, y1, *_rest) in enumerate(words):
            dx = max(x0 - x, 0, x - x1)
            dy = max(y0 - y, 0, y - y1)
            distance = dx + dy * 4
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best

    def update_text_selection(self, page, start, end):
        words = self.doc.page_words(page)
        if not words:
            return
        first = self.nearest_word(words, *start)
        last = self.nearest_word(words, *end)
        if first > last:
            first, last = last, first
        self.set_text_selection(page, words[first:last + 1])

    def set_text_selection(self, page, words):
        lines = {}
        order = []
        for x0, y0, x1, y1, text, block, line in words:
            key = (block, line)
            if key not in lines:
                lines[key] = [x0, y0, x1, y1, []]
                order.append(key)
            rect = lines[key]
            rect[0] = min(rect[0], x0)
            rect[1] = min(rect[1], y0)
            rect[2] = max(rect[2], x1)
            rect[3] = max(rect[3], y1)
            rect[4].append(text)

        self.text_selection = {
            "page": page,
            "rects": [tuple(lines[key][:4]) for key in order],
            "text": "\n".join(" ".join(lines[key][4]) for key in order),
        }
        self.queue_draw()
        self.emit("selection-changed")

    def select_word(self, page, x, y):
        words = self.doc.page_words(page)
        index = self.nearest_word(words, x, y) if words else None
        if index is not None:
            self.set_text_selection(page, [words[index]])

    def select_all_text(self):
        if self.doc is None or self.doc.kind != "pdf":
            return False
        words = self.doc.page_words(self.current_page)
        if words:
            self.set_text_selection(self.current_page, words)
        return True

    def selected_text(self):
        return self.text_selection["text"] if self.text_selection else ""

    # ========================================================
    # TEXT EDITING
    # ========================================================

    def start_editing(self, annotation, page, checkpoint=True, select_all=False):
        self.finish_editing()
        if checkpoint:
            self.doc.checkpoint()
        self.editing = annotation
        self.editing_page = page
        self.edit_text_before = annotation.text
        self.edit_checkpoint = checkpoint
        self.select(annotation, page)

        editor = Gtk.TextView()
        editor.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        editor.set_accepts_tab(False)
        editor.add_css_class("prevux-editor")
        if isinstance(annotation, NoteAnnotation):
            editor.add_css_class("note-editor")

        buffer = editor.get_buffer()
        buffer.set_text(annotation.text)
        self.editor_tag = buffer.create_tag("style")
        buffer.connect("changed", self.on_editor_changed)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_editor_key)
        editor.add_controller(keys)

        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_args: GLib.idle_add(self.finish_if_unfocused))
        editor.add_controller(focus)

        self.editor = editor
        editor.set_parent(self)
        self.update_editor_style()

        if select_all:
            buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
        else:
            buffer.place_cursor(buffer.get_end_iter())
        editor.grab_focus()
        self.queue_resize()

    def finish_if_unfocused(self):
        if self.editor is not None and not self.editor.has_focus():
            root = self.get_root()
            focus = root.get_focus() if root else None
            # Keep editing while the text style popover is used.
            if focus is not None and focus.get_ancestor(Gtk.Popover) is not None:
                return False
            self.finish_editing()
        return False

    def update_editor_style(self):
        if self.editor is None:
            return
        annotation = self.editing
        buffer = self.editor.get_buffer()
        tag = self.editor_tag

        if isinstance(annotation, NoteAnnotation):
            description = Pango.FontDescription.from_string("Sans 11")
            tag.set_property("font-desc", description)
            self.editor.set_left_margin(8)
            self.editor.set_right_margin(8)
            self.editor.set_top_margin(6)
            self.editor.set_bottom_margin(6)
        else:
            description = annotation.font_description()
            description.set_absolute_size(annotation.size * self.zoom * Pango.SCALE)
            tag.set_property("font-desc", description)
            red, green, blue, alpha = annotation.color
            tag.set_property("foreground-rgba", rgba(red, green, blue, alpha))
            tag.set_property(
                "underline", Pango.Underline.SINGLE if annotation.underline else Pango.Underline.NONE
            )
            tag.set_property("strikethrough", annotation.strike)
            self.editor.set_justification(
                {
                    "left": Gtk.Justification.LEFT,
                    "center": Gtk.Justification.CENTER,
                    "right": Gtk.Justification.RIGHT,
                }[annotation.align]
            )
            padding = int(round(annotation.PADDING * self.zoom))
            self.editor.set_left_margin(padding)
            self.editor.set_right_margin(padding)
            self.editor.set_top_margin(padding)
            self.editor.set_bottom_margin(padding)

        buffer.apply_tag(tag, buffer.get_start_iter(), buffer.get_end_iter())
        self.queue_resize()

    def on_editor_changed(self, buffer):
        buffer.apply_tag(self.editor_tag, buffer.get_start_iter(), buffer.get_end_iter())
        self.editing.text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if isinstance(self.editing, TextAnnotation):
            self.editing.fit_height()
        self.queue_resize()
        self.queue_draw()

    def on_editor_key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.finish_editing()
            self.grab_focus()
            return True
        return False

    def allocate_editor(self):
        annotation = self.editing
        page = self.editing_page
        if page >= len(self.page_rects()):
            return

        if isinstance(annotation, NoteAnnotation):
            x, y = self.to_widget(page, annotation.x, annotation.y)
            x += 30
            width = 220
            _minimum, natural, _mb, _nb = self.editor.measure(Gtk.Orientation.VERTICAL, width)
            height = max(120, natural)
            x = min(x, self.get_width() - width - 4)
        else:
            x0, y0, x1, y1 = annotation.bounds()
            x, y = self.to_widget(page, x0, y0)
            width = max(20, int((x1 - x0) * self.zoom))
            _minimum, natural, _mb, _nb = self.editor.measure(Gtk.Orientation.VERTICAL, width)
            height = max(natural, int((y1 - y0) * self.zoom))

        transform = Gsk.Transform().translate(Graphene.Point().init(x, y))
        self.editor.allocate(int(width), int(height), -1, transform)

    def finish_editing(self):
        if self.editor is None:
            return
        editor = self.editor
        annotation = self.editing
        page = self.editing_page
        self.editor = None
        self.editing = None
        # Move focus away first and let the text view die later: the input
        # method may still deliver events to it during this main loop turn.
        if editor.has_focus() or editor.get_focus_child() is not None:
            self.grab_focus()
        editor.set_visible(False)
        self.retired_editors.append(editor)
        GLib.timeout_add(500, self.release_editors)

        changed = annotation.text != self.edit_text_before
        empty = isinstance(annotation, TextAnnotation) and not annotation.text.strip()

        if empty:
            # Removing an emptied text box is a change of its own.
            if annotation in self.doc.annotations[page]:
                self.doc.annotations[page].remove(annotation)
            self.select(None)
        elif not changed:
            if self.edit_checkpoint:
                self.doc.discard_checkpoint()
            self.queue_resize()
            self.queue_draw()
            return

        self.notify_modified()
        self.queue_resize()

    def release_editors(self):
        while self.retired_editors:
            self.retired_editors.pop().unparent()
        return False

    def do_unroot(self):
        self.finish_editing()
        Gtk.Widget.do_unroot(self)
