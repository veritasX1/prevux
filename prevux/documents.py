"""Documents: images and PDFs, with editable annotations and undo."""

import io
import json
import os
from pathlib import Path

import cairo
import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib
from PIL import Image, ImageOps

import pymupdf

from . import settings
from .model import (
    Annotation,
    LoupeAnnotation,
    RedactAnnotation,
    InkAnnotation,
    LineAnnotation,
    MarkupAnnotation,
    NoteAnnotation,
    ShapeAnnotation,
    Style,
    TextAnnotation,
)


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
}

PDF_EXTENSIONS = {".pdf"}
# Shown as pages (converted on opening; saving exports a PDF, the original stays untouched).
TEXT_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd", ".txt", ".text", ".html", ".htm"}
BOOK_EXTENSIONS = {".epub", ".mobi", ".fb2", ".cbz", ".xps", ".oxps", ".svg"}

PREVUX_KEY = "PrevuxData"

# Thumbnails dragged out of the sidebar become PDFs here first (the file manager copies them).
DRAG_FOLDER = Path(GLib.get_user_cache_dir()) / "prevux" / "drag"


def open_document(path):
    suffix = Path(path).suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return PDFDocument(path)
    if suffix in TEXT_EXTENSIONS:
        return ConvertedDocument(path, text_to_pdf(path))
    if suffix in BOOK_EXTENSIONS:
        return ConvertedDocument(path, book_to_pdf(path))
    if suffix in IMAGE_EXTENSIONS:
        return ImageDocument(path)

    # Unknown extension: try image first, then PDF.
    try:
        return ImageDocument(path)
    except Exception:
        return PDFDocument(path)


def pil_to_texture(image):
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    return Gdk.MemoryTexture.new(
        image.width,
        image.height,
        Gdk.MemoryFormat.R8G8B8A8,
        GLib.Bytes.new(image.tobytes()),
        image.width * 4,
    )


def pixmap_to_texture(pixmap):
    return Gdk.MemoryTexture.new(
        pixmap.width,
        pixmap.height,
        Gdk.MemoryFormat.R8G8B8,
        GLib.Bytes.new(bytes(pixmap.samples)),
        pixmap.stride,
    )


def pil_to_surface(image):
    image = image.convert("RGBA")
    data = bytearray(image.tobytes("raw", "BGRa"))
    surface = cairo.ImageSurface.create_for_data(
        data, cairo.FORMAT_ARGB32, image.width, image.height,
        image.width * 4,
    )
    return surface, data


def surface_to_pil(surface):
    surface.flush()
    return Image.frombuffer(
        "RGBA",
        (surface.get_width(), surface.get_height()),
        bytes(surface.get_data()),
        "raw",
        "BGRa",
        surface.get_stride(),
        1,
    )


def rotate_point_function(degrees, width, height):
    """Map a point when a page of the given size is rotated clockwise."""
    degrees %= 360
    if degrees == 90:
        return lambda x, y: (height - y, x)
    if degrees == 180:
        return lambda x, y: (width - x, height - y)
    if degrees == 270:
        return lambda x, y: (y, width - x)
    return lambda x, y: (x, y)


# ============================================================
# BASE
# ============================================================

class Snapshot:

    def __init__(self, annotations, data=None):
        self.annotations = annotations
        self.data = data


