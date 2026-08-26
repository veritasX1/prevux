import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk

from .text_box import TextBox


class TextTool:

    def __init__(self, window):

        self.window = window

        self.active = False
        self.controller = None

    # ======================================================
    # ACTIVATE
    # ======================================================

    def activate(self):

        self.active = True

        canvas = self.window.canvas

        canvas.add_css_class(
            "text-mode"
        )

        canvas.set_cursor(
            Gdk.Cursor.new_from_name(
                "text",
                None
            )
        )

        self.install_click_handler()

        self.window.message(
            "Click where you want to place text."
        )

    # ======================================================
    # DEACTIVATE
    # ======================================================

    def deactivate(self):

        self.active = False

        canvas = self.window.canvas

        canvas.remove_css_class(
            "text-mode"
        )

        canvas.set_cursor(
            None
        )

    # ======================================================
    # CANVAS CONTROLLER
    # ======================================================

    def install_click_handler(self):

        if self.controller is not None:
            return

        self.controller = Gtk.GestureClick()

        self.controller.set_button(1)

        self.controller.set_propagation_phase(
            Gtk.PropagationPhase.BUBBLE
        )

        self.controller.connect(
            "pressed",
            self.on_canvas_click
        )

        self.window.canvas.add_controller(
            self.controller
        )

    # ======================================================
    # CLICK
    # ======================================================

    def on_canvas_click(
        self,
        gesture,
        n_press,
        x,
        y
    ):

        # --------------------------------------------------
        # Text tool is only used to CREATE one new object.
        # --------------------------------------------------

        if not self.active:
            return

        canvas = self.window.canvas

        widget = canvas.pick(
            x,
            y,
            Gtk.PickFlags.DEFAULT
        )

        current = widget

        while current is not None:

            if isinstance(
                current,
                TextBox
            ):

                # Existing object.
                #
                # Do not create another one.
                # Just activate its editor.

                current.select()
                current.begin_edit()

                self.window.active_text_box = (
                    current
                )

                self.deactivate()

                return

            current = current.get_parent()

        # --------------------------------------------------
        # NEW TEXTBOX
        # --------------------------------------------------

        textbox = TextBox()

        textbox.set_size_request(
            240,
            50
        )

        canvas.add_text_box(
            textbox,
            x,
            y
        )

        # --------------------------------------------------
        # Select immediately
        # --------------------------------------------------

        self.select_textbox(
            textbox
        )

        # --------------------------------------------------
        # Immediately enter editing mode
        # --------------------------------------------------

        textbox.begin_edit()

        self.window.active_text_box = (
            textbox
        )

        self.window.unsaved_changes = True

        # --------------------------------------------------
        # One-shot creation mode
        # --------------------------------------------------

        self.deactivate()

        self.window.message(
            "Type text. Select text to format it."
        )

    # ======================================================
    # SELECT
    # ======================================================

    def select_textbox(
        self,
        textbox
    ):

        for item in (
            self.window.canvas
            .get_text_boxes()
        ):

            if item is textbox:

                item.select()

            else:

                item.deselect()

        self.window.active_text_box = (
            textbox
        )

        if hasattr(
            self.window,
            "show_text_toolbar"
        ):

            self.window.show_text_toolbar(
                textbox
            )

    # ======================================================
    # FINISH
    # ======================================================

    def finish(self):

        self.deactivate()
