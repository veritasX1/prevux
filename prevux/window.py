import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import (
    Gtk,
    Gio,
    GLib,
    GdkPixbuf
)

from PIL import ImageDraw, ImageFont

from .canvas import ZoomCanvas
from .document import Document
from .image_document import ImageDocument
from .pdf_document import PDFDocument
from .dialogs import TextDialog


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff"
}


class PrevuxWindow(Gtk.ApplicationWindow):

    def __init__(self, app):

        super().__init__(
            application=app,
            title="Prevux",
            default_width=1200,
            default_height=800
        )

        self.documents = []
        self.current = None
        self.current_index = -1
        self.temp_files = []

        self.build_ui()
        self.install_shortcuts()

    # ========================================================
    # MAIN UI
    # ========================================================

    def build_ui(self):

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL
        )

        self.set_child(root)

        root.append(
            self.build_menu_bar()
        )

        root.append(
            self.build_toolbar()
        )

        self.paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL
        )

        root.append(self.paned)

        # ----------------------------------------------------
        # Sidebar
        # ----------------------------------------------------

        self.sidebar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6
        )

        self.sidebar.set_size_request(
            220,
            -1
        )

        self.sidebar.set_margin_start(8)
        self.sidebar.set_margin_end(8)
        self.sidebar.set_margin_top(8)
        self.sidebar.set_margin_bottom(8)

        title = Gtk.Label(
            label="Pages"
        )

        title.set_xalign(0)

        self.sidebar.append(title)

        self.page_list = Gtk.ListBox()

        scroll = Gtk.ScrolledWindow()

        scroll.set_child(
            self.page_list
        )

        scroll.set_vexpand(True)

        self.sidebar.append(scroll)

        self.paned.set_start_child(
            self.sidebar
        )

        # ----------------------------------------------------
        # Canvas
        # ----------------------------------------------------

        self.canvas = ZoomCanvas()

        self.paned.set_end_child(
            self.canvas
        )

        # Sidebar starts hidden.
        self.sidebar.set_visible(False)

        # ----------------------------------------------------
        # Status bar
        # ----------------------------------------------------

        self.status = Gtk.Label(
            label="Ready"
        )

        self.status.set_xalign(0)

        self.status.set_margin_start(10)
        self.status.set_margin_top(4)
        self.status.set_margin_bottom(4)

        root.append(
            self.status
        )

    # ========================================================
    # MENU BAR
    # ========================================================

    def build_menu_bar(self):

        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=2
        )

        bar.set_margin_start(6)
        bar.set_margin_top(3)
        bar.set_margin_bottom(3)

        # ----------------------------------------------------
        # File
        # ----------------------------------------------------

        self.create_menu(
            bar,
            "File",
            [
                (
                    "Open…",
                    self.open_files
                ),
                (
                    "Save",
                    self.save
                ),
                (
                    "Save As…",
                    self.save_as
                ),
                (
                    "Quit",
                    lambda:
                    self.get_application().quit()
                )
            ]
        )

        # ----------------------------------------------------
        # Edit
        # ----------------------------------------------------

        self.create_menu(
            bar,
            "Edit",
            [
                (
                    "Undo",
                    lambda:
                    self.message(
                        "Undo is planned."
                    )
                ),
                (
                    "Redo",
                    lambda:
                    self.message(
                        "Redo is planned."
                    )
                )
            ]
        )

        # ----------------------------------------------------
        # View
        # ----------------------------------------------------

        self.create_menu(
            bar,
            "View",
            [
                (
                    "Fit to Window",
                    self.fit
                ),
                (
                    "Actual Size",
                    self.actual_size
                ),
                (
                    "Toggle Sidebar",
                    self.toggle_sidebar
                )
            ]
        )

        # ----------------------------------------------------
        # Tools
        # ----------------------------------------------------

        self.create_menu(
            bar,
            "Tools",
            [
                (
                    "Add Text…",
                    self.add_text
                ),
                (
                    "Crop…",
                    lambda:
                    self.message(
                        "Crop tool is planned."
                    )
                ),
                (
                    "Adjustments…",
                    lambda:
                    self.message(
                        "Adjustments are planned."
                    )
                )
            ]
        )

        # ----------------------------------------------------
        # Annotate
        # ----------------------------------------------------

        self.create_menu(
            bar,
            "Annotate",
            [
                (
                    "Highlight",
                    lambda:
                    self.message(
                        "PDF highlighting is planned."
                    )
                ),
                (
                    "Underline",
                    lambda:
                    self.message(
                        "PDF underline is planned."
                    )
                ),
                (
                    "Strikeout",
                    lambda:
                    self.message(
                        "PDF strikeout is planned."
                    )
                ),
                (
                    "Text Note",
                    lambda:
                    self.message(
                        "PDF notes are planned."
                    )
                )
            ]
        )

        return bar

    def create_menu(
        self,
        bar,
        title,
        entries
    ):

        button = Gtk.MenuButton(
            label=title
        )

        popover = Gtk.Popover()

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2
        )

        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        for label, callback in entries:

            item = Gtk.Button(
                label=label
            )

            item.set_has_frame(False)

            item.connect(
                "clicked",
                lambda _button,
                function=callback:
                function()
            )

            box.append(item)

        popover.set_child(
            box
        )

        button.set_popover(
            popover
        )

        bar.append(button)

    # ========================================================
    # TOOLBAR
    # ========================================================

    def build_toolbar(self):

        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=5
        )

        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)

        self.add_toolbar_button(
            bar,
            "−",
            "Zoom Out",
            lambda:
            self.zoom(-1)
        )

        self.zoom_label = Gtk.Label(
            label="Fit"
        )

        self.zoom_label.set_width_chars(
            6
        )

        bar.append(
            self.zoom_label
        )

        self.add_toolbar_button(
            bar,
            "+",
            "Zoom In",
            lambda:
            self.zoom(1)
        )

        self.add_toolbar_button(
            bar,
            "Fit",
            "Fit to Window",
            self.fit
        )

        self.add_toolbar_button(
            bar,
            "100%",
            "Actual Size",
            self.actual_size
        )

        self.add_toolbar_button(
            bar,
            "Text",
            "Add Text",
            self.add_text
        )

        self.add_toolbar_button(
            bar,
            "Sidebar",
            "Toggle Sidebar",
            self.toggle_sidebar
        )

        return bar

    def add_toolbar_button(
        self,
        bar,
        label,
        tooltip,
        callback
    ):

        button = Gtk.Button(
            label=label
        )

        button.set_tooltip_text(
            tooltip
        )

        button.connect(
            "clicked",
            lambda _button:
            callback()
        )

        bar.append(button)

    # ========================================================
    # KEYBOARD SHORTCUTS
    # ========================================================

    def install_shortcuts(self):

        controller = Gtk.ShortcutController()

        shortcuts = [
            (
                "<Control>o",
                self.open_files
            ),
            (
                "<Control>s",
                self.save
            ),
            (
                "<Control>0",
                self.actual_size
            ),
            (
                "<Control>1",
                self.fit
            )
        ]

        for key, callback in shortcuts:

            controller.add_shortcut(
                Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(
                        key
                    ),
                    Gtk.CallbackAction.new(
                        lambda *args,
                        function=callback:
                        function()
                    )
                )
            )

        self.add_controller(
            controller
        )

    # ========================================================
    # OPEN FILES
    # ========================================================

    def open_files(self):

        dialog = Gtk.FileDialog()

        dialog.set_title(
            "Open"
        )

        filters = Gio.ListStore.new(
            Gtk.FileFilter
        )

        file_filter = Gtk.FileFilter()

        file_filter.set_name(
            "Images and PDFs"
        )

        patterns = [
            "*.jpg",
            "*.jpeg",
            "*.png",
            "*.webp",
            "*.bmp",
            "*.gif",
            "*.tif",
            "*.tiff",
            "*.pdf"
        ]

        for pattern in patterns:

            file_filter.add_pattern(
                pattern
            )

        filters.append(
            file_filter
        )

        dialog.set_filters(
            filters
        )

        dialog.open_multiple(
            self,
            None,
            self.files_selected
        )

    def files_selected(
        self,
        dialog,
        result
    ):

        try:

            files = (
                dialog
                .open_multiple_finish(
                    result
                )
            )

        except GLib.Error:

            return

        if not files:

            return

        self.documents = []

        for index in range(
            files.get_n_items()
        ):

            path = (
                files
                .get_item(index)
                .get_path()
            )

            if path:

                self.documents.append(
                    Document(path)
                )

        if self.documents:

            self.load_document(0)

    # ========================================================
    # LOAD DOCUMENT
    # ========================================================

    def load_document(
        self,
        index
    ):

        if not (
            0 <= index
            < len(self.documents)
        ):

            return

        self.current_index = index

        document = (
            self.documents[index]
        )

        try:

            if document.suffix == ".pdf":

                self.current = PDFDocument(
                    document.path
                )

                self.load_pdf()

            elif (
                document.suffix
                in IMAGE_EXTENSIONS
            ):

                self.current = ImageDocument(
                    document.path
                )

                self.load_image()

            else:

                return

            self.update_sidebar()

            self.set_title(
                f"{document.name} — Prevux"
            )

        except Exception as error:

            self.show_error(
                str(error)
            )

    # ========================================================
    # IMAGE
    # ========================================================

    def load_image(self):

        temp = tempfile.NamedTemporaryFile(
            suffix=".png",
            delete=False
        )

        temp.close()

        self.temp_files.append(
            temp.name
        )

        self.current.image.save(
            temp.name,
            "PNG"
        )

        pixbuf = (
            GdkPixbuf.Pixbuf
            .new_from_file(
                temp.name
            )
        )

        self.canvas.set_pixbuf(
            pixbuf
        )

        self.canvas.fit()

        self.zoom_label.set_text(
            "Fit"
        )

        self.message(
            f"{self.current.image.width} × "
            f"{self.current.image.height} px"
        )

    # ========================================================
    # PDF
    # ========================================================

    def load_pdf(self):

        self.canvas.set_png(
            self.current.render()
        )

        self.canvas.fit()

        self.zoom_label.set_text(
            "Fit"
        )

        self.message(
            f"Page "
            f"{self.current.page_index + 1}"
            f" / "
            f"{self.current.page_count}"
        )

    # ========================================================
    # SIDEBAR
    # ========================================================

    def update_sidebar(self):

        while True:

            row = (
                self.page_list
                .get_row_at_index(0)
            )

            if row is None:

                break

            self.page_list.remove(
                row
            )

        show_sidebar = (
            len(self.documents) > 1
        )

        # ----------------------------------------------------
        # PDF pages
        # ----------------------------------------------------

        if (
            isinstance(
                self.current,
                PDFDocument
            )
            and
            self.current.page_count > 1
        ):

            show_sidebar = True

            for index in range(
                self.current.page_count
            ):

                row = Gtk.ListBoxRow()

                row.set_child(
                    Gtk.Label(
                        label=
                        f"Page {index + 1}"
                    )
                )

                row.connect(
                    "activate",
                    lambda _row,
                    page=index:
                    self.select_pdf_page(
                        page
                    )
                )

                self.page_list.append(
                    row
                )

        # ----------------------------------------------------
        # Multiple documents
        # ----------------------------------------------------

        if len(self.documents) > 1:

            for index, document in enumerate(
                self.documents
            ):

                row = Gtk.ListBoxRow()

                row.set_child(
                    Gtk.Label(
                        label=document.name
                    )
                )

                row.connect(
                    "activate",
                    lambda _row,
                    item=index:
                    self.load_document(
                        item
                    )
                )

                self.page_list.append(
                    row
                )

        self.sidebar.set_visible(
            show_sidebar
        )

    def select_pdf_page(
        self,
        index
    ):

        if not isinstance(
            self.current,
            PDFDocument
        ):

            return

        self.current.page_index = index

        self.load_pdf()

    def toggle_sidebar(self):

        self.sidebar.set_visible(
            not self.sidebar.get_visible()
        )

    # ========================================================
    # ZOOM
    # ========================================================

    def zoom(
        self,
        direction
    ):

        self.canvas.step_zoom(
            direction
        )

        self.zoom_label.set_text(
            f"{round(self.canvas.zoom * 100)}%"
        )

    def fit(self):

        self.canvas.fit()

        self.zoom_label.set_text(
            "Fit"
        )

    def actual_size(self):

        self.canvas.actual()

        self.zoom_label.set_text(
            "100%"
        )

    # ========================================================
    # TEXT TOOL
    # ========================================================

    def add_text(self):

        if not isinstance(
            self.current,
            ImageDocument
        ):

            self.message(
                "Text editing currently targets images."
            )

            return

        dialog = TextDialog(
            self
        )

        dialog.connect(
            "response",
            self.text_response
        )

        dialog.present()

    def text_response(
        self,
        dialog,
        response
    ):

        if (
            response
            == Gtk.ResponseType.OK
            and
            dialog.text.get_text()
        ):

            text = (
                dialog.text.get_text()
            )

            image = (
                self.current.image
            )

            draw = ImageDraw.Draw(
                image
            )

            size = int(
                dialog.size.get_value()
            )

            try:

                font = ImageFont.truetype(
                    "/usr/share/fonts/"
                    "truetype/dejavu/"
                    "DejaVuSans.ttf",
                    size
                )

            except Exception:

                font = ImageFont.load_default()

            x = 50
            y = 50

            # ------------------------------------------------
            # Background
            # ------------------------------------------------

            if dialog.background.get_active():

                bounds = draw.textbbox(
                    (x, y),
                    text,
                    font=font
                )

                padding = max(
                    6,
                    size // 5
                )

                draw.rounded_rectangle(
                    (
                        bounds[0] - padding,
                        bounds[1] - padding,
                        bounds[2] + padding,
                        bounds[3] + padding
                    ),
                    radius=max(
                        2,
                        padding // 2
                    ),
                    fill=(0, 0, 0, 170)
                )

            # ------------------------------------------------
            # Outline
            # ------------------------------------------------

            if dialog.outline.get_active():

                outline_width = int(
                    dialog
                    .outline_width
                    .get_value()
                )

            else:

                outline_width = 0

            # ------------------------------------------------
            # Text
            # ------------------------------------------------

            draw.text(
                (x, y),
                text,
                font=font,
                fill="white",
                stroke_width=outline_width,
                stroke_fill="black"
            )

            # ------------------------------------------------
            # Underline
            # ------------------------------------------------

            if dialog.underline.get_active():

                bounds = draw.textbbox(
                    (x, y),
                    text,
                    font=font
                )

                draw.line(
                    (
                        bounds[0],
                        bounds[3] + 3,
                        bounds[2],
                        bounds[3] + 3
                    ),
                    fill="white",
                    width=max(
                        1,
                        size // 15
                    )
                )

            self.load_image()

            self.message(
                "Text added."
            )

        dialog.destroy()

    # ========================================================
    # SAVE
    # ========================================================

    def save(self):

        if self.current is None:

            self.message(
                "Nothing to save."
            )

            return

        try:

            self.current.save()

            self.message(
                "Saved."
            )

        except Exception as error:

            self.show_error(
                str(error)
            )

    def save_as(self):

        if self.current is None:

            return

        dialog = Gtk.FileDialog()

        dialog.set_title(
            "Save As…"
        )

        dialog.save(
            self,
            None,
            self.save_as_finished
        )

    def save_as_finished(
        self,
        dialog,
        result
    ):

        try:

            file = (
                dialog
                .save_finish(
                    result
                )
            )

        except GLib.Error:

            return

        if file is None:

            return

        try:

            self.current.save(
                file.get_path()
            )

            self.message(
                "Saved."
            )

        except Exception as error:

            self.show_error(
                str(error)
            )

    # ========================================================
    # STATUS / ERROR
    # ========================================================

    def message(
        self,
        text
    ):

        self.status.set_text(
            text
        )

    def show_error(
        self,
        text
    ):

        print(
            "Prevux:",
            text
        )

        dialog = Gtk.AlertDialog(
            message=text
        )

        dialog.show(
            self
        )

