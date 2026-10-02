"""Annotation model.

Annotations live in page coordinates (PDF points or image pixels, in the
page's displayed orientation).  The same draw() code is used for the screen,
for printing and for flattening markup into saved images.
"""

import copy
import math
import uuid

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Pango, PangoCairo


# ============================================================
# COLORS
# ============================================================

# Palette in the spirit of the macOS system colors.
PALETTE = [
    ("Red", (1.00, 0.23, 0.19, 1.0)),
    ("Orange", (1.00, 0.58, 0.00, 1.0)),
    ("Yellow", (1.00, 0.80, 0.00, 1.0)),
    ("Green", (0.20, 0.78, 0.35, 1.0)),
    ("Mint", (0.00, 0.78, 0.75, 1.0)),
    ("Blue", (0.00, 0.48, 1.00, 1.0)),
    ("Indigo", (0.35, 0.34, 0.84, 1.0)),
    ("Purple", (0.69, 0.32, 0.87, 1.0)),
    ("Pink", (1.00, 0.18, 0.33, 1.0)),
    ("Brown", (0.64, 0.52, 0.37, 1.0)),
    ("Gray", (0.56, 0.56, 0.58, 1.0)),
    ("Black", (0.0, 0.0, 0.0, 1.0)),
    ("White", (1.0, 1.0, 1.0, 1.0)),
]

RED = PALETTE[0][1]
BLACK = (0.0, 0.0, 0.0, 1.0)

HIGHLIGHT_COLORS = [
    ("Yellow", (1.00, 0.87, 0.20, 1.0)),
    ("Green", (0.55, 0.90, 0.40, 1.0)),
    ("Blue", (0.45, 0.75, 1.00, 1.0)),
    ("Pink", (1.00, 0.55, 0.75, 1.0)),
    ("Purple", (0.78, 0.60, 1.00, 1.0)),
]

NOTE_SIZE = 22


def set_color(cr, color):
    cr.set_source_rgba(*color)


# ============================================================
# STYLE
# ============================================================

class Style:

    def __init__(
        self,
        stroke=RED,
        fill=None,
        width=3.0,
        dash="solid",
        shadow=False,
    ):
        self.stroke = stroke
        self.fill = fill
        self.width = width
        self.dash = dash
        self.shadow = shadow

    def copy(self):
        return copy.copy(self)

    def to_dict(self):
        return {
            "stroke": self.stroke,
            "fill": self.fill,
            "width": self.width,
            "dash": self.dash,
            "shadow": self.shadow,
        }

    @classmethod
    def from_dict(cls, data):
        style = cls()
        for key, value in data.items():
            if isinstance(value, list):
                value = tuple(value)
            setattr(style, key, value)
        return style

    def apply_line(self, cr):
        cr.set_line_width(self.width)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        if self.dash == "dashed":
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
            cr.set_dash([self.width * 3, self.width * 2])
        elif self.dash == "dotted":
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_dash([0.01, self.width * 2])
        else:
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_dash([])


# ============================================================
# BASE
# ============================================================

