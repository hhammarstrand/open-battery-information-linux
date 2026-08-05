import importlib
import os
import pkgutil
import queue
import sys
import threading

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # pragma: no cover - depends on the distro package set
    sys.stderr.write(
        "OBI needs Python's tkinter bindings, which are packaged separately on Linux.\n"
        "\n"
        "  Debian / Ubuntu / Pop!_OS / Mint : sudo apt install python3-tk\n"
        "  Fedora                           : sudo dnf install python3-tkinter\n"
        "  Arch                             : sudo pacman -S tk\n"
        "\n"
        "The precompiled release binary bundles tkinter and does not need this.\n")
    raise SystemExit(1)

from components import theme
from components.default_module import DefaultModule
from components.theme import PAD_L, PAD_M, PAD_S, PAD_XS, Card

#: Keep the debug pane from growing without bound during long logging runs.
DEBUG_MAX_LINES = 500
DEBUG_POLL_MS = 200

APP_NAME = "OBI Linux"
APP_TAGLINE = "Battery diagnostics"


class OBI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1280x820")
        self.minsize(960, 620)

        self.theme = theme.apply_theme(self)
        self.set_icon("icon.png")

        self.main_app = None
        self.loaded_modules = {}
        self.loaded_interfaces = {}
        self.module_names = {}
        self.interface_names = {}

        self._debug_queue = queue.Queue()
        self._pending_debug = []
        self._debug_job = None
        self._closing = False
        self._cleanup_callbacks = []

        self.setup_header()
        self.setup_status_bar()
        self.setup_log_pane()
        self.setup_body()

        self.default_module = DefaultModule(self.main_window)
        self.display_default_content()

        self.current_interface = None

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._schedule_debug_drain()

    # ------------------------------------------------------------------
    # appearance
    # ------------------------------------------------------------------
    def set_icon(self, icon_path):
        resolved = self.get_resource_path(icon_path)
        try:
            # Kept on the instance: Tk does not own the image, and letting it
            # be garbage collected makes the window icon disappear.
            self._icon = tk.PhotoImage(file=resolved)
            self.iconphoto(False, self._icon)
        except tk.TclError as exc:
            self.update_debug("Could not load window icon %s: %s" % (resolved, exc))

    def _separator(self, parent, side="top"):
        colors = self.theme.colors
        line = tk.Frame(parent, background=colors["border"],
                        height=1 if side in ("top", "bottom") else 0,
                        width=1 if side in ("left", "right") else 0)
        line.pack(side=side, fill="x" if side in ("top", "bottom") else "y")
        return line

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def setup_header(self):
        """A header bar, the way current desktop applications are laid out.

        Tk cannot draw client side decorations, so this sits below the window
        manager's title bar rather than replacing it.
        """
        header = ttk.Frame(self, style="Header.TFrame", padding=(PAD_L, PAD_S))
        header.pack(side="top", fill="x")

        titles = ttk.Frame(header, style="Header.TFrame")
        titles.pack(side="left")
        ttk.Label(titles, text=APP_NAME, style="HeaderHeading.TLabel").pack(anchor="w")
        self.subtitle_label = ttk.Label(titles, text=APP_TAGLINE,
                                        style="HeaderCaption.TLabel")
        self.subtitle_label.pack(anchor="w")

        actions = ttk.Frame(header, style="Header.TFrame")
        actions.pack(side="right")
        self.log_button = ttk.Button(actions, text="Show log", style="Header.TButton",
                                     command=self.toggle_log)
        self.log_button.pack(side="right", padx=(PAD_M, 0))
        self.connection_label = ttk.Label(actions, text="●  Not connected",
                                          style="HeaderDim.TLabel")
        self.connection_label.pack(side="right")

        self._separator(self, side="top")

    def setup_body(self):
        body = self.body = ttk.Frame(self, style="Window.TFrame")
        body.pack(side="top", fill="both", expand=True)

        self.sidebar = ttk.Frame(body, style="Sidebar.TFrame", padding=PAD_M)
        self.sidebar.pack(side="left", fill="y")
        self._separator(body, side="left")

        self.setup_module_frame()
        self.setup_interface_frame()

        self.main_window = ttk.Frame(body, style="Window.TFrame", padding=PAD_L)
        self.main_window.pack(side="left", fill="both", expand=True)

    def setup_module_frame(self):
        card = Card(self.sidebar)
        card.pack(fill="x", pady=(0, PAD_M))
        card.title("Module")

        self.module_var = tk.StringVar()
        self.module_combobox = ttk.Combobox(card, textvariable=self.module_var,
                                            state="readonly", width=24)
        self.module_combobox.pack(fill="x")

        self.load_modules()
        self.module_combobox.bind("<<ComboboxSelected>>", self.display_module)

    def setup_interface_frame(self):
        card = Card(self.sidebar)
        card.pack(fill="both", expand=True)
        card.title("Interface")

        self.interface_var = tk.StringVar()
        self.interface_combobox = ttk.Combobox(card, textvariable=self.interface_var,
                                               state="readonly", width=24)
        self.interface_combobox.pack(fill="x")

        self.load_interfaces()
        self.interface_combobox.bind("<<ComboboxSelected>>", self.display_interface_settings)

        self.interface_wireframe = ttk.Frame(card, style="Card.TFrame")
        self.interface_wireframe.pack(fill="both", expand=True, pady=(PAD_M, 0))

    def setup_status_bar(self):
        bar = ttk.Frame(self, style="Window.TFrame", padding=(PAD_L, PAD_XS))
        bar.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(bar, text="Ready", style="WindowCaption.TLabel",
                                      anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)
        self._separator(self, side="bottom")

    def setup_log_pane(self):
        """Protocol trace, collapsed by default.

        Progressive disclosure: the status bar carries the last line, and the
        full trace is one click away when something needs diagnosing.
        """
        self.log_frame = ttk.Frame(self, style="Window.TFrame",
                                   padding=(PAD_L, 0, PAD_L, PAD_S))
        self.log_visible = False

        header = ttk.Frame(self.log_frame, style="Window.TFrame")
        header.pack(fill="x", pady=(0, PAD_XS))
        ttk.Label(header, text="Debug log", style="WindowCaption.TLabel").pack(side="left")
        ttk.Button(header, text="Clear", style="Header.TButton",
                   command=self.clear_debug).pack(side="right")

        container = ttk.Frame(self.log_frame, style="Window.TFrame")
        container.pack(fill="both", expand=True)

        scroll = ttk.Scrollbar(container, orient="vertical")
        scroll.pack(side="right", fill="y")

        self.debug_text = tk.Text(container, height=8, wrap="none",
                                  yscrollcommand=scroll.set)
        self.theme.style_text(self.debug_text)
        self.debug_text.pack(fill="both", expand=True)
        self.debug_text.config(state="disabled")
        scroll.config(command=self.debug_text.yview)

        for message in self._pending_debug:
            self._write_debug(message)
        self._pending_debug = []

    def toggle_log(self):
        if self.log_visible:
            self.log_frame.pack_forget()
            self.log_button.config(text="Show log")
        else:
            # 'before' puts the log ahead of the body in the packing order, so
            # it gets its own height instead of being squeezed to nothing by
            # the expanding content area.
            self.log_frame.pack(side="bottom", fill="both", expand=False,
                                before=self.body)
            self.log_button.config(text="Hide log")
            self.debug_text.see("end")
        self.log_visible = not self.log_visible

    def clear_debug(self):
        self.debug_text.config(state="normal")
        self.debug_text.delete("1.0", "end")
        self.debug_text.config(state="disabled")

    # ------------------------------------------------------------------
    # module / interface discovery
    # ------------------------------------------------------------------
    def get_resource_path(self, relative_path):
        """ Get the absolute path to the resource, works for dev and for PyInstaller """
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, relative_path)
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

    def _discover(self, package, directory, required_attribute):
        """Import every module in ``directory`` that looks like a plugin."""
        found = []
        names = sorted({name for _, name, _ in pkgutil.iter_modules([directory])})
        for name in names:
            if name.startswith('_'):
                continue
            try:
                module = self.import_module("%s.%s" % (package, name))
                display_name = getattr(module, "get_display_name", None)
                if not callable(display_name) or not hasattr(module, required_attribute):
                    continue
                found.append((display_name(), name))
            except Exception as e:
                self.update_debug("Failed to load %s '%s': %s" % (package, name, e))
        return found

    def load_modules(self):
        modules_dir = self.get_resource_path('modules')
        display_names = []
        for display_name, module_name in self._discover('modules', modules_dir, 'ModuleApplication'):
            self.module_names[display_name] = module_name
            display_names.append(display_name)
        self.module_combobox['values'] = display_names

    def load_interfaces(self):
        interfaces_dir = self.get_resource_path('interfaces')
        display_names = []
        for display_name, interface_name in self._discover('interfaces', interfaces_dir, 'Interface'):
            self.interface_names[display_name] = interface_name
            display_names.append(display_name)
        self.interface_combobox['values'] = display_names

    def display_default_content(self):
        self.clear_main_window()
        self.default_module.pack(fill='both', expand=True)

    def display_module(self, event=None):
        display_name = self.module_var.get()
        selected_module = self.module_names.get(display_name, None)

        if selected_module:
            module_to_display = self.load_cached_module(selected_module)
            if self.main_app is not None:
                # Destroying the old view stops anything it started, such as a
                # running data logger, instead of leaving it running unseen.
                self.main_app.destroy()
                self.main_app = None
            self.clear_main_window()
            self.main_app = module_to_display.ModuleApplication(self.main_window, None, self)
            self.main_app.set_interface(self.current_interface)
            self.subtitle_label.config(text=display_name)

    def display_interface_settings(self, event=None):
        display_name = self.interface_var.get()
        selected_interface = self.interface_names.get(display_name, None)

        if selected_interface:
            interface_module = self.load_cached_interface(selected_interface)
            if self.current_interface:
                self.current_interface.destroy()
            self.current_interface = interface_module.Interface(self.interface_wireframe, self)
            self.current_interface.pack(fill='both', expand=True)
            if self.main_app:
                self.main_app.set_interface(self.current_interface)

    def load_cached_module(self, module_name):
        if module_name not in self.loaded_modules:
            module_to_display = self.import_module("modules.%s" % module_name)
            self.loaded_modules[module_name] = module_to_display
            self.update_debug("Imported module: %s" % module_name)
        else:
            module_to_display = self.loaded_modules[module_name]
        return module_to_display

    def load_cached_interface(self, interface_name):
        if interface_name not in self.loaded_interfaces:
            interface_module = self.import_module("interfaces.%s" % interface_name)
            self.loaded_interfaces[interface_name] = interface_module
            self.update_debug("Imported interface: %s" % interface_name)
        else:
            interface_module = self.loaded_interfaces[interface_name]
        return interface_module

    def import_module(self, module_path):
        return importlib.import_module(module_path)

    def clear_main_window(self):
        for widget in self.main_window.winfo_children():
            widget.pack_forget()

    # ------------------------------------------------------------------
    # interface hooks
    # ------------------------------------------------------------------
    def on_interface_connected(self, interface):
        version = getattr(interface, "firmware_version", None)
        text = "●  Connected"
        if version:
            text += "  ·  firmware %s" % version
        self.connection_label.config(text=text, style="HeaderSuccess.TLabel")
        self._forward_to_module("on_interface_connected", interface)

    def on_interface_disconnected(self, interface):
        self.connection_label.config(text="●  Not connected", style="HeaderDim.TLabel")
        self._forward_to_module("on_interface_disconnected", interface)

    def _forward_to_module(self, hook, interface):
        callback = getattr(self.main_app, hook, None) if self.main_app else None
        if callable(callback):
            try:
                callback(interface)
            except Exception as exc:  # pragma: no cover - defensive
                self.update_debug("%s failed: %s" % (hook, exc))

    # ------------------------------------------------------------------
    # debug pane
    # ------------------------------------------------------------------
    def update_debug(self, message):
        """Append a line to the debug pane. Safe to call from any thread."""
        if threading.current_thread() is not threading.main_thread():
            self._debug_queue.put(message)
            return
        self._write_debug(message)

    def _write_debug(self, message):
        if not hasattr(self, 'debug_text'):
            # Widgets are not up yet (module discovery runs first); keep the
            # message and flush it once the pane exists.
            self._pending_debug.append(message)
            print("Debug:", message)
            return
        self.debug_text.config(state='normal')
        self.debug_text.insert('end', message + '\n')
        self._trim_debug()
        self.debug_text.see('end')
        self.debug_text.config(state='disabled')
        self._show_status(message)

    def _show_status(self, message):
        if not hasattr(self, "status_label"):
            return
        line = " ".join(str(message).split())
        if len(line) > 140:
            line = line[:139] + "…"
        try:
            self.status_label.config(text=line)
        except tk.TclError:  # pragma: no cover - teardown race
            pass

    def _trim_debug(self):
        try:
            lines = int(self.debug_text.index('end-1c').split('.')[0])
        except (tk.TclError, ValueError):  # pragma: no cover - defensive
            return
        if lines > DEBUG_MAX_LINES:
            self.debug_text.delete('1.0', '%d.0' % (lines - DEBUG_MAX_LINES + 1))

    def _drain_debug_queue(self):
        if self._closing or not self.winfo_exists():
            return
        while True:
            try:
                message = self._debug_queue.get_nowait()
            except queue.Empty:
                break
            self._write_debug(message)
        self._schedule_debug_drain()

    def _schedule_debug_drain(self):
        """Keep exactly one pending drain job.

        Cancelling the stored id first means a direct call to
        _drain_debug_queue() collapses into the existing chain instead of
        starting a second one that would outlive the window.
        """
        if self._closing:
            return
        if self._debug_job is not None:
            try:
                self.after_cancel(self._debug_job)
            except tk.TclError:  # already fired
                pass
        self._debug_job = self.after(DEBUG_POLL_MS, self._drain_debug_queue)

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def register_cleanup(self, callback):
        """Register a callable to run before the window closes."""
        self._cleanup_callbacks.append(callback)

    def on_close(self):
        self._closing = True
        for callback in list(self._cleanup_callbacks):
            try:
                callback()
            except Exception:  # pragma: no cover - teardown best effort
                pass
        if self._debug_job is not None:
            try:
                self.after_cancel(self._debug_job)
            except Exception:  # pragma: no cover - teardown best effort
                pass
            self._debug_job = None
        if self.current_interface is not None:
            try:
                self.current_interface.destroy()
            except Exception:  # pragma: no cover - teardown best effort
                pass
        self.destroy()


