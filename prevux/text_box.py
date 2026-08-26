import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk, Pango, PangoCairo


class TextBox(Gtk.Frame):

    def __init__(self, text=""):

        super().__init__()

        self.selected = False
        self.editing = False

        # Textbox border
        self.border_enabled = False
        self.border_color = (0.21, 0.52, 0.89, 1.0)
        self.border_width = 1

        # Jede Textbox bekommt eine eigene CSS-Klasse.
        # Text outline / stroke
        self.outline_enabled = False
        self.outline_color = (
            0.0,
            0.0,
            0.0,
            1.0
        )
        self.outline_width = 2.0

        # Letzte Textauswahl merken.
        # Die Toolbar kann dem TextView den Fokus wegnehmen,
        # die Auswahl bleibt dadurch trotzdem verfügbar.
        self.saved_selection = None

        # ==================================================
        # TEXT VIEW
        # ==================================================

        # --------------------------------------------------
        # TEXT EDITOR + OUTLINE OVERLAY
        # --------------------------------------------------

        self.overlay = Gtk.Overlay()

        self.text_view = Gtk.TextView()

        self.text_view.set_wrap_mode(
            Gtk.WrapMode.WORD_CHAR
        )

        # Drawing layer used exclusively for the text outline.
        self.outline_area = Gtk.DrawingArea()

        self.outline_area.set_draw_func(
            self.draw_text_outline
        )

        self.outline_area.set_can_target(False)
        self.outline_area.set_visible(True)
        self.outline_area.set_hexpand(True)
        self.outline_area.set_vexpand(True)

        self.overlay.set_child(
            self.text_view
        )

        self.overlay.add_overlay(
            self.outline_area
        )

        self.text_view.set_hexpand(True)
        self.text_view.set_vexpand(True)

        self.text_view.set_editable(True)
        self.text_view.set_cursor_visible(True)

        self.text_view.set_left_margin(5)
        self.text_view.set_right_margin(5)
        self.text_view.set_top_margin(4)
        self.text_view.set_bottom_margin(4)

        self.text_view.add_css_class(
            "prevux-text-editor"
        )

        buffer = self.text_view.get_buffer()

        buffer.set_text(text)

        buffer.connect(
            "changed",
            self.on_text_changed
        )

        self.set_child(
            self.overlay
        )

        self.add_css_class(
            "prevux-textbox"
        )

        # ==================================================
        # FOCUS
        # ==================================================

        focus_controller = Gtk.EventControllerFocus()

        focus_controller.connect(
            "enter",
            self.on_focus_enter
        )

        focus_controller.connect(
            "leave",
            self.on_focus_leave
        )

        self.text_view.add_controller(
            focus_controller
        )

        # ==================================================
        # FRAME CLICK
        #
        # We deliberately do NOT use a drag gesture here.
        # Gtk.TextView must have complete control over:
        #
        # - cursor placement
        # - text selection
        # - double-click word selection
        # - keyboard selection
        # ==================================================

        self.frame_click = Gtk.GestureClick()

        self.frame_click.set_button(1)

        self.frame_click.set_propagation_phase(
            Gtk.PropagationPhase.BUBBLE
        )

        self.frame_click.connect(
            "pressed",
            self.on_frame_pressed
        )

        self.add_controller(
            self.frame_click
        )

    # ======================================================
    # FOCUS
    # ======================================================

    def on_focus_enter(
        self,
        controller
    ):

        self.select()

        self.editing = True

    def on_focus_leave(
        self,
        controller
    ):

        self.save_selection()

        self.editing = False

    # ======================================================
    # TEXT CHANGED
    # ======================================================

    def on_text_changed(
        self,
        buffer
    ):

        self.outline_area.queue_draw()

        self.window_mark_changed()

    def window_mark_changed(self):

        parent = self.get_parent()

        if parent is None:
            return

        # Walk upward until we find the canvas.
        widget = parent

        while widget is not None:

            if hasattr(
                widget,
                "window"
            ):

                window = widget.window

                if hasattr(
                    window,
                    "unsaved_changes"
                ):

                    window.unsaved_changes = True

                return

            widget = widget.get_parent()

    # ======================================================
    # SELECTION
    # ======================================================

    def select(self):

        self.selected = True

        self.add_css_class(
            "selected"
        )

    def deselect(self):

        self.selected = False

        self.remove_css_class(
            "selected"
        )

    # ======================================================
    # EDITING
    # ======================================================

    def begin_edit(self):

        self.select()

        self.editing = True

        self.text_view.set_editable(
            True
        )

        self.text_view.set_cursor_visible(
            True
        )

        self.text_view.grab_focus()

        buffer = self.text_view.get_buffer()

        buffer.place_cursor(
            buffer.get_end_iter()
        )

    def finish_edit(self):

        self.editing = False

        # We deliberately keep the TextView editable.
        #
        # Clicking the textbox again can therefore
        # immediately resume editing.

    # ======================================================
    # CLICK
    # ======================================================

    def on_frame_pressed(
        self,
        gesture,
        n_press,
        x,
        y
    ):

        self.select()

        # IMPORTANT:
        #
        # Do not claim the event.
        #
        # Gtk.TextView needs the event for normal
        # cursor placement and text selection.

    # ======================================================
    # TEXT
    # ======================================================

    def get_text(self):

        buffer = self.text_view.get_buffer()

        start = buffer.get_start_iter()
        end = buffer.get_end_iter()

        return buffer.get_text(
            start,
            end,
            True
        )

    def set_text(
        self,
        text
    ):

        self.text_view.get_buffer().set_text(
            text
        )

    def get_buffer(self):

        return self.text_view.get_buffer()

    # ======================================================
    # SELECTION INFORMATION
    # ======================================================

    def get_selection(self):

        buffer = self.text_view.get_buffer()

        bounds = buffer.get_selection_bounds()

        if not bounds:
            return None

        start, end = bounds

        return (
            start.get_offset(),
            end.get_offset()
        )

    def has_selection(self):

        return self.get_selection() is not None

    # ======================================================
    # TEXT OUTLINE / STROKE
    # ======================================================

    def set_border_enabled(self, enabled):

        self.outline_enabled = bool(enabled)

        self.outline_area.queue_draw()

        self.window_mark_changed()


    def set_border_color(self, rgba):

        if rgba is None:
            return

        self.outline_color = (
            rgba.red,
            rgba.green,
            rgba.blue,
            rgba.alpha
        )

        self.outline_area.queue_draw()

        self.window_mark_changed()


    def set_outline_width(self, width):

        self.outline_width = max(
            0.5,
            float(width)
        )

        self.outline_area.queue_draw()

        self.window_mark_changed()


    def draw_text_outline(
        self,
        area,
        cr,
        width,
        height
    ):

        if not getattr(
            self,
            "outline_enabled",
            False
        ):
            return

        buffer = self.text_view.get_buffer()

        start_iter = buffer.get_start_iter()
        end_iter = buffer.get_end_iter()

        text = buffer.get_text(
            start_iter,
            end_iter,
            True
        )

        if not text:
            return

        # ----------------------------------------------------
        # Pango-Umgebung der TextView verwenden
        # ----------------------------------------------------

        context = self.text_view.get_pango_context()

        layout = Pango.Layout.new(context)

        layout.set_text(
            text,
            -1
        )

        layout.set_wrap(
            Pango.WrapMode.WORD_CHAR
        )

        # Die TextView hat links/rechts eigene Text-Ränder.
        left_margin = self.text_view.get_left_margin()
        right_margin = self.text_view.get_right_margin()

        top_margin = self.text_view.get_top_margin()

        layout_width = max(
            1,
            width - left_margin - right_margin
        )

        layout.set_width(
            layout_width * Pango.SCALE
        )

        # ----------------------------------------------------
        # Grundschrift der TextView
        # ----------------------------------------------------

        font_description = (
            context.get_font_description()
        )

        if font_description is not None:
            layout.set_font_description(
                font_description.copy()
            )

        # ----------------------------------------------------
        # TextBuffer-Tags auf das Pango-Layout übertragen
        #
        # Dadurch entsprechen Größe, Familie, Fett, Kursiv
        # usw. exakt dem Inhalt der editierbaren TextView.
        # ----------------------------------------------------

        attr_list = Pango.AttrList()

        iterator = buffer.get_start_iter()

        while True:

            tags = iterator.get_tags()

            next_iterator = iterator.copy()

            if not next_iterator.forward_to_tag_toggle():
                next_iterator = buffer.get_end_iter()

            start_offset = iterator.get_offset()
            end_offset = next_iterator.get_offset()

            for tag in tags:

                # Schriftgröße
                try:

                    size = tag.get_property(
                        "size-points"
                    )

                    if size is not None and float(size) > 0:

                        attr = Pango.attr_size_new(
                            int(
                                float(size)
                                * Pango.SCALE
                            )
                        )

                        attr.start_index = start_offset
                        attr.end_index = end_offset

                        attr_list.insert(attr)

                except Exception:
                    pass

                # Schriftgewicht / Fett
                try:

                    weight = tag.get_property(
                        "weight"
                    )

                    if weight is not None:

                        attr = Pango.attr_weight_new(
                            weight
                        )

                        attr.start_index = start_offset
                        attr.end_index = end_offset

                        attr_list.insert(attr)

                except Exception:
                    pass

                # Kursiv
                try:

                    style = tag.get_property(
                        "style"
                    )

                    if style is not None:

                        attr = Pango.attr_style_new(
                            style
                        )

                        attr.start_index = start_offset
                        attr.end_index = end_offset

                        attr_list.insert(attr)

                except Exception:
                    pass

                # Schriftfamilie
                try:

                    family = tag.get_property(
                        "family"
                    )

                    if family:

                        attr = Pango.attr_family_new(
                            str(family)
                        )

                        attr.start_index = start_offset
                        attr.end_index = end_offset

                        attr_list.insert(attr)

                except Exception:
                    pass

                # Unterstreichung
                try:

                    underline = tag.get_property(
                        "underline"
                    )

                    if underline is not None:

                        attr = Pango.attr_underline_new(
                            underline
                        )

                        attr.start_index = start_offset
                        attr.end_index = end_offset

                        attr_list.insert(attr)

                except Exception:
                    pass

            if next_iterator.equal(
                buffer.get_end_iter()
            ):
                break

            iterator = next_iterator

        layout.set_attributes(
            attr_list
        )

        # ----------------------------------------------------
        # Gleiche Position wie die TextView
        # ----------------------------------------------------

        cr.save()

        cr.translate(
            float(left_margin),
            float(top_margin)
        )

        # ----------------------------------------------------
        # Glyphenpfad erzeugen
        # ----------------------------------------------------

        PangoCairo.layout_path(
            cr,
            layout
        )

        # ----------------------------------------------------
        # Kontur zeichnen
        # ----------------------------------------------------

        r, g, b, a = self.outline_color

        cr.set_source_rgba(
            float(r),
            float(g),
            float(b),
            float(a)
        )

        cr.set_line_width(
            max(
                1.0,
                float(
                    getattr(
                        self,
                        "outline_width",
                        2.0
                    )
                )
            )
        )

        cr.set_line_join(
            1
        )

        cr.stroke()

        cr.restore()

    def save_selection(self):

        buffer = self.text_view.get_buffer()

        bounds = buffer.get_selection_bounds()

        if not bounds:
            return

        start, end = bounds

        self.saved_selection = (
            start.get_offset(),
            end.get_offset()
        )


    def restore_selection(self):

        if self.saved_selection is None:
            return

        buffer = self.text_view.get_buffer()

        start_offset, end_offset = (
            self.saved_selection
        )

        start = buffer.get_iter_at_offset(
            start_offset
        )

        end = buffer.get_iter_at_offset(
            end_offset
        )

        buffer.select_range(
            start,
            end
        )


    def _selection_iters(self):

        buffer = self.text_view.get_buffer()

        # Aktuelle Auswahl vorhanden?
        bounds = buffer.get_selection_bounds()

        if bounds:
            self.save_selection()
            return bounds

        # Keine aktuelle Auswahl mehr, z.B. weil die
        # Text-Toolbar den Fokus bekommen hat.
        if self.saved_selection is None:
            return None

        start_offset, end_offset = (
            self.saved_selection
        )

        start = buffer.get_iter_at_offset(
            start_offset
        )

        end = buffer.get_iter_at_offset(
            end_offset
        )

        return (
            start,
            end
        )

    def apply_bold(self, enabled=True):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        tag = self._get_or_create_tag(
            "bold",
            weight=Pango.Weight.BOLD
        )

        self._set_tag(
            tag,
            start,
            end,
            enabled
        )

    def apply_italic(self, enabled=True):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        tag = self._get_or_create_tag(
            "italic",
            style=Pango.Style.ITALIC
        )

        self._set_tag(
            tag,
            start,
            end,
            enabled
        )

    def apply_underline(self, enabled=True):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        tag = self._get_or_create_tag(
            "underline",
            underline=Pango.Underline.SINGLE
        )

        self._set_tag(
            tag,
            start,
            end,
            enabled
        )

    def apply_strikethrough(self, enabled=True):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        tag = self._get_or_create_tag(
            "strikethrough",
            strikethrough=True
        )

        self._set_tag(
            tag,
            start,
            end,
            enabled
        )

    def apply_font_size(self, size):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        buffer = self.text_view.get_buffer()

        tag = buffer.create_tag(
            None,
            size_points=float(size)
        )

        buffer.apply_tag(
            tag,
            start,
            end
        )

        self.save_selection()
        self.restore_selection()

        self.window_mark_changed()

    def apply_font_family(self, family):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        buffer = self.text_view.get_buffer()

        tag = buffer.create_tag(
            None,
            family=str(family)
        )

        buffer.apply_tag(
            tag,
            start,
            end
        )

        self.save_selection()
        self.restore_selection()

        self.window_mark_changed()

    def apply_text_color(
        self,
        rgba
    ):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        buffer = self.text_view.get_buffer()

        tag = buffer.create_tag(
            None,
            foreground_rgba=rgba
        )

        buffer.apply_tag(
            tag,
            start,
            end
        )

        self.save_selection()
        self.restore_selection()

        self.window_mark_changed()

    def apply_background_color(
        self,
        rgba
    ):

        bounds = self._selection_iters()

        if bounds is None:
            return

        start, end = bounds

        buffer = self.text_view.get_buffer()

        tag = buffer.create_tag(
            None,
            background_rgba=rgba
        )

        buffer.apply_tag(
            tag,
            start,
            end
        )

        self.save_selection()
        self.restore_selection()

        self.window_mark_changed()

    def _get_or_create_tag(
        self,
        name,
        **properties
    ):

        buffer = self.text_view.get_buffer()

        table = buffer.get_tag_table()

        tag = table.lookup(name)

        if tag is None:

            tag = buffer.create_tag(
                name,
                **properties
            )

        return tag

    def _set_tag(
        self,
        tag,
        start,
        end,
        enabled
    ):

        buffer = self.text_view.get_buffer()

        if enabled:

            buffer.apply_tag(
                tag,
                start,
                end
            )

        else:

            buffer.remove_tag(
                tag,
                start,
                end
            )

        self.save_selection()
        self.restore_selection()

        self.window_mark_changed()

    # ======================================================
    # POSITION
    # ======================================================

    def get_position(self):

        allocation = self.get_allocation()

        return (
            allocation.x,
            allocation.y
        )
