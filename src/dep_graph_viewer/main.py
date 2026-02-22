"""Dep Graph Viewer — Visualize dependency trees for .deb packages."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Pango

import gettext
import locale
import os
import sys
import json
import datetime
import threading
import subprocess
import re

LOCALE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "po")
if not os.path.isdir(LOCALE_DIR):
    LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain("dep-graph-viewer", LOCALE_DIR)
gettext.bindtextdomain("dep-graph-viewer", LOCALE_DIR)
gettext.textdomain("dep-graph-viewer")
_ = gettext.gettext

APP_ID = "se.danielnylander.dep.graph.viewer"
SETTINGS_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "dep-graph-viewer"
)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")


def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {"welcome_shown": False}


def _save_settings(s):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)



def _get_deps(package):
    """Get dependencies for a package using dpkg/apt-cache."""
    deps = []
    try:
        r = subprocess.run(["apt-cache", "depends", package],
                          capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("Depends:") or line.startswith("PreDepends:"):
                dep = line.split(":", 1)[1].strip().split()[0]
                if dep.startswith("<"):
                    dep = dep.strip("<>")
                deps.append(dep)
    except:
        pass
    return deps


def _get_rdeps(package):
    """Get reverse dependencies."""
    rdeps = []
    try:
        r = subprocess.run(["apt-cache", "rdepends", package],
                          capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines()[2:]:
            line = line.strip()
            if line and not line.startswith("|"):
                rdeps.append(line)
    except:
        pass
    return rdeps


def _find_circular(package, visited=None, path=None):
    """Detect circular dependencies."""
    if visited is None:
        visited = set()
    if path is None:
        path = []
    
    if package in path:
        return [path[path.index(package):] + [package]]
    
    if package in visited:
        return []
    
    visited.add(package)
    path.append(package)
    circles = []
    
    for dep in _get_deps(package)[:10]:  # limit depth
        circles.extend(_find_circular(dep, visited, path[:]))
    
    return circles



class DepGraphViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("Dep Graph Viewer"), default_width=1100, default_height=750)
        self.settings = _load_settings()
        self._dep_tree = {}

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        headerbar = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title=_("Dep Graph Viewer"), subtitle="")
        headerbar.set_title_widget(title_widget)
        self._title_widget = title_widget

        
        self._pkg_entry = Gtk.Entry(placeholder_text=_("Package name..."))
        self._pkg_entry.set_size_request(200, -1)
        self._pkg_entry.connect("activate", self._on_search)
        headerbar.pack_start(self._pkg_entry)
        
        search_btn = Gtk.Button(icon_name="system-search-symbolic", tooltip_text=_("Show dependencies"))
        search_btn.connect("clicked", self._on_search)
        headerbar.pack_start(search_btn)
        
        rdeps_btn = Gtk.Button(label=_("Reverse"), tooltip_text=_("Show reverse dependencies"))
        rdeps_btn.connect("clicked", self._on_rdeps)
        headerbar.pack_start(rdeps_btn)
        
        circular_btn = Gtk.Button(label=_("Circular"), tooltip_text=_("Find circular dependencies"))
        circular_btn.add_css_class("warning")
        circular_btn.connect("clicked", self._on_circular)
        headerbar.pack_start(circular_btn)

        # Menu
        menu = Gio.Menu()
        menu.append(_("Settings"), "app.settings")
        menu.append(_("Copy Debug Info"), "app.copy-debug")
        menu.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu.append(_("About Dep Graph Viewer"), "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        headerbar.pack_end(menu_btn)

        main_box.append(headerbar)

        
        # Tree view
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self._tree_list = Gtk.ListBox()
        self._tree_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._tree_list.add_css_class("boxed-list")
        self._tree_list.set_margin_start(12)
        self._tree_list.set_margin_end(12)
        self._tree_list.set_margin_top(8)
        self._tree_list.set_margin_bottom(8)
        
        self._empty = Adw.StatusPage()
        self._empty.set_icon_name("network-workgroup-symbolic")
        self._empty.set_title(_("No package selected"))
        self._empty.set_description(_("Enter a package name to view its dependency tree."))
        self._empty.set_vexpand(True)
        
        self._stack = Gtk.Stack()
        self._stack.add_named(self._empty, "empty")
        scroll.set_child(self._tree_list)
        self._stack.add_named(scroll, "tree")
        self._stack.set_vexpand(True)
        main_box.append(self._stack)

        # Status bar
        self._status = Gtk.Label(label=_("Ready"), xalign=0)
        self._status.set_margin_start(12)
        self._status.set_margin_end(12)
        self._status.set_margin_top(4)
        self._status.set_margin_bottom(4)
        self._status.add_css_class("dim-label")
        main_box.append(self._status)

        self.set_content(main_box)

        if not self.settings.get("welcome_shown"):
            GLib.idle_add(self._show_welcome)

    def _show_welcome(self):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(420)
        dialog.set_content_height(480)

        page = Adw.StatusPage()
        page.set_icon_name("network-workgroup-symbolic")
        page.set_title(_("Welcome to Dep Graph Viewer"))
        page.set_description(_("Explore package dependency trees.\n\n"
            "✓ Visualize .deb package dependencies\n"
            "✓ Detect circular dependencies\n"
            "✓ Search and filter packages\n"
            "✓ Reverse dependency lookup\n"
            "✓ Export dependency graph"))

        btn = Gtk.Button(label=_("Get Started"))
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(12)
        btn.connect("clicked", self._on_welcome_close, dialog)
        page.set_child(btn)

        box = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_show_title(False)
        box.add_top_bar(hb)
        box.set_content(page)
        dialog.set_child(box)
        dialog.present(self)

    def _on_welcome_close(self, btn, dialog):
        self.settings["welcome_shown"] = True
        _save_settings(self.settings)
        dialog.close()

    
    def _on_search(self, *_args):
        pkg = self._pkg_entry.get_text().strip()
        if not pkg:
            return
        self._status.set_text(_("Loading dependencies for %s...") % pkg)
        threading.Thread(target=self._load_deps, args=(pkg,), daemon=True).start()

    def _load_deps(self, pkg):
        deps = _get_deps(pkg)
        GLib.idle_add(self._show_deps, pkg, deps, _("Dependencies of %s") % pkg)

    def _show_deps(self, pkg, deps, title):
        while True:
            row = self._tree_list.get_row_at_index(0)
            if row is None:
                break
            self._tree_list.remove(row)
        
        header = Adw.ActionRow()
        header.set_title(title)
        header.set_subtitle(_("%(count)d packages") % {"count": len(deps)})
        self._tree_list.append(header)
        
        for dep in deps:
            row = Adw.ActionRow()
            row.set_title(dep)
            subdeps = _get_deps(dep)
            if subdeps:
                row.set_subtitle(_("%(count)d dependencies") % {"count": len(subdeps)})
            self._tree_list.append(row)
        
        self._stack.set_visible_child_name("tree")
        self._status.set_text(_("%(pkg)s: %(count)d dependencies") % {"pkg": pkg, "count": len(deps)})

    def _on_rdeps(self, btn):
        pkg = self._pkg_entry.get_text().strip()
        if not pkg:
            return
        self._status.set_text(_("Loading reverse dependencies..."))
        threading.Thread(target=self._load_rdeps, args=(pkg,), daemon=True).start()

    def _load_rdeps(self, pkg):
        rdeps = _get_rdeps(pkg)
        GLib.idle_add(self._show_deps, pkg, rdeps, _("Reverse dependencies of %s") % pkg)

    def _on_circular(self, btn):
        pkg = self._pkg_entry.get_text().strip()
        if not pkg:
            return
        self._status.set_text(_("Checking for circular dependencies..."))
        threading.Thread(target=self._check_circular, args=(pkg,), daemon=True).start()

    def _check_circular(self, pkg):
        circles = _find_circular(pkg)
        GLib.idle_add(self._show_circular, pkg, circles)

    def _show_circular(self, pkg, circles):
        while True:
            row = self._tree_list.get_row_at_index(0)
            if row is None:
                break
            self._tree_list.remove(row)
        
        if not circles:
            row = Adw.ActionRow()
            row.set_title(_("No circular dependencies found"))
            row.set_subtitle(pkg)
            self._tree_list.append(row)
        else:
            for circle in circles[:20]:
                row = Adw.ActionRow()
                row.set_title(" → ".join(circle))
                row.add_css_class("error")
                self._tree_list.append(row)
        
        self._stack.set_visible_child_name("tree")
        self._status.set_text(_("%(count)d circular dependencies found") % {"count": len(circles)})


class DepGraphViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

        for name, callback in [
            ("settings", self._on_settings),
            ("copy-debug", self._on_copy_debug),
            ("shortcuts", self._on_shortcuts),
            ("about", self._on_about),
            ("quit", self._on_quit),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("app.shortcuts", ["<Ctrl>slash"])

    def do_activate(self):
        if not self.window:
            self.window = DepGraphViewerWindow(self)
        self.window.present()

    def _on_settings(self, *_args):
        if not self.window:
            return
        dialog = Adw.PreferencesDialog()
        dialog.set_title(_("Settings"))
        page = Adw.PreferencesPage()
        
        group = Adw.PreferencesGroup(title=_("Graph"))
        row = Adw.SpinRow.new_with_range(1, 10, 1)
        row.set_title(_("Maximum depth"))
        row.set_value(3)
        group.add(row)
        page.add(group)
        dialog.add(page)
        dialog.present(self.window)

    def _on_copy_debug(self, *_args):
        if not self.window:
            return
        from . import __version__
        info = (
            f"Dep Graph Viewer {__version__}\n"
            f"Python {sys.version}\n"
            f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}\n"
            f"Adw {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}\n"
            f"OS: {os.uname().sysname} {os.uname().release}\n"
        )
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(info)
        self.window._status.set_text(_("Debug info copied"))

    def _on_shortcuts(self, *_args):
        if self.window:
            dialog = Gtk.ShortcutsWindow(transient_for=self.window)
            section = Gtk.ShortcutsSection(visible=True)
            group = Gtk.ShortcutsGroup(title=_("General"), visible=True)
            for accel, title in [
                ("<Ctrl>q", _("Quit")),
                ("<Ctrl>slash", _("Keyboard shortcuts")),
            ]:
                group.append(Gtk.ShortcutsShortcut(accelerator=accel, title=title, visible=True))
            section.append(group)
            dialog.append(section)
            dialog.present()

    def _on_about(self, *_args):
        from . import __version__
        dialog = Adw.AboutDialog(
            application_name=_("Dep Graph Viewer"),
            application_icon="network-workgroup-symbolic",
            version=__version__,
            developer_name="Daniel Nylander",
            website="https://github.com/yeager/dep-graph-viewer",
            license_type=Gtk.License.GPL_3_0,
            issue_url="https://github.com/yeager/dep-graph-viewer/issues",
            comments=_("Visualize dependency trees for .deb packages. Find circular dependencies."),
        )
        dialog.present(self.window)

    def _on_quit(self, *_args):
        self.quit()


def main():
    app = DepGraphViewerApp()
    app.run(sys.argv)