class BaseDocument:

    kind = ""

    def __init__(self, path):
        self.path = str(path)
        self.annotations = []
        self.undo_stack = []
        self.redo_stack = []
        self.modified = False
        self.version = 0

    @property
    def name(self):
        return Path(self.path).name

    @property
    def page_count(self):
        return len(self.annotations)

    def page_size(self, index):
        raise NotImplementedError

    # --- undo -------------------------------------------------

    def snapshot(self, structure=False):
        annotations = [
            [annotation.clone() for annotation in page]
            for page in self.annotations
        ]
        # Keep identities stable so selections survive undo.
        for old_page, new_page in zip(self.annotations, annotations):
            for old, new in zip(old_page, new_page):
                new.id = old.id
        return Snapshot(
            annotations,
            self.structure_data() if structure else None,
        )

    def checkpoint(self, structure=False):
        self.undo_stack.append(self.snapshot(structure))
        del self.undo_stack[:-100]
        self.redo_stack.clear()
        self.modified = True

    def discard_checkpoint(self):
        if self.undo_stack:
            self.undo_stack.pop()

    def can_undo(self):
        return bool(self.undo_stack)

    def can_redo(self):
        return bool(self.redo_stack)

    def undo(self):
        self._swap(self.undo_stack, self.redo_stack)

    def redo(self):
        self._swap(self.redo_stack, self.undo_stack)

    def _swap(self, source, target):
        if not source:
            return
        snapshot = source.pop()
        target.append(self.snapshot(snapshot.data is not None))
        if snapshot.data is not None:
            self.restore_structure(snapshot.data)
        self.annotations = snapshot.annotations
        self.modified = True
        self.changed()

    def changed(self):
        self.version += 1

    def structure_data(self):
        raise NotImplementedError

    def restore_structure(self, data):
        raise NotImplementedError

    # --- page operations --------------------------------------

    def rotate_page(self, index, degrees):
        width, height = self.page_size(index)
        function = rotate_point_function(degrees, width, height)
        for annotation in self.annotations[index]:
            annotation.reorient(function)

    def render_annotations(self, cr, index, source=None, source_scale=1.0):
        """Draw the markup of a page. `source` is an unmarked rendering of
        the page (cairo surface at `source_scale`) for loupes."""
        for annotation in self.annotations[index]:
            cr.save()
            if isinstance(annotation, LoupeAnnotation):
                annotation.draw(cr, source, source_scale)
            else:
                annotation.draw(cr)
            cr.restore()

    def info(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return {"path": self.path, "size": size}


# ============================================================
# IMAGE
# ============================================================

class ImageDocument(BaseDocument):

    kind = "image"

    def __init__(self, path):
        super().__init__(path)
        with Image.open(path) as image:
            self.format = image.format
            self.dpi = image.info.get("dpi")
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            self.image = image.copy()
        self.annotations = [[]]
        self._texture = None
        self.ocr_words = None        # Live Text, filled in the background

    # --- Live Text ----------------------------------------------

    def pages_needing_ocr(self):
        return [] if self.ocr_words is not None else [0]

    def ocr_page(self, index):
        from . import ocr
        return ocr.recognize(self.composited())

    def set_ocr(self, index, words):
        self.ocr_words = words

    def page_words(self, index=0):
        return self.ocr_words or []

    def search(self, text):
        from . import ocr
        return [(0, rect) for rect in ocr.search_words(self.page_words(0), text)]

    def page_size(self, index=0):
        return self.image.size

    def texture(self, index=0, scale=None):
        if self._texture is None:
            self._texture = pil_to_texture(self.image)
        return self._texture, 1.0

    def thumbnail(self, index, max_size):
        image = self.composited()
        image.thumbnail((max_size, max_size))
        return pil_to_texture(image)

    def changed(self):
        super().changed()
        self._texture = None
        self.ocr_words = None        # the picture changed: recognise again

    def default_text_size(self):
        return max(14.0, min(self.image.size) / 22)

    def default_line_width(self):
        return max(2.0, min(self.image.size) / 250)

    def structure_data(self):
        return (self.image.copy(), self.dpi)

    def restore_structure(self, data):
        self.image, self.dpi = data

    def resolution(self):
        """Pixels per inch stored in the file (72 if it says nothing, as Preview assumes)."""
        dpi = self.dpi
        if isinstance(dpi, (tuple, list)):
            dpi = dpi[0]
        try:
            return float(dpi) if dpi and float(dpi) > 1 else 72.0
        except (TypeError, ValueError):
            return 72.0

    def set_resolution(self, dpi):
        """Change only the print size: the pixels stay as they are (Resample off)."""
        self.dpi = (round(dpi, 2), round(dpi, 2))
        self.changed()

    # --- editing ----------------------------------------------

    def rotate_page(self, index, degrees):
        super().rotate_page(index, degrees)
        self.image = self.image.rotate(-degrees, expand=True)
        self.changed()

    def flip(self, horizontal):
        width, height = self.image.size
        if horizontal:
            self.image = ImageOps.mirror(self.image)
            function = lambda x, y: (width - x, y)
        else:
            self.image = ImageOps.flip(self.image)
            function = lambda x, y: (x, height - y)
        for annotation in self.annotations[0]:
            annotation.reorient(function)
        self.changed()

    def remove_background(self):
        from .cutout import remove_background
        self.image = remove_background(self.image)
        self.changed()

    def crop_to_cutout(self, box, cut):
        """Smart Lasso → Crop: keep only the cut-out object, transparent around it."""
        self.image = cut.copy()
        for annotation in self.annotations[0]:
            annotation.move(-box[0], -box[1])
        self.changed()

    def erase_cutout(self, box, cut):
        """Smart Lasso → Delete: the object becomes transparent."""
        from PIL import ImageChops
        image = self.image.convert("RGBA")
        hole = Image.new("L", image.size, 0)
        hole.paste(cut.getchannel("A"), box[:2])
        image.putalpha(ImageChops.subtract(image.getchannel("A"), hole))
        self.image = image
        self.changed()

    def needs_alpha_format(self):
        """True if the image now has transparency but its file type cannot keep it."""
        from .cutout import has_transparency
        return has_transparency(self.image) and Path(self.path).suffix.lower() not in (".png", ".webp", ".tif", ".tiff", ".gif")

    def crop(self, index, rect):
        x0, y0, x1, y1 = (int(round(value)) for value in rect)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.image.width, x1), min(self.image.height, y1)
        if x1 - x0 < 1 or y1 - y0 < 1:
            return
        self.image = self.image.crop((x0, y0, x1, y1))
        for annotation in self.annotations[0]:
            annotation.move(-x0, -y0)
        self.changed()

    def resize(self, width, height):
        old_width, old_height = self.image.size
        self.image = self.image.resize(
            (width, height), Image.Resampling.LANCZOS
        )
        sx = width / old_width
        sy = height / old_height
        for annotation in self.annotations[0]:
            annotation.transform(lambda x, y: (x * sx, y * sy))
            if hasattr(annotation, "size"):
                annotation.size *= (sx + sy) / 2
            annotation.style.width *= (sx + sy) / 2
            annotation.normalize()
        self.changed()

    # --- output -----------------------------------------------

    def composited(self, index=0):
        """The image with all markup burned in."""
        if not self.annotations[0]:
            return self.image.copy()
        surface, _data = pil_to_surface(self.image)
        source, _source_data = pil_to_surface(self.image)
        cr = cairo.Context(surface)
        self.render_annotations(cr, 0, source)
        return surface_to_pil(surface)

    def save(self, path=None, format_name=None):
        path = str(path or self.path)
        image = self.composited()
        suffix = Path(path).suffix.lower()

        if suffix == ".pdf" or format_name == "PDF":
            image.convert("RGB").save(path, "PDF", resolution=72)
            return

        options = {}
        if suffix in (".jpg", ".jpeg"):
            image = image.convert("RGB")
            options["quality"] = 92
        if self.dpi:
            options["dpi"] = self.dpi

        temp = path + ".prevux-tmp"
        image.save(temp, format_name or Image.registered_extensions().get(suffix), **options)
        os.replace(temp, path)

        if path == self.path or format_name is None:
            self.path = path
            self.modified = False

    def info(self):
        info = super().info()
        info["type"] = self.format or "Image"
        info["dimensions"] = f"{self.image.width} × {self.image.height}"
        return info


