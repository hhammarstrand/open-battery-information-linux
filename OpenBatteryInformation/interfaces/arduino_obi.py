import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

import serial

from core import serial_ports
from core.obi_link import ObiLink, ObiLinkError


def get_display_name():
    return "Arduino OBI"


class Interface(tk.Frame):
    """Sidebar widget for connecting to the ArduinoOBI adapter.

    The actual protocol lives in :class:`core.obi_link.ObiLink` so that the
    command line logger can talk to the same hardware without tkinter.
    """

    def __init__(self, parent, obi_instance):
        super().__init__(parent)
        self.parent = parent
        self.obi_instance = obi_instance
        self.link = ObiLink(trace=self._trace)
        self._ports = []
        self.create_widgets()

    # ------------------------------------------------------------------
    # widgets
    # ------------------------------------------------------------------
    def create_widgets(self):
        serial_label = tk.Label(self, text="Serial Port:")
        serial_label.pack(pady=5)

        self.conf_port = ttk.Combobox(self, values=[], state="readonly", width=28)
        self.conf_port.pack(pady=5)

        self.show_all_var = tk.BooleanVar(value=False)
        show_all = tk.Checkbutton(
            self, text="Show non-USB ports", variable=self.show_all_var,
            command=self.refresh_serial_list)
        show_all.pack()

        self.connect_button = tk.Button(self, text="Connect", command=self.toggle_connection)
        self.connect_button.pack(pady=10)
        self.connect_button.config(width=20)

        self.refresh_button = tk.Button(self, text="Refresh port list", command=self.refresh_serial_list)
        self.refresh_button.pack(pady=10)
        self.refresh_button.config(width=20)

        self.version_label = tk.Label(self, anchor="w", width=20, text="Version:")
        self.version_label.pack(pady=5)

        self.refresh_serial_list(announce=False)

    # ------------------------------------------------------------------
    # port discovery
    # ------------------------------------------------------------------
    def refresh_serial_list(self, announce=True):
        previous = self.selected_port()
        self._ports = serial_ports.list_ports(include_non_usb=self.show_all_var.get())
        labels = [port.label for port in self._ports]
        self.conf_port["values"] = labels

        if previous:
            for index, port in enumerate(self._ports):
                if port.device == previous:
                    self.conf_port.current(index)
                    break
            else:
                self.conf_port.set("")
        elif len(self._ports) == 1:
            # Only one candidate: preselect it so the common case is one click.
            self.conf_port.current(0)

        if announce:
            if labels:
                self._trace("Found %d serial port(s)" % len(labels))
            else:
                self._trace("No USB serial adapters found. Check the cable, or enable "
                            "'Show non-USB ports'.")

    def get_available_serial_ports(self):
        """Kept for compatibility with code that inspects the interface."""
        return [port.device for port in self._ports]

    def selected_port(self):
        info = self._selected_port_info()
        return info.device if info else None

    def _selected_port_info(self):
        label = self.conf_port.get()
        for port in self._ports:
            if port.label == label:
                return port
        return None

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    @property
    def is_connected(self):
        return self.link.is_open

    def toggle_connection(self):
        if self.link.is_open:
            self.close_serial_port()
        else:
            self.open_serial_port()

    def open_serial_port(self):
        port = self._selected_port_info()
        if port is None:
            self._trace("No serial port selected. Please select a port from the dropdown.")
            messagebox.showwarning(
                "No port selected",
                "Select the serial port your ArduinoOBI adapter is on.\n\n"
                "If the list is empty, press 'Refresh port list' after plugging it in.")
            return

        device = port.stable_device
        # Opening the port resets an Arduino, so this blocks for a couple of
        # seconds. Say so instead of just freezing the window.
        self.connect_button.config(text="Connecting...", state="disabled")
        self.update_idletasks()
        try:
            self.link.open(device)
        except (serial.SerialException, ObiLinkError, OSError) as exc:
            self.link.close()
            self.connect_button.config(text="Connect", state="normal")
            advice = serial_ports.diagnose(port.device, exc)
            for line in advice.splitlines():
                self._trace(line)
            messagebox.showerror("Could not open %s" % port.device, advice)
            return
        finally:
            self.connect_button.config(state="normal")

        self._trace("Opened serial port: %s" % device)
        self.connect_button.config(text="Disconnect")
        self.update_version()
        self._notify("on_interface_connected")

    def close_serial_port(self):
        if self.link.is_open:
            self.link.close()
            self._trace("Closed serial port")
        self.connect_button.config(text="Connect")
        self.version_label.config(text="Version:")
        self._notify("on_interface_disconnected")

    def get_version(self):
        # Three attempts, not five: the boot wait in ObiLink.open() has
        # already given the board time to start, and every failed attempt
        # costs a full read timeout with the window unresponsive.
        return self.link.get_version(max_attempts=3)

    def update_version(self):
        try:
            self.version_label.config(text="Version: %s" % self.get_version())
        except Exception as exc:
            self.version_label.config(text="Version: unknown")
            self._trace("Could not read adapter version: %s" % exc)

    # ------------------------------------------------------------------
    def request(self, request, max_attempts=2, trace=True):
        """Send a frame to the adapter. See :meth:`core.obi_link.ObiLink.request`."""
        return self.link.request(request, max_attempts=max_attempts, trace=trace)

    # ------------------------------------------------------------------
    def _trace(self, message):
        if self.obi_instance is not None:
            self.obi_instance.update_debug(message)

    def _notify(self, hook):
        app = self.obi_instance
        callback = getattr(app, hook, None) if app is not None else None
        if callable(callback):
            callback(self)

    def destroy(self):
        try:
            self.link.close()
        except Exception:  # pragma: no cover - teardown best effort
            pass
        super().destroy()
