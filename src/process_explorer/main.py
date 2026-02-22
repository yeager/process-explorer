"""Process Explorer - htop in GTK4."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
import psutil
import signal
import gettext
from datetime import datetime

_ = gettext.gettext
APP_ID = "io.github.yeager.ProcessExplorer"



def _wlc_settings_path():
    import os
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    d = os.path.join(xdg, "process-explorer")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "welcome.json")

def _load_wlc_settings():
    import os, json
    p = _wlc_settings_path()
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"welcome_shown": False}

def _save_wlc_settings(s):
    import json
    with open(_wlc_settings_path(), "w") as f:
        json.dump(s, f, indent=2)

class ProcessExplorerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs, title=_("Process Explorer"), default_width=1100, default_height=700)
        self._sort_col = 3  # CPU
        self._sort_asc = False
        self._search_text = ""
        self._auto_refresh = True

        header = Adw.HeaderBar()
        self.theme_btn = Gtk.Button(icon_name="weather-clear-night-symbolic", tooltip_text=_("Toggle theme"))
        self.theme_btn.connect("clicked", self._toggle_theme)
        header.pack_end(self.theme_btn)
        about_btn = Gtk.Button(icon_name="help-about-symbolic")
        about_btn.connect("clicked", self._show_about)
        header.pack_end(about_btn)

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_start=12, margin_end=12, margin_top=8)

        self.search_entry = Gtk.Entry(placeholder_text=_("Search processes..."), hexpand=True)
        self.search_entry.connect("changed", self._on_search)
        toolbar.append(self.search_entry)

        kill_btn = Gtk.Button(label=_("Kill"), css_classes=["destructive-action"])
        kill_btn.connect("clicked", self._kill_selected)
        toolbar.append(kill_btn)

        term_btn = Gtk.Button(label=_("SIGTERM"))
        term_btn.connect("clicked", lambda _: self._signal_selected(signal.SIGTERM))
        toolbar.append(term_btn)

        self.auto_btn = Gtk.ToggleButton(label=_("Auto-refresh"), active=True)
        self.auto_btn.connect("toggled", self._toggle_auto)
        toolbar.append(self.auto_btn)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.connect("clicked", lambda _: self._refresh())
        toolbar.append(refresh_btn)

        # System stats
        self.stats_label = Gtk.Label(label="", css_classes=["dim-label"], margin_start=12, margin_top=4, xalign=0)

        # Tree view
        # Columns: PID, Name, User, CPU%, MEM%, RSS(MB), Status, PPID
        self.store = Gtk.TreeStore(int, str, str, float, float, float, str, int)
        self.filter_model = self.store.filter_new()
        self.filter_model.set_visible_func(self._filter_func)
        self.sort_model = Gtk.TreeModelSort(model=self.filter_model)

        self.tree = Gtk.TreeView(model=self.sort_model, headers_clickable=True, enable_search=False)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)

        cols = [
            (_("PID"), 0, 70),
            (_("Name"), 1, 200),
            (_("User"), 2, 100),
            (_("CPU %"), 3, 80),
            (_("MEM %"), 4, 80),
            (_("RSS MB"), 5, 80),
            (_("Status"), 6, 80),
        ]
        for title, idx, width in cols:
            if isinstance(idx, int) and idx in (3, 4, 5):
                renderer = Gtk.CellRendererText(xalign=1.0, font="monospace 9")
            else:
                renderer = Gtk.CellRendererText(font="monospace 9")
            col = Gtk.TreeViewColumn(title, renderer, text=idx)
            col.set_resizable(True)
            col.set_fixed_width(width)
            col.set_sort_column_id(idx)
            col.set_clickable(True)
            if idx == 1:
                col.set_expand(True)
            self.tree.append_column(col)

        sw = Gtk.ScrolledWindow(vexpand=True, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        sw.set_child(self.tree)

        self.statusbar = Gtk.Label(label="", xalign=0, css_classes=["dim-label"], margin_start=12, margin_bottom=4)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(toolbar)
        content.append(self.stats_label)
        content.append(sw)
        content.append(self.statusbar)
        self.set_content(content)

        self._refresh()
        self._timer_id = GLib.timeout_add_seconds(3, self._auto_refresh_cb)
        GLib.timeout_add_seconds(1, self._update_status)

    def _refresh(self):
        # Remember selection
        sel_pids = set()
        selection = self.tree.get_selection()
        model, paths = selection.get_selected_rows()
        for path in paths:
            iter_ = model.get_iter(path)
            if iter_:
                sel_pids.add(model.get_value(iter_, 0))

        self.store.clear()
        procs = {}
        for p in psutil.process_iter(['pid', 'ppid', 'name', 'username', 'cpu_percent', 'memory_percent', 'memory_info', 'status']):
            try:
                info = p.info
                procs[info['pid']] = info
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Build tree - just flat for simplicity with parent info
        iters = {}
        # Add root processes first (ppid=0 or ppid not in procs)
        def add_proc(pid, parent_iter=None):
            info = procs.get(pid)
            if info is None:
                return
            rss = (info.get('memory_info') and info['memory_info'].rss or 0) / 1024 / 1024
            it = self.store.append(parent_iter, [
                info['pid'],
                info.get('name', '?'),
                info.get('username', '?') or '?',
                info.get('cpu_percent', 0) or 0,
                info.get('memory_percent', 0) or 0,
                round(rss, 1),
                info.get('status', '?'),
                info.get('ppid', 0),
            ])
            iters[pid] = it
            return it

        children = {}
        for pid, info in procs.items():
            ppid = info.get('ppid', 0)
            children.setdefault(ppid, []).append(pid)

        def add_tree(pid, parent_iter=None):
            it = add_proc(pid, parent_iter)
            for child_pid in children.get(pid, []):
                add_tree(child_pid, it)

        roots = [pid for pid, info in procs.items() if info.get('ppid', 0) not in procs]
        for pid in sorted(roots):
            add_tree(pid)

        # Update system stats
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        self.stats_label.set_label(
            f"  CPU: {cpu:.1f}% | RAM: {mem.percent:.1f}% ({mem.used/1024/1024/1024:.1f}/{mem.total/1024/1024/1024:.1f} GB) | "
            f"Disk: {disk.percent:.1f}% | Processes: {len(procs)}"
        )

    def _filter_func(self, model, iter_, _data=None):
        if not self._search_text:
            return True
        name = (model.get_value(iter_, 1) or "").lower()
        user = (model.get_value(iter_, 2) or "").lower()
        pid = str(model.get_value(iter_, 0))
        s = self._search_text.lower()
        return s in name or s in user or s in pid

    def _on_search(self, entry):
        self._search_text = entry.get_text()
        self.filter_model.refilter()

    def _kill_selected(self, _btn):
        self._signal_selected(signal.SIGKILL)

    def _signal_selected(self, sig):
        selection = self.tree.get_selection()
        model, paths = selection.get_selected_rows()
        for path in paths:
            iter_ = model.get_iter(path)
            if iter_:
                pid = model.get_value(iter_, 0)
                try:
                    psutil.Process(pid).send_signal(sig)
                except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                    pass
        GLib.timeout_add(500, self._refresh)

    def _toggle_auto(self, btn):
        self._auto_refresh = btn.get_active()

    def _auto_refresh_cb(self):
        if self._auto_refresh:
            self._refresh()
        return True

    def _update_status(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.statusbar.set_label(f"  {now}")
        return True

    def _toggle_theme(self, _btn):
        mgr = Adw.StyleManager.get_default()
        if mgr.get_dark():
            mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def _show_about(self, _btn):
        about = Adw.AboutWindow(
            transient_for=self,
            application_name="Process Explorer",
            application_icon="utilities-system-monitor",
            version="0.1.0",
            developer_name="Daniel Nylander",
            developers=["Daniel Nylander"],
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/yeager/process-explorer",
            issue_url="https://github.com/yeager/process-explorer/issues",
            translator_credits=_("translator-credits"),
            comments=_("GTK4 process explorer"),
        )
        about.add_link(_("Translations"), "https://www.transifex.com/danielnylander/process-explorer")
        about.present(self)


class ProcessExplorerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window or ProcessExplorerWindow(application=self)
        win.present()
        # Welcome dialog
        self._wlc_settings = _load_wlc_settings()
        if not self._wlc_settings.get("welcome_shown"):
            self._show_welcome(self.props.active_window or self)


    def do_startup(self):
        Adw.Application.do_startup(self)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])


def main():
    app = ProcessExplorerApp()
    app.run()


if __name__ == "__main__":
    main()

    def _show_welcome(self, win):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(420)
        dialog.set_content_height(480)
        page = Adw.StatusPage()
        page.set_icon_name("utilities-system-monitor-symbolic")
        page.set_title(_("Welcome to Process Explorer"))
        page.set_description(_("Monitor and manage system processes.\n\n✓ View running processes\n✓ CPU and memory usage\n✓ Process tree view"))
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
        dialog.present(win)

    def _on_welcome_close(self, btn, dialog):
        self._wlc_settings["welcome_shown"] = True
        _save_wlc_settings(self._wlc_settings)
        dialog.close()