# ============================================================
# PDF
# ============================================================

class PDFDocument(BaseDocument):

    kind = "pdf"

    def __init__(self, path, password=None, data=None):
        super().__init__(path)
        self.doc = pymupdf.open("pdf", data) if data is not None else pymupdf.open(path)
        if self.doc.needs_pass and not self.doc.authenticate(password or ""):
            raise PermissionError("password")
        self.annotations = [self.import_annotations(page) for page in self.doc]
        self.cache = {}
        self.words = {}
        self.fields = {}

    def page_size(self, index):
        rect = self.doc[index].rect
        return (rect.width, rect.height)

    def _short_side(self):
        """Shorter edge of the first page in points (A4: 595)."""
        try:
            return min(self.page_size(0)) if len(self.doc) else 595.0
        except Exception:
            return 595.0

    def default_text_size(self):
        # A4 and smaller: 14 pt; larger formats (A3, posters, scans) grow along.
        return max(14.0, self._short_side() / 42)

    def default_line_width(self):
        return max(2.0, self._short_side() / 300)

    # --- rendering --------------------------------------------

    def texture(self, index, scale):
        """Return (texture, rendered_scale) from the cache, or (None, 0)."""
        entry = self.cache.get(index)
        if entry is None:
            return None, 0
        return entry

    def render(self, index, scale):
        width, height = self.page_size(index)
        # Keep textures within sensible GPU limits.
        scale = min(scale, 8000 / max(width, height))
        pixmap = self.doc[index].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True,
        )
        texture = pixmap_to_texture(pixmap)
        self.cache[index] = (texture, scale)
        return texture

    def evict(self, keep):
        for index in list(self.cache):
            if index not in keep:
                del self.cache[index]

    def thumbnail(self, index, max_size):
        width, height = self.page_size(index)
        scale = max_size / max(width, height)
        pixmap = self.doc[index].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False,
        )
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        if self.annotations[index]:
            surface, _data = pil_to_surface(image)
            source, _source_data = pil_to_surface(image)
            cr = cairo.Context(surface)
            cr.scale(scale, scale)
            self.render_annotations(cr, index, source, scale)
            image = surface_to_pil(surface)
        return pil_to_texture(image)

    def changed(self):
        super().changed()
        self.cache.clear()
        self.words.clear()
        self.fields.clear()

    # --- text -------------------------------------------------

    # --- Live Text: scanned pages without a text layer -------------
    OCR_PAGE_LIMIT = 60
    OCR_DPI = 200

    def pages_needing_ocr(self):
        ocr_pages = getattr(self, "ocr_pages", {})
        pages = []
        for index in range(min(self.page_count, self.OCR_PAGE_LIMIT)):
            if index not in ocr_pages and not self.doc[index].get_text("text").strip():
                pages.append(index)
        return pages

    def ocr_page(self, index):
        from . import ocr
        page = self.doc[index]
        pixmap = page.get_pixmap(dpi=self.OCR_DPI, annots=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples) if pixmap.n == 3 else \
            Image.frombytes("RGBA", (pixmap.width, pixmap.height), pixmap.samples).convert("RGB")
        return ocr.recognize(image, self.OCR_DPI / 72)

    def set_ocr(self, index, words):
        if not hasattr(self, "ocr_pages"):
            self.ocr_pages = {}
        self.ocr_pages[index] = words
        self.words.pop(index, None)

    def page_words(self, index):
        """Words in display coordinates, in reading order."""
        ocr_pages = getattr(self, "ocr_pages", {})
        if index in ocr_pages:
            return ocr_pages[index]
        if index not in self.words:
            page = self.doc[index]
            matrix = page.rotation_matrix
            words = []
            for x0, y0, x1, y1, text, block, line, _number in page.get_text("words", sort=True):
                rect = pymupdf.Rect(x0, y0, x1, y1) * matrix
                words.append((rect.x0, rect.y0, rect.x1, rect.y1, text, block, line))
            self.words[index] = words
        return self.words[index]

    # --- forms ------------------------------------------------

    @property
    def has_forms(self):
        return bool(self.doc.is_form_pdf)

    def form_fields(self, index):
        """Fillable fields of a page, with rectangles in display space."""
        if index not in self.fields:
            page = self.doc[index]
            matrix = page.rotation_matrix
            fields = []
            for widget in page.widgets() or []:
                kind = widget.field_type
                if kind not in (
                    pymupdf.PDF_WIDGET_TYPE_TEXT,
                    pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
                    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
                    pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
                    pymupdf.PDF_WIDGET_TYPE_LISTBOX,
                ):
                    continue
                rect = pymupdf.Rect(widget.rect) * matrix
                rect.normalize()
                flags = widget.field_flags or 0
                fields.append({
                    "xref": widget.xref,
                    "kind": {
                        pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
                        pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
                        pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
                        pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "choice",
                        pymupdf.PDF_WIDGET_TYPE_LISTBOX: "choice",
                    }[kind],
                    "name": widget.field_name or "",
                    "rect": (rect.x0, rect.y0, rect.x1, rect.y1),
                    "value": widget.field_value,
                    "checked": widget.field_value not in (None, "", "Off", False),
                    "choices": list(widget.choice_values or []),
                    "multiline": bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE),
                    "readonly": bool(flags & pymupdf.PDF_FIELD_IS_READ_ONLY),
                    "font_size": widget.text_fontsize or 0,
                    "max_length": widget.text_maxlen or 0,
                })
            self.fields[index] = fields
        return self.fields[index]

    def set_field(self, index, xref, value):
        page = self.doc[index]
        widget = page.load_widget(xref)
        if widget.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            # Only one button of a group may be on.
            for other in page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON]):
                if other.field_name == widget.field_name and other.xref != xref:
                    other.field_value = False
                    other.update()
            widget = page.load_widget(xref)
            widget.field_value = True
        elif widget.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            widget.field_value = widget.on_state() if value else "Off"
        else:
            widget.field_value = value
        widget.update()
        self.cache.pop(index, None)
        self.fields.pop(index, None)

    def outline(self):
        """Table of contents as (level, title, page, y) in display space."""
        result = []
        for entry in self.doc.get_toc(simple=False):
            level, title, number = entry[:3]
            page = number - 1
            if not 0 <= page < self.page_count:
                continue
            y = 0.0
            destination = entry[3] if len(entry) > 3 else {}
            point = destination.get("to") if isinstance(destination, dict) else None
            if point is not None:
                page_object = self.doc[page]
                y = max(0.0, (pymupdf.Point(point) * page_object.rotation_matrix).y)
            result.append((level, title, page, y))
        return result

    def search(self, text):
        from . import ocr
        results = []
        ocr_pages = getattr(self, "ocr_pages", {})
        for index, page in enumerate(self.doc):
            if index in ocr_pages:
                results += [(index, rect) for rect in ocr.search_words(ocr_pages[index], text)]
                continue
            for quad in page.search_for(text):
                rect = quad * page.rotation_matrix
                results.append((index, (rect.x0, rect.y0, rect.x1, rect.y1)))
        return results

    def snippet(self, index, rect, limit=90):
        """The text line around a search hit, for the results list."""
        x0, y0, x1, y1 = rect
        middle = (y0 + y1) / 2
        words = self.page_words(index)
        line = None
        for wx0, wy0, wx1, wy1, _text, block, number in words:
            if wy0 <= middle <= wy1 and wx1 >= x0 - 1 and wx0 <= x1 + 1:
                line = (block, number)
                break
        if line is None:
            return ""
        text = " ".join(word[4] for word in words if (word[5], word[6]) == line)
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    # --- structure --------------------------------------------

    def structure_data(self):
        return self.doc.tobytes()

    def restore_structure(self, data):
        self.doc = pymupdf.open("pdf", data)
        self.changed()

    def rotate_page(self, index, degrees):
        super().rotate_page(index, degrees)
        page = self.doc[index]
        page.set_rotation((page.rotation + degrees) % 360)
        self.changed()

    def delete_pages(self, indices):
        for index in sorted(indices, reverse=True):
            self.doc.delete_page(index)
            del self.annotations[index]
        self.changed()

    def move_page(self, source, target):
        """Move page `source` so that it ends up at position `target`."""
        if source == target:
            return
        # PyMuPDF inserts before the given page number.
        self.doc.move_page(source, target + 1 if target > source else target)
        annotations = self.annotations.pop(source)
        self.annotations.insert(target, annotations)
        self.changed()

    def insert_page_from(self, source, page, index):
        """Copy a page of another document (PDF or image) to `index`."""
        annotations = [annotation.clone() for annotation in source.annotations[page]]
        if source.kind == "pdf":
            self.doc.insert_pdf(source.doc, from_page=page, to_page=page, start_at=index)
        else:
            # Images become pages at 96 dpi, the usual screen resolution.
            scale = 72 / 96
            width, height = source.page_size(0)
            new = self.doc.new_page(pno=index, width=width * scale, height=height * scale)
            buffer = io.BytesIO()
            source.image.save(buffer, "PNG")
            new.insert_image(new.rect, stream=buffer.getvalue())
            for annotation in annotations:
                annotation.transform(lambda x, y: (x * scale, y * scale))
                annotation.style.width *= scale
                if isinstance(annotation, TextAnnotation):
                    annotation.size *= scale
                annotation.normalize()
        self.annotations.insert(index, annotations)
        self.changed()

    # --- permissions (Preview: File → Edit Permissions) ----------------
    # protection: None = keep as it is; {} = remove all protection;
    # {"open": "...", "owner": "...", "allow": ["print", "copy", ...]} = protect.
    PERMISSIONS = {
        "print": pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ,
        "copy": pymupdf.PDF_PERM_COPY | pymupdf.PDF_PERM_ACCESSIBILITY,
        "assemble": pymupdf.PDF_PERM_ASSEMBLE,
        "annotate": pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_MODIFY,
        "forms": pymupdf.PDF_PERM_FORM,
    }

    def encryption_options(self):
        protection = getattr(self, "protection", None)
        if protection is None:
            return {}
        if not protection:
            return {"encryption": pymupdf.PDF_ENCRYPT_NONE}
        permissions = pymupdf.PDF_PERM_ACCESSIBILITY
        for name in protection.get("allow", []):
            permissions |= self.PERMISSIONS.get(name, 0)
        return {
            "encryption": pymupdf.PDF_ENCRYPT_AES_256,
            "owner_pw": protection.get("owner") or protection.get("open") or "",
            "user_pw": protection.get("open") or "",
            "permissions": permissions,
        }

    def current_permissions(self):
        """What the file allows now (for the dialog)."""
        allowed = []
        bits = self.doc.permissions
        for name, flag in self.PERMISSIONS.items():
            if bits & flag:
                allowed.append(name)
        return allowed

    def pages_to_pdf(self, pages, path):
        """Write some pages – with their markup – into a new PDF (dragging thumbnails out)."""
        added = self.export_annotations()
        try:
            target = pymupdf.open()
            for page in sorted(pages):
                target.insert_pdf(self.doc, from_page=page, to_page=page)
            target.save(str(path), garbage=1, deflate=True)
            target.close()
        finally:
            self.remove_exported(added)

    def insert_blank_page(self, index):
        width, height = self.page_size(max(0, min(index, self.page_count - 1)))
        self.doc.new_page(pno=index, width=width, height=height)
        self.annotations.insert(index, [])
        self.changed()

    def crop(self, index, rect):
        page = self.doc[index]
        x0, y0, x1, y1 = rect
        cropped = (pymupdf.Rect(x0, y0, x1, y1) * page.derotation_matrix)
        cropped.normalize()
        # The crop box is relative to the media box origin.
        origin = page.cropbox.tl
        cropped = cropped + (origin.x, origin.y, origin.x, origin.y)
        page.set_cropbox(cropped & page.mediabox)
        for annotation in self.annotations[index]:
            annotation.move(-x0, -y0)
        self.changed()

    # --- annotations <-> PDF ----------------------------------

    def import_annotations(self, page):
        """Take over supported annotations so they become editable."""
        result = []
        matrix = page.rotation_matrix
        taken = []

        for annot in page.annots():
            annotation = None
            try:
                kind, stored = self.doc.xref_get_key(annot.xref, PREVUX_KEY)
                if kind == "string":
                    annotation = Annotation.from_dict(json.loads(stored))
                else:
                    annotation = foreign_annotation(annot, matrix)
            except Exception as error:
                print("Prevux: could not import annotation:", error)
                annotation = None

            if annotation is not None:
                result.append(annotation)
                taken.append(annot.xref)

        for xref in taken:
            for annot in page.annots():
                if annot.xref == xref:
                    page.delete_annot(annot)
                    break

        return result

    def export_annotations(self):
        added = []
        for index, page in enumerate(self.doc):
            matrix = page.derotation_matrix
            for annotation in self.annotations[index]:
                try:
                    annot = export_annotation(page, annotation, matrix)
                except Exception as error:
                    print("Prevux: could not export annotation:", error)
                    continue
                if annot is None:
                    continue
                author = settings.get("author")
                if author:
                    # Shown as the author of notes and markup in other PDF readers too.
                    info = annot.info
                    info["title"] = author
                    annot.set_info(info)
                self.doc.xref_set_key(
                    annot.xref, PREVUX_KEY,
                    pymupdf.get_pdf_str(json.dumps(annotation.to_dict())),
                )
                added.append((index, annot.xref))
        return added

    def remove_exported(self, added):
        for index, xref in added:
            page = self.doc[index]
            for annot in page.annots():
                if annot.xref == xref:
                    page.delete_annot(annot)
                    break

    def apply_redactions(self):
        """Remove everything under redaction boxes for good."""
        applied = False
        for index, page in enumerate(self.doc):
            redactions = [
                annotation for annotation in self.annotations[index]
                if isinstance(annotation, RedactAnnotation)
            ]
            if not redactions:
                continue
            matrix = page.derotation_matrix
            for annotation in redactions:
                for rect in annotation.rects:
                    area = pymupdf.Rect(*rect) * matrix
                    area.normalize()
                    page.add_redact_annot(area, fill=(0, 0, 0))
                self.annotations[index].remove(annotation)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
            applied = True
        if applied:
            self.changed()
        return applied

    def save(self, path=None, format_name=None, flatten=False):
        path = str(path or self.path)
        self.apply_redactions()
        added = self.export_annotations()
        try:
            if format_name and format_name != "PDF":
                self.save_page_image(path, format_name)
                return
            temp = path + ".prevux-tmp"
            if flatten:
                data = self.doc.tobytes()
                flat = pymupdf.open("pdf", data)
                flat.bake(annots=True, widgets=False)
                flat.save(temp, garbage=3, deflate=True)
                flat.close()
            else:
                self.doc.save(temp, garbage=1, deflate=True, **self.encryption_options())
            os.replace(temp, path)
        finally:
            self.remove_exported(added)

        if not flatten:
            self.path = path
            self.modified = False

    def save_page_image(self, path, format_name, index=0, dpi=150):
        self.apply_redactions()
        pixmap = self.doc[index].get_pixmap(dpi=dpi, annots=True)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        image.save(path, format_name)

    def info(self):
        info = super().info()
        metadata = self.doc.metadata or {}
        info["type"] = "PDF"
        info["pages"] = self.page_count
        width, height = self.page_size(0)
        info["dimensions"] = f"{width / 72 * 25.4:.0f} × {height / 72 * 25.4:.0f} mm"
        info["title"] = metadata.get("title") or ""
        info["author"] = metadata.get("author") or ""
        return info


