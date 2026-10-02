"""The Prevux document window."""

import os
import shutil
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
    duplicate,
    is_locked,
    set_locked,
    pil_to_surface,
    open_document,
)
from . import settings, versions
from .i18n import _, decimal
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


def human_size(size):
    """1,4 MB – sizes as people read them."""
    for unit in ("Bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            text = f"{size:.0f}" if unit in ("Bytes", "KB") else f"{size:.1f}".replace(".", ",")
            return f"{text} {unit}"
        size /= 1024


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
        self.sidebar.connect("bookmark-activated", lambda _sidebar, page: self.go_page(page))
        self.sidebar.connect("bookmark-removed", lambda _sidebar, page: self.set_bookmark(page, False))
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

        # View → Customize Toolbar: each optional item sits in its own box that the setting
        # shows or hides (the breakpoints below hide the inner widgets when space runs out).
        self.toolbar_items = {}
        for key, widget in (("search", self.search_slot), ("markup", self.markup_button),
                            ("rotate", rotate), ("highlight", highlight_slot), ("share", share),
                            ("zoom", zoom_box), ("info", info)):
            holder = Gtk.Box()
            holder.append(widget)
            self.toolbar_items[key] = holder
            end_items[end_items.index(widget)] = holder
        # The compact search button belongs to "search" as well.
        compact_holder = Gtk.Box()
        compact_holder.append(self.compact_search_slot)
        end_items[end_items.index(self.compact_search_slot)] = compact_holder
        self.toolbar_items["search-compact"] = compact_holder

        # One row for title and items: the title shrinks with an ellipsis
        # instead of sliding under the buttons.
        bar = Gtk.Box(spacing=6, hexpand=True)
        for widget in start_items:
            bar.append(widget)
        titles.set_hexpand(True)
        for widget in reversed(end_items):
            bar.append(widget)
        header.set_title_widget(bar)
        self.apply_toolbar_settings()

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

        # Documents as tabs (Preview: window tabs). The pages only carry the titles;
        # the document area below is shared and shows the selected tab's document.
        self.tab_view = Adw.TabView()
        self.tab_pages = []
        self.syncing_tabs = False
        self.tab_view.connect("notify::selected-page", self.on_tab_selected)
        self.tab_view.connect("close-page", self.on_tab_close)
        self.tab_view.connect("page-reordered", self.on_tab_reordered)
        self.tab_view.connect("create-window", self.on_tab_create_window)
        self.tab_bar = Adw.TabBar(view=self.tab_view, autohide=True)
        content_view.add_top_bar(self.tab_bar)

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
                    (_("Duplicate"), "win.duplicate", "<Control><Shift>d"),
                    (_("Rename…"), "win.rename"),
                    (_("Move To…"), "win.move-to"),
                    (_("Revert To…"), "win.revert"),
                    (_("Lock"), "win.lock"),
                    (_("Export…"), "win.export", "<Control><Shift>s"),
                    (_("Export as PDF…"), "win.export-pdf"),
                    (_("Export with Filter…"), "win.export-filtered"),
                    (_("Edit Permissions…"), "win.edit-permissions"),
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
                    (_("Bookmarks"), "win.show-bookmarks", "<Control><Alt>5"),
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
                    (_("Customize Toolbar…"), "win.customize-toolbar"),
                    (_("Soft Proof with Profile…"), "win.soft-proof"),
                    (_("Slideshow"), "win.slideshow", "<Control><Shift>f"),
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
                    (_("Remove Background"), "win.remove-background", "<Control><Shift>k"),
                ),
                section((_("Add Bookmark"), "win.bookmark", "<Control>d")),
                section(
                    (_("Highlight Text"), "win.highlight", "<Control><Shift>h"),
                    (_("Crop"), "win.crop", "<Control>k"),
                    (_("Adjust Color…"), "win.adjust-color", "<Control><Alt>c"),
                    (_("Adjust Size…"), "win.adjust-size"),
                    (_("Assign Profile…"), "win.assign-profile"),
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

        menus.append(submenu(
            _("Window"),
            section(
                (_("Show Previous Tab"), "win.previous-tab", "<Control><Shift>Tab"),
                (_("Show Next Tab"), "win.next-tab", "<Control>Tab"),
                (_("Move Tab to New Window"), "win.move-tab-to-window"),
                (_("Merge All Windows"), "win.merge-windows"),
            ),
        ))

        menu = Gio.Menu()
        top = Gio.Menu()
        for label, submenu_model in menus:
            top.append_submenu(label, submenu_model)
        menu.append_section(None, top)
        menu.append_section(
            None,
            section(
                (_("Settings…"), "app.preferences", "<Control>comma"),
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
            "close": self.close_tab_or_window,
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
            "show-bookmarks": lambda: self.show_sidebar_mode("bookmarks"),
            "zoom-selection": self.zoom_to_selection,
            "zoom-level": self.ask_zoom_level,
            "actual-size": lambda: self.view.set_zoom(settings.actual_size_zoom(self, self.doc)),
            "zoom-fit": lambda: self.view.zoom_to_fit("page"),
            "zoom-width": lambda: self.view.zoom_to_fit("width"),
            "zoom-in": lambda: self.view.zoom_step(1),
            "zoom-out": lambda: self.view.zoom_step(-1),
            "markup": lambda: self.markup_button.set_active(not self.markup_button.get_active()),
            "fullscreen": self.toggle_fullscreen,
            "slideshow": self.start_slideshow,
            "duplicate": self.duplicate_document,
            "soft-proof": self.choose_soft_proof,
            "assign-profile": self.choose_profile,
            "rename": self.rename_document,
            "move-to": self.move_document,
            "revert": self.show_versions,
            "customize-toolbar": lambda: self.get_application().show_preferences("toolbar"),
            "next-tab": lambda: self.cycle_tab(1),
            "previous-tab": lambda: self.cycle_tab(-1),
            "move-tab-to-window": self.move_tab_to_window,
            "merge-windows": self.merge_windows,
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
            "remove-background": self.remove_background,
            "edit-permissions": self.edit_permissions,
            "export-filtered": self.export_filtered,
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

        lock = Gio.SimpleAction.new_stateful("lock", None, GLib.Variant.new_boolean(False))
        lock.connect("activate", lambda action, _param: self.set_lock(not action.get_state().get_boolean()))
        self.add_action(lock)
        self.actions["lock"] = lock

        # Checked while the current page is bookmarked; choosing it again removes the bookmark.
        bookmark = Gio.SimpleAction.new_stateful("bookmark", None, GLib.Variant.new_boolean(False))
        bookmark.connect("activate", lambda action, _param: self.set_bookmark(
            self.view.current_page, not action.get_state().get_boolean()))
        self.add_action(bookmark)
        self.actions["bookmark"] = bookmark

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
            "zoom-level", "display-mode", "show-sheet", "slideshow",
            "rotate-left", "rotate-right", "inspector", "add-text", "add-shape", "go-to-page",
            "first-page", "last-page", "previous-page", "next-page", "select-all",
            "paste",
        ):
            self.enable(name, has_doc)
        self.enable("close", True)
        self.enable("undo", has_doc and doc.can_undo())
        self.enable("redo", has_doc and doc.can_redo())
        self.enable("find", pdf)
        self.enable("soft-proof", has_doc)
        self.enable("assign-profile", image)
        on_disk = has_doc and not getattr(doc, "untitled", False) and not getattr(doc, "converted", False)
        self.enable("duplicate", has_doc)
        self.enable("rename", on_disk)
        self.enable("move-to", on_disk)
        self.enable("revert", on_disk)
        self.enable("lock", on_disk)
        self.actions["lock"].set_state(GLib.Variant.new_boolean(bool(has_doc and doc.locked)))
        several = len(self.documents) > 1
        for name in ("next-tab", "previous-tab", "move-tab-to-window"):
            self.enable(name, several)
        self.enable("merge-windows", any(
            isinstance(window, PrevuxWindow) and window is not self
            for window in self.get_application().get_windows()))
        bookmarkable = pdf and not getattr(doc, "untitled", False) and doc.path is not None
        self.enable("bookmark", bookmarkable)
        self.enable("show-bookmarks", bookmarkable)
        self.enable("highlight", pdf)
        self.enable("insert-blank-page", pdf)
        self.enable("insert-from-file", pdf)
        self.enable("delete-pages", pdf and doc.page_count > 1)
        self.enable("flip-horizontal", image)
        self.enable("flip-vertical", image)
        self.enable("adjust-color", image)
        self.enable("adjust-size", image)
        self.enable("crop", has_doc and (self.view.rect_selection is not None or getattr(self.view, "lasso", None) is not None))
        selection = has_doc and (
            self.view.selected is not None or self.view.text_selection is not None
            or self.view.rect_selection is not None or getattr(self.view, "lasso", None) is not None
        )
        self.enable("copy", selection)
        self.enable("cut", has_doc and self.view.selected is not None)
        self.enable("delete", has_doc and (self.view.selected is not None or getattr(self.view, "lasso", None) is not None))
        self.enable("remove-background", image)
        self.enable("edit-permissions", pdf)
        self.enable("export-filtered", has_doc)
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
        if doc.locked:
            title += " — " + _("Locked")
        elif doc.modified:
            title += " — " + _("Edited")
        self.title_label.set_text(title)
        self.set_title(title)

        if getattr(doc, "proof", None):
            from . import colors
            proof_name = colors.name(doc.proof) or Path(doc.proof).stem
        else:
            proof_name = None
        if getattr(doc, "frame_durations", None):
            index = min(self.view.current_page, len(doc.frame_durations) - 1)
            subtitle = _("Frame {page} of {count} · {seconds} s").format(
                page=index + 1, count=doc.page_count, seconds=decimal(doc.frame_durations[index] / 1000, 2))
        elif doc.kind == "pdf":
            subtitle = _("Page {page} of {count}").format(
                page=self.view.current_page + 1, count=doc.page_count,
            )
        else:
            width, height = doc.page_size(0)
            subtitle = f"{width} × {height} px"
        if proof_name:
            subtitle += "  ·  " + _("Soft Proof: {name}").format(name=proof_name)
        if len(self.documents) > 1:
            subtitle += "  ·  " + _("{index} of {count} documents").format(
                index=self.doc_index + 1, count=len(self.documents),
            )
        self.subtitle_label.set_text(subtitle)
        self.subtitle_label.set_visible(True)

        if getattr(self, "tab_pages", None) is not None and len(self.tab_pages) == len(self.documents):
            self.sync_tabs()          # titles and "edited" dots of the tabs
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
            elif self.view.tool in ("sketch", "draw", "note", "redact"):
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

    def watch_lock(self, doc):
        on_disk = not getattr(doc, "untitled", False) and not getattr(doc, "converted", False)
        doc.locked = on_disk and is_locked(doc.path)
        doc.on_locked_edit = self.on_locked_edit

    def add_documents(self, documents):
        for doc in documents:
            self.watch_lock(doc)
        self.documents.extend(documents)
        self.sidebar.set_documents(self.documents)
        self.sync_tabs()
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
        self.apply_opening_settings(doc)
        self.start_live_text(doc)
        self.refresh_bookmarks()
        self.select_tab(index)
        self.update_state()

    # ========================================================
    # TABS
    # ========================================================

    def sync_tabs(self):
        """One tab page per document, titles up to date, the current one selected."""
        self.syncing_tabs = True
        while len(self.tab_pages) > len(self.documents):
            page = self.tab_pages.pop()
            self.tab_view.close_page(page)
        while len(self.tab_pages) < len(self.documents):
            self.tab_pages.append(self.tab_view.append(Gtk.Box()))
        for doc, page in zip(self.documents, self.tab_pages):
            page.set_title(doc.name)
            page.set_tooltip(str(doc.path) if doc.path else doc.name)
            page.set_indicator_icon(Gio.ThemedIcon.new("media-record-symbolic") if doc.modified else None)
            page.set_indicator_tooltip(_("Edited") if doc.modified else "")
        self.syncing_tabs = False
        self.select_tab(self.doc_index)

    def select_tab(self, index):
        if 0 <= index < len(self.tab_pages) and self.tab_view.get_selected_page() is not self.tab_pages[index]:
            self.syncing_tabs = True
            self.tab_view.set_selected_page(self.tab_pages[index])
            self.syncing_tabs = False

    def on_tab_selected(self, view, _param):
        page = view.get_selected_page()
        if not self.syncing_tabs and page in self.tab_pages:
            self.show_document(self.tab_pages.index(page))

    def on_tab_close(self, view, page):
        if self.syncing_tabs:
            view.close_page_finish(page, True)
            return True
        view.close_page_finish(page, False)       # we remove it ourselves, after asking
        if page in self.tab_pages:
            self.close_document(self.tab_pages.index(page))
        return True

    def on_tab_reordered(self, view, page, position):
        old = self.tab_pages.index(page)
        current = self.doc
        self.tab_pages.insert(position, self.tab_pages.pop(old))
        self.documents.insert(position, self.documents.pop(old))
        self.doc_index = self.documents.index(current)
        self.sidebar.set_documents(self.documents)
        self.sidebar.select_page(self.doc_index, self.view.current_page)

    def on_tab_create_window(self, _view):
        return None          # tabs are moved with "Move Tab to New Window"

    def cycle_tab(self, delta):
        if len(self.documents) > 1:
            self.show_document((self.doc_index + delta) % len(self.documents))

    def close_tab_or_window(self):
        if len(self.documents) > 1:
            self.close_document(self.doc_index)
        else:
            self.close()

    def close_document(self, index):
        """Close one document of the window (Preview: close a tab), asking about changes."""
        doc = self.documents[index]
        self.show_document(index)
        self.view.finish_editing()
        if not doc.modified:
            self.remove_document(index)
            return
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

        def answered(_dialog, response):
            if response == "discard":
                self.remove_document(self.documents.index(doc))
            elif response == "save":
                self.save(then=lambda: doc in self.documents and self.remove_document(self.documents.index(doc)))
        dialog.connect("response", answered)
        dialog.present(self)

    def remove_document(self, index):
        """Take a document out of this window (closed, or moved to another window)."""
        self.view.finish_editing()
        doc = self.documents.pop(index)
        if not self.documents:
            self.force_close = True
            self.close()
            return doc
        self.doc_index = -1
        self.sidebar.set_documents(self.documents)
        self.sync_tabs()
        self.show_document(min(index, len(self.documents) - 1))
        return doc

    def move_tab_to_window(self):
        if len(self.documents) < 2:
            return
        doc = self.remove_document(self.doc_index)
        window = self.get_application().new_window()
        window.present()
        window.add_documents([doc])

    def merge_windows(self):
        """Window → Merge All Windows: every other Prevux window becomes tabs here."""
        for window in list(self.get_application().get_windows()):
            if window is self or not isinstance(window, PrevuxWindow) or not window.documents:
                continue
            window.view.finish_editing()
            documents, window.documents = window.documents, []
            window.force_close = True
            window.close()
            for doc in documents:
                doc.settings_applied = True       # keep where it was, no reopening rules
            self.add_documents(documents)
        self.present()

    def apply_toolbar_settings(self):
        hidden = set(settings.get("toolbar_hidden") or [])
        for key, holder in self.toolbar_items.items():
            holder.set_visible(key.split("-")[0] not in hidden)

    def start_live_text(self, doc):
        """Recognise text in images and scanned pages in the background (Live Text)."""
        from . import ocr
        if doc is None or getattr(doc, "ocr_running", False) or not ocr.available():
            return
        pages = doc.pages_needing_ocr()
        if not pages:
            return
        doc.ocr_running = True
        version = doc.version
        import threading

        def work():
            for index in pages:
                try:
                    words = doc.ocr_page(index)
                except Exception as error:
                    print("Prevux: Live Text failed:", error)
                    words = []
                GLib.idle_add(self.live_text_ready, doc, index, words, version)
            GLib.idle_add(finished)

        def finished():
            doc.ocr_running = False
            if doc.version != version and doc.kind == "image":
                self.start_live_text(doc)        # edited while recognising: once more
            return False
        threading.Thread(target=work, daemon=True).start()

    def live_text_ready(self, doc, index, words, version):
        if doc.version != version and doc.kind == "image":
            return False          # the picture was edited meanwhile; a new run follows
        doc.set_ocr(index, words)
        if doc is self.doc:
            self.view.queue_draw()
        return False

    def apply_opening_settings(self, doc):
        """Preview's PDF settings: view mode for first opening, continue at the last page."""
        if doc is None or getattr(doc, "settings_applied", False):
            return
        doc.settings_applied = True
        if doc.kind != "pdf":
            return
        mode = settings.get("pdf_view")
        self.activate_action("win.display-mode", GLib.Variant.new_string(mode))
        page = settings.last_page(doc.path) if settings.get("reopen_last_page") else None
        if page and 0 < page < doc.page_count:
            GLib.timeout_add(250, lambda: self.go_page(page) and False)

    def settings_changed(self):
        self.apply_toolbar_settings()
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
    # DUPLICATE, RENAME, MOVE, REVERT
    # ========================================================

    def set_lock(self, locked):
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        if locked and doc.modified:
            self.toast(_("Save your changes before locking the file."))
            self.update_state()
            return
        try:
            set_locked(doc.path, locked)
        except OSError as error:
            self.show_error(_("The protection could not be changed."), str(error))
            return
        doc.locked = is_locked(doc.path)
        if not locked and doc.locked:
            self.show_error(_("The file stays write-protected."),
                            _("It belongs to someone else or lies in a protected folder."))
        self.update_titles()
        self.update_state()
        self.toast(_("Locked") if doc.locked else _("Unlocked"))

    def on_locked_edit(self, doc):
        """Preview's question when a locked file is about to change."""
        if getattr(self, "lock_dialog", None) is not None or doc not in self.documents:
            return False
        dialog = Adw.AlertDialog(
            heading=_("“{name}” is locked").format(name=doc.name),
            body=_("Locked files cannot be changed. Unlock it, or duplicate it and edit the copy."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("duplicate", _("Duplicate"))
        dialog.add_response("unlock", _("Unlock"))
        dialog.set_response_appearance("unlock", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("unlock")
        dialog.set_close_response("cancel")

        def answered(_dialog, response):
            self.lock_dialog = None
            if response == "unlock":
                self.show_document(self.documents.index(doc))
                self.set_lock(False)
            elif response == "duplicate":
                self.show_document(self.documents.index(doc))
                self.duplicate_document()
        dialog.connect("response", answered)
        self.lock_dialog = dialog
        dialog.present(self)
        return False

    def duplicate_document(self):
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        try:
            copy = duplicate(doc, _("{name} copy").format(name=Path(doc.name).stem))
        except Exception as error:
            self.show_error(_("The document could not be duplicated."), str(error))
            return
        window = self.get_application().new_window()
        window.present()
        window.add_documents([copy])

    def relocate(self, doc, target):
        """Rename or move the file on disk; everything that knows its path follows."""
        old = Path(doc.path)
        target = Path(target)
        if target.exists():
            self.show_error(_("“{name}” already exists there.").format(name=target.name))
            return False
        try:
            shutil.move(str(old), str(target))
        except OSError as error:
            self.show_error(_("The document could not be moved."), str(error))
            return False
        doc.path = str(target)
        settings.path_moved(old, target)
        versions.moved(old, target)
        self.get_application().note_recent(doc.path)
        self.sidebar.set_documents(self.documents)
        self.sidebar.select_page(self.doc_index, self.view.current_page)
        self.update_titles()
        self.update_state()
        return True

    def rename_document(self):
        doc = self.doc
        if doc is None:
            return
        old = Path(doc.path)
        dialog = Adw.AlertDialog(heading=_("Rename"), body=_("The file keeps its place, only the name changes."))
        entry = Gtk.Entry(text=old.stem, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")
        entry.connect("changed", lambda e: dialog.set_response_enabled(
            "rename", bool(e.get_text().strip()) and "/" not in e.get_text()))

        def answered(_dialog, response):
            name = entry.get_text().strip()
            if response != "rename" or not name or name in (old.stem, old.name):
                return
            if not Path(name).suffix or Path(name).suffix.lower() != old.suffix.lower():
                name += old.suffix               # the type stays, like Finder keeps it
            if self.relocate(doc, old.with_name(name)):
                self.toast(_("Renamed to “{name}”").format(name=name))
        dialog.connect("response", answered)
        dialog.present(self)
        entry.grab_focus()
        entry.select_region(0, -1)

    def move_document(self):
        doc = self.doc
        if doc is None:
            return
        dialog = Gtk.FileDialog(title=_("Move To"), accept_label=_("Move"))
        dialog.set_initial_folder(Gio.File.new_for_path(str(Path(doc.path).parent)))

        def chosen(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is None or Path(folder.get_path()) == Path(doc.path).parent:
                return
            if self.relocate(doc, Path(folder.get_path()) / Path(doc.path).name):
                self.toast(_("Moved to “{folder}”").format(folder=Path(folder.get_path()).name))
        dialog.select_folder(self, None, chosen)

    def show_versions(self):
        """Revert To: the saved file, or one of the versions Prevux kept before saving."""
        doc = self.doc
        if doc is None:
            return
        self.view.finish_editing()
        choices = []
        if doc.modified:
            choices.append((Path(doc.path), Path(doc.path).stat().st_mtime, _("Last Saved"), False))
        for path, moment, _size in versions.versions(doc.path):
            choices.append((path, moment, None, True))

        dialog = Adw.Dialog(title=_("Revert To"), content_width=420, content_height=520)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        if not choices:
            view.set_content(Adw.StatusPage(
                icon_name="document-open-recent-symbolic", title=_("No Earlier Versions"),
                description=_("Each time you save, Prevux keeps the previous state here.")))
        else:
            group = Adw.PreferencesGroup(description=_("Restoring opens that state; save to keep it."))
            for path, moment, title, is_version in choices:
                when = GLib.DateTime.new_from_unix_local(int(moment)).format("%d.%m.%Y, %H:%M")
                row = Adw.ActionRow(title=title or when, subtitle=when if title else human_size(path.stat().st_size))
                try:
                    texture = open_document(str(path)).thumbnail(0, 64)
                    picture = Gtk.Picture(paintable=texture, can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
                    picture.set_size_request(48, 48)
                    picture.add_css_class("thumbnail")
                    row.add_prefix(picture)
                except Exception:
                    pass
                button = Gtk.Button(label=_("Restore"), valign=Gtk.Align.CENTER)
                button.connect("clicked", lambda _b, path=path, is_version=is_version:
                               (dialog.close(), self.restore_version(doc, path, is_version)))
                row.add_suffix(button)
                group.add(row)
            page = Adw.PreferencesPage()
            page.add(group)
            view.set_content(page)
        dialog.set_child(view)
        dialog.present(self)

    def restore_version(self, doc, path, is_version):
        if doc not in self.documents:
            return
        try:
            restored = open_document(str(path))
        except Exception as error:
            self.show_error(_("This version could not be opened."), str(error))
            return
        restored.path = doc.path
        restored.modified = is_version          # the saved state is already on disk
        restored.settings_applied = True
        self.watch_lock(restored)
        index = self.documents.index(doc)
        self.documents[index] = restored
        self.doc_index = -1
        self.sidebar.set_documents(self.documents)
        self.show_document(index)
        self.update_titles()
        self.toast(_("Version restored – save to keep it") if is_version else _("Reverted to the last saved state"))

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
        if doc.locked:
            self.on_locked_edit(doc)
            return
        try:
            versions.keep(doc.path)          # the state before this save stays restorable
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
            self.refresh_bookmarks()
        else:
            self.sidebar.refresh_document(self.doc_index, [self.view.current_page])
        self.view.refresh()
        doc.modified = True
        self.update_state()
        if doc.kind == "image":
            GLib.timeout_add(600, lambda: self.start_live_text(doc) and False)

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
        elif getattr(view, "lasso", None) and self.doc.kind == "image":
            # The cut-out object, with a transparent background.
            from .documents import pil_to_texture
            clipboard.set(pil_to_texture(view.lasso["cut"]))
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
        lasso = getattr(self.view, "lasso", None)
        if lasso is not None and self.doc is not None and self.doc.kind == "image":
            self.doc.checkpoint(structure=True)
            self.doc.erase_cutout(lasso["box"], lasso["cut"])
            self.view.lasso = None
            self.after_edit(structure=True)
            self.keep_transparency()
            return
        self.view.delete_selected()
        self.update_state()

    def export_filtered(self):
        """Preview's Quartz filters: a smaller or recoloured copy, the original stays."""
        doc = self.doc
        if doc is None:
            return
        from .documents import FILTERS, export_filtered
        self.view.finish_editing()
        keys = [key for key, (_label, pdf, image, _suffix) in FILTERS.items() if (pdf if doc.kind == "pdf" else image)]
        dialog = Adw.AlertDialog(heading=_("Export with Filter"),
                                 body=_("Creates a copy – the original stays as it is."))
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        group.add_css_class("boxed-list")
        first = None
        buttons = {}
        for key in keys:
            check = Gtk.CheckButton(group=first)
            first = first or check
            row = Adw.ActionRow(title=_(FILTERS[key][0]), activatable_widget=check)
            row.add_prefix(check)
            group.append(row)
            buttons[key] = check
        first.set_active(True)
        dialog.set_extra_child(group)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("export", _("Export…"))
        dialog.set_default_response("export")
        dialog.set_response_appearance("export", Adw.ResponseAppearance.SUGGESTED)

        def chosen_filter(_dialog, response):
            if response != "export":
                return
            key = next(k for k, b in buttons.items() if b.get_active())
            stem = Path(doc.name).stem
            suffix = ".pdf" if doc.kind == "pdf" else (".jpg" if key == "reduce" else Path(doc.name).suffix.lower())
            files = Gtk.FileDialog(title=_("Export with Filter"))
            files.set_initial_name(f"{stem} ({_(FILTERS[key][3])}){suffix}")
            if not getattr(doc, "untitled", False):
                files.set_initial_folder(Gio.File.new_for_path(str(Path(doc.path).parent)))

            def saved(dialog, result):
                try:
                    path = dialog.save_finish(result).get_path()
                except GLib.Error:
                    return
                try:
                    export_filtered(doc, path, key)
                except Exception as error:
                    self.show_error(_("The document could not be exported."), str(error))
                    return
                before = Path(doc.path).stat().st_size if Path(doc.path).exists() else 0
                after = Path(path).stat().st_size
                if before:
                    self.toast(_("Exported – {before} → {after}").format(before=human_size(before), after=human_size(after)))
                else:
                    self.toast(_("Exported"))
            files.save(self, None, saved)
        dialog.connect("response", chosen_filter)
        dialog.present(self)

    def edit_permissions(self):
        doc = self.doc
        if doc is None or doc.kind != "pdf":
            return
        from .permissions import PermissionsDialog

        def apply(protection):
            doc.protection = protection
            doc.modified = True
            self.view.notify_modified()
            self.update_state()
            self.toast(_("The permissions apply when you save the document."))
        PermissionsDialog(doc, apply).present(self)

    def remove_background(self):
        """Preview: Tools → Remove Background (⇧⌘K)."""
        doc = self.doc
        if doc is None or doc.kind != "image":
            return
        doc.checkpoint(structure=True)
        doc.remove_background()
        self.after_edit(structure=True)
        self.keep_transparency()

    def keep_transparency(self):
        """JPEG and BMP cannot store transparency: save next to the original as PNG instead."""
        doc = self.doc
        if doc is None or not doc.needs_alpha_format():
            return
        target = Path(doc.path).with_suffix(".png")
        number = 2
        while target.exists():
            target = Path(doc.path).with_name(f"{Path(doc.path).stem} {number}.png")
            number += 1
        doc.path = str(target)
        doc.format = "PNG"
        self.update_titles()
        self.toast(_("Will be saved as PNG – this file type cannot keep transparency. The original stays."))

    def select_all(self):
        if self.doc is not None and self.doc.kind == "pdf":
            self.view.select_all_text()
        elif self.doc is not None:
            width, height = self.doc.page_size(0)
            self.view.rect_selection = (0, 0, 0, width, height)
            self.view.queue_draw()
        self.update_state()

    def selected_images(self):
        """The images marked in the sidebar (Preview edits them together), else the current one."""
        chosen = []
        for doc_index, _page in self.sidebar.selected_pages():
            doc = self.documents[doc_index]
            if doc.kind == "image" and doc not in chosen:
                chosen.append(doc)
        if len(chosen) > 1:
            return chosen
        return [self.doc] if self.doc is not None and self.doc.kind == "image" else []

    def edit_images(self, docs, function):
        """Apply function(doc) to every image, each with its own undo step."""
        self.view.finish_editing()
        for doc in docs:
            doc.checkpoint(structure=True)
            function(doc)
            doc.modified = True
        if self.doc in docs:
            self.view.rect_selection = None
            self.view.document_structure_changed()
            self.view.refresh()
        for doc in docs:
            self.sidebar.refresh_document(self.documents.index(doc))
        self.update_state()
        self.update_titles()
        for doc in docs:
            GLib.timeout_add(600, lambda doc=doc: self.start_live_text(doc) and False)
        if len(docs) > 1:
            self.toast(_("{count} images changed").format(count=len(docs)))

    def rotate(self, degrees):
        doc = self.doc
        if doc is None:
            return
        images = self.selected_images()
        if len(images) > 1:
            self.edit_images(images, lambda image: image.rotate_page(0, degrees))
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
        images = self.selected_images()
        if images:
            self.edit_images(images, lambda image: image.flip(horizontal))

    def crop(self):
        doc = self.doc
        if doc is None:
            return
        if getattr(self.view, "lasso", None) is not None and doc.kind == "image":
            self.crop_lasso()
            return
        if self.view.rect_selection is None:
            self.toast(_("Select an area with Rectangular Selection first."))
            return
        page, x0, y0, x1, y1 = self.view.rect_selection
        images = self.selected_images() if doc.kind == "image" else []
        if len(images) > 1:
            # The same area (in pixels) is cut from every image, as in Preview.
            box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            self.edit_images(images, lambda image: image.crop(0, box))
            return
        doc.checkpoint(structure=True)
        doc.crop(page, (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        self.view.rect_selection = None
        self.after_edit(structure=True)

    def crop_lasso(self):
        lasso = self.view.lasso
        self.doc.checkpoint(structure=True)
        self.doc.crop_to_cutout(lasso["box"], lasso["cut"])
        self.view.lasso = None
        self.after_edit(structure=True)
        self.keep_transparency()

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

    def remember_page(self, path, page):
        self.page_memory_source = None
        settings.remember_page(path, page)
        return False

    def go_page(self, page):
        doc = self.doc
        if doc is None:
            return
        page = max(0, min(doc.page_count - 1, page))
        self.view.scroll_to_page(page)

    # ========================================================
    # BOOKMARKS
    # ========================================================

    def bookmark_path(self):
        doc = self.doc
        if doc is None or doc.kind != "pdf" or getattr(doc, "untitled", False) or doc.path is None:
            return None
        return doc.path

    def set_bookmark(self, page, on):
        path = self.bookmark_path()
        if path is None:
            return
        items = [item for item in settings.bookmarks(path) if item["page"] != page]
        if on:
            items.append({"page": page, "added": GLib.get_real_time() / 1e6})
        settings.set_bookmarks(path, items)
        self.refresh_bookmarks()
        self.toast(_("Bookmark added") if on else _("Bookmark removed"))

    def refresh_bookmarks(self):
        """Sidebar list, page ribbons and the menu checkmark from the stored bookmarks."""
        path = self.bookmark_path()
        doc = self.doc
        items = [item for item in settings.bookmarks(path) if item["page"] < doc.page_count] if path else []
        outline = doc.outline() if path else []
        rows = []
        for item in items:
            chapters = [title for _level, title, page, _y in outline if page <= item["page"]]
            added = GLib.DateTime.new_from_unix_local(int(item.get("added", 0)))
            subtitle = chapters[-1] if chapters else added.format("%d.%m.%Y, %H:%M")
            rows.append((item["page"], _("Page {page}").format(page=item["page"] + 1), subtitle))
        self.sidebar.set_bookmarks(rows)
        self.view.bookmarks = {item["page"] for item in items}
        self.view.queue_draw()
        self.update_bookmark_state()

    def update_bookmark_state(self):
        action = self.actions.get("bookmark")
        if action is not None:
            marked = self.view.current_page in getattr(self.view, "bookmarks", ())
            action.set_state(GLib.Variant.new_boolean(marked))

    def on_view_page_changed(self, view, page):
        doc = self.doc
        if doc is not None and doc.kind == "pdf" and not getattr(doc, "untitled", False):
            # Remember where you are, a moment after the page stops changing.
            if getattr(self, "page_memory_source", None):
                GLib.source_remove(self.page_memory_source)
            self.page_memory_source = GLib.timeout_add(800, self.remember_page, doc.path, page)
        self.sidebar.select_page(self.doc_index, page)
        self.update_titles()
        self.update_bookmark_state()

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

    def start_slideshow(self):
        """All pages of all open documents, starting at the current one (Preview: Slideshow)."""
        if self.doc is None:
            return
        from .slideshow import Slideshow
        self.view.finish_editing()
        slides, start = [], 0
        for doc in self.documents:
            for page in range(doc.page_count):
                if doc is self.doc and page == self.view.current_page:
                    start = len(slides)
                slides.append((doc, page))

        def finished(doc, page):
            if doc in self.documents:
                self.show_document(self.documents.index(doc))
                self.go_page(page)
        self.slideshow = Slideshow(self, slides, start, finished)
        self.slideshow.present()

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
            if self.view.tool in ("sketch", "draw", "note", "redact"):
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
        if doc.kind == "image":
            rows.append((_("Color Profile"), info.get("profile") or _("None (sRGB assumed)")))
        rows.append((_("Where"), str(Path(info["path"]).parent)))

        for row, (label, value) in enumerate(rows):
            key = Gtk.Label(label=label, xalign=1, yalign=0)
            key.add_css_class("dim-label")
            grid.attach(key, 0, row, 1, 1)
            content = Gtk.Label(label=value, xalign=0, wrap=True, selectable=True, max_width_chars=28)
            content.set_wrap_mode(2)
            grid.attach(content, 1, row, 1, 1)
        box.append(grid)
        location = doc.location() if doc.kind == "image" else None
        if location:
            box.append(self.location_section(doc, location))
        self.info_popover.set_child(box)

    def location_section(self, doc, location):
        """Where the photo was taken. No map is loaded here: only a click hands the place
        to the maps app or the browser, so nothing leaves the computer unasked."""
        latitude, longitude, altitude = location
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        section.set_margin_top(10)
        heading = Gtk.Label(label=_("Location"), xalign=0)
        heading.add_css_class("heading")
        section.append(heading)
        text = "{lat}° {ns}, {lon}° {ew}".format(
            lat=decimal(abs(latitude), 5), ns=_("N") if latitude >= 0 else _("S"),
            lon=decimal(abs(longitude), 5), ew=_("E") if longitude >= 0 else _("W"))
        if altitude is not None:
            text += "\n" + _("Altitude {meters} m").format(meters=decimal(altitude, 0))
        section.append(Gtk.Label(label=text, xalign=0, selectable=True))
        note = Gtk.Label(label=_("Prevux loads no map. A click opens the place in Maps or the browser."),
                         xalign=0, wrap=True, max_width_chars=34)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        section.append(note)

        def launch(uri):
            self.info_popover.popdown()
            Gtk.UriLauncher.new(uri).launch(self, None, launched)

        def launched(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                self.toast(_("No app for maps is installed – try the browser."))

        buttons = Gtk.Box(spacing=6, homogeneous=True)
        maps = Gtk.Button(label=_("Open in Maps"))
        maps.connect("clicked", lambda _b: launch(f"geo:{latitude:.6f},{longitude:.6f}"))
        browser = Gtk.Button(label=_("Show in Browser"))
        browser.set_tooltip_text("OpenStreetMap")
        browser.connect("clicked", lambda _b: launch(
            f"https://www.openstreetmap.org/?mlat={latitude:.6f}&mlon={longitude:.6f}#map=16/{latitude:.6f}/{longitude:.6f}"))
        buttons.append(maps)
        buttons.append(browser)
        section.append(buttons)

        remove = Gtk.Button(label=_("Remove Location Info"))
        remove.add_css_class("destructive-action")
        remove.connect("clicked", lambda _b: self.remove_location(doc))
        section.append(remove)
        return section

    # ---- colour profiles ---------------------------------------

    def profile_dialog(self, heading, body, choices, current, apply, accept):
        """A choice of installed profiles in an alert, as Preview's profile sheets."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        names = [label for label, _value in choices]
        dropdown = Gtk.DropDown(model=Gtk.StringList.new(names), enable_search=True)
        dropdown.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        values = [value for _label, value in choices]
        if current in values:
            dropdown.set_selected(values.index(current))
        dialog.set_extra_child(dropdown)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", accept)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "ok" and apply(values[dropdown.get_selected()]))
        dialog.present(self)

    def choose_soft_proof(self):
        from . import colors
        if self.doc is None:
            return
        kinds = {"CMYK": _("Print"), "GRAY": _("Grayscale"), "RGB": _("Screen")}
        choices = [(_("Off"), None)] + [
            (f"{title} ({kinds[kind]})", str(path))
            for kind in ("CMYK", "GRAY", "RGB")
            for title, path, profile_kind in colors.installed() if profile_kind == kind]

        def apply(path):
            for doc in self.documents:
                if hasattr(doc, "set_proof"):
                    doc.set_proof(path)
            self.view.queue_draw()
            self.update_titles()
        self.profile_dialog(_("Soft Proof with Profile"),
                            _("Shows how the document looks on another device, e.g. a printing press. "
                              "The file is not changed."),
                            choices, getattr(self.doc, "proof", None), apply, _("Show"))

    def choose_profile(self):
        from . import colors
        doc = self.doc
        if doc is None or doc.kind != "image":
            return
        rgb = [(title, path) for title, path, kind in colors.installed() if kind == "RGB"]
        choices = [(_("sRGB (standard)"), None)] + [(title, str(path)) for title, path in rgb
                                                     if "srgb" not in title.lower().replace(" ", "")]
        current = None
        if doc.icc and not colors.is_srgb(doc.icc):
            own = colors.name(doc.icc)
            for title, path in choices[1:]:
                if title == own:
                    current = path
            if current is None:
                choices.insert(1, (own or _("Embedded profile"), "embedded"))
                current = "embedded"

        def apply(path):
            if path == "embedded":
                return
            doc.checkpoint(structure=True)
            doc.assign_profile(None if path is None else Path(path).read_bytes())
            self.after_edit()
        self.profile_dialog(_("Assign Profile"),
                            _("Tells how the colour values of the picture are meant. The pixels stay unchanged; "
                              "the profile is saved with the picture."),
                            choices, current, apply, _("Assign"))

    def remove_location(self, doc):
        self.info_popover.popdown()
        doc.checkpoint(structure=True)
        doc.remove_location()
        doc.modified = True
        self.update_state()
        self.update_titles()
        self.toast(_("Location removed – save to remove it from the file"))

    # ========================================================
    # IMAGE ADJUSTMENTS
    # ========================================================

    def adjust_size(self):
        images = self.selected_images()
        if images:
            AdjustSizeDialog(self, images).present(self)

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
    """Preview's Adjust Size: fit-into presets, pixels/percent/print units, resolution and
    Resample. With several images marked, the same scaling is applied to each of them."""

    PRESETS = [(320, 240), (640, 480), (800, 600), (1024, 768), (1280, 720), (1280, 1024),
               (1920, 1080), (2560, 1440), (3840, 2160)]
    PRESET_NAMES = {(1280, 720): "HD", (1920, 1080): "Full HD", (3840, 2160): "4K"}
    INCH = {"cm": 2.54, "mm": 25.4, "in": 1.0}

    def __init__(self, window, docs):
        super().__init__(title=_("Adjust Size"))
        self.window = window
        self.docs = docs
        self.single = len(docs) == 1
        doc = docs[0]
        self.width, self.height = doc.page_size(0)
        self.original_dpi = doc.resolution()
        self.px_w, self.px_h, self.dpi = float(self.width), float(self.height), self.original_dpi
        self.units = ["px", "%", "cm", "mm", "in"] if self.single else ["%", "px"]
        unit_names = {"px": _("Pixels"), "%": _("Percent"), "cm": _("Centimeters"), "mm": _("Millimeters"),
                      "in": _("Inches")}
        self.box = None             # several images in pixels: the size they are fitted into
        self.updating = False
        self.set_content_width(400)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup()
        names = [_("Custom")] + [
            f"{w} × {h}" + (f" ({self.PRESET_NAMES[(w, h)]})" if (w, h) in self.PRESET_NAMES else "")
            for w, h in self.PRESETS]
        self.preset = Adw.ComboRow(title=_("Fit into"), model=Gtk.StringList.new(names))
        self.preset.connect("notify::selected", self.on_preset)
        group.add(self.preset)
        page.add(group)

        group = Adw.PreferencesGroup(
            description=None if self.single else _("{count} images are changed together.").format(count=len(docs)))
        self.unit = Adw.ComboRow(title=_("Unit"), model=Gtk.StringList.new([unit_names[u] for u in self.units]))
        self.unit.connect("notify::selected", lambda *_args: self.show_values())
        group.add(self.unit)
        self.width_row = Adw.SpinRow.new_with_range(0.01, 100000, 1)
        self.width_row.set_title(_("Width"))
        self.width_row.connect("notify::value", self.on_value, "width")
        group.add(self.width_row)
        self.height_row = Adw.SpinRow.new_with_range(0.01, 100000, 1)
        self.height_row.set_title(_("Height"))
        self.height_row.connect("notify::value", self.on_value, "height")
        group.add(self.height_row)
        self.resolution_row = Adw.SpinRow.new_with_range(1, 10000, 1)
        self.resolution_row.set_title(_("Resolution"))
        self.resolution_row.set_subtitle(_("Pixels per inch"))
        self.resolution_row.connect("notify::value", self.on_resolution)
        self.resolution_row.set_visible(self.single)
        group.add(self.resolution_row)
        self.proportional = Adw.SwitchRow(title=_("Scale proportionally"), active=True)
        self.proportional.connect("notify::active", lambda *_args: self.on_proportional())
        group.add(self.proportional)
        self.resample = Adw.SwitchRow(title=_("Resample image"), active=True,
                                      subtitle=_("Off: only the print size changes, every pixel stays"))
        self.resample.connect("notify::active", lambda *_args: self.show_values())
        self.resample.set_visible(self.single)
        group.add(self.resample)
        page.add(group)

        group = Adw.PreferencesGroup()
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
        self.show_values()

    # ---- units -----------------------------------------------

    def current_unit(self):
        return self.units[self.unit.get_selected()]

    def to_unit(self, pixels, original):
        unit = self.current_unit()
        if unit == "px":
            return pixels
        if unit == "%":
            return pixels / original * 100
        return pixels / self.dpi * self.INCH[unit]

    def show_values(self):
        """Fill the fields from the target size (in the chosen unit)."""
        unit = self.current_unit()
        self.updating = True
        digits = 0 if unit in ("px", "%") else 2
        for row in (self.width_row, self.height_row):
            row.set_digits(digits)
            row.get_adjustment().set_step_increment(1 if digits == 0 else 0.1)
        if not self.single and unit == "px":
            box = self.box or (self.width, self.height)
            self.width_row.set_value(box[0])
            self.height_row.set_value(box[1])
        else:
            self.width_row.set_value(self.to_unit(self.px_w, self.width))
            self.height_row.set_value(self.to_unit(self.px_h, self.height))
        self.resolution_row.set_value(self.dpi)
        # Without resampling the pixels are fixed: only print size and resolution can change.
        fixed = self.single and not self.resample.get_active() and unit in ("px", "%")
        self.width_row.set_sensitive(not fixed)
        self.height_row.set_sensitive(not fixed)
        self.updating = False
        self.update_result()

    # ---- changes ---------------------------------------------

    def on_preset(self, row, _param):
        index = row.get_selected()
        if self.updating or index == 0:
            return
        box = self.PRESETS[index - 1]
        self.resample.set_active(True)
        if self.single:
            scale = min(box[0] / self.width, box[1] / self.height)
            self.px_w, self.px_h = self.width * scale, self.height * scale
        else:
            self.box = box
            self.proportional.set_active(True)
        self.updating = True
        self.unit.set_selected(self.units.index("px"))
        self.updating = False
        self.show_values()

    def on_value(self, row, _param, which):
        if self.updating:
            return
        self.updating = True
        self.preset.set_selected(0)
        self.updating = False
        value = row.get_value()
        unit = self.current_unit()
        original = self.width if which == "width" else self.height
        ratio = self.height / self.width if which == "width" else self.width / self.height
        if not self.single and unit == "px":
            box = list(self.box or (self.width, self.height))
            box[0 if which == "width" else 1] = value
            if self.proportional.get_active():
                box[1 if which == "width" else 0] = value * ratio
            self.box = (max(1, round(box[0])), max(1, round(box[1])))
            self.show_values()
            return
        if unit == "px":
            pixels = value
        elif unit == "%":
            pixels = original * value / 100
        else:
            inches = value / self.INCH[unit]
            if not self.resample.get_active():
                # The pixels stay; a different print size means a different resolution.
                self.dpi = original / max(inches, 0.0001)
                self.show_values()
                return
            pixels = inches * self.dpi
        if which == "width":
            self.px_w = pixels
            if self.proportional.get_active():
                self.px_h = pixels * ratio
        else:
            self.px_h = pixels
            if self.proportional.get_active():
                self.px_w = pixels * ratio
        self.show_values()

    def on_resolution(self, row, _param):
        if self.updating:
            return
        new = row.get_value()
        if self.resample.get_active() and self.current_unit() in self.INCH:
            # Same print size at a different resolution: more or fewer pixels.
            self.px_w *= new / self.dpi
            self.px_h *= new / self.dpi
        self.dpi = new
        self.show_values()

    def on_proportional(self):
        if self.proportional.get_active() and self.single:
            self.px_h = self.px_w * self.height / self.width
            self.show_values()

    # ---- result ----------------------------------------------

    def target(self, doc):
        """Pixel size for one image."""
        width, height = doc.page_size(0)
        if self.single:
            return max(1, round(self.px_w)), max(1, round(self.px_h))
        unit = self.current_unit()
        if unit == "%":
            fx = self.width_row.get_value() / 100
            fy = self.height_row.get_value() / 100 if not self.proportional.get_active() else fx
            return max(1, round(width * fx)), max(1, round(height * fy))
        box = self.box or (self.width, self.height)
        if not self.proportional.get_active():
            return box
        scale = min(box[0] / width, box[1] / height)
        return max(1, round(width * scale)), max(1, round(height * scale))

    def update_result(self):
        if self.single:
            width, height = self.target(self.docs[0])
            cm_w, cm_h = width / self.dpi * 2.54, height / self.dpi * 2.54
            self.result.set_subtitle(
                _("{w} × {h} pixels · {cw} × {ch} cm at {dpi} ppi").format(
                    w=width, h=height, cw=decimal(cm_w), ch=decimal(cm_h), dpi=round(self.dpi)))
        else:
            sizes = [self.target(doc) for doc in self.docs]
            first = sizes[0]
            self.result.set_subtitle(
                _("{count} images, e.g. {w} × {h} pixels").format(count=len(sizes), w=first[0], h=first[1]))

    def on_apply(self, button):
        changes = []
        for doc in self.docs:
            size = self.target(doc)
            dpi = self.dpi if self.single and abs(self.dpi - self.original_dpi) > 0.01 else None
            if tuple(size) != tuple(doc.page_size(0)) or dpi:
                changes.append((doc, size, dpi))
        if changes:
            plan = {id(doc): (size, dpi) for doc, size, dpi in changes}

            def apply(doc):
                size, dpi = plan[id(doc)]
                if tuple(size) != tuple(doc.page_size(0)):
                    doc.resize(*size)
                if dpi:
                    doc.set_resolution(dpi)
            self.window.edit_images([doc for doc, _size, _dpi in changes], apply)
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
