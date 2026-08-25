import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class TextDialog(Gtk.Dialog):

    def __init__(self, parent):

        super().__init__(
            title="Add Text",
            transient_for=parent,
            modal=True
        )

        self.add_button(
            "Cancel",
            Gtk.ResponseType.CANCEL
        )

        self.add_button(
            "Add",
            Gtk.ResponseType.OK
        )

        box = self.get_content_area()

        box.set_spacing(8)

        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)

        self.text = Gtk.Entry()

        self.text.set_placeholder_text(
            "Text"
        )

        box.append(
            self.text
        )

        self.size = Gtk.SpinButton.new_with_range(
            6,
            500,
            1
        )

        self.size.set_value(32)

        box.append(
            self.size
        )

        self.bold = Gtk.CheckButton(
            label="Bold"
        )

        self.italic = Gtk.CheckButton(
            label="Italic"
        )

        self.underline = Gtk.CheckButton(
            label="Underline"
        )

        self.background = Gtk.CheckButton(
            label="Background"
        )

        self.outline = Gtk.CheckButton(
            label="Outline"
        )

        for widget in (
            self.bold,
            self.italic,
            self.underline,
            self.background,
            self.outline
        ):
            box.append(widget)

        self.outline_width = Gtk.SpinButton.new_with_range(
            1,
            20,
            1
        )

        self.outline_width.set_value(2)

        box.append(
            self.outline_width
        )

        self.text.grab_focus()