# ============================================================
# PDF CONVERSION
# ============================================================

def color_to_pdf(color):
    return tuple(color[:3]) if color else None


def color_from_pdf(color, fallback=None):
    if not color:
        return fallback
    if len(color) == 1:
        return (color[0], color[0], color[0], 1.0)
    if len(color) == 4:
        c, m, y, k = color
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k), 1.0)
    return (*color[:3], 1.0)


def dashes_for(style):
    if style.dash == "dashed":
        return [style.width * 3, style.width * 2]
    if style.dash == "dotted":
        return [style.width, style.width * 2]
    return None


def apply_style(annot, style, fill=True):
    annot.set_colors(
        stroke=color_to_pdf(style.stroke),
        fill=color_to_pdf(style.fill) if fill else None,
    )
    border = {"width": style.width if style.stroke else 0}
    dashes = dashes_for(style)
    if dashes:
        border["dashes"] = dashes
    annot.set_border(border)


FONT_MAP = {
    "serif": "TiRo",
    "mono": "Cour",
    "courier": "Cour",
    "times": "TiRo",
}


def pdf_font(family):
    lowered = family.lower()
    for key, name in FONT_MAP.items():
        if key in lowered:
            return name
    return "Helv"


def export_annotation(page, annotation, matrix):
    def point(x, y):
        return pymupdf.Point(x, y) * matrix

    def rect(x0, y0, x1, y1):
        result = pymupdf.Rect(x0, y0, x1, y1) * matrix
        result.normalize()
        return result

    if isinstance(annotation, LoupeAnnotation) or (
        isinstance(annotation, ShapeAnnotation) and annotation.kind == "spotlight"
    ):
        return export_stamp(page, annotation, matrix)

    if isinstance(annotation, TextAnnotation):
        if not annotation.text.strip():
            return None
        annot = page.add_freetext_annot(
            rect(*annotation.bounds()),
            annotation.text,
            fontsize=annotation.size,
            fontname=pdf_font(annotation.family),
            text_color=color_to_pdf(annotation.color),
            fill_color=color_to_pdf(annotation.style.fill),
            border_color=color_to_pdf(annotation.style.stroke),
            border_width=annotation.style.width if annotation.style.stroke else 0,
            align={"left": 0, "center": 1, "right": 2}[annotation.align],
            rotate=page.rotation,
        )
        annot.update()
        return annot

    if isinstance(annotation, NoteAnnotation):
        annot = page.add_text_annot(point(annotation.x, annotation.y), annotation.text)
        annot.set_colors(stroke=color_to_pdf(annotation.color))
        annot.update()
        return annot

    if isinstance(annotation, MarkupAnnotation):
        quads = [rect(*item).quad for item in annotation.rects]
        add = {
            "highlight": page.add_highlight_annot,
            "underline": page.add_underline_annot,
            "strike": page.add_strikeout_annot,
        }[annotation.kind]
        annot = add(quads=quads)
        annot.set_colors(stroke=color_to_pdf(annotation.color))
        annot.update()
        return annot

    if isinstance(annotation, InkAnnotation):
        strokes = [
            [tuple(point(x, y)) for x, y in stroke]
            for stroke in annotation.strokes
            if stroke
        ]
        annot = page.add_ink_annot(strokes)
        apply_style(annot, annotation.style, fill=False)
        annot.update()
        return annot

    if isinstance(annotation, LineAnnotation):
        annot = page.add_line_annot(point(*annotation.start), point(*annotation.end))
        if annotation.kind == "arrow":
            annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
        apply_style(annot, annotation.style)
        if annotation.kind == "arrow":
            annot.set_colors(
                stroke=color_to_pdf(annotation.style.stroke),
                fill=color_to_pdf(annotation.style.stroke),
            )
        annot.update()
        return annot

    if isinstance(annotation, ShapeAnnotation):
        if annotation.kind in ("rect", "rounded"):
            annot = page.add_rect_annot(rect(*annotation.bounds()))
        elif annotation.kind == "oval":
            annot = page.add_circle_annot(rect(*annotation.bounds()))
        else:
            points = flatten_path(annotation)
            annot = page.add_polygon_annot([point(x, y) for x, y in points])
        apply_style(annot, annotation.style)
        annot.update()
        return annot

    return None


