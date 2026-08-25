import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib


class ZoomCanvas(Gtk.ScrolledWindow):

    def __init__(self):

        super().__init__()

        self.set_hexpand(True)
        self.set_vexpand(True)

        self.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC
        )

        self.picture = Gtk.Picture()

        self.picture.set_content_fit(
            Gtk.ContentFit.FILL
        )

        self.picture.set_can_shrink(False)

        self.picture.set_halign(
            Gtk.Align.START
        )

        self.picture.set_valign(
            Gtk.Align.START
        )

        self.set_child(self.picture)

        self.pixbuf = None
        self.zoom = 1.0
        self.fit_mode = True

        # ----------------------------------------------------
        # Mouse position
        #
        # GTK4/Wayland does not provide the old
        # GdkDevice.get_position() API.
        # We therefore track the pointer with GTK4 directly.
        # ----------------------------------------------------

        self.mouse_x = 0.0
        self.mouse_y = 0.0

        motion = Gtk.EventControllerMotion()

        motion.connect(
            "motion",
            self.on_motion
        )

        motion.connect(
            "enter",
            self.on_enter
        )

        motion.connect(
            "leave",
            self.on_leave
        )

        self.add_controller(
            motion
        )

        # ----------------------------------------------------
        # Ctrl + mouse wheel = zoom
        # Normal mouse wheel = scrolling
        # ----------------------------------------------------

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )

        scroll.connect(
            "scroll",
            self.on_scroll
        )

        self.add_controller(
            scroll
        )

    # ========================================================
    # POINTER
    # ========================================================

    def on_enter(
        self,
        controller,
        x,
        y
    ):

        self.mouse_x = x
        self.mouse_y = y

    def on_motion(
        self,
        controller,
        x,
        y
    ):

        self.mouse_x = x
        self.mouse_y = y

    def on_leave(
        self,
        controller
    ):

        pass

    # ========================================================
    # IMAGE
    # ========================================================

    def set_pixbuf(
        self,
        pixbuf
    ):

        self.pixbuf = pixbuf
        self.render()

    def set_png(
        self,
        data
    ):

        loader = GdkPixbuf.PixbufLoader()

        loader.write(data)
        loader.close()

        pixbuf = loader.get_pixbuf()

        if pixbuf is not None:

            self.set_pixbuf(
                pixbuf
            )

    def render(self):

        if self.pixbuf is None:
            return

        width = max(
            1,
            round(
                self.pixbuf.get_width()
                * self.zoom
            )
        )

        height = max(
            1,
            round(
                self.pixbuf.get_height()
                * self.zoom
            )
        )

        if (
            width == self.pixbuf.get_width()
            and
            height == self.pixbuf.get_height()
        ):

            scaled = self.pixbuf

        else:

            scaled = self.pixbuf.scale_simple(
                width,
                height,
                GdkPixbuf.InterpType.BILINEAR
            )

        self.picture.set_pixbuf(
            scaled
        )

        self.picture.set_size_request(
            width,
            height
        )

        self.picture.queue_resize()

    # ========================================================
    # FIT TO WINDOW
    # ========================================================

    def fit(self):

        if self.pixbuf is None:
            return

        viewport_width = self.get_width()
        viewport_height = self.get_height()

        image_width = self.pixbuf.get_width()
        image_height = self.pixbuf.get_height()

        if (
            viewport_width <= 1
            or
            viewport_height <= 1
        ):

            GLib.idle_add(
                self.fit
            )

            return

        zoom_x = (
            viewport_width - 20
        ) / image_width

        zoom_y = (
            viewport_height - 20
        ) / image_height

        self.zoom = max(
            0.05,
            min(
                5.0,
                zoom_x,
                zoom_y
            )
        )

        self.fit_mode = True

        self.picture.set_halign(
            Gtk.Align.CENTER
        )

        self.picture.set_valign(
            Gtk.Align.CENTER
        )

        self.render()

    # ========================================================
    # ACTUAL SIZE
    # ========================================================

    def actual(self):

        if self.pixbuf is None:
            return

        self.zoom = 1.0
        self.fit_mode = False

        self.picture.set_halign(
            Gtk.Align.START
        )

        self.picture.set_valign(
            Gtk.Align.START
        )

        self.render()

    # ========================================================
    # ZOOM AT MOUSE
    # ========================================================

    def set_zoom_at(
        self,
        new_zoom,
        pointer
    ):

        if self.pixbuf is None:
            return

        old_zoom = self.zoom

        new_zoom = max(
            0.05,
            min(
                5.0,
                new_zoom
            )
        )

        if abs(
            new_zoom - old_zoom
        ) < 0.000001:

            return

        horizontal = (
            self.get_hadjustment()
        )

        vertical = (
            self.get_vadjustment()
        )

        pointer_x, pointer_y = pointer

        # ----------------------------------------------------
        # Position in image coordinates before zoom
        # ----------------------------------------------------

        image_x = (
            horizontal.get_value()
            + pointer_x
        ) / old_zoom

        image_y = (
            vertical.get_value()
            + pointer_y
        ) / old_zoom

        self.zoom = new_zoom
        self.fit_mode = False

        self.picture.set_halign(
            Gtk.Align.START
        )

        self.picture.set_valign(
            Gtk.Align.START
        )

        self.render()

        # ----------------------------------------------------
        # Restore the position so the point under the mouse
        # remains under the mouse after zooming.
        # ----------------------------------------------------

        def restore_position():

            target_x = (
                image_x * new_zoom
                - pointer_x
            )

            target_y = (
                image_y * new_zoom
                - pointer_y
            )

            maximum_x = max(
                horizontal.get_lower(),
                horizontal.get_upper()
                - horizontal.get_page_size()
            )

            maximum_y = max(
                vertical.get_lower(),
                vertical.get_upper()
                - vertical.get_page_size()
            )

            horizontal.set_value(
                max(
                    horizontal.get_lower(),
                    min(
                        target_x,
                        maximum_x
                    )
                )
            )

            vertical.set_value(
                max(
                    vertical.get_lower(),
                    min(
                        target_y,
                        maximum_y
                    )
                )
            )

            return False

        GLib.idle_add(
            restore_position
        )

    # ========================================================
    # ZOOM STEP
    # ========================================================

    def step_zoom(
        self,
        direction,
        pointer=None
    ):

        if self.pixbuf is None:
            return

        if pointer is None:

            pointer = (
                self.mouse_x,
                self.mouse_y
            )

        factor = (
            1.15
            if direction > 0
            else 1 / 1.15
        )

        self.set_zoom_at(
            self.zoom * factor,
            pointer
        )

    # ========================================================
    # SCROLL
    # ========================================================

    def on_scroll(
        self,
        controller,
        dx,
        dy
    ):

        state = (
            controller
            .get_current_event_state()
        )

        # ----------------------------------------------------
        # Without Ctrl:
        # normal scrolling / PDF navigation remains normal.
        # ----------------------------------------------------

        if not (
            state
            & Gdk.ModifierType.CONTROL_MASK
        ):

            return False

        # ----------------------------------------------------
        # With Ctrl:
        # zoom around mouse pointer.
        # ----------------------------------------------------

        pointer = (
            self.mouse_x,
            self.mouse_y
        )

        if dy < 0:

            self.step_zoom(
                1,
                pointer
            )

        elif dy > 0:

            self.step_zoom(
                -1,
                pointer
            )

        return True
