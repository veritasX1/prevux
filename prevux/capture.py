"""Import from a scanner or camera (Preview: File → Import from Scanner, Take Photo).

Scanners through SANE (scanimage – USB and network scanners, eSCL/AirScan included),
cameras through GStreamer with a live picture. The result opens as a new, untitled
document; several pages from a document feeder become one PDF."""

import os
import re
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GLib, Gtk

from .i18n import _

MODES = {"color": "Color", "gray": "Grayscale", "lineart": "Black & White", "binary": "Black & White"}
SOURCES = {"flatbed": "Flatbed", "adf": "Document Feeder", "adf duplex": "Document Feeder, both sides",
           "automatic document feeder": "Document Feeder"}
COMMON_RESOLUTIONS = [75, 100, 150, 200, 300, 600, 1200]
EXTRA_SCANNERS = []          # e.g. [("test", "SANE test scanner")] for trying things out


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H.%M.%S")


# ---- scanner ------------------------------------------------

def list_scanners():
    """[(device, label)] of the scanners SANE finds (cameras are left out)."""
    try:
        out = subprocess.run(["scanimage", "-L"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return list(EXTRA_SCANNERS)
    found, names = [], set()
    for device, label in re.findall(r"device `([^']+)' is an? (.+)", out):
        if device.startswith("v4l:"):
            continue
        label = re.sub(r"\s+(platen,adf|flatbed) scanner$|\s+ip=.*$", "", label).strip()
        label = re.sub(r"^(eSCL|Noname)\s+", "", label)
        # The same network scanner often shows up twice (escl and airscan): keep one.
        if label.lower() in names:
            continue
        names.add(label.lower())
        found.append((device, label))
    return found + list(EXTRA_SCANNERS)


def scanner_options(device):
    """{"mode": [...], "resolution": [...], "source": [...]} with the defaults first."""
    try:
        out = subprocess.run(["scanimage", "-d", device, "-A"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    options = {}
    for name in ("mode", "resolution", "source"):
        match = re.search(rf"^\s+--{name} (.+?) \[(.+?)\]\s*$", out, re.M)
        if not match:
            continue
        values, default = match.group(1), match.group(2)
        if name == "resolution":
            values = values.replace("dpi", "")
            if ".." in values:
                low, high = (int(v) for v in re.findall(r"\d+", values)[:2])
                choices = [r for r in COMMON_RESOLUTIONS if low <= r <= high]
            else:
                choices = [int(v) for v in values.split("|") if v.isdigit()]
            default = int(re.sub(r"\D", "", default) or 0)
            preferred = 300 if 300 in choices else default
            options[name] = [preferred] + [c for c in choices if c != preferred]
        else:
            choices = values.split("|")
            options[name] = [default] + [c for c in choices if c != default]
    return options


def scan(device, mode=None, resolution=None, source=None):
    """Scan; returns the image files (several from a document feeder)."""
    folder = Path(tempfile.mkdtemp(prefix="prevux-scan-"))
    command = ["scanimage", "-d", device, "--format=png"]
    if mode:
        command += ["--mode", mode]
    if resolution:
        command += ["--resolution", str(resolution)]
    if source:
        command += ["--source", source]
    feeder = bool(source) and ("adf" in source.lower() or "feeder" in source.lower())
    if feeder:
        command += [f"--batch={folder}/page-%03d.png"]
        run = subprocess.run(command, capture_output=True, text=True, timeout=900)
    else:
        with open(folder / "page-001.png", "wb") as target:
            run = subprocess.run(command, stdout=target, stderr=subprocess.PIPE, text=False, timeout=900)
    pages = sorted(p for p in folder.glob("page-*.png") if p.stat().st_size > 0)
    if not pages:
        error = run.stderr if isinstance(run.stderr, str) else (run.stderr or b"").decode(errors="replace")
        raise RuntimeError(error.strip().splitlines()[-1] if error.strip() else _("The scanner returned no page."))
    return pages


def pages_to_document(paths, title):
    """One picture stays a picture; several become a PDF with one page each."""
    from .documents import ImageDocument, PDFDocument
    folder = Path(paths[0]).parent
    if len(paths) == 1:
        target = folder / f"{title}.png"
        os.replace(paths[0], target)
        doc = ImageDocument(target)
    else:
        import pymupdf
        pdf = pymupdf.open()
        for path in paths:
            image = pymupdf.open(str(path))
            pdf.insert_pdf(pymupdf.open("pdf", image.convert_to_pdf()))
            image.close()
        target = folder / f"{title}.pdf"
        pdf.save(str(target), deflate=True)
        pdf.close()
        doc = PDFDocument(target)
    doc.untitled = True
    doc.modified = True
    return doc


class ScanDialog(Adw.Dialog):
    """Pick scanner, source, colour and resolution; scan; the pages open in Prevux."""

    def __init__(self, done):
        super().__init__(title=_("Import from Scanner"), content_width=420)
        self.done = done
        self.devices = []
        self.options = {}

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.busy_page(_("Looking for scanners …")), "searching")
        self.status = Adw.StatusPage(icon_name="scanner-symbolic", title=_("No Scanner Found"),
                                     description=_("Switch the scanner on and connect it to this computer or network."))
        retry = Gtk.Button(label=_("Search Again"), halign=Gtk.Align.CENTER, css_classes=["pill"])
        retry.connect("clicked", lambda _b: self.search())
        self.status.set_child(retry)
        self.stack.add_named(self.status, "none")

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self.device_row = Adw.ComboRow(title=_("Scanner"))
        self.device_row.connect("notify::selected", lambda *_args: self.load_options())
        group.add(self.device_row)
        self.source_row = Adw.ComboRow(title=_("Source"))
        self.mode_row = Adw.ComboRow(title=_("Kind"))
        self.resolution_row = Adw.ComboRow(title=_("Resolution"))
        for row in (self.source_row, self.mode_row, self.resolution_row):
            group.add(row)
        page.add(group)
        button = Gtk.Button(label=_("Scan"), halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        button.set_margin_top(12)
        button.connect("clicked", lambda _b: self.start_scan())
        self.scan_button = button
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(page)
        box.append(button)
        box.set_margin_bottom(18)
        self.stack.add_named(box, "settings")
        self.stack.add_named(self.busy_page(_("Scanning …")), "scanning")
        view.set_content(self.stack)
        self.set_child(view)
        self.search()

    @staticmethod
    def busy_page(text):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        box.set_margin_top(48)
        box.set_margin_bottom(48)
        box.append(Adw.Spinner(width_request=32, height_request=32, halign=Gtk.Align.CENTER))
        box.append(Gtk.Label(label=text, css_classes=["dim-label"]))
        return box

    def search(self):
        self.stack.set_visible_child_name("searching")

        def work():
            devices = list_scanners()
            GLib.idle_add(self.found, devices)
        threading.Thread(target=work, daemon=True).start()

    def found(self, devices):
        self.devices = devices
        if not devices:
            self.stack.set_visible_child_name("none")
            return False
        self.device_row.set_model(Gtk.StringList.new([label for _device, label in devices]))
        self.load_options()
        return False

    def load_options(self):
        if not self.devices:
            return
        device = self.devices[self.device_row.get_selected()][0]
        self.stack.set_visible_child_name("searching")

        def work():
            options = scanner_options(device)
            GLib.idle_add(self.show_options, options)
        threading.Thread(target=work, daemon=True).start()

    def show_options(self, options):
        self.options = options

        def fill(row, key, label):
            values = options.get(key, [])
            row.set_visible(bool(values))
            row.set_model(Gtk.StringList.new([label(v) for v in values]))
            row.set_selected(0)
        fill(self.source_row, "source", lambda v: _(SOURCES.get(v.lower(), v)))
        fill(self.mode_row, "mode", lambda v: _(MODES.get(v.lower(), v)))
        fill(self.resolution_row, "resolution", lambda v: f"{v} dpi")
        self.stack.set_visible_child_name("settings")
        return False

    def choice(self, row, key):
        values = self.options.get(key, [])
        return values[row.get_selected()] if values else None

    def start_scan(self):
        device = self.devices[self.device_row.get_selected()][0]
        settings = (self.choice(self.mode_row, "mode"), self.choice(self.resolution_row, "resolution"),
                    self.choice(self.source_row, "source"))
        self.stack.set_visible_child_name("scanning")

        def work():
            try:
                pages, error = scan(device, *settings), None
            except Exception as problem:
                pages, error = None, str(problem)
            GLib.idle_add(self.finished, pages, error)
        threading.Thread(target=work, daemon=True).start()

    def finished(self, pages, error):
        if error:
            self.stack.set_visible_child_name("settings")
            toast = Adw.AlertDialog(heading=_("Scanning failed"), body=error)
            toast.add_response("ok", _("OK"))
            toast.present(self)
            return False
        self.close()
        self.done(pages)
        return False


# ---- camera -------------------------------------------------

class CameraDialog(Adw.Dialog):
    """Live picture of a camera; "Take Photo" keeps the current frame."""

    SOURCE = None        # a GStreamer source description instead of the cameras, for testing

    def __init__(self, done):
        super().__init__(title=_("Take Photo"), content_width=640, content_height=560)
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        self.Gst = Gst
        self.done = done
        self.pipeline = None

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.camera_menu = Gtk.DropDown()
        self.camera_menu.connect("notify::selected", lambda *_args: self.start())
        header.set_title_widget(self.camera_menu)
        view.add_top_bar(header)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
        self.picture.add_css_class("camera-preview")
        self.stack = Gtk.Stack()
        self.stack.add_named(self.picture, "live")
        self.stack.add_named(Adw.StatusPage(icon_name="camera-disabled-symbolic", title=_("No Camera Found")), "none")
        view.set_content(self.stack)
        shutter = Gtk.Button(icon_name="camera-photo-symbolic", tooltip_text=_("Take Photo"),
                             halign=Gtk.Align.CENTER, css_classes=["circular", "suggested-action", "shutter"])
        shutter.set_margin_top(10)
        shutter.set_margin_bottom(10)
        shutter.connect("clicked", lambda _b: self.take())
        self.shutter = shutter
        view.add_bottom_bar(shutter)
        self.set_child(view)
        self.connect("closed", lambda _d: self.stop())

        self.cameras = self.find_cameras()
        self.camera_menu.set_model(Gtk.StringList.new([name for name, _source in self.cameras]))
        self.camera_menu.set_visible(len(self.cameras) > 1)
        if not self.cameras:
            self.stack.set_visible_child_name("none")
            shutter.set_sensitive(False)
        else:
            self.start()

    def find_cameras(self):
        if self.SOURCE:
            return [(_("Test Picture"), self.SOURCE)]
        monitor = self.Gst.DeviceMonitor()
        monitor.add_filter("Video/Source", None)
        monitor.start()
        cameras, seen = [], set()
        for device in monitor.get_devices():
            name = device.get_display_name()
            properties = device.get_properties()
            path = properties.get_string("device.path") or properties.get_string("api.v4l2.path") if properties else None
            if not path or name in seen:
                continue
            seen.add(name)
            cameras.append((name, f"v4l2src device={path}"))
        monitor.stop()
        return cameras

    def start(self):
        self.stop()
        if not self.cameras:
            return
        source = self.cameras[self.camera_menu.get_selected()][1]
        description = (f"{source} ! videoconvert ! tee name=t "
                       "t. ! queue ! gtk4paintablesink name=screen "
                       "t. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! video/x-raw,format=RGB "
                       "! appsink name=grab max-buffers=1 drop=true sync=false")
        try:
            self.pipeline = self.Gst.parse_launch(description)
        except GLib.Error as error:
            print("Prevux: camera:", error)
            self.stack.set_visible_child_name("none")
            return
        self.picture.set_paintable(self.pipeline.get_by_name("screen").get_property("paintable"))
        self.pipeline.set_state(self.Gst.State.PLAYING)
        self.stack.set_visible_child_name("live")

    def take(self):
        if self.pipeline is None:
            return
        sample = self.pipeline.get_by_name("grab").emit("try-pull-sample", 2 * self.Gst.SECOND)
        if sample is None:
            return
        from PIL import Image
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        buffer = sample.get_buffer()
        ok, info = buffer.map(self.Gst.MapFlags.READ)
        if not ok:
            return
        try:
            stride = len(info.data) // height
            image = Image.frombuffer("RGB", (width, height), bytes(info.data), "raw", "RGB", stride, 1)
        finally:
            buffer.unmap(info)
        folder = Path(tempfile.mkdtemp(prefix="prevux-photo-"))
        path = folder / "photo.png"
        image.save(path)
        self.close()
        self.done([path])

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
            self.pipeline = None