def export_stamp(page, annotation, matrix):
    """Loupe and spotlight have no PDF equivalent: store their look as an
    image stamp (shown by every viewer) that Prevux can edit again."""
    width, height = page.rect.width, page.rect.height
    if isinstance(annotation, LoupeAnnotation):
        x0, y0, x1, y1 = annotation.bounds()
        pad = annotation.style.width * 2
        area = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    else:
        area = (0, 0, width, height)

    scale = min(3.0, 3000 / max(area[2] - area[0], area[3] - area[1]))
    pixel_width = max(1, int((area[2] - area[0]) * scale))
    pixel_height = max(1, int((area[3] - area[1]) * scale))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixel_width, pixel_height)
    cr = cairo.Context(surface)
    cr.scale(scale, scale)
    cr.translate(-area[0], -area[1])
    cr.rectangle(*area[:2], area[2] - area[0], area[3] - area[1])
    cr.clip()

    if isinstance(annotation, LoupeAnnotation):
        # Render the page (without other markup) as the magnified source.
        source_scale = scale * annotation.magnification
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(source_scale, source_scale), alpha=False, annots=False,
        )
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        source, _data = pil_to_surface(image)
        annotation.draw(cr, source, source_scale)
    else:
        annotation.draw(cr)

    png = surface_to_pil(surface)
    if page.rotation:
        # The stamp lives in unrotated page space.
        png = png.rotate(page.rotation, expand=True)
    buffer = io.BytesIO()
    png.save(buffer, "PNG")

    rect = pymupdf.Rect(*area) * matrix
    rect.normalize()
    # PyMuPDF adjusts image stamps to the page rotation on its own, and not
    # predictably; place the stamp on the unrotated page instead.
    rotation = page.rotation
    page.set_rotation(0)
    try:
        return page.add_stamp_annot(rect, stamp=buffer.getvalue())
    finally:
        page.set_rotation(rotation)