class Annotation:

    kind = ""
    # Whether the style popovers (border/fill/shape style) apply.
    uses_style = True

    def __init__(self, style=None):
        self.id = uuid.uuid4().hex
        self.style = style.copy() if style else Style()

    def clone(self):
        other = copy.deepcopy(self)
        other.id = uuid.uuid4().hex
        return other

    # --- geometry ---------------------------------------------

    def bounds(self):
        raise NotImplementedError

    def move(self, dx, dy):
        self.transform(lambda x, y: (x + dx, y + dy))

    def transform(self, function):
        raise NotImplementedError

    def reorient(self, function):
        """Rotate or flip with the page; text stays upright."""
        self.transform(function)
        self.normalize()

    def handles(self):
        x0, y0, x1, y1 = self.bounds()
        xm = (x0 + x1) / 2
        ym = (y0 + y1) / 2
        return [
            ("nw", x0, y0), ("n", xm, y0), ("ne", x1, y0),
            ("w", x0, ym), ("e", x1, ym),
            ("sw", x0, y1), ("s", xm, y1), ("se", x1, y1),
        ]

    def resize(self, handle, x, y, original):
        """Resize from a copy of the original state while dragging."""
        ox0, oy0, ox1, oy1 = original.bounds()
        x0, y0, x1, y1 = ox0, oy0, ox1, oy1

        if "w" in handle:
            x0 = x
        if "e" in handle:
            x1 = x
        if "n" in handle:
            y0 = y
        if "s" in handle:
            y1 = y

        self.set_bounds_from(original, (x0, y0, x1, y1))

    def set_bounds_from(self, original, rect):
        ox0, oy0, ox1, oy1 = original.bounds()
        nx0, ny0, nx1, ny1 = rect
        sx = (nx1 - nx0) / (ox1 - ox0) if ox1 != ox0 else 1
        sy = (ny1 - ny0) / (oy1 - oy0) if oy1 != oy0 else 1

        state = copy.deepcopy(original.__dict__)
        state["id"] = self.id
        self.__dict__.update(state)
        self.transform(
            lambda px, py: (
                nx0 + (px - ox0) * sx,
                ny0 + (py - oy0) * sy,
            )
        )
        self.normalize()

    def normalize(self):
        pass

    def hit(self, x, y, tolerance):
        x0, y0, x1, y1 = self.bounds()
        return (
            x0 - tolerance <= x <= x1 + tolerance
            and y0 - tolerance <= y <= y1 + tolerance
        )

    # --- drawing ----------------------------------------------

    def draw(self, cr):
        raise NotImplementedError

    def draw_shadow(self, cr, path_function):
        if not self.style.shadow:
            return

        offset = max(2.0, self.style.width)

        cr.save()
        cr.translate(offset, offset)
        path_function(cr)
        cr.set_source_rgba(0, 0, 0, 0.30)

        if self.style.fill:
            cr.fill_preserve()

        self.style.apply_line(cr)
        cr.stroke()
        cr.restore()

    # --- serialization ----------------------------------------

    def to_dict(self):
        data = {
            key: value
            for key, value in self.__dict__.items()
            if key not in ("style", "id")
        }
        data["kind"] = self.kind
        data["style"] = self.style.to_dict()
        return data

    @staticmethod
    def from_dict(data):
        data = dict(data)
        kind = data.pop("kind")
        style = Style.from_dict(data.pop("style", {}))

        cls = ANNOTATION_TYPES[kind]
        annotation = cls.__new__(cls)
        annotation.id = uuid.uuid4().hex
        annotation.style = style

        for key, value in data.items():
            setattr(annotation, key, to_tuples(value))

        if hasattr(annotation, "strokes"):
            annotation.strokes = [list(stroke) for stroke in annotation.strokes]
        if hasattr(annotation, "rects"):
            annotation.rects = list(annotation.rects)

        if cls in (ShapeAnnotation, LineAnnotation, MarkupAnnotation, RedactAnnotation):
            annotation.kind = kind

        return annotation


def to_tuples(value):
    if isinstance(value, list):
        return tuple(to_tuples(item) for item in value)
    return value


# ============================================================
# SHAPES IN A RECTANGLE
# ============================================================

