import gi

gi.require_version("Adw", "1")

from gi.repository import Adw

from .i18n import _


SECTIONS = [
    ("File", [
        ("Open…", "<Control>o"),
        ("New from Clipboard", "<Control>n"),
        ("Save", "<Control>s"),
        ("Export…", "<Control><Shift>s"),
        ("Print…", "<Control>p"),
        ("Close", "<Control>w"),
    ]),
    ("Edit", [
        ("Undo", "<Control>z"),
        ("Redo", "<Control><Shift>z"),
        ("Copy", "<Control>c"),
        ("Paste", "<Control>v"),
        ("Select All", "<Control>a"),
        ("Delete", "Delete"),
        ("Find…", "<Control>f"),
    ]),
    ("View", [
        ("Content Only", "<Control><Alt>1"),
        ("Thumbnails", "<Control><Alt>2"),
        ("Table of Contents", "<Control><Alt>3"),
        ("Contact Sheet", "<Control><Alt>4"),
        ("Bookmarks", "<Control><Alt>5"),
        ("Actual Size", "<Control>0"),
        ("Zoom to Fit", "<Control>9"),
        ("Zoom In", "<Control>plus"),
        ("Zoom Out", "<Control>minus"),
        ("Show Markup Toolbar", "<Control><Shift>a"),
        ("Slideshow", "<Control><Shift>f"),
        ("Enter Full Screen", "F11"),
    ]),
    ("Go", [
        ("Previous Page", "<Alt>Up"),
        ("Next Page", "<Alt>Down"),
        ("Go to Page…", "<Control><Alt>g"),
        ("Previous Document", "<Alt>Page_Up"),
        ("Next Document", "<Alt>Page_Down"),
    ]),
    ("Tools", [
        ("Inspector", "<Control>i"),
        ("Add Bookmark", "<Control>d"),
        ("Rotate Left", "<Control>l"),
        ("Rotate Right", "<Control>r"),
        ("Highlight Text", "<Control><Shift>h"),
        ("Crop", "<Control>k"),
        ("Adjust Color…", "<Control><Alt>c"),
    ]),
]


def show_shortcuts(parent):
    dialog = Adw.ShortcutsDialog()
    for title, items in SECTIONS:
        section = Adw.ShortcutsSection(title=_(title))
        for label, accelerator in items:
            section.add(Adw.ShortcutsItem.new(_(label), accelerator))
        dialog.add(section)
    dialog.present(parent)