def flatten_path(annotation):
    surface = cairo.RecordingSurface(cairo.CONTENT_ALPHA, None)
    cr = cairo.Context(surface)
    annotation.path(cr)
    cr.set_tolerance(0.5)
    points = []
    for kind, values in cr.copy_path_flat():
        if kind in (cairo.PATH_MOVE_TO, cairo.PATH_LINE_TO):
            points.append(values)
    return points


def foreign_annotation(annot, matrix):
    """Convert annotations made by other programs where it is lossless enough."""
    kind = annot.type[1]
    colors = annot.colors or {}
    stroke = color_from_pdf(colors.get("stroke"))
    fill = color_from_pdf(colors.get("fill"))
    width = (annot.border or {}).get("width") or 1.0
    if width < 0:
        width = 1.0

    def point(x, y):
        return tuple(pymupdf.Point(x, y) * matrix)

    def rect(value):
        result = pymupdf.Rect(value) * matrix
        result.normalize()
        return (result.x0, result.y0, result.x1, result.y1)

    style = Style(stroke=stroke, fill=fill, width=width)

    if kind == "Square":
        return ShapeAnnotation("rect", rect(annot.rect), style)

    if kind == "Circle":
        return ShapeAnnotation("oval", rect(annot.rect), style)

    if kind == "Line":
        vertices = annot.vertices
        if not vertices or len(vertices) < 2:
            return None
        ends = annot.line_ends or (0, 0)
        arrow = ends[1] in (pymupdf.PDF_ANNOT_LE_OPEN_ARROW, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
        return LineAnnotation(
            "arrow" if arrow else "line",
            point(*vertices[0]), point(*vertices[1]), style,
        )

    if kind == "Ink":
        strokes = [[point(*item) for item in stroke] for stroke in annot.vertices or []]
        return InkAnnotation(strokes, style)

    if kind in ("Highlight", "Underline", "StrikeOut"):
        vertices = annot.vertices or []
        rects = []
        for start in range(0, len(vertices) - 3, 4):
            quad = pymupdf.Quad(vertices[start:start + 4])
            rects.append(rect(quad.rect))
        if not rects:
            return None
        return MarkupAnnotation(
            {"Highlight": "highlight", "Underline": "underline", "StrikeOut": "strike"}[kind],
            rects,
            stroke or (1.0, 0.87, 0.2, 1.0),
            annot.info.get("content", ""),
        )

    if kind == "Text":
        x0, y0, _x1, _y1 = rect(annot.rect)
        return NoteAnnotation((x0, y0), annot.info.get("content", ""), stroke)

    if kind == "FreeText":
        info = annot.info
        text = info.get("content", "")
        bounds = rect(annot.rect)
        annotation = TextAnnotation(bounds, text, style=Style(stroke=None, fill=fill, width=1))
        annotation.auto_width = False
        # The default appearance string looks like "/Helv 12 Tf 0 0 0 rg".
        _kind, appearance = annot.parent.parent.xref_get_key(annot.xref, "DA")
        tokens = appearance.strip("()").split()
        for position, token in enumerate(tokens):
            if token == "Tf" and position >= 1:
                annotation.size = float(tokens[position - 1])
            if token == "rg" and position >= 3:
                annotation.color = (*map(float, tokens[position - 3:position]), 1.0)
        annotation.y1 = bounds[3]
        return annotation

    return None



# --- other formats shown as pages --------------------------------------

def duplicate(doc, name):
    """An untitled copy with all current changes (Preview: File → Duplicate)."""
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix="prevux-"))
    if doc.kind == "image":
        path = folder / (name + Path(doc.path).suffix.lower())
        image = doc.image
        if path.suffix in (".jpg", ".jpeg"):
            image = image.convert("RGB")
        image.save(path, **({"dpi": doc.dpi} if doc.dpi else {}))
        copy = ImageDocument(path)
        copy.dpi = doc.dpi
        copy.annotations = [[annotation.clone() for annotation in doc.annotations[0]]]
    else:
        path = folder / (name + ".pdf")
        added = doc.export_annotations()
        try:
            data = doc.doc.tobytes(garbage=1, deflate=True)
        finally:
            doc.remove_exported(added)
        path.write_bytes(data)
        copy = PDFDocument(path)
    copy.untitled = True
    copy.modified = True          # closing asks where to keep it
    return copy