class ShapeAnnotation(Annotation):

    KINDS = ("rect", "rounded", "oval", "star", "bubble", "polygon", "spotlight")

    def __init__(self, kind, rect, style=None):
        super().__init__(style)
        self.kind = kind
        self.x0, self.y0, self.x1, self.y1 = rect
        self.normalize()

    def bounds(self):
        return (self.x0, self.y0, self.x1, self.y1)

    def transform(self, function):
        self.x0, self.y0 = function(self.x0, self.y0)
        self.x1, self.y1 = function(self.x1, self.y1)

    def normalize(self):
        self.x0, self.x1 = sorted((self.x0, self.x1))
        self.y0, self.y1 = sorted((self.y0, self.y1))

    def path(self, cr):
        x0, y0, x1, y1 = self.bounds()
        width = x1 - x0
        height = y1 - y0

        if self.kind in ("rect", "spotlight"):
            cr.rectangle(x0, y0, width, height)

        elif self.kind == "rounded":
            rounded_rectangle(
                cr, x0, y0, width, height,
                min(width, height) * 0.18,
            )

        elif self.kind == "oval":
            if width <= 0 or height <= 0:
                return
            cr.save()
            cr.translate(x0 + width / 2, y0 + height / 2)
            cr.scale(width / 2, height / 2)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()

        elif self.kind == "star":
            cx = x0 + width / 2
            cy = y0 + height / 2
            for index in range(10):
                angle = -math.pi / 2 + index * math.pi / 5
                factor = 1.0 if index % 2 == 0 else 0.42
                px = cx + math.cos(angle) * width / 2 * factor
                py = cy + math.sin(angle) * height / 2 * factor
                if index == 0:
                    cr.move_to(px, py)
                else:
                    cr.line_to(px, py)
            cr.close_path()

        elif self.kind == "polygon":
            cx = x0 + width / 2
            cy = y0 + height / 2
            for index in range(6):
                angle = index * math.pi / 3
                px = cx + math.cos(angle) * width / 2
                py = cy + math.sin(angle) * height / 2
                if index == 0:
                    cr.move_to(px, py)
                else:
                    cr.line_to(px, py)
            cr.close_path()

        elif self.kind == "bubble":
            body = height * 0.78
            radius = min(width, body) * 0.2
            tail_x = x0 + width * 0.22
            cr.new_sub_path()
            cr.arc(x0 + radius, y0 + radius, radius, math.pi, 1.5 * math.pi)
            cr.arc(x1 - radius, y0 + radius, radius, 1.5 * math.pi, 0)
            cr.arc(x1 - radius, y0 + body - radius, radius, 0, 0.5 * math.pi)
            cr.line_to(tail_x + width * 0.14, y0 + body)
            cr.line_to(tail_x - width * 0.06, y1)
            cr.line_to(tail_x, y0 + body)
            cr.arc(x0 + radius, y0 + body - radius, radius, 0.5 * math.pi, math.pi)
            cr.close_path()

    def draw(self, cr):
        if self.kind == "spotlight":
            self.draw_spotlight(cr)
            return

        self.draw_shadow(cr, self.path)

        self.path(cr)

        if self.style.fill:
            set_color(cr, self.style.fill)
            cr.fill_preserve()

        if self.style.stroke:
            set_color(cr, self.style.stroke)
            self.style.apply_line(cr)
            cr.stroke()
        else:
            cr.new_path()

    def draw_spotlight(self, cr):
        """Dim everything except the rectangle, like Preview's highlight."""
        x0, y0, x1, y1 = self.bounds()
        cx0, cy0, cx1, cy1 = cr.clip_extents()
        cr.rectangle(cx0, cy0, cx1 - cx0, cy1 - cy0)
        rounded_rectangle(cr, x0, y0, x1 - x0, y1 - y0, min(x1 - x0, y1 - y0) * 0.08)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.set_source_rgba(0, 0, 0, 0.5)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)

    def hit(self, x, y, tolerance):
        if not super().hit(x, y, tolerance):
            return False

        if self.style.fill or self.kind == "spotlight":
            return True

        # Unfilled shapes are only hit on their outline.
        surface = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
        cr = cairo.Context(surface)
        self.path(cr)
        cr.set_line_width(self.style.width + tolerance * 2)
        return cr.in_stroke(x, y)


def rounded_rectangle(cr, x, y, width, height, radius):
    radius = max(0.0, min(radius, width / 2, height / 2))
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
    cr.close_path()


# ============================================================
# LINES AND ARROWS
# ============================================================