def selftest():
    """Start the app, verify it found its plugins, and exit.

    Useful for checking an installed binary or a fresh Linux box:
    ``obi-linux --selftest``. Needs a display (or xvfb-run).
    """
    try:
        app = OBI()
        app.update()
    except tk.TclError as exc:
        print("FAIL: could not open a window: %s" % exc)
        print("Run it from a desktop session, or under 'xvfb-run -a'.")
        return 1

    modules = list(app.module_combobox["values"])
    interfaces = list(app.interface_combobox["values"])
    print("theme     : %s" % app.theme.name)
    print("modules   : %s" % (", ".join(modules) or "none"))
    print("interfaces: %s" % (", ".join(interfaces) or "none"))

    failures = []
    # Actually build every view: an import that succeeds but a widget tree
    # that explodes is exactly the kind of packaging bug worth catching here.
    for name in interfaces:
        try:
            app.interface_var.set(name)
            app.display_interface_settings()
            app.update()
        except Exception as exc:
            failures.append("interface %s: %s: %s" % (name, type(exc).__name__, exc))
    for name in modules:
        try:
            app.module_var.set(name)
            app.display_module()
            app.update()
        except Exception as exc:
            failures.append("module %s: %s: %s" % (name, type(exc).__name__, exc))

    app.on_close()

    for failure in failures:
        print("FAIL: %s" % failure)
    if failures or not modules or not interfaces:
        if not modules or not interfaces:
            print("FAIL: no plugins were loaded")
        return 1
    print("OK")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        from core import __version__
        print("OBI Linux %s" % __version__)
        return 0
    if "--selftest" in argv:
        return selftest()
    if "--help" in argv or "-h" in argv:
        print("OBI Linux - battery diagnostics\n\n"
              "  obi-linux              start the application\n"
              "  obi-linux --selftest   check that the installation works\n"
              "  obi-linux --version    print the version\n\n"
              "Log a battery over time from the command line with obi-log.\n"
              "Environment:\n"
              "  OBI_THEME=dark|light   override the desktop colour scheme\n"
              "  OBI_SCALING=1.5        override the desktop text scaling\n"
              "  OBI_EXTRA_PORTS=...    extra serial devices for the picker\n"
              "  OBI_TIMEOUT, OBI_BOOT_DELAY  serial timing")
        return 0

    OBI().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