class ConvertedDocument(PDFDocument):
    """Markdown, text, HTML, e-books …: laid out as PDF pages for viewing and marking up.
    Saving never writes into the original file – it is exported as a PDF."""

    converted = True

    def __init__(self, path, data):
        super().__init__(path, data=data)


PAGE_CSS = """
* { font-family: sans-serif; }
body { font-size: 11pt; line-height: 1.45; color: #1d1d1f; }
h1 { font-size: 22pt; margin: 0 0 10pt 0; }
h2 { font-size: 16pt; margin: 16pt 0 6pt 0; }
h3 { font-size: 13pt; margin: 12pt 0 4pt 0; }
h4, h5, h6 { font-size: 11pt; margin: 10pt 0 4pt 0; }
p { margin: 0 0 7pt 0; }
ul, ol { margin: 0 0 7pt 0; }
li { margin: 0 0 2pt 0; }
code { font-family: monospace; font-size: 9.5pt; background-color: #f2f2f6; }
pre { font-family: monospace; font-size: 9.5pt; background-color: #f2f2f6; padding: 6pt; white-space: pre-wrap; }
blockquote { color: #6e6e73; margin: 0 0 7pt 0; padding-left: 10pt; border-left: 2pt solid #d2d2d7; }
table { border-collapse: collapse; margin: 0 0 8pt 0; }
th, td { border: 0.6pt solid #c7c7cc; padding: 3pt 6pt; }
th { background-color: #f2f2f6; }
a { color: #0066cc; }
hr { border: 0; border-top: 0.6pt solid #c7c7cc; }
"""


