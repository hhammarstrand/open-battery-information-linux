"""Reusable "log this battery over time" panel for OBI modules.

A module supplies a ``prepare`` callback that validates the connection and
returns ``(fieldnames, sample_fn)``; everything else - file handling, the
worker thread, status updates and cleanup - happens here.

Thread safety: :class:`core.sampling.SampleLogger` calls back from its own
thread, so events are pushed onto a queue and drained from the Tk event loop.
Nothing outside this queue touches a widget from the worker thread.
"""

import functools
import os
import platform
import queue
import subprocess
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk

from core import sampling

#: Give up after this many failed samples in a row (battery pulled, adapter
#: unplugged, ...) instead of filling the log with error rows forever.
STOP_AFTER_ERRORS = 10

POLL_MS = 400


class LoggingFrame(tk.LabelFrame):
    def __init__(self, parent, prepare, prefix="obi", obi_instance=None,
                 text="Data logging (over time)"):
        super().__init__(parent, text=text, padx=10, pady=10)
        self.prepare = prepare
        self.prefix = prefix
        self.obi_instance = obi_instance

        self.logger = None
        self._events = queue.Queue()
        self._poll_job = None

        self._build()
        self._register_cleanup()

    # ------------------------------------------------------------------
    def _build(self):
        self.columnconfigure(1, weight=1)

        controls = tk.Frame(self)
        controls.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        tk.Label(controls, text="Interval (s):").pack(side="left")
        self.interval_var = tk.StringVar(value="10")
        self.interval_spin = tk.Spinbox(
            controls, from_=1, to=3600, width=6, textvariable=self.interval_var)
        self.interval_spin.pack(side="left", padx=(4, 12))

        self.include_status_var = tk.BooleanVar(value=False)
        self.status_check = tk.Checkbutton(
            controls, text="Include state / charge count (slower)",
            variable=self.include_status_var)
        self.status_check.pack(side="left", padx=(0, 12))

        tk.Label(controls, text="Format:").pack(side="left")
        self.format_var = tk.StringVar(value="csv")
        self.format_box = ttk.Combobox(
            controls, textvariable=self.format_var, values=["csv", "jsonl"],
            state="readonly", width=6)
        self.format_box.pack(side="left", padx=4)

        tk.Label(self, text="File:").grid(row=1, column=0, sticky="w")
        self.path_var = tk.StringVar(value="")
        self.path_entry = tk.Entry(self, textvariable=self.path_var)
        self.path_entry.grid(row=1, column=1, sticky="ew", padx=6)

        buttons = tk.Frame(self)
        buttons.grid(row=1, column=2, sticky="e")
        self.browse_button = tk.Button(buttons, text="Browse...", command=self.browse)
        self.browse_button.pack(side="left", padx=2)
        self.open_button = tk.Button(buttons, text="Open folder", command=self.open_folder)
        self.open_button.pack(side="left", padx=2)
        self.start_button = tk.Button(buttons, text="Start logging", width=14,
                                      command=self.toggle)
        self.start_button.pack(side="left", padx=2)

        self.status_label = tk.Label(self, text="Idle. Logs are written to %s"
                                     % sampling.log_dir(), anchor="w", justify="left")
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _register_cleanup(self):
        register = getattr(self.obi_instance, "register_cleanup", None)
        if callable(register):
            register(functools.partial(self.stop_logging, wait=True))
        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------------
    # user actions
    # ------------------------------------------------------------------
    def browse(self):
        fmt = self.format_var.get()
        initial = self.path_var.get() or sampling.default_log_path(self.prefix, fmt)
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save battery log as",
            initialdir=os.path.dirname(initial),
            initialfile=os.path.basename(initial),
            defaultextension="." + fmt,
            filetypes=[("CSV", "*.csv"), ("JSON Lines", "*.jsonl"), ("All files", "*")])
        if path:
            self.path_var.set(path)

    def open_folder(self):
        path = self.path_var.get()
        folder = os.path.dirname(path) if path else sampling.log_dir()
        try:
            os.makedirs(folder)
        except OSError:
            pass
        try:
            system = platform.system()
            if system == "Windows":  # pragma: no cover - not our target
                os.startfile(folder)  # noqa: S606
            elif system == "Darwin":  # pragma: no cover - not our target
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            messagebox.showinfo("Log folder", "%s\n\n(could not open a file manager: %s)"
                                % (folder, exc))

    def toggle(self):
        if self.logger is not None and self.logger.is_running:
            self.stop_logging()
        else:
            self.start_logging()

    # ------------------------------------------------------------------
    def start_logging(self):
        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messagebox.showerror("Invalid interval",
                                 "The logging interval must be a positive number of seconds.")
            return

        options = {"include_status": bool(self.include_status_var.get())}
        try:
            fieldnames, sample_fn = self.prepare(options)
        except Exception as exc:
            messagebox.showerror("Cannot start logging", str(exc))
            return

        fmt = self.format_var.get()
        path = self.path_var.get().strip() or sampling.default_log_path(self.prefix, fmt)
        writer = sampling.make_writer(path, fieldnames, fmt=fmt, append=True)
        logger = sampling.SampleLogger(
            sample_fn, writer, interval=interval,
            on_event=self._events.put,
            stop_after_errors=STOP_AFTER_ERRORS,
            name="obi-logger")

        try:
            logger.start()
        except ValueError as exc:
            # Column mismatch on append; the message already explains itself.
            messagebox.showerror("Cannot start logging", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("Cannot start logging",
                                 "Could not open %s:\n\n%s: %s" % (path, type(exc).__name__, exc))
            return

        self.logger = logger
        self.path_var.set(path)
        self._set_running(True)
        self._status("Logging to %s ..." % os.path.basename(path))
        self._debug("Started logging to %s (every %gs)" % (path, interval))
        self._schedule_poll()

    def stop_logging(self, wait=False, timeout=5.0):
        """Stop the run. ``wait`` blocks until the file is closed.

        The application shutdown path waits, so a sample that is in flight
        finishes and the log is closed properly instead of being cut off by
        the interpreter exiting.
        """
        logger = self.logger
        if logger is not None and logger.is_running:
            self._status("Stopping ...")
            logger.stop(wait=wait, timeout=timeout)

    # ------------------------------------------------------------------
    # event pump
    # ------------------------------------------------------------------
    def _schedule_poll(self):
        if self._poll_job is None:
            self._poll_job = self.after(POLL_MS, self._poll)

    def _poll(self):
        self._poll_job = None
        if not self.winfo_exists():  # pragma: no cover - teardown race
            return
        finished = None
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if event.get("type") == "finished":
                finished = event
            elif event.get("type") == "error":
                self._debug("Logging: %s" % event.get("message"))

        logger = self.logger
        if finished is not None:
            self._set_running(False)
            reason = {
                "sample-limit": "sample limit reached",
                "duration-limit": "time limit reached",
                "too-many-errors": "stopped after %d failed samples in a row" % STOP_AFTER_ERRORS,
                "write-failed": "stopped, writing failed",
            }.get(finished.get("reason"), "stopped")
            self._status("Log %s - %d samples, %d errors -> %s"
                         % (reason, finished["samples"], finished["errors"],
                            os.path.basename(finished["path"])))
            self._debug("Logging %s after %d samples (%d errors): %s"
                        % (reason, finished["samples"], finished["errors"], finished["path"]))
            self.logger = None
            return

        if logger is not None and logger.is_running:
            text = "Logging to %s - %d samples, %d errors" % (
                os.path.basename(logger.path), logger.samples, logger.errors)
            if logger.last_error:
                text += "\nLast error: %s" % _shorten(logger.last_error, 90)
            self._status(text)
            self._schedule_poll()

    def _alive(self):
        """False once Tk has started tearing this frame down."""
        try:
            return bool(self.winfo_exists())
        except tk.TclError:  # pragma: no cover - interpreter already gone
            return False

    def _set_running(self, running):
        if not self._alive():
            return
        state = "disabled" if running else "normal"
        try:
            self.start_button.config(text="Stop logging" if running else "Start logging")
            for widget in (self.interval_spin, self.status_check, self.path_entry,
                           self.browse_button):
                widget.config(state=state)
            self.format_box.config(state="disabled" if running else "readonly")
        except tk.TclError:  # pragma: no cover - teardown race
            pass

    def _status(self, text):
        # Status updates during teardown (stopping a logger because the view
        # is being destroyed) must not raise out of the Tk callback.
        if not self._alive():
            return
        try:
            self.status_label.config(text=text)
        except tk.TclError:  # pragma: no cover - teardown race
            pass

    def _debug(self, message):
        app = self.obi_instance
        if app is not None and hasattr(app, "update_debug"):
            app.update_debug(message)

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:  # pragma: no cover - teardown best effort
                pass
            self._poll_job = None
        self.stop_logging()


def _shorten(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"
