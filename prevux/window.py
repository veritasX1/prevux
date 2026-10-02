"""The Prevux document window."""

import os
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk
from PIL import Image, ImageEnhance, ImageOps

from .documents import (
    BOOK_EXTENSIONS,
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    TEXT_EXTENSIONS,
    ImageDocument,
    PDFDocument,
    pil_to_surface,
    open_document,
)
from .i18n import _
from .icons import Icon, Swatch, icon_button, icon_menu_button
from .markup import MarkupDefaults, MarkupToolbar, popover_box
from .model import HIGHLIGHT_COLORS, TextAnnotation, NoteAnnotation
from .sidebar import Sidebar
from .view import DocumentView


# Window controls on the leading edge, as on the Mac.
DECORATION_LAYOUT = "close,minimize,maximize:"

EXPORT_FORMATS = [
    ("PNG", ".png"),
    ("JPEG", ".jpg"),
    ("TIFF", ".tiff"),
    ("WebP", ".webp"),
    ("BMP", ".bmp"),
    ("PDF", ".pdf"),
]


def slot(widget):
    """A wrapper whose visibility the breakpoints control, independent of
    the child's own (document-dependent) visibility."""
    box = Gtk.Box()
    box.append(widget)
    return box


class PrevuxWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1100, 800)
        self.set_title("Prevux")

        self.documents = []
        self.doc_index = -1
        self.defaults = MarkupDefaults()
        self.force_close = False
        self.highlight_choice = ("highlight", HIGHLIGHT_COLORS[0][1])
        self.search_results = []
        self.sidebar_before_search = None
        self.adjusting = None

        self.build_ui()
        self.install_actions()
        self.install_input()
        self.update_state()

    @property
    def doc(self):
        if 0 <= self.doc_index < len(self.documents):
            return self.documents[self.doc_index]
        return None

    # ========================================================
    # UI
    # ========================================================

    def build_ui(self):
        self.split = Adw.OverlaySplitView()
        self.split.set_min_sidebar_width(150)
        self.split.set_max_sidebar_width(240)
        self.split.set_show_sidebar(False)

        # --- sidebar ------------------------------------------
        sidebar_view = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_decoration_layout(DECORATION_LAYOUT)
        sidebar_header.set_show_title(False)
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_view.add_top_bar(sidebar_header)

        self.sidebar = Sidebar()
        self.sidebar.connect("page-activated", self.on_page_activated)
        self.sidebar.connect("drop-page", self.on_drop_page)
        self.sidebar.connect("search-activated", self.on_search_result)
        self.sidebar.connect(
            "outline-activated", lambda _sidebar, page, y: self.view.scroll_to_page(page, y),
        )
        self.sidebar.connect("delete-pages", lambda *_args: self.activate_action("win.delete-pages"))
        sidebar_view.set_content(self.sidebar)
        self.split.set_sidebar(sidebar_view)

        # --- content ------------------------------------------
        content_view = Adw.ToolbarView()
        content_view.set_top_bar_style(Adw.ToolbarStyle.RAISED_BORDER)

        header = Adw.HeaderBar()
        header.set_decoration_layout(DECORATION_LAYOUT)
        start_items = []
        end_items = []
        self.split.bind_property(
            "show-sidebar", header, "show-start-title-buttons",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN,
        )

        self.sidebar_button = icon_button("sidebar", _("Show Sidebar"), toggle=True)
        self.split.bind_property(
            "show-sidebar", self.sidebar_button, "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        start_items.append(self.sidebar_button)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.set_margin_start(6)
        self.title_label = Gtk.Label(xalign=0, ellipsize=3, width_chars=6)
        self.title_label.add_css_class("document-title")
        self.subtitle_label = Gtk.Label(xalign=0, ellipsize=3)
        self.subtitle_label.add_css_class("document-subtitle")
        titles.append(self.title_label)
        titles.append(self.subtitle_label)
        start_items.append(titles)

        # Trailing items, packed from the right edge inwards.
        menu_button = icon_menu_button("more", _("Menu"))
        menu_button.set_menu_model(self.build_menu())
        end_items.append(menu_button)

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search"))
        self.search_entry.set_size_request(170, -1)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("activate", lambda _entry: self.search_step(1))
        self.search_entry.connect("next-match", lambda _entry: self.search_step(1))
        self.search_entry.connect("previous-match", lambda _entry: self.search_step(-1))
        self.search_entry.connect("stop-search", self.on_stop_search)
        self.search_slot = slot(self.search_entry)
        end_items.append(self.search_slot)

        # In narrow windows the search field moves into a popover.
        compact_entry = Gtk.SearchEntry(placeholder_text=_("Search"))
        compact_entry.set_size_request(220, -1)
        compact_entry.connect("activate", lambda _entry: self.search_step(1))
        compact_entry.connect("stop-search", self.on_stop_search)
        self.search_entry.bind_property(
            "text", compact_entry, "text",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        compact_popover = Gtk.Popover(child=compact_entry)
        compact_popover.connect("show", lambda _popover: compact_entry.grab_focus())
        self.compact_search = icon_menu_button("search", _("Search"), compact_popover)
        self.compact_search_slot = slot(self.compact_search)
        self.compact_search_slot.set_visible(False)
        end_items.append(self.compact_search_slot)

        self.markup_button = icon_button("markup", _("Show Markup Toolbar"), toggle=True)
        self.markup_button.connect("toggled", self.on_markup_toggled)
        end_items.append(self.markup_button)

        rotate = icon_button("rotate", _("Rotate"))
        rotate.set_action_name("win.rotate-left")
        end_items.append(rotate)

        self.highlight_box = Gtk.Box()
        self.highlight_button = icon_button("highlight", _("Highlight"), toggle=True)
        self.highlight_button.connect("toggled", self.on_highlight_toggled)
        self.highlight_box.append(self.highlight_button)
        highlight_menu = Gtk.MenuButton(popover=self.build_highlight_popover())
        highlight_menu.add_css_class("flat")
        highlight_menu.add_css_class("narrow-arrow")
        highlight_menu.set_tooltip_text(_("Highlight Color"))
        self.highlight_box.append(highlight_menu)
        highlight_slot = slot(self.highlight_box)
        end_items.append(highlight_slot)

        share = icon_button("share", _("Share"))
        share.set_action_name("win.share")
        end_items.append(share)

        zoom_box = Gtk.Box()
        zoom_box.add_css_class("linked")
        zoom_out = icon_button("zoom-out", _("Zoom Out"))
        zoom_out.set_action_name("win.zoom-out")
        zoom_in = icon_button("zoom-in", _("Zoom In"))
        zoom_in.set_action_name("win.zoom-in")
        zoom_box.append(zoom_out)
        zoom_box.append(zoom_in)
        end_items.append(zoom_box)

        self.info_popover = Gtk.Popover()
        self.info_popover.connect("show", lambda _popover: self.fill_info())
        info = icon_menu_button("info", _("Inspector"), self.info_popover)
        end_items.append(info)

        # One row for title and items: the title shrinks with an ellipsis
        # instead of sliding under the buttons.
        bar = Gtk.Box(spacing=6, hexpand=True)
        for widget in start_items:
            bar.append(widget)
        titles.set_hexpand(True)
        for widget in reversed(end_items):
            bar.append(widget)
        header.set_title_widget(bar)

        content_view.add_top_bar(header)

        # Like a Mac toolbar, less important items give way when space
        # runs out; everything stays reachable through the menu.
        self.content_bin = Adw.BreakpointBin(child=content_view)
        self.content_bin.set_size_request(360, 240)
        for width, hidden in (
            (900, [share, self.search_slot]),
            (740, [share, self.search_slot, zoom_box, info]),
            (580, [share, self.search_slot, zoom_box, info, rotate, highlight_slot]),
        ):
            breakpoint = Adw.Breakpoint.new(
                Adw.BreakpointCondition.parse(f"max-width: {width}px")
            )
            for widget in hidden:
                breakpoint.add_setter(widget, "visible", False)
            breakpoint.add_setter(self.compact_search_slot, "visible", True)
            self.content_bin.add_breakpoint(breakpoint)

        self.markup = MarkupToolbar(self)
        markup_scroller = Gtk.ScrolledWindow(child=self.markup)
        markup_scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        markup_scroller.set_propagate_natural_height(True)
        self.markup_revealer = Gtk.Revealer(child=markup_scroller)
        self.markup_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        content_view.add_top_bar(self.markup_revealer)

        # --- document area ------------------------------------
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.add_css_class("document-scroller")
        self.view = DocumentView(self.scroller)
        self.view.defaults = self.defaults
        self.scroller.set_child(self.view)
        # GTK wraps the view in a viewport that scrolls to the focused child – the whole page,
        # i.e. to the top – whenever a text box gets the focus. Insertions must stay in view.
        viewport = self.scroller.get_child()
        if isinstance(viewport, Gtk.Viewport):
            viewport.set_scroll_to_focus(False)
        self.view.connect("page-changed", self.on_view_page_changed)
        self.view.connect("selection-changed", self.on_view_selection_changed)
        self.view.connect("modified", self.on_view_modified)
        self.view.connect("notice", lambda _view, text: self.toast(text))
        self.view.connect("zoom-changed", lambda *_args: self.update_state())
        self.markup.update_color_icons()

        empty = Adw.StatusPage()
        empty.set_title(_("No Document Open"))
        empty.set_description(_("Open an image or a PDF, or drop a file here."))
        open_button = Gtk.Button(label=_("Open…"), halign=Gtk.Align.CENTER)
        open_button.add_css_class("pill")
        open_button.add_css_class("suggested-action")
        open_button.set_action_name("win.open")
        empty.set_child(open_button)

        self.stack = Gtk.Stack()
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self.scroller, "document")

        self.toasts = Adw.ToastOverlay(child=self.stack)
        content_view.set_content(self.toasts)
        self.split.set_content(self.content_bin)
        self.set_content(self.split)

    def build_highlight_popover(self):
        popover = Gtk.Popover()
        box = popover_box(spacing=6)
        row = Gtk.Box(spacing=4)
        for name, color in HIGHLIGHT_COLORS:
            button = Gtk.Button(child=Swatch(color))
            button.add_css_class("flat")
            button.add_css_class("swatch-button")
            button.set_tooltip_text(_(name))
            button.connect("clicked", self.on_highlight_choice, ("highlight", color), popover)
            row.append(button)
        box.append(row)
        for kind, label in (("underline", _("Underline")), ("strike", _("Strike Through"))):
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            button.connect(
                "clicked", self.on_highlight_choice, (kind, (1.0, 0.23, 0.19, 1.0)), popover,
            )
            box.append(button)
        popover.set_child(box)
        return popover

    def build_menu(self):
        def section(*items):
            menu = Gio.Menu()
            for label, action, *accel in items:
                item = Gio.MenuItem.new(label, action)
                if accel:
                    item.set_attribute_value("accel", GLib.Variant.new_string(accel[0]))
                menu.append_item(item)
            return menu

        def submenu(label, *sections):
            menu = Gio.Menu()
            for part in sections:
                menu.append_section(None, part)
            return label, menu

        menus = [
            submenu(
                _("File"),
                section(
                    (_("New Window"), "app.new-window"),
                    (_("New from Clipboard"), "win.new-from-clipboard", "<Control>n"),
                    (_("Open…"), "win.open", "<Control>o"),
                ),
                section(
                    (_("Close"), "win.close", "<Control>w"),
                    (_("Save"), "win.save", "<Control>s"),
                    (_("Export…"), "win.export", "<Control><Shift>s"),
                    (_("Export as PDF…"), "win.export-pdf"),
                ),
                section(
                    (_("Share…"), "win.share"),
                    (_("Show in Files"), "win.show-in-files"),
                ),
                section((_("Print…"), "win.print", "<Control>p")),
            ),
            submenu(
                _("Edit"),
                section(
                    (_("Undo"), "win.undo", "<Control>z"),
                    (_("Redo"), "win.redo", "<Control><Shift>z"),
                ),
                section(
                    (_("Cut"), "win.cut", "<Control>x"),
                    (_("Copy"), "win.copy", "<Control>c"),
                    (_("Paste"), "win.paste", "<Control>v"),
                    (_("Delete"), "win.delete", "Delete"),
                    (_("Select All"), "win.select-all", "<Control>a"),
                ),
                section(
                    (_("Insert Blank Page"), "win.insert-blank-page"),
                    (_("Insert Pages from File…"), "win.insert-from-file"),
                    (_("Delete Pages"), "win.delete-pages"),
                ),
                section((_("Find…"), "win.find", "<Control>f")),
            ),
            submenu(
                _("View"),
                section(
                    (_("Content Only"), "win.hide-sidebar", "<Control><Alt>1"),
                    (_("Thumbnails"), "win.show-sidebar", "<Control><Alt>2"),
                    (_("Table of Contents"), "win.show-contents", "<Control><Alt>3"),
                    (_("Contact Sheet"), "win.show-sheet", "<Control><Alt>4"),
                ),
                section(
                    (_("Continuous Scroll"), "win.display-mode::continuous", "<Control>1"),
                    (_("Single Page"), "win.display-mode::single", "<Control>2"),
                    (_("Two Pages"), "win.display-mode::two", "<Control>3"),
                ),
                section(
                    (_("Actual Size"), "win.actual-size", "<Control>0"),
                    (_("Zoom to Fit"), "win.zoom-fit", "<Control>9"),
                    (_("Zoom to Width"), "win.zoom-width"),
                    (_("Zoom to Selection"), "win.zoom-selection", "<Control>asterisk"),
                    (_("Zoom Level…"), "win.zoom-level"),
                    (_("Zoom In"), "win.zoom-in", "<Control>plus"),
                    (_("Zoom Out"), "win.zoom-out", "<Control>minus"),
                ),
                section(
                    (_("Show Markup Toolbar"), "win.markup", "<Control><Shift>a"),
                    (_("Enter Full Screen"), "win.fullscreen", "F11"),
                ),
            ),
            submenu(
                _("Go"),
                section(
                    (_("Previous Page"), "win.previous-page", "<Alt>Up"),
                    (_("Next Page"), "win.next-page", "<Alt>Down"),
                    (_("First Page"), "win.first-page", "Home"),
                    (_("Last Page"), "win.last-page", "End"),
                    (_("Go to Page…"), "win.go-to-page", "<Control><Alt>g"),
                ),
                section(
                    (_("Previous Document"), "win.previous-document", "<Alt>Page_Up"),
                    (_("Next Document"), "win.next-document", "<Alt>Page_Down"),
                ),
            ),
            submenu(
                _("Tools"),
                section(
                    (_("Inspector"), "win.inspector", "<Control>i"),
                ),
                section(
                    (_("Rotate Left"), "win.rotate-left", "<Control>l"),
                    (_("Rotate Right"), "win.rotate-right", "<Control>r"),
                    (_("Flip Horizontal"), "win.flip-horizontal"),
                    (_("Flip Vertical"), "win.flip-vertical"),
                ),
                section(
                    (_("Highlight Text"), "win.highlight", "<Control><Shift>h"),
                    (_("Crop"), "win.crop", "<Control>k"),
                    (_("Adjust Color…"), "win.adjust-color", "<Control><Alt>c"),
                    (_("Adjust Size…"), "win.adjust-size"),
                ),
                section(
                    (_("Add Text"), "win.add-text"),
                    (_("Add Rectangle"), "win.add-shape::rect"),
                    (_("Add Oval"), "win.add-shape::oval"),
                    (_("Add Line"), "win.add-shape::line"),
                    (_("Add Arrow"), "win.add-shape::arrow"),
                    (_("Add Speech Bubble"), "win.add-shape::bubble"),
                    (_("Add Star"), "win.add-shape::star"),
                ),
            ),
        ]

        menu = Gio.Menu()
        top = Gio.Menu()
        for label, submenu_model in menus:
            top.append_submenu(label, submenu_model)
        menu.append_section(None, top)
        menu.append_section(
            None,
            section(
                (_("Keyboard Shortcuts"), "app.shortcuts"),
                (_("About Prevux"), "app.about"),
            ),
        )
        return menu

    # ========================================================
    # ACTIONS
    # ========================================================

    def install_actions(self):
        self.actions = {}
        simple = {
            "open": self.open_dialog,
            "new-from-clipboard": self.new_from_clipboard,
            "close": self.close,
            "save": self.save,
            "export": self.export_dialog,
            "export-pdf": lambda: self.export_dialog(pdf=True),
            "print": self.print_document,
            "share": self.share,
            "show-in-files": self.show_in_files,
            "undo": self.undo,
            "redo": self.redo,
            "cut": self.cut,
            "copy": self.copy,
            "paste": self.paste,
            "delete": self.delete,
            "select-all": self.select_all,
            "find": self.focus_search,
            "insert-blank-page": self.insert_blank_page,
            "insert-from-file": self.insert_from_file,
            "delete-pages": self.delete_pages,
            "hide-sidebar": lambda: self.split.set_show_sidebar(False),
            "show-sidebar": lambda: self.show_sidebar_mode("thumbnails"),
            "show-contents": lambda: self.show_sidebar_mode("contents"),
            "show-sheet": lambda: self.show_sidebar_mode("sheet"),
            "zoom-selection": self.zoom_to_selection,
            "zoom-level": self.ask_zoom_level,
            "actual-size": lambda: self.view.set_zoom(1.0),
            "zoom-fit": lambda: self.view.zoom_to_fit("page"),
            "zoom-width": lambda: self.view.zoom_to_fit("width"),
            "zoom-in": lambda: self.view.zoom_step(1),
            "zoom-out": lambda: self.view.zoom_step(-1),
            "markup": lambda: self.markup_button.set_active(not self.markup_button.get_active()),
            "fullscreen": self.toggle_fullscreen,
            "previous-page": lambda: self.go_page(self.view.current_page - 1),
            "next-page": lambda: self.go_page(self.view.current_page + 1),
            "first-page": lambda: self.go_page(0),
            "last-page": lambda: self.go_page(self.doc.page_count - 1),
            "go-to-page": self.go_to_page_dialog,
            "previous-document": lambda: self.show_document(self.doc_index - 1),
            "next-document": lambda: self.show_document(self.doc_index + 1),
            "inspector": lambda: self.info_popover.popup(),
            "rotate-left": lambda: self.rotate(-90),
            "rotate-right": lambda: self.rotate(90),
            "flip-horizontal": lambda: self.flip(True),
            "flip-vertical": lambda: self.flip(False),
            "highlight": self.highlight_now,
            "crop": self.crop,
            "adjust-color": self.adjust_color,
            "adjust-size": self.adjust_size,
            "add-text": lambda: self.view.insert_text(),
        }
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _param, function=callback: function())
            self.add_action(action)
            self.actions[name] = action

        mode = Gio.SimpleAction.new_stateful("display-mode", GLib.VariantType.new("s"), GLib.Variant.new_string("continuous"))
        mode.connect("activate", self.on_display_mode)
        self.add_action(mode)
        self.actions["display-mode"] = mode

        action = Gio.SimpleAction.new("add-shape", GLib.VariantType.new("s"))
        action.connect("activate", lambda _action, param: self.view.insert_shape(param.get_string()))
        self.add_action(action)
        self.actions["add-shape"] = action

    def enable(self, name, enabled):
        self.actions[name].set_enabled(bool(enabled))

    def update_state(self):
        doc = self.doc
        has_doc = doc is not None
        pdf = has_doc and doc.kind == "pdf"
        image = has_doc and doc.kind == "image"

        for name in (
            "close", "save", "export", "export-pdf", "print", "share", "show-in-files",
            "actual-size", "zoom-fit", "zoom-width", "zoom-in", "zoom-out", "markup", "zoom-selection",
            "zoom-level", "display-mode", "show-sheet",
            "rotate-left", "rotate-right", "inspector", "add-text", "add-shape", "go-to-page",
            "first-page", "last-page", "previous-page", "next-page", "select-all",
            "paste",
        ):
            self.enable(name, has_doc)
        self.enable("close", True)
        self.enable("undo", has_doc and doc.can_undo())
        self.enable("redo", has_doc and doc.can_redo())
        self.enable("find", pdf)
        self.enable("highlight", pdf)
        self.enable("insert-blank-page", pdf)
        self.enable("insert-from-file", pdf)
        self.enable("delete-pages", pdf and doc.page_count > 1)
        self.enable("flip-horizontal", image)
        self.enable("flip-vertical", image)
        self.enable("adjust-color", image)
        self.enable("adjust-size", image)
        self.enable("crop", has_doc and self.view.rect_selection is not None)
        selection = has_doc and (
            self.view.selected is not None or self.view.text_selection is not None
            or self.view.rect_selection is not None
        )
        self.enable("copy", selection)
        self.enable("cut", has_doc and self.view.selected is not None)
        self.enable("delete", has_doc and self.view.selected is not None)
        self.enable("previous-document", self.doc_index > 0)
        self.enable("next-document", self.doc_index < len(self.documents) - 1)

        self.search_entry.set_visible(pdf)
        self.compact_search.set_visible(pdf)
        self.highlight_box.set_visible(pdf)
        self.markup_button.set_sensitive(has_doc)
        self.stack.set_visible_child_name("document" if has_doc else "empty")
        if has_doc:
            self.markup.set_document_kind(doc.kind)
        self.update_titles()

    def update_titles(self):
        doc = self.doc
        if doc is None:
            self.title_label.set_text("Prevux")
            self.subtitle_label.set_text("")
            self.subtitle_label.set_visible(False)
            self.set_title("Prevux")
            return

        title = doc.name
        if doc.modified:
            title += " — " + _("Edited")
        self.title_label.set_text(title)
        self.set_title(title)

        if doc.kind == "pdf":
            subtitle = _("Page {page} of {count}").format(
                page=self.view.current_page + 1, count=doc.page_count,
            )
        else:
            width, height = doc.page_size(0)
            subtitle = f"{width} × {height} px"
        if len(self.documents) > 1:
            subtitle += "  ·  " + _("{index} of {count} documents").format(
                index=self.doc_index + 1, count=len(self.documents),
            )
        self.subtitle_label.set_text(subtitle)
        self.subtitle_label.set_visible(True)

    def toast(self, text):
        toast = Adw.Toast(title=text)
        toast.set_timeout(2)
        self.toasts.add_toast(toast)

    # ========================================================
    # INPUT
    # ========================================================

    def install_input(self):
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.stack.add_controller(drop)

    def on_key(self, controller, keyval, keycode, state):
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.TextView, Gtk.SpinButton)):
            return False

        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        key = Gdk.keyval_to_lower(keyval)

        if control and not alt:
            shortcuts = {
                Gdk.KEY_z: "redo" if shift else "undo",
                Gdk.KEY_y: "redo",
                Gdk.KEY_c: "copy",
                Gdk.KEY_x: "cut",
                Gdk.KEY_v: "paste",
                Gdk.KEY_a: "select-all",
            }
            if key in shortcuts:
                self.activate_action("win." + shortcuts[key])
                return True
            return False

        if self.doc is None or alt:
            return False

        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete):
            return self.view.delete_selected()

        if keyval == Gdk.KEY_Escape:
            if self.view.selected is not None:
                self.view.select(None)
            elif self.view.rect_selection or self.view.text_selection:
                self.view.rect_selection = None
                self.view.clear_text_selection()
                self.view.queue_draw()
            elif self.is_fullscreen():
                self.unfullscreen()
            elif self.view.tool in ("sketch", "note", "redact"):
                self.view.set_tool("select_default")
                self.markup.sync_tool(self.view.tool)
            self.update_state()
            return True

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if isinstance(self.view.selected, (TextAnnotation, NoteAnnotation)):
                self.view.start_editing(self.view.selected, self.view.selected_page)
                return True

        step = 10 if shift else 1
        nudges = {
            Gdk.KEY_Left: (-step, 0), Gdk.KEY_Right: (step, 0),
            Gdk.KEY_Up: (0, -step), Gdk.KEY_Down: (0, step),
        }
        if keyval in nudges and self.view.selected is not None:
            return self.view.nudge(*nudges[keyval])

        if self.doc.kind == "pdf" and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            if self.view.fit_mode == "page" or self.view.zoom * self.doc.page_size(0)[1] < self.scroller.get_height():
                self.go_page(self.view.current_page + (1 if keyval == Gdk.KEY_Right else -1))
                return True
        return False

    def on_drop(self, target, value, x, y):
        from .documents import DRAG_FOLDER
        # Our own dragged thumbnails also carry a PDF file: ignore that here.
        paths = [file.get_path() for file in value.get_files()
                 if file.get_path() and Path(file.get_path()).parent != DRAG_FOLDER]
        if paths:
            self.get_application().open_paths(paths, self if self.doc is None else None)
            return True
        return False

    # ========================================================
    # DOCUMENTS
    # ========================================================

    def load_paths(self, paths):
        opened = []
        for path in paths:
            try:
                opened.append(open_document(path))
            except PermissionError:
                self.ask_password(path)
            except Exception as error:
                self.show_error(
                    _("“{name}” could not be opened.").format(name=Path(path).name),
                    str(error),
                )
        if opened:
            self.add_documents(opened)

    def add_documents(self, documents):
        self.documents.extend(documents)
        self.sidebar.set_documents(self.documents)
        self.show_document(len(self.documents) - len(documents))
        if len(self.documents) > 1 or documents[0].page_count > 1:
            self.show_sidebar_when_ready()
        for doc in documents:
            self.get_application().note_recent(doc.path)

    def show_sidebar_when_ready(self):
        # Revealing the sidebar before the window has its first size makes
        # the split view allocate nonsense sizes; wait for the first frame.
        if self.get_mapped() and self.content_bin.get_width() > 0:
            self.split.set_show_sidebar(True)
            return
        GLib.timeout_add(50, self._show_sidebar_retry)

    def _show_sidebar_retry(self):
        self.show_sidebar_when_ready()
        return False

    def show_document(self, index):
        if not 0 <= index < len(self.documents):
            return
        if index == self.doc_index:
            return
        self.view.finish_editing()
        self.doc_index = index
        doc = self.doc
        self.defaults.set_document(doc)
        self.view.set_document(doc)
        self.view.set_tool("select_default")
        self.markup.sync_tool(self.view.tool)
        self.search_results = []
        self.search_entry.set_text("")
        self.sidebar.select_page(index, 0)
        if self.sidebar.mode == "contents":
            self.sidebar.fill_contents(doc)
        self.update_state()

    def ask_password(self, path):
        dialog = Adw.AlertDialog(
            heading=_("“{name}” is password protected").format(name=Path(path).name),
            body=_("Enter the password to open this document."),
        )
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("open")

        def on_response(dialog, response):
            if response != "open":
                return
            try:
                doc = PDFDocument(path, entry.get_text())
            except PermissionError:
                self.toast(_("Wrong password."))
                self.ask_password(path)
                return
            self.add_documents([doc])

        dialog.connect("response", on_response)
        dialog.present(self)

    def open_dialog(self):
        dialog = Gtk.FileDialog(title=_("Open"))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        supported = Gtk.FileFilter(name=_("All Supported Documents"))
        for suffix in sorted(IMAGE_EXTENSIONS | PDF_EXTENSIONS | TEXT_EXTENSIONS | BOOK_EXTENSIONS):
            supported.add_suffix(suffix[1:])
        filters.append(supported)
        dialog.set_filters(filters)
        dialog.set_default_filter(supported)
        if self.doc is not None:
            dialog.set_initial_folder(Gio.File.new_for_path(str(Path(self.doc.path).parent)))
        dialog.open_multiple(self, None, self.on_open_finished)

    def on_open_finished(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        paths = [
            files.get_item(index).get_path()
            for index in range(files.get_n_items())
            if files.get_item(index).get_path()
        ]
        if paths:
            self.get_application().open_paths(paths, self if self.doc is None else None)

    def new_from_clipboard(self):
        clipboard = self.get_clipboard()
        clipboard.read_texture_async(None, self.on_clipboard_texture)

    def on_clipboard_texture(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error:
            texture = None
        if texture is None:
            self.toast(_("The clipboard contains no image."))
            return
        folder = Path(tempfile.mkdtemp(prefix="prevux-"))
        path = folder / (_("Untitled") + ".png")
        texture.save_to_png(str(path))
        doc = ImageDocument(path)
        doc.untitled = True
        target = self if self.doc is None else self.get_application().new_window()
        target.add_documents([doc])
        target.present()

    # ========================================================
    # SAVING
    # ========================================================

    def save(self, then=None):
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        if getattr(doc, "untitled", False):
            self.export_dialog(then=then)
            return
        if getattr(doc, "converted", False):
            self.export_dialog(pdf=True, then=then)
            return
        try:
            doc.save()
        except Exception as error:
            self.show_error(_("The document could not be saved."), str(error))
            return
        self.after_save(doc)
        if then:
            then()

    def after_save(self, doc):
        doc.untitled = False
        self.sidebar.set_documents(self.documents)
        self.sidebar.select_page(self.doc_index, self.view.current_page)
        self.update_state()
        self.toast(_("Saved"))

    def export_dialog(self, pdf=False, then=None):
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        stem = Path(doc.name).stem
        suffix = ".pdf" if pdf else Path(doc.name).suffix.lower()

        dialog = Gtk.FileDialog(title=_("Export as PDF") if pdf else _("Export"))
        dialog.set_initial_name(stem + suffix)
        if not getattr(doc, "untitled", False):
            dialog.set_initial_folder(Gio.File.new_for_path(str(Path(doc.path).parent)))

        filters = Gio.ListStore.new(Gtk.FileFilter)
        formats = [("PDF", ".pdf")] if pdf else (
            EXPORT_FORMATS if doc.kind == "image" else [("PDF", ".pdf"), ("PNG", ".png"), ("JPEG", ".jpg")]
        )
        for name, extension in formats:
            file_filter = Gtk.FileFilter(name=name)
            file_filter.add_suffix(extension[1:])
            if extension == ".jpg":
                file_filter.add_suffix("jpeg")
            if extension == ".tiff":
                file_filter.add_suffix("tif")
            filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.save(self, None, self.on_export_finished, doc, then)

    def on_export_finished(self, dialog, result, doc, then):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        path = file.get_path()
        suffix = Path(path).suffix.lower()
        known = {extension for _name, extension in EXPORT_FORMATS} | {".jpeg", ".tif"}
        if suffix not in known:
            path += ".pdf" if doc.kind == "pdf" else ".png"
            suffix = Path(path).suffix.lower()

        try:
            if doc.kind == "pdf" and suffix != ".pdf":
                format_name = "JPEG" if suffix in (".jpg", ".jpeg") else "PNG"
                doc.save_page_image(path, format_name, self.view.current_page)
                self.toast(_("Exported"))
                return
            if doc.kind == "image" and suffix == ".pdf":
                doc.save(path, "PDF")
                self.toast(_("Exported"))
                return
            doc.save(path)
        except Exception as error:
            self.show_error(_("The document could not be exported."), str(error))
            return
        if getattr(doc, "converted", False):
            # From now on the PDF is the document; the original Markdown/e-book stays as it was.
            doc.path = path
            doc.converted = False
        self.after_save(doc)
        if then:
            then()

    def share(self):
        doc = self.doc
        if doc is None:
            return
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(doc.path))
        launcher.set_always_ask(True)
        launcher.launch(self, None, None)

    def show_in_files(self):
        doc = self.doc
        if doc is None:
            return
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(doc.path))
        launcher.open_containing_folder(self, None, None)

    def print_document(self):
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        operation = Gtk.PrintOperation()
        operation.set_job_name(doc.name)
        operation.set_n_pages(doc.page_count)
        operation.set_embed_page_setup(True)
        operation.connect("draw-page", self.on_draw_page, doc)
        try:
            operation.run(Gtk.PrintOperationAction.PRINT_DIALOG, self)
        except GLib.Error as error:
            self.show_error(_("Printing failed."), str(error))

    def on_draw_page(self, operation, context, page, doc):
        cr = context.get_cairo_context()
        width, height = context.get_width(), context.get_height()
        page_width, page_height = doc.page_size(page)
        scale = min(width / page_width, height / page_height)

        if doc.kind == "pdf":
            import pymupdf
            dpi_scale = min(300 / 72, 6000 / max(page_width, page_height))
            pixmap = doc.doc[page].get_pixmap(matrix=pymupdf.Matrix(dpi_scale, dpi_scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        else:
            image = doc.image
            dpi_scale = 1.0

        surface, _data = pil_to_surface(image)
        cr.translate((width - page_width * scale) / 2, (height - page_height * scale) / 2)
        cr.scale(scale, scale)
        cr.save()
        cr.scale(1 / dpi_scale, 1 / dpi_scale)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()
        doc.render_annotations(cr, page, surface, dpi_scale)

    # ========================================================
    # CLOSING
    # ========================================================

    def do_close_request(self):
        if self.force_close:
            return False
        self.view.finish_editing()
        modified = [doc for doc in self.documents if doc.modified]
        if not modified:
            return False

        doc = modified[0]
        self.show_document(self.documents.index(doc))
        dialog = Adw.AlertDialog(
            heading=_("Do you want to keep the changes you made to “{name}”?").format(name=doc.name),
            body=_("Your changes will be lost if you don’t save them."),
        )
        dialog.add_response("discard", _("Don’t Save"))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_close_response, doc)
        dialog.present(self)
        return True

    def on_close_response(self, dialog, response, doc):
        if response == "cancel":
            return
        if response == "discard":
            doc.modified = False
            self.close()
            return
        self.save(then=self.close)

    # ========================================================
    # EDITING
    # ========================================================

    def after_edit(self, structure=False):
        doc = self.doc
        if structure:
            self.view.document_structure_changed()
            self.sidebar.set_documents(self.documents)
            self.sidebar.select_page(self.doc_index, self.view.current_page)
        else:
            self.sidebar.refresh_document(self.doc_index, [self.view.current_page])
        self.view.refresh()
        doc.modified = True
        self.update_state()

    def undo(self):
        self.history(True)

    def redo(self):
        self.history(False)

    def history(self, undo):
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        pages = doc.page_count
        size = doc.page_size(0)
        (doc.undo if undo else doc.redo)()
        self.view.refresh()
        if doc.page_count != pages or doc.page_size(0) != size or doc.kind == "image":
            self.view.document_structure_changed()
            self.sidebar.set_documents(self.documents)
        else:
            self.sidebar.refresh_document(self.doc_index)
        self.update_state()

    def copy(self):
        view = self.view
        clipboard = self.get_clipboard()
        if view.text_selection:
            clipboard.set(view.selected_text())
        elif view.selected is not None:
            self.get_application().clipboard_annotation = view.selected.clone()
            clipboard.set(getattr(view.selected, "text", "") or " ")
            self.get_application().clipboard_marker = clipboard.get_content()
        elif view.rect_selection and self.doc.kind == "image":
            _page, x0, y0, x1, y1 = view.rect_selection
            box = tuple(int(round(value)) for value in (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            from .documents import pil_to_texture
            clipboard.set(pil_to_texture(self.doc.composited().crop(box)))

    def cut(self):
        if self.view.selected is not None:
            self.copy()
            self.view.delete_selected()
            self.update_state()

    def paste(self):
        app = self.get_application()
        annotation = getattr(app, "clipboard_annotation", None)
        clipboard = self.get_clipboard()
        if annotation is not None and clipboard.get_content() is getattr(app, "clipboard_marker", None):
            copy = annotation.clone()
            offset = 12 / self.view.zoom
            copy.move(offset, offset)
            app.clipboard_annotation = copy
            self.view.insert(copy, self.view.current_page)
            self.update_state()
            return
        self.new_from_clipboard()

    def delete(self):
        self.view.delete_selected()
        self.update_state()

    def select_all(self):
        if self.doc is not None and self.doc.kind == "pdf":
            self.view.select_all_text()
        elif self.doc is not None:
            width, height = self.doc.page_size(0)
            self.view.rect_selection = (0, 0, 0, width, height)
            self.view.queue_draw()
        self.update_state()

    def rotate(self, degrees):
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        pages = [page for doc_index, page in self.sidebar.selected_pages() if doc_index == self.doc_index]
        if doc.kind != "pdf" or not pages:
            pages = [self.view.current_page]
        doc.checkpoint(structure=doc.kind == "image")
        for page in pages:
            doc.rotate_page(page, degrees)
        self.view.rect_selection = None
        self.after_edit(structure=True)

    def flip(self, horizontal):
        doc = self.doc
        if doc is None or doc.kind != "image":
            return
        doc.checkpoint(structure=True)
        doc.flip(horizontal)
        self.after_edit(structure=True)

    def crop(self):
        doc = self.doc
        if doc is None:
            return
        if self.view.rect_selection is None:
            self.toast(_("Select an area with Rectangular Selection first."))
            return
        page, x0, y0, x1, y1 = self.view.rect_selection
        doc.checkpoint(structure=True)
        doc.crop(page, (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        self.view.rect_selection = None
        self.after_edit(structure=True)

    def insert_blank_page(self):
        doc = self.doc
        if doc is None or doc.kind != "pdf":
            return
        doc.checkpoint(structure=True)
        doc.insert_blank_page(self.view.current_page + 1)
        self.after_edit(structure=True)
        self.go_page(self.view.current_page + 1)

    def insert_from_file(self):
        """Like Preview's Edit → Insert → Page from File: all pages after the current one."""
        doc = self.doc
        if doc is None or doc.kind != "pdf":
            return
        dialog = Gtk.FileDialog(title=_("Insert Pages from File…"))
        supported = Gtk.FileFilter(name=_("All Supported Documents"))
        for suffix in sorted(IMAGE_EXTENSIONS | PDF_EXTENSIONS | TEXT_EXTENSIONS | BOOK_EXTENSIONS):
            supported.add_suffix(suffix[1:])
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(supported)
        dialog.set_filters(filters)

        def chosen(dialog, result):
            try:
                path = dialog.open_finish(result).get_path()
            except GLib.Error:
                return
            try:
                source = open_document(path)
            except Exception as error:
                self.show_error(_("“{name}” could not be opened.").format(name=Path(path).name), str(error))
                return
            at = self.view.current_page + 1
            doc.checkpoint(structure=True)
            for offset in range(source.page_count):
                doc.insert_page_from(source, offset, at + offset)
            self.after_edit(structure=True)
            self.go_page(at)
        dialog.open(self, None, chosen)

    def delete_pages(self):
        doc = self.doc
        if doc is None or doc.kind != "pdf":
            return
        pages = [page for doc_index, page in self.sidebar.selected_pages() if doc_index == self.doc_index]
        if not pages:
            pages = [self.view.current_page]
        if len(pages) >= doc.page_count:
            self.toast(_("A PDF needs at least one page."))
            return
        doc.checkpoint(structure=True)
        doc.delete_pages(pages)
        self.after_edit(structure=True)

    def on_drop_page(self, sidebar, key, doc_index, at):
        owner, source_index, page = Sidebar.parse_key(key)
        doc = self.documents[doc_index]
        if doc.kind != "pdf":
            return

        if owner == sidebar.owner_id and source_index == doc_index:
            # Reorder within the same PDF.
            target = at if at <= page else at - 1
            if target == page:
                return
            self.show_document(doc_index)
            doc.checkpoint(structure=True)
            doc.move_page(page, target)
            self.after_edit(structure=True)
            self.sidebar.select_page(doc_index, target)
            GLib.idle_add(lambda: self.view.scroll_to_page(target) and False)
            return

        # Copy from another document, possibly in another window.
        source = None
        for window in self.get_application().get_windows():
            if isinstance(window, PrevuxWindow) and window.sidebar.owner_id == owner:
                if 0 <= source_index < len(window.documents):
                    source = window.documents[source_index]
        if source is None or page >= source.page_count:
            return
        self.show_document(doc_index)
        doc.checkpoint(structure=True)
        doc.insert_page_from(source, page, at)
        self.after_edit(structure=True)
        self.sidebar.select_page(doc_index, at)
        GLib.idle_add(lambda: self.view.scroll_to_page(at) and False)
        self.toast(_("Page copied to “{name}”").format(name=doc.name))

    # ========================================================
    # HIGHLIGHT
    # ========================================================

    def on_highlight_toggled(self, button):
        if button.get_active():
            self.view.highlight_mode = self.highlight_choice
            if self.view.text_selection:
                self.view.add_markup(*self.highlight_choice)
            if self.view.tool != "text-select":
                self.view.set_tool("text-select")
                self.markup.sync_tool(self.view.tool)
        else:
            self.view.highlight_mode = None

    def on_highlight_choice(self, button, choice, popover):
        popover.popdown()
        self.highlight_choice = choice
        if self.highlight_button.get_active():
            self.view.highlight_mode = choice
            if self.view.text_selection:
                self.view.add_markup(*choice)
        else:
            self.highlight_button.set_active(True)

    def highlight_now(self):
        if self.view.text_selection:
            self.view.add_markup(*self.highlight_choice)
        else:
            self.highlight_button.set_active(not self.highlight_button.get_active())

    # ========================================================
    # NAVIGATION
    # ========================================================

    def show_sidebar_mode(self, mode):
        self.sidebar.set_mode(mode, self.doc)
        # The contact sheet needs room for a grid (Preview widens its sidebar too).
        self.split.set_max_sidebar_width(420 if mode == "sheet" else 240)
        self.split.set_sidebar_width_fraction(0.38 if mode == "sheet" else 0.25)
        self.split.set_show_sidebar(True)

    def on_display_mode(self, action, value):
        action.set_state(value)
        self.view.set_display_mode(value.get_string())

    def zoom_to_selection(self):
        if not self.view.zoom_to_selection():
            self.toast(_("Select an area with Rectangular Selection first."))

    def ask_zoom_level(self):
        """Type a zoom level in percent (Preview's Scale field)."""
        dialog = Adw.AlertDialog(heading=_("Zoom Level"))
        spin = Gtk.SpinButton.new_with_range(5, 1600, 5)
        spin.set_value(round(self.view.zoom * 100))
        spin.set_activates_default(True)
        box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        box.append(spin)
        box.append(Gtk.Label(label="%"))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, response: response == "ok" and self.view.set_zoom(spin.get_value() / 100))
        dialog.present(self)

    def go_page(self, page):
        doc = self.doc
        if doc is None:
            return
        page = max(0, min(doc.page_count - 1, page))
        self.view.scroll_to_page(page)

    def on_view_page_changed(self, view, page):
        self.sidebar.select_page(self.doc_index, page)
        self.update_titles()

    def on_page_activated(self, sidebar, doc_index, page):
        if doc_index != self.doc_index:
            self.show_document(doc_index)
        self.view.scroll_to_page(page)

    def go_to_page_dialog(self):
        doc = self.doc
        if doc is None:
            return
        dialog = Adw.AlertDialog(heading=_("Go to Page"))
        spin = Gtk.SpinButton.new_with_range(1, doc.page_count, 1)
        spin.set_value(self.view.current_page + 1)
        spin.set_activates_default(True)
        dialog.set_extra_child(spin)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("go", _("Go"))
        dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("go")
        dialog.connect(
            "response",
            lambda _dialog, response: response == "go" and self.go_page(int(spin.get_value()) - 1),
        )
        dialog.present(self)

    def toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    # ========================================================
    # SEARCH
    # ========================================================

    def on_search_changed(self, entry):
        doc = self.doc
        text = entry.get_text().strip()
        if doc is None or doc.kind != "pdf" or not text:
            self.view.search_hits = []
            self.view.search_current = -1
            self.view.queue_draw()
            self.end_search_sidebar()
            return
        hits = doc.search(text)
        self.view.search_hits = hits
        self.view.search_current = -1

        # Show the results in the sidebar; restore it when the search ends.
        if self.sidebar_before_search is None:
            self.sidebar_before_search = self.split.get_show_sidebar()
        snippets = [doc.snippet(page, rect) for page, rect in hits[:500]]
        self.sidebar.show_results(hits[:500], snippets, text)
        self.split.set_show_sidebar(True)

        if hits:
            self.search_step(1)
        else:
            self.view.queue_draw()

    def end_search_sidebar(self):
        if self.sidebar_before_search is None:
            return
        self.sidebar.hide_results()
        self.split.set_show_sidebar(self.sidebar_before_search)
        self.sidebar_before_search = None

    def search_step(self, direction):
        hits = self.view.search_hits
        if not hits:
            return
        self.view.search_current = (self.view.search_current + direction) % len(hits)
        page, rect = hits[self.view.search_current]
        self.view.scroll_to_page(page, rect[1])
        self.view.queue_draw()
        self.sidebar.select_result(self.view.search_current)

    def on_search_result(self, sidebar, index):
        self.view.search_current = index - 1
        self.search_step(1)

    def focus_search(self):
        if self.search_slot.get_visible():
            self.search_entry.grab_focus()
        else:
            self.compact_search.popup()

    def on_stop_search(self, entry):
        entry.set_text("")
        self.view.grab_focus()

    # ========================================================
    # VIEW EVENTS
    # ========================================================

    def on_view_selection_changed(self, view):
        self.markup.update_color_icons()
        self.markup.sync_tool(view.tool)
        self.update_state()

    def on_view_modified(self, view):
        if self.doc is not None:
            self.sidebar.refresh_document(self.doc_index, [view.selected_page, view.current_page])
        self.update_state()

    def on_markup_toggled(self, button):
        active = button.get_active()
        self.markup_revealer.set_reveal_child(active)
        if not active:
            self.view.finish_editing()
            if self.view.tool in ("sketch", "note", "redact"):
                self.view.set_tool("select_default")
                self.markup.sync_tool(self.view.tool)

    # ========================================================
    # INSPECTOR
    # ========================================================

    def fill_info(self):
        doc = self.doc
        box = popover_box(spacing=4, margin=14)
        box.set_size_request(260, -1)
        if doc is None:
            self.info_popover.set_child(box)
            return

        info = doc.info()
        title = Gtk.Label(label=doc.name, xalign=0, wrap=True, max_width_chars=30)
        title.add_css_class("heading")
        box.append(title)

        grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        grid.set_margin_top(6)
        rows = [
            (_("Kind"), info.get("type", "")),
            (_("Size"), GLib.format_size(info["size"])),
            (_("Dimensions"), info.get("dimensions", "")),
        ]
        if "pages" in info:
            rows.append((_("Pages"), str(info["pages"])))
        if info.get("title"):
            rows.append((_("Title"), info["title"]))
        if info.get("author"):
            rows.append((_("Author"), info["author"]))
        rows.append((_("Where"), str(Path(info["path"]).parent)))

        for row, (label, value) in enumerate(rows):
            key = Gtk.Label(label=label, xalign=1, yalign=0)
            key.add_css_class("dim-label")
            grid.attach(key, 0, row, 1, 1)
            content = Gtk.Label(label=value, xalign=0, wrap=True, selectable=True, max_width_chars=28)
            content.set_wrap_mode(2)
            grid.attach(content, 1, row, 1, 1)
        box.append(grid)
        self.info_popover.set_child(box)

    # ========================================================
    # IMAGE ADJUSTMENTS
    # ========================================================

    def adjust_size(self):
        doc = self.doc
        if doc is None or doc.kind != "image":
            return
        AdjustSizeDialog(self, doc).present(self)

    def adjust_color(self):
        doc = self.doc
        if doc is None or doc.kind != "image":
            return
        AdjustColorDialog(self, doc).present(self)

    # ========================================================
    # ERRORS
    # ========================================================

    def show_error(self, heading, body=""):
        print("Prevux:", heading, body)
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)


# ============================================================
# ADJUST SIZE
# ============================================================

class AdjustSizeDialog(Adw.Dialog):

    def __init__(self, window, doc):
        super().__init__(title=_("Adjust Size"))
        self.window = window
        self.doc = doc
        self.width, self.height = doc.page_size(0)
        self.updating = False
        self.set_content_width(360)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()

        self.unit = Adw.ComboRow(title=_("Unit"), model=Gtk.StringList.new([_("Pixels"), _("Percent")]))
        self.unit.connect("notify::selected", self.on_unit)
        group.add(self.unit)

        self.width_row = Adw.SpinRow.new_with_range(1, 100000, 1)
        self.width_row.set_title(_("Width"))
        self.width_row.set_value(self.width)
        self.width_row.connect("notify::value", self.on_value, "width")
        group.add(self.width_row)

        self.height_row = Adw.SpinRow.new_with_range(1, 100000, 1)
        self.height_row.set_title(_("Height"))
        self.height_row.set_value(self.height)
        self.height_row.connect("notify::value", self.on_value, "height")
        group.add(self.height_row)

        self.proportional = Adw.SwitchRow(title=_("Scale proportionally"), active=True)
        group.add(self.proportional)

        self.result = Adw.ActionRow(title=_("Resulting Size"))
        self.result.add_css_class("property")
        group.add(self.result)
        page.add(group)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        buttons.set_margin_end(12)
        buttons.set_margin_bottom(12)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: self.close())
        apply = Gtk.Button(label=_("OK"))
        apply.add_css_class("suggested-action")
        apply.connect("clicked", self.on_apply)
        buttons.append(cancel)
        buttons.append(apply)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(page)
        box.append(buttons)
        view.set_content(box)
        self.set_child(view)
        self.update_result()

    def percent(self):
        return self.unit.get_selected() == 1

    def target(self):
        width = self.width_row.get_value()
        height = self.height_row.get_value()
        if self.percent():
            width = self.width * width / 100
            height = self.height * height / 100
        return max(1, int(round(width))), max(1, int(round(height)))

    def on_unit(self, row, _param):
        self.updating = True
        if self.percent():
            self.width_row.set_value(100)
            self.height_row.set_value(100)
        else:
            self.width_row.set_value(self.width)
            self.height_row.set_value(self.height)
        self.updating = False
        self.update_result()

    def on_value(self, row, _param, which):
        if self.updating:
            return
        if self.proportional.get_active():
            self.updating = True
            value = row.get_value()
            if self.percent():
                other = value
            elif which == "width":
                other = value * self.height / self.width
            else:
                other = value * self.width / self.height
            (self.height_row if which == "width" else self.width_row).set_value(round(other))
            self.updating = False
        self.update_result()

    def update_result(self):
        width, height = self.target()
        self.result.set_subtitle(f"{width} × {height} px")

    def on_apply(self, button):
        width, height = self.target()
        if (width, height) != (self.width, self.height):
            self.doc.checkpoint(structure=True)
            self.doc.resize(width, height)
            self.window.after_edit(structure=True)
        self.close()


# ============================================================
# ADJUST COLOR
# ============================================================

class AdjustColorDialog(Adw.Dialog):

    SLIDERS = [
        ("exposure", "Exposure", -1.0, 1.0),
        ("contrast", "Contrast", -1.0, 1.0),
        ("saturation", "Saturation", -1.0, 1.0),
        ("temperature", "Temperature", -1.0, 1.0),
        ("sharpness", "Sharpness", -1.0, 1.0),
    ]

    def __init__(self, window, doc):
        super().__init__(title=_("Adjust Color"))
        self.window = window
        self.doc = doc
        self.original = doc.image.copy()
        self.values = {key: 0.0 for key, *_rest in self.SLIDERS}
        self.source = None
        self.applied = False
        self.set_content_width(380)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = popover_box(spacing=10, margin=18)

        self.scales = {}
        for key, label, low, high in self.SLIDERS:
            title = Gtk.Label(label=_(label), xalign=0)
            box.append(title)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, 0.01)
            scale.set_value(0)
            scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
            scale.connect("value-changed", self.on_changed, key)
            self.scales[key] = scale
            box.append(scale)

        buttons = Gtk.Box(spacing=8)
        auto = Gtk.Button(label=_("Auto Levels"))
        auto.connect("clicked", self.on_auto)
        buttons.append(auto)
        reset = Gtk.Button(label=_("Reset All"))
        reset.connect("clicked", self.on_reset)
        buttons.append(reset)
        spacer = Gtk.Box(hexpand=True)
        buttons.append(spacer)
        done = Gtk.Button(label=_("Done"))
        done.add_css_class("suggested-action")
        done.connect("clicked", self.on_done)
        buttons.append(done)
        box.append(buttons)

        view.set_content(box)
        self.set_child(view)
        self.connect("closed", self.on_closed)
        self.auto = False

    def on_changed(self, scale, key):
        self.values[key] = scale.get_value()
        if self.source is None:
            self.source = GLib.timeout_add(120, self.apply_preview)

    def on_auto(self, button):
        self.auto = True
        self.apply_preview()

    def on_reset(self, button):
        self.auto = False
        for scale in self.scales.values():
            scale.set_value(0)
        self.apply_preview()

    def adjusted(self):
        image = self.original
        alpha = image.getchannel("A") if image.mode == "RGBA" else None
        image = image.convert("RGB")
        if self.auto:
            image = ImageOps.autocontrast(image, cutoff=0.5)
        values = self.values
        if values["exposure"]:
            image = ImageEnhance.Brightness(image).enhance(2 ** values["exposure"])
        if values["contrast"]:
            image = ImageEnhance.Contrast(image).enhance(1 + values["contrast"])
        if values["saturation"]:
            image = ImageEnhance.Color(image).enhance(1 + values["saturation"])
        if values["temperature"]:
            amount = values["temperature"] * 0.15
            red, green, blue = image.split()
            red = red.point(lambda value: min(255, max(0, value * (1 + amount))))
            blue = blue.point(lambda value: min(255, max(0, value * (1 - amount))))
            image = Image.merge("RGB", (red, green, blue))
        if values["sharpness"]:
            image = ImageEnhance.Sharpness(image).enhance(1 + values["sharpness"] * 2)
        if alpha is not None:
            image.putalpha(alpha)
        return image

    def apply_preview(self):
        self.source = None
        self.doc.image = self.adjusted()
        self.doc.changed()
        self.window.view.queue_draw()
        return False

    def on_done(self, button):
        self.applied = True
        adjusted = self.adjusted()
        self.doc.image = self.original
        self.doc.checkpoint(structure=True)
        self.doc.image = adjusted
        self.doc.changed()
        self.window.after_edit(structure=True)
        self.close()

    def on_closed(self, dialog):
        if self.source is not None:
            GLib.source_remove(self.source)
            self.source = None
        if not self.applied:
            self.doc.image = self.original
            self.doc.changed()
            self.window.view.queue_draw()