def text_to_pdf(path, paper="a4"):
    """Markdown, plain text or HTML as A4 pages (PDF bytes)."""
    import html
    path = Path(path)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        body = text
    elif suffix in (".txt", ".text"):
        body = "<pre style='background-color: transparent; padding: 0; font-size: 10pt'>" + html.escape(text) + "</pre>"
    else:
        # CommonMark (as on GitHub) with tables and strikethrough.
        from markdown_it import MarkdownIt
        body = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"]).render(text)
    story = pymupdf.Story(html=body, user_css=PAGE_CSS, archive=pymupdf.Archive(str(path.parent)))
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.paper_rect(paper)
    where = mediabox + (56, 56, -56, -56)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _filled = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buffer.getvalue()


def book_to_pdf(path):
    """E-books, comics, XPS and SVG through MuPDF, as PDF bytes."""
    source = pymupdf.open(str(path))
    try:
        return source.convert_to_pdf()
    finally:
        source.close()


# --- export filters (Preview: Export → Quartz Filter) ------------------

FILTERS = {
    # key: (label, for PDFs, for images, file name suffix)
    "reduce": ("Reduce File Size", True, True, "small"),
    "gray": ("Grayscale", True, True, "grayscale"),
    "bw": ("Black & White", False, True, "black and white"),
    "sepia": ("Sepia", False, True, "sepia"),
}


def export_filtered(doc, path, key):
    """Write a filtered copy of the document; the document itself stays as it is."""
    if doc.kind == "pdf":
        added = doc.export_annotations()
        try:
            copy = pymupdf.open("pdf", doc.doc.tobytes())
        finally:
            doc.remove_exported(added)
        if key == "reduce":
            copy.rewrite_images(dpi_threshold=150, dpi_target=120, quality=60)
        elif key == "gray":
            copy.recolor(1)
            # Recolouring stores images uncompressed: compress them again, same resolution.
            copy.rewrite_images(quality=85, set_to_gray=True)
        copy.save(str(path), garbage=4, deflate=True, clean=True)
        copy.close()
        return
    image = doc.composited().convert("RGB")
    if key == "reduce":
        image.thumbnail((2000, 2000))
        image.save(str(path), "JPEG", quality=70, optimize=True)
        return
    if key == "gray":
        image = ImageOps.grayscale(image)
    elif key == "bw":
        image = ImageOps.grayscale(image).point(lambda v: 255 if v > 140 else 0).convert("1")
    elif key == "sepia":
        image = ImageOps.colorize(ImageOps.grayscale(image), "#2e1f0f", "#f3e3c3", mid="#a07850")
    suffix = Path(path).suffix.lower()
    image.save(str(path), "JPEG" if suffix in (".jpg", ".jpeg") else (Image.registered_extensions().get(suffix) or "PNG"))


# Images reuse the PDF's line snippet for search results (it only needs page_words).
ImageDocument.snippet = PDFDocument.snippet
