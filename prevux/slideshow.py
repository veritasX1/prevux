"""Slideshow (Preview: View → Slideshow): pages and images full screen, one after another,
on black. The controls fade in when the mouse moves and hide again, together with the
pointer, after a moment – nothing stands between you and the picture."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from .i18n import _

INTERVAL = 4          # seconds per slide while playing
HIDE_AFTER = 2000     # ms until the controls disappear


class Slideshow(Gtk.Window):

    def __init__(self, parent, slides, start=0, on_close=None):
        """slides: [(document, page), …]; on_close(document, page) gets the slide shown last."""
        super().__init__(transient_for=parent, modal=True, decorated=False, title=_("Slideshow"))
        self.add_css_class("slideshow")
        self.slides = slides
        self.index = max(0, min(len(slides) - 1, start))
        self.on_close = on_close
        self.textures = {}
        self.timer = None
        self.hide_source = None
        self.last_pointer = None

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   hexpand=True, vexpand=True)
        overlay = Gtk.Overlay(child=self.picture)

        bar = Gtk.Box(spacing=6, css_classes=["toolbar", "osd", "slideshow-controls"])
        self.previous_button = self.control(bar, "go-previous-symbolic", _("Previous"), lambda: self.step(-1))
        self.play_button = self.control(bar, "media-playback-start-symbolic", _("Play"), self.toggle_play)
        self.next_button = self.control(bar, "go-next-symbolic", _("Next"), lambda: self.step(1))
        self.counter = Gtk.Label(css_classes=["numeric"], width_chars=7)
        bar.append(self.counter)
        self.control(bar, "window-close-symbolic", _("End Slideshow"), self.close)
        self.controls = Gtk.Revealer(child=bar, transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                     halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=36)
        overlay.add_overlay(self.controls)
        self.set_child(overlay)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_args: self.step(1))
        self.picture.add_controller(click)
        self.connect("close-request", self.on_close_request)

        self.show_slide()
        # Photos play by themselves; documents wait for you, as in Preview.
        if slides and all(doc.kind == "image" for doc, _page in slides) and len(slides) > 1:
            self.toggle_play()
        self.fullscreen()

    def control(self, bar, icon, tooltip, callback):
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, css_classes=["circular", "flat"])
        button.connect("clicked", lambda _button: (callback(), self.reveal()))
        bar.append(button)
        return button

    # ---- slides ----------------------------------------------

    def texture(self, index):
        if index not in self.textures:
            doc, page = self.slides[index]
            display = self.get_display()
            monitor = display.get_monitor_at_surface(self.get_surface()) if self.get_surface() else None
            geometry = monitor.get_geometry() if monitor else None
            side = max(geometry.width, geometry.height) if geometry else 1920
            side *= monitor.get_scale_factor() if monitor else 1
            self.textures[index] = doc.thumbnail(page, side)
        return self.textures[index]

    def show_slide(self):
        if not self.slides:
            return
        self.picture.set_paintable(self.texture(self.index))
        self.counter.set_label(f"{self.index + 1} / {len(self.slides)}")
        self.previous_button.set_sensitive(self.index > 0)
        self.next_button.set_sensitive(self.index < len(self.slides) - 1)
        # Keep only the neighbours; prepare the next slide while this one is shown.
        self.textures = {i: t for i, t in self.textures.items() if abs(i - self.index) <= 1}
        following = self.index + 1
        if following < len(self.slides):
            GLib.idle_add(lambda: following < len(self.slides) and self.texture(following) and False)

    def step(self, delta):
        index = max(0, min(len(self.slides) - 1, self.index + delta))
        if index == self.index:
            return
        self.index = index
        self.show_slide()

    def toggle_play(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        else:
            if self.index == len(self.slides) - 1:
                self.index = 0               # play again from the start
                self.show_slide()
            self.timer = GLib.timeout_add_seconds(INTERVAL, self.advance)
        self.update_play_button()

    def update_play_button(self):
        playing = self.timer is not None
        self.play_button.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_button.set_tooltip_text(_("Pause") if playing else _("Play"))

    def advance(self):
        self.step(1)
        if self.index >= len(self.slides) - 1:
            self.timer = None                # the last slide stays; returning False ends the timer
            self.update_play_button()
            return False
        return True

    # ---- controls --------------------------------------------

    def reveal(self):
        self.controls.set_reveal_child(True)
        self.set_cursor(None)
        if self.hide_source:
            GLib.source_remove(self.hide_source)
        self.hide_source = GLib.timeout_add(HIDE_AFTER, self.conceal)

    def conceal(self):
        self.hide_source = None
        self.controls.set_reveal_child(False)
        self.set_cursor(Gdk.Cursor.new_from_name("none"))
        return False

    def on_motion(self, _controller, x, y):
        # Fullscreen windows get a motion event without moving; only real moves count.
        if self.last_pointer is not None and (abs(x - self.last_pointer[0]) + abs(y - self.last_pointer[1])) > 2:
            self.reveal()
        self.last_pointer = (x, y)

    def on_key(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.close()
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Page_Down, Gdk.KEY_Return):
            self.step(1)
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_Page_Up, Gdk.KEY_BackSpace):
            self.step(-1)
        elif keyval == Gdk.KEY_Home:
            self.step(-len(self.slides))
        elif keyval == Gdk.KEY_End:
            self.step(len(self.slides))
        elif keyval == Gdk.KEY_space:
            self.toggle_play()
            self.reveal()
        else:
            return False
        return True

    def on_close_request(self, _window):
        for source in (self.timer, self.hide_source):
            if source:
                GLib.source_remove(source)
        self.timer = self.hide_source = None
        if self.on_close and self.slides:
            self.on_close(*self.slides[self.index])
        return False
