"""Thumbnail sidebar with page management (reorder, rotate, delete)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk

from .i18n import _


THUMBNAIL_SIZE = 120


class PageRow(Gtk.ListBoxRow):

    def __init__(self, doc_index, page):
        super().__init__()
        self.doc_index = doc_index
        self.page = page

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_halign(Gtk.Align.CENTER)

        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.add_css_class("thumbnail")
        self.frame = Gtk.Box(halign=Gtk.Align.CENTER)
        self.frame.add_css_class("thumbnail-frame")
        self.frame.append(self.picture)
        box.append(self.frame)

        self.label = Gtk.Label(label=str(page + 1), halign=Gtk.Align.CENTER)
        self.label.add_css_class("caption")
        self.label.add_css_class("thumbnail-label")
        box.append(self.label)
        self.set_child(box)

    def set_size(self, width, height):
        scale = THUMBNAIL_SIZE / max(width, height)
        self.picture.set_size_request(int(width * scale), int(height * scale))

    def set_texture(self, texture):
        self.picture.set_paintable(texture)


class Sidebar(Gtk.Box):

    __gsignals__ = {
        "page-activated": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        "move-page": (GObject.SignalFlags.RUN_FIRST, None, (int, int, int)),
        "delete-pages": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("thumbnail-sidebar")
        self.documents = []
        self.rows = {}
        self.pending = []
        self.render_source = None
        self.updating = False

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.list.set_activate_on_single_click(True)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self.on_activated)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.list.add_controller(keys)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self.on_context_menu)
        self.list.add_controller(click)

        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.list)
        self.append(self.scroller)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Rotate Left"), "win.rotate-left")
        section.append(_("Rotate Right"), "win.rotate-right")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Insert Blank Page"), "win.insert-blank-page")
        section.append(_("Delete"), "win.delete-pages")
        menu.append_section(None, section)
        self.context_menu = Gtk.PopoverMenu.new_from_model(menu)
        self.context_menu.set_has_arrow(False)
        self.context_menu.set_parent(self)

    # ========================================================
    # CONTENT
    # ========================================================

    def set_documents(self, documents):
        self.documents = list(documents)
        self.rebuild()

    def rebuild(self):
        self.updating = True
        self.list.remove_all()
        self.rows = {}
        self.pending = []

        for doc_index, doc in enumerate(self.documents):
            if len(self.documents) > 1:
                header = Gtk.ListBoxRow(selectable=False, activatable=False)
                label = Gtk.Label(label=doc.name, xalign=0, ellipsize=3)
                label.add_css_class("heading")
                label.set_margin_top(10)
                label.set_margin_start(6)
                header.set_child(label)
                self.list.append(header)

            for page in range(doc.page_count):
                row = PageRow(doc_index, page)
                row.set_size(*doc.page_size(page))
                if doc.kind == "pdf":
                    self.setup_drag(row)
                self.list.append(row)
                self.rows[(doc_index, page)] = row
                self.pending.append((doc_index, page))

        self.updating = False
        self.start_rendering()

    def refresh_document(self, doc_index, pages=None):
        doc = self.documents[doc_index]
        if pages is None:
            pages = range(doc.page_count)
        for page in pages:
            row = self.rows.get((doc_index, page))
            if row is not None:
                row.set_size(*doc.page_size(page))
                if (doc_index, page) not in self.pending:
                    self.pending.append((doc_index, page))
        self.start_rendering()

    def start_rendering(self):
        if self.render_source is None and self.pending:
            self.render_source = GLib.idle_add(self.render_next, priority=GLib.PRIORITY_LOW)

    def render_next(self):
        if not self.pending:
            self.render_source = None
            return False
        doc_index, page = self.pending.pop(0)
        row = self.rows.get((doc_index, page))
        if row is not None and doc_index < len(self.documents):
            doc = self.documents[doc_index]
            if page < doc.page_count:
                try:
                    row.set_texture(doc.thumbnail(page, THUMBNAIL_SIZE * 2))
                except Exception as error:
                    print("Prevux: thumbnail failed:", error)
        return True

    def select_page(self, doc_index, page):
        row = self.rows.get((doc_index, page))
        if row is None:
            return
        self.updating = True
        self.list.unselect_all()
        self.list.select_row(row)
        self.updating = False

        GLib.idle_add(self.scroll_to_row, row)

    def scroll_to_row(self, row):
        """Keep the selected thumbnail visible."""
        adjustment = self.scroller.get_vadjustment()
        found, bounds = row.compute_bounds(self.list)
        if found:
            top = bounds.get_y()
            bottom = top + bounds.get_height()
            if top < adjustment.get_value():
                adjustment.set_value(top - 8)
            elif bottom > adjustment.get_value() + adjustment.get_page_size():
                adjustment.set_value(bottom - adjustment.get_page_size() + 8)
        return False

    def selected_pages(self):
        return sorted(
            (row.doc_index, row.page)
            for row in self.list.get_selected_rows()
            if isinstance(row, PageRow)
        )

    # ========================================================
    # EVENTS
    # ========================================================

    def on_activated(self, listbox, row):
        if not self.updating and isinstance(row, PageRow):
            self.emit("page-activated", row.doc_index, row.page)

    def on_key(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete):
            self.emit("delete-pages")
            return True
        return False

    def on_context_menu(self, gesture, n_press, x, y):
        row = self.list.get_row_at_y(int(y))
        if isinstance(row, PageRow):
            if not row.is_selected():
                self.list.unselect_all()
                self.list.select_row(row)
            self.emit("page-activated", row.doc_index, row.page)
        found, point = self.list.compute_point(self, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(point.x), int(point.y), 1, 1
        self.context_menu.set_pointing_to(rect)
        self.context_menu.popup()

    # ========================================================
    # DRAG AND DROP
    # ========================================================

    def setup_drag(self, row):
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self.on_drag_prepare, row)
        source.connect("drag-begin", self.on_drag_begin, row)
        row.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self.on_drop, row)
        target.connect("motion", self.on_drop_motion, row)
        target.connect("leave", lambda *_args: row.remove_css_class("drop-target"))
        row.add_controller(target)

    def on_drag_prepare(self, source, x, y, row):
        value = GObject.Value(GObject.TYPE_STRING, f"{row.doc_index}:{row.page}")
        return Gdk.ContentProvider.new_for_value(value)

    def on_drag_begin(self, source, drag, row):
        paintable = row.picture.get_paintable()
        if paintable is not None:
            source.set_icon(paintable, 20, 20)

    def on_drop_motion(self, target, x, y, row):
        row.add_css_class("drop-target")
        return Gdk.DragAction.MOVE

    def on_drop(self, target, value, x, y, row):
        row.remove_css_class("drop-target")
        try:
            doc_index, page = (int(part) for part in value.split(":"))
        except ValueError:
            return False
        if doc_index != row.doc_index or page == row.page:
            return False
        self.emit("move-page", doc_index, page, row.page)
        return True
