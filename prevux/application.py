import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from .window import PrevuxWindow


class PrevuxApplication(Gtk.Application):

    def __init__(self):
        super().__init__(
            application_id="com.prevux.Prevux"
        )

    def do_activate(self):
        window = self.props.active_window

        if window is None:
            window = PrevuxWindow(self)

        window.present()