class LineAnnotation(Annotation):

    KINDS = ("line", "arrow")

    def __init__(self, kind, start, end, style=None):
        super().__init__(style)
        self.kind = kind
        self.start = tuple(start)
        self.end = tuple(end)

    def bounds(self):
        (ax, ay), (bx, by) = self.start, self.end
        return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

    def transform(self, function):
        self.start = function(*self.start)
        self.end = function(*self.end)

    def handles(self):
        return [
            ("start", *self.start),
            ("end", *self.end),
        ]

    def resize(self, handle, x, y, original):
        self.start = original.start
        self.end = original.end
        if handle == "start":
            self.start = (x, y)
        else:
            self.end = (x, y)

    def arrow_size(self):
        return max(9.0, self.style.width * 3.5)

    def draw(self, cr):
        (ax, ay), (bx, by) = self.start, self.end
        angle = math.atan2(by - ay, bx - ax)
        length = math.hypot(bx - ax, by - ay)
        head = min(self.arrow_size(), length * 0.6)

        end_x, end_y = bx, by
        if self.kind == "arrow":
            end_x = bx - math.cos(angle) * head * 0.8
            end_y = by - math.sin(angle) * head * 0.8

        def path(cr):
            cr.move_to(ax, ay)
            cr.line_to(end_x, end_y)

        self.draw_shadow(cr, path)

        color = self.style.stroke or BLACK
        set_color(cr, color)
        self.style.apply_line(cr)
        path(cr)
        cr.stroke()

        if self.kind == "arrow":
            cr.set_dash([])
            cr.move_to(bx, by)
            cr.line_to(
                bx - math.cos(angle - 0.45) * head,
                by - math.sin(angle - 0.45) * head,
            )
            cr.line_to(
                bx - math.cos(angle + 0.45) * head,
                by - math.sin(angle + 0.45) * head,
            )
            cr.close_path()
            cr.fill()

    def hit(self, x, y, tolerance):
        return distance_to_segment(
            x, y, self.start, self.end
        ) <= tolerance + self.style.width / 2


def distance_to_segment(x, y, a, b):
    (ax, ay), (bx, by) = a, b
    dx = bx - ax
    dy = by - ay
    length = dx * dx + dy * dy

    if length == 0:
        return math.hypot(x - ax, y - ay)

    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


# ============================================================
# SKETCH (INK)
# ============================================================

class InkAnnotation(Annotation):

    kind = "ink"

    def __init__(self, strokes=None, style=None):
        super().__init__(style)
        self.strokes = strokes or []

    def bounds(self):
        points = [point for stroke in self.strokes for point in stroke]
        if not points:
            return (0, 0, 0, 0)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return (min(xs), min(ys), max(xs), max(ys))

    def transform(self, function):
        self.strokes = [
            [function(*point) for point in stroke]
            for stroke in self.strokes
        ]

    def path(self, cr):
        for stroke in self.strokes:
            if not stroke:
                continue
            cr.move_to(*stroke[0])
            if len(stroke) == 1:
                cr.line_to(*stroke[0])
            elif len(stroke) == 2:
                cr.line_to(*stroke[1])
            else:
                # Smooth through midpoints with quadratic-like curves.
                for index in range(1, len(stroke) - 1):
                    (px, py), (qx, qy) = stroke[index], stroke[index + 1]
                    mx, my = (px + qx) / 2, (py + qy) / 2
                    x0, y0 = cr.get_current_point()
                    cr.curve_to(
                        x0 + (px - x0) * 2 / 3, y0 + (py - y0) * 2 / 3,
                        mx + (px - mx) * 2 / 3, my + (py - my) * 2 / 3,
                        mx, my,
                    )
                cr.line_to(*stroke[-1])

    def draw(self, cr):
        self.draw_shadow(cr, self.path)
        set_color(cr, self.style.stroke or BLACK)
        self.style.apply_line(cr)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        self.path(cr)
        cr.stroke()

    def hit(self, x, y, tolerance):
        if not super().hit(x, y, tolerance):
            return False
        limit = tolerance + self.style.width / 2
        for stroke in self.strokes:
            if len(stroke) == 1 and math.hypot(
                x - stroke[0][0], y - stroke[0][1]
            ) <= limit:
                return True
            for a, b in zip(stroke, stroke[1:]):
                if distance_to_segment(x, y, a, b) <= limit:
                    return True
        return False


