"""Edit Permissions (Preview: File → Edit Permissions …): a password to open the PDF, and an
owner password with what others may do. Applied when the document is saved (AES-256)."""

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from .i18n import _

CHOICES = [
    ("print", "Printing"),
    ("copy", "Copying text and images"),
    ("assemble", "Inserting, deleting and rotating pages"),
    ("annotate", "Adding annotations"),
    ("forms", "Filling out form fields"),
]


class PermissionsDialog(Adw.Dialog):

    def __init__(self, doc, on_apply):
        super().__init__(title=_("Edit Permissions"), content_width=480)
        self.doc = doc
        self.on_apply = on_apply
        protection = getattr(doc, "protection", None) or {}
        allowed = protection.get("allow", doc.current_permissions()) if protection else doc.current_permissions()

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        apply = Gtk.Button(label=_("Apply"))
        apply.add_css_class("suggested-action")
        apply.connect("clicked", self.on_done)
        header.pack_end(apply)
        view.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=_("Opening"),
                                     description=_("Anyone opening the PDF needs this password."))
        self.require_open = Adw.SwitchRow(title=_("Require password to open document"), active=bool(protection.get("open")))
        self.open_password = Adw.PasswordEntryRow(title=_("Password"), text=protection.get("open", ""))
        self.open_password.set_sensitive(self.require_open.get_active())
        self.require_open.connect("notify::active", lambda r, _p: self.open_password.set_sensitive(r.get_active()))
        group.add(self.require_open)
        group.add(self.open_password)
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Permissions"),
                                     description=_("Allowed without the owner password:"))
        self.owner_password = Adw.PasswordEntryRow(title=_("Owner password"), text=protection.get("owner", ""))
        group.add(self.owner_password)
        self.switches = {}
        for key, label in CHOICES:
            row = Adw.SwitchRow(title=_(label), active=key in allowed)
            self.switches[key] = row
            group.add(row)
        page.add(group)

        self.message = Gtk.Label(wrap=True, margin_start=18, margin_end=18, margin_bottom=12)
        self.message.add_css_class("error")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(page)
        box.append(self.message)
        view.set_content(box)
        self.set_child(view)

    def on_done(self, _button):
        open_password = self.open_password.get_text() if self.require_open.get_active() else ""
        owner = self.owner_password.get_text()
        allow = [key for key, row in self.switches.items() if row.get_active()]
        restricted = len(allow) < len(self.switches)
        if self.require_open.get_active() and not open_password:
            self.message.set_label(_("Enter a password to open the document."))
            return
        if restricted and not owner:
            self.message.set_label(_("Restricting permissions needs an owner password."))
            return
        if open_password and owner and open_password == owner:
            self.message.set_label(_("The owner password must differ from the password to open."))
            return
        if not open_password and not restricted:
            protection = {}                       # no protection at all
        else:
            protection = {"open": open_password, "owner": owner, "allow": allow}
        self.close()
        self.on_apply(protection)