# ============================================================
# TEXT
# ============================================================

class TextAnnotation(Annotation):

    kind = "text"
    PADDING = 4

    def __init__(self, rect, text="", size=18.0, style=None):
        super().__init__(style or Style(stroke=None, fill=None, width=1))
        self.x0, self.y0, self.x1, self.y1 = rect
        self.text = text
        self.family = "Sans"
        self.size = size
        self.color = BLACK
        self.bold = False
        self.italic = False
        self.underline = False
        self.strike = False
        self.align = "left"
        # Like in Preview, a new text box grows with its text until its
        # width is changed by hand.
        self.auto_width = True
        self.max_width = None

    def bounds(self):
        return (self.x0, self.y0, self.x1, self.y1)

    def transform(self, function):
        self.x0, self.y0 = function(self.x0, self.y0)
        self.x1, self.y1 = function(self.x1, self.y1)

    def normalize(self):
        self.x0, self.x1 = sorted((self.x0, self.x1))
        self.y0, self.y1 = sorted((self.y0, self.y1))
        self.x1 = max(self.x1, self.x0 + self.size)
        self.fit_height()

    def reorient(self, function):
        width = self.x1 - self.x0
        height = self.y1 - self.y0
        cx, cy = function((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)
        self.x0, self.y0 = cx - width / 2, cy - height / 2
        self.x1, self.y1 = cx + width / 2, cy + height / 2

    def handles(self):
        # Text boxes grow in height automatically; only the width is
        # adjustable, like in Preview.
        ym = (self.y0 + self.y1) / 2
        return [("w", self.x0, ym), ("e", self.x1, ym)]

    def resize(self, handle, x, y, original):
        self.auto_width = False
        self.x0, self.x1 = original.x0, original.x1
        if handle == "w":
            self.x0 = min(x, self.x1 - self.size)
        else:
            self.x1 = max(x, self.x0 + self.size)
        self.fit_height()

    def font_description(self):
        description = Pango.FontDescription.from_string(self.family)
        description.set_absolute_size(self.size * Pango.SCALE)
        description.set_weight(
            Pango.Weight.BOLD if self.bold else Pango.Weight.NORMAL
        )
        description.set_style(
            Pango.Style.ITALIC if self.italic else Pango.Style.NORMAL
        )
        return description

    def layout(self, cr):
        layout = PangoCairo.create_layout(cr)
        # Keep glyph metrics independent of the zoom level.
        options = cairo.FontOptions()
        options.set_hint_metrics(cairo.HINT_METRICS_OFF)
        options.set_hint_style(cairo.HINT_STYLE_NONE)
        PangoCairo.context_set_font_options(layout.get_context(), options)
        layout.context_changed()

        layout.set_font_description(self.font_description())
        layout.set_width(
            int(max(1, self.x1 - self.x0 - 2 * self.PADDING) * Pango.SCALE)
        )
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_alignment(
            {
                "left": Pango.Alignment.LEFT,
                "center": Pango.Alignment.CENTER,
                "right": Pango.Alignment.RIGHT,
            }[self.align]
        )

        attributes = Pango.AttrList()
        if self.underline:
            attributes.insert(Pango.attr_underline_new(Pango.Underline.SINGLE))
        if self.strike:
            attributes.insert(Pango.attr_strikethrough_new(True))
        layout.set_attributes(attributes)

        layout.set_text(self.text or " ", -1)
        return layout

    def fit_width(self):
        """Grow or shrink an auto-width box to its longest line."""
        if not getattr(self, "auto_width", False):
            return
        surface = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
        layout = self.layout(cairo.Context(surface))
        layout.set_width(-1)
        _ink, logical = layout.get_extents()
        width = logical.width / Pango.SCALE + 2 * self.PADDING + self.size * 0.6
        if self.max_width:
            width = min(width, self.max_width)
        width = max(width, self.size * 2)
        if self.align == "right":
            self.x0 = self.x1 - width
        elif self.align == "center":
            center = (self.x0 + self.x1) / 2
            self.x0, self.x1 = center - width / 2, center + width / 2
        else:
            self.x1 = self.x0 + width

    def fit_height(self):
        self.fit_width()
        surface = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
        layout = self.layout(cairo.Context(surface))
        _ink, logical = layout.get_extents()
        height = logical.height / Pango.SCALE + 2 * self.PADDING
        self.y1 = self.y0 + height

    def draw(self, cr, with_text=True):
        x0, y0, x1, y1 = self.bounds()

        if self.style.fill:
            set_color(cr, self.style.fill)
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.fill()

        if self.style.stroke:
            set_color(cr, self.style.stroke)
            self.style.apply_line(cr)
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.stroke()

        if with_text and self.text:
            set_color(cr, self.color)
            cr.move_to(x0 + self.PADDING, y0 + self.PADDING)
            PangoCairo.show_layout(cr, self.layout(cr))


# ============================================================
# NOTES
# ============================================================

class NoteAnnotation(Annotation):

    kind = "note"
    uses_style = False

    def __init__(self, point, text="", color=None):
        super().__init__()
        self.x, self.y = point
        self.text = text
        self.color = color or (1.0, 0.85, 0.25, 1.0)

    def bounds(self):
        return (self.x, self.y, self.x + NOTE_SIZE, self.y + NOTE_SIZE)

    def transform(self, function):
        self.x, self.y = function(self.x, self.y)

    def reorient(self, function):
        half = NOTE_SIZE / 2
        cx, cy = function(self.x + half, self.y + half)
        self.x, self.y = cx - half, cy - half

    def handles(self):
        return []

    def draw(self, cr):
        x, y, s = self.x, self.y, NOTE_SIZE
        fold = s * 0.3

        cr.save()
        cr.translate(1, 1.5)
        cr.rectangle(x, y, s, s)
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.fill()
        cr.restore()

        cr.move_to(x, y)
        cr.line_to(x + s, y)
        cr.line_to(x + s, y + s - fold)
        cr.line_to(x + s - fold, y + s)
        cr.line_to(x, y + s)
        cr.close_path()
        set_color(cr, self.color)
        cr.fill()

        cr.move_to(x + s, y + s - fold)
        cr.line_to(x + s - fold, y + s - fold)
        cr.line_to(x + s - fold, y + s)
        cr.close_path()
        cr.set_source_rgba(0, 0, 0, 0.15)
        cr.fill()

        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.set_line_width(1)
        for index in range(3):
            line_y = y + s * (0.3 + index * 0.18)
            cr.move_to(x + s * 0.2, line_y)
            cr.line_to(x + s * (0.8 if index < 2 else 0.55), line_y)
        cr.stroke()


# ============================================================
# TEXT MARKUP (PDF)
# ============================================================

class MarkupAnnotation(Annotation):

    KINDS = ("highlight", "underline", "strike")
    uses_style = False

    def __init__(self, kind, rects, color, text=""):
        super().__init__()
        self.kind = kind
        self.rects = [tuple(rect) for rect in rects]
        self.color = color
        self.text = text

    def bounds(self):
        return (
            min(rect[0] for rect in self.rects),
            min(rect[1] for rect in self.rects),
            max(rect[2] for rect in self.rects),
            max(rect[3] for rect in self.rects),
        )

    def transform(self, function):
        rects = []
        for x0, y0, x1, y1 in self.rects:
            ax, ay = function(x0, y0)
            bx, by = function(x1, y1)
            rects.append((min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))
        self.rects = rects

    def handles(self):
        return []

    def hit(self, x, y, tolerance):
        return any(
            x0 - tolerance <= x <= x1 + tolerance
            and y0 - tolerance <= y <= y1 + tolerance
            for x0, y0, x1, y1 in self.rects
        )

    def draw(self, cr):
        for x0, y0, x1, y1 in self.rects:
            height = y1 - y0
            if self.kind == "highlight":
                cr.save()
                cr.set_operator(cairo.OPERATOR_MULTIPLY)
                set_color(cr, self.color)
                rounded_rectangle(cr, x0, y0, x1 - x0, height, height * 0.15)
                cr.fill()
                cr.restore()
            else:
                line_y = y1 - height * 0.08
                if self.kind == "strike":
                    line_y = y0 + height * 0.55
                set_color(cr, self.color)
                cr.set_line_width(max(1.0, height * 0.07))
                cr.set_dash([])
                cr.move_to(x0, line_y)
                cr.line_to(x1, line_y)
                cr.stroke()


class RedactAnnotation(MarkupAnnotation):
    """Black boxes; the content underneath is removed when saving."""

    KINDS = ("redact",)

    def __init__(self, rects):
        super().__init__("redact", rects, BLACK)

    def draw(self, cr):
        cr.set_source_rgb(0, 0, 0)
        for x0, y0, x1, y1 in self.rects:
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
        cr.fill()


# ============================================================
# LOUPE
# ============================================================

class LoupeAnnotation(Annotation):
    """A round magnifier. The lens shows the area around its target, magnified. When the
    lens is moved off its target, a thin circle marks the magnified area and a line with an
    end point leads from it to the lens – nothing important is covered.
    The magnified content comes from a page source (a cairo surface or the view's texture);
    draw() adds frame and pointer."""

    kind = "loupe"

    def __init__(self, center, radius, magnification=2.0, style=None, target=None):
        super().__init__(style)
        self.cx, self.cy = center
        self.radius = radius
        self.magnification = magnification
        self.tx, self.ty = target if target is not None else center

    def target(self):
        # Loupes from before the pointer have no target: they magnify their centre.
        return getattr(self, "tx", self.cx), getattr(self, "ty", self.cy)

    def area_radius(self):
        """Radius of the magnified area around the target."""
        return self.radius / self.magnification

    def detached(self):
        tx, ty = self.target()
        return math.hypot(tx - self.cx, ty - self.cy) > self.radius * 0.35

    def bounds(self):
        r = self.radius
        x0, y0, x1, y1 = self.cx - r, self.cy - r, self.cx + r, self.cy + r
        if self.detached():
            tx, ty = self.target()
            a = self.area_radius()
            x0, y0, x1, y1 = min(x0, tx - a), min(y0, ty - a), max(x1, tx + a), max(y1, ty + a)
        return (x0, y0, x1, y1)

    def transform(self, function):
        """Page changes (rotate, crop, scale) take lens and target along."""
        tx, ty = self.target()
        ax, ay = function(self.cx - self.radius, self.cy)
        bx, by = function(self.cx + self.radius, self.cy)
        self.cx, self.cy = function(self.cx, self.cy)
        self.tx, self.ty = function(tx, ty)
        self.radius = max(4.0, math.hypot(bx - ax, by - ay) / 2)

    def move(self, dx, dy):
        """Dragging moves the lens only; the magnified spot stays where it is."""
        self.tx, self.ty = self.target()
        self.cx += dx
        self.cy += dy

    def reorient(self, function):
        tx, ty = self.target()
        self.cx, self.cy = function(self.cx, self.cy)
        self.tx, self.ty = function(tx, ty)

    def magnifier_handle(self):
        angle = -math.pi / 4
        return (
            self.cx + math.cos(angle) * self.radius,
            self.cy + math.sin(angle) * self.radius,
        )

    def handles(self):
        r = self.radius
        x0, y0, x1, y1 = self.cx - r, self.cy - r, self.cx + r, self.cy + r
        return [
            ("nw", x0, y0), ("ne", x1, y0), ("sw", x0, y1), ("se", x1, y1),
            ("magnify", *self.magnifier_handle()),
            ("target", *self.target()),
        ]

    def resize(self, handle, x, y, original):
        self.cx, self.cy = original.cx, original.cy
        self.radius = original.radius
        self.magnification = original.magnification
        self.tx, self.ty = original.target()
        if handle == "target":
            self.tx, self.ty = x, y
            return
        distance = math.hypot(x - original.cx, y - original.cy)
        if handle == "magnify":
            factor = distance / max(1.0, original.radius)
            self.magnification = max(1.1, min(8.0, original.magnification * factor))
        else:
            self.radius = max(8.0, distance / math.sqrt(2))

    def hit(self, x, y, tolerance):
        if math.hypot(x - self.cx, y - self.cy) <= self.radius + tolerance:
            return True
        tx, ty = self.target()
        return self.detached() and math.hypot(x - tx, y - ty) <= self.area_radius() + tolerance

    def clip_path(self, cr):
        cr.new_path()
        cr.arc(self.cx, self.cy, self.radius, 0, 2 * math.pi)

    def draw(self, cr, source=None, source_scale=1.0):
        tx, ty = self.target()
        if source is not None:
            cr.save()
            self.clip_path(cr)
            cr.clip()
            cr.translate(self.cx, self.cy)
            cr.scale(self.magnification, self.magnification)
            cr.translate(-tx, -ty)
            cr.scale(1 / source_scale, 1 / source_scale)
            cr.set_source_surface(source, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()

        color = self.style.stroke or (0.55, 0.55, 0.6, 1.0)
        if self.detached():
            # Pointer: circle around the magnified spot, a line to the lens, an end point.
            area = self.area_radius()
            angle = math.atan2(self.cy - ty, self.cx - tx)
            set_color(cr, color)
            cr.set_line_width(max(0.6, self.style.width * 0.6))
            cr.new_path()
            cr.arc(tx, ty, area, 0, 2 * math.pi)
            cr.stroke()
            cr.move_to(tx + math.cos(angle) * area, ty + math.sin(angle) * area)
            cr.line_to(self.cx - math.cos(angle) * self.radius, self.cy - math.sin(angle) * self.radius)
            cr.stroke()
            cr.arc(tx + math.cos(angle) * area, ty + math.sin(angle) * area, max(1.2, self.style.width * 1.1), 0, 2 * math.pi)
            cr.fill()

        # Soft shadow and frame.
        cr.save()
        cr.arc(self.cx, self.cy + self.radius * 0.03, self.radius, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(self.style.width * 1.8)
        cr.stroke()
        cr.restore()

        self.clip_path(cr)
        set_color(cr, color)
        cr.set_line_width(self.style.width)
        cr.stroke()


# ============================================================
# SIGNATURE
# ============================================================

class SignatureAnnotation(InkAnnotation):

    kind = "signature"


ANNOTATION_TYPES = {
    **{kind: ShapeAnnotation for kind in ShapeAnnotation.KINDS},
    **{kind: LineAnnotation for kind in LineAnnotation.KINDS},
    **{kind: MarkupAnnotation for kind in MarkupAnnotation.KINDS},
    "ink": InkAnnotation,
    "loupe": LoupeAnnotation,
    "redact": RedactAnnotation,
    "signature": SignatureAnnotation,
    "text": TextAnnotation,
    "note": NoteAnnotation,
}


def new_shape(kind, center, size, style):
    """Create a shape centered on a point, as Preview inserts shapes."""
    cx, cy = center
    half = size / 2

    if kind == "loupe":
        return LoupeAnnotation((cx, cy), size * 0.45, 2.0, style)

    if kind in LineAnnotation.KINDS:
        return LineAnnotation(
            kind, (cx - half, cy + half * 0.4), (cx + half, cy - half * 0.4),
            style,
        )

    height = size * (0.75 if kind == "bubble" else 1.0)
    width = size * (1.4 if kind in ("rect", "rounded", "oval", "bubble", "spotlight") else 1.0)
    return ShapeAnnotation(
        kind,
        (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2),
        style,
    )
