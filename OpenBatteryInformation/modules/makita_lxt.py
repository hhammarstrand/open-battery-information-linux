import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from components import theme
from components.logging_frame import LoggingFrame
from components.theme import PAD_L, PAD_M, PAD_S, PAD_XS, Card
from core import makita
from core.makita import MakitaClient


def get_display_name():
    return "Makita LXT"


NO_INTERFACE_MESSAGE = ("No interface selected. Please select and connect an interface "
                        "from the sidebar.")

#: Triage thresholds for the cell spread, used only to colour the summary.
#: Rules of thumb for a 5 cell LXT pack, not manufacturer specifications: a
#: healthy pack sits within a few tens of millivolts, and a spread that keeps
#: growing points at one weak cell.
CELL_SPREAD_OK_V = 0.030
CELL_SPREAD_WARN_V = 0.080

initial_data = {
    "Model": "",
    "Charge count*": "",
    "State": "",
    "Status code": "",
    "Pack Voltage": "",
    "Cell 1 Voltage": "",
    "Cell 2 Voltage": "",
    "Cell 3 Voltage": "",
    "Cell 4 Voltage": "",
    "Cell 5 Voltage": "",
    "Cell Voltage Difference": "",
    "Temperature Sensor 1": "",
    "Temperature Sensor 2": "",
    "ROM ID": "",
    "Manufacturing date": "",
    "Battery message": "",
    "Capacity": "",
    "Battery type": "",
}


class Metric(Card):
    """A single headline number, the way status dashboards present them."""

    def __init__(self, parent, caption):
        super().__init__(parent, padding=PAD_M)
        ttk.Label(self, text=caption, style="CardCaption.TLabel").pack(anchor="w")
        self.value_label = ttk.Label(self, text="—", style="CardMetric.TLabel")
        self.value_label.pack(anchor="w")
        self.detail_label = ttk.Label(self, text="", style="CardDim.TLabel")
        self.detail_label.pack(anchor="w")

    def set(self, value, detail="", tone=""):
        style = "CardMetric%s.TLabel" % tone.capitalize() if tone else "CardMetric.TLabel"
        self.value_label.config(text=value, style=style)
        self.detail_label.config(text=detail)

    def clear(self):
        self.value_label.config(text="—", style="CardMetric.TLabel")
        self.detail_label.config(text="")


class ModuleApplication(ttk.Frame):
    def __init__(self, parent, interface_module=None, obi_instance=None):
        super().__init__(parent, style="Window.TFrame")
        self.parent = parent
        self.interface = None
        self.interface_module = interface_module
        self.obi_instance = obi_instance
        self.client = None
        self.battery_present = False
        self.create_widgets()

    # ------------------------------------------------------------------
    def set_interface(self, interface_instance):
        self.interface = interface_instance
        self.client = MakitaClient(interface_instance) if interface_instance else None

    @property
    def command_version(self):
        """Detected command set: ``""`` (standard), ``"F0513"`` or ``None``."""
        return self.client.command_version if self.client else None

    # ------------------------------------------------------------------
    # widgets
    # ------------------------------------------------------------------
    def create_widgets(self):
        # Packing order is the layout contract here: everything with a fixed
        # height claims its space first, and the table - the only flexible
        # element - absorbs whatever is left. Pack the table earlier and a
        # short window silently clips the panels below it.
        self.build_actions()
        self.build_metrics()

        self.logging_frame = LoggingFrame(
            self, self.prepare_logging, prefix="makita-lxt",
            obi_instance=self.obi_instance)
        self.logging_frame.pack(side='bottom', fill='x', pady=(PAD_M, 0))

        self.build_table()

        self.pack(fill='both', expand=True)
        self.insert_battery_data(initial_data)

    def build_actions(self):
        card = Card(self)
        card.pack(fill='x')

        self.buttons = []
        groups = (
            ("Read data", (
                ("Read model", self.on_read_static_click, "Accent.TButton", False),
                ("Read cell data", self.on_read_data_click, "TButton", True),
            )),
            ("Function test", (
                ("LEDs on", self.on_all_leds_on_click, "TButton", True),
                ("LEDs off", self.on_all_leds_off_click, "TButton", True),
            )),
            ("Reset battery", (
                ("Clear errors", self.on_reset_errors_click, "TButton", True),
                ("Reset message", self.on_reset_message_click, "TButton", True),
            )),
        )

        colors = theme.current().colors
        for index, (caption, buttons) in enumerate(groups):
            if index:
                divider = tk.Frame(card, background=colors["border"], width=1)
                divider.pack(side="left", fill="y", padx=PAD_M)

            group = ttk.Frame(card, style="Card.TFrame")
            group.pack(side="left", anchor="n")
            ttk.Label(group, text=caption, style="CardCaption.TLabel").pack(
                anchor="w", pady=(0, PAD_XS))

            # Stacked rather than side by side: three short columns always
            # fit, where one long row gets clipped at the minimum window width.
            for index, (text, command, style, disabled) in enumerate(buttons):
                button = ttk.Button(group, text=text, command=command, style=style,
                                    width=16)
                button.pack(fill="x", pady=(0 if not index else PAD_XS, 0))
                if disabled:
                    button.state(["disabled"])
                self.buttons.append(button)

    def build_metrics(self):
        row = ttk.Frame(self, style="Window.TFrame")
        row.pack(fill='x', pady=(PAD_M, 0))

        self.metrics = {}
        for key, caption in (("pack", "Pack voltage"),
                             ("spread", "Cell spread"),
                             ("state", "Battery state")):
            metric = Metric(row, caption)
            metric.pack(side="left", fill="x", expand=True,
                        padx=(0, PAD_M) if key != "state" else 0)
            self.metrics[key] = metric

    def build_table(self):
        footer = ttk.Frame(self, style="Window.TFrame")
        footer.pack(side='bottom', fill='x', pady=(PAD_S, 0))
        ttk.Label(footer, text="* charge count is a best-effort reading",
                  style="WindowCaption.TLabel").pack(side="left")
        ttk.Button(footer, text="Clear", command=self.clear_data).pack(side="right")
        ttk.Button(footer, text="Copy", command=self.copy_to_clipboard).pack(
            side="right", padx=(0, PAD_S))

        card = Card(self, padding=1)
        card.pack(side='top', fill='both', expand=True, pady=(PAD_M, 0))

        container = ttk.Frame(card, style="Card.TFrame")
        container.pack(fill='both', expand=True)

        scroll = ttk.Scrollbar(container, orient="vertical")
        scroll.pack(side="right", fill="y")

        # A small requested height on purpose: the table expands into whatever
        # space is left rather than pushing the panels below it off screen.
        self.tree = ttk.Treeview(container, columns=("Value",), height=6,
                                 yscrollcommand=scroll.set)
        scroll.config(command=self.tree.yview)

        self.tree.heading("#0", text="PARAMETER", anchor="w")
        self.tree.heading("Value", text="VALUE", anchor="w")
        self.tree.column("#0", width=280, anchor="w")
        self.tree.column("Value", anchor="w")

        colors = theme.current().colors
        self.tree.tag_configure('evenrow', background=colors["view"],
                                foreground=colors["text"])
        self.tree.tag_configure('oddrow', background=colors["row_alt"],
                                foreground=colors["text"])

        self.tree.pack(fill='both', expand=True)

    # ------------------------------------------------------------------
    def enable_all_buttons(self):
        """Enable all buttons."""
        for button in self.buttons:
            button.state(["!disabled"])

    def _check_interface(self):
        if not self.interface or not self.client:
            messagebox.showerror("Error", NO_INTERFACE_MESSAGE)
            return False
        return True

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def on_read_static_click(self):
        if not self._check_interface():
            return

        try:
            message = self.client.read_message()
            self.insert_battery_data({
                "ROM ID": message["rom_id"],
                "Battery message": message["battery_message"],
                "Charge count*": message["charge_count"],
                "State": message["state"],
                "Status code": message["status_code"],
                "Manufacturing date": message["manufacturing_date"],
                "Capacity": "%s Ah" % message["capacity_ah"],
                "Battery type": message["battery_type"],
            })
            self.metrics["state"].set(
                message["state"],
                "status %s · %s charges" % (message["status_code"], message["charge_count"]),
                tone="error" if message["locked"] else "success")
            self.battery_present = True
        except ConnectionError as e:
            messagebox.showerror("Connection Error",
                                 "Could not communicate with the battery:\n\n%s" % e)
            return
        except (IndexError, ValueError) as e:
            messagebox.showerror("Data Error",
                                 "Received an unexpected response while reading battery info:"
                                 "\n\n%s: %s" % (type(e).__name__, e))
            return
        except Exception as e:
            messagebox.showerror("Error", "Failed to read battery static data:\n\n%s: %s"
                                 % (type(e).__name__, e))
            return

        try:
            model, version = self.client.detect_model()
        except Exception as e:
            messagebox.showerror("Unsupported Battery", str(e))
            return

        if version == makita.F0513:
            messagebox.showwarning("Limited", "This model only supports diagnostics")
            self.buttons[1].state(["!disabled"])
        else:
            self.enable_all_buttons()

        self.insert_battery_data({"Model": model})

    def on_read_data_click(self):
        if not self._check_interface():
            return

        try:
            data = self.client.read_pack_data()
            self.insert_battery_data({
                "Pack Voltage": data["pack_v"],
                "Cell 1 Voltage": data["cell1_v"],
                "Cell 2 Voltage": data["cell2_v"],
                "Cell 3 Voltage": data["cell3_v"],
                "Cell 4 Voltage": data["cell4_v"],
                "Cell 5 Voltage": data["cell5_v"],
                "Cell Voltage Difference": data["cell_delta_v"],
                "Temperature Sensor 1": data["temp1_c"],
                "Temperature Sensor 2": "" if data["temp2_c"] is None else data["temp2_c"],
            })
            self.update_metrics(data)
        except ConnectionError as e:
            messagebox.showerror("Connection Error",
                                 "Lost communication while reading battery data:\n\n%s" % e)
        except (IndexError, ValueError) as e:
            messagebox.showerror("Data Error",
                                 "Received an unexpected response while reading battery data:"
                                 "\n\n%s: %s" % (type(e).__name__, e))
        except Exception as e:
            messagebox.showerror("Error", "Failed to read battery data:\n\n%s: %s"
                                 % (type(e).__name__, e))

    def update_metrics(self, data):
        cells = [data["cell%d_v" % i] for i in range(1, makita.CELL_COUNT + 1)]
        self.metrics["pack"].set(
            "%.2f V" % data["pack_v"],
            "%.3f – %.3f V per cell" % (min(cells), max(cells)))

        spread = data["cell_delta_v"]
        if spread <= CELL_SPREAD_OK_V:
            tone, detail = "success", "cells are balanced"
        elif spread <= CELL_SPREAD_WARN_V:
            tone, detail = "warning", "worth watching over time"
        else:
            tone, detail = "error", "one cell is lagging"
        self.metrics["spread"].set("%d mV" % round(spread * 1000), detail, tone=tone)

    # ------------------------------------------------------------------
    # logging
    # ------------------------------------------------------------------
    def prepare_logging(self, options):
        """Validate state and hand the logger a sampling function.

        Called from :class:`components.logging_frame.LoggingFrame` when the
        user presses "Start logging".
        """
        if not self.interface or not self.client:
            raise RuntimeError(NO_INTERFACE_MESSAGE)
        if not getattr(self.interface, "is_connected", True):
            raise RuntimeError("Connect to the adapter first: pick the serial port in the "
                               "sidebar and press Connect.")

        client = self.client
        try:
            if client.command_version is None:
                client.detect_model()
            if client.rom_id is None:
                client.read_message()
        except ConnectionError as exc:
            raise RuntimeError("Could not talk to the battery:\n\n%s" % exc)
        except Exception as exc:
            raise RuntimeError("Could not identify the battery:\n\n%s: %s"
                               % (type(exc).__name__, exc))

        include_status = bool(options.get("include_status"))
        fieldnames = makita.log_fieldnames(include_status)

        def sample():
            return client.sample(include_status=include_status, trace=False)

        return fieldnames, sample

    # ------------------------------------------------------------------
    # function test / reset
    # ------------------------------------------------------------------
    def on_all_leds_on_click(self):
        if not self._check_interface():
            return

        try:
            self.interface.request(makita.TESTMODE_CMD)
            self.interface.request(makita.LEDS_ON_CMD)
        except ConnectionError as e:
            messagebox.showerror("Connection Error",
                                 "Lost communication while turning LEDs on:\n\n%s" % e)
        except Exception as e:
            messagebox.showerror("Error", "Failed to turn LEDs on:\n\n%s: %s"
                                 % (type(e).__name__, e))

    def on_all_leds_off_click(self):
        if not self._check_interface():
            return

        try:
            if self.command_version == makita.F0513:
                self.interface.request(makita.F0513_TESTMODE_CMD)
            else:
                self.interface.request(makita.TESTMODE_CMD)

            self.interface.request(makita.LEDS_OFF_CMD)
        except ConnectionError as e:
            messagebox.showerror("Connection Error",
                                 "Lost communication while turning LEDs off:\n\n%s" % e)
        except Exception as e:
            messagebox.showerror("Error", "Failed to turn LEDs off:\n\n%s: %s"
                                 % (type(e).__name__, e))

    def on_reset_errors_click(self):
        if not self._check_interface():
            return

        try:
            self.interface.request(makita.TESTMODE_CMD)
            self.interface.request(makita.RESET_ERROR_CMD)
        except ConnectionError as e:
            messagebox.showerror("Connection Error",
                                 "Lost communication while resetting errors:\n\n%s" % e)
        except Exception as e:
            messagebox.showerror("Error", "Failed to reset errors:\n\n%s: %s"
                                 % (type(e).__name__, e))

    def on_reset_message_click(self):
        if not self._check_interface():
            return

        # TODO: Replace clean frame with the frame from the battery.
        # 1. Read frame
        # 2. set nibble 0
        # 3. write as usual
        messagebox.showwarning("Not Implemented", "This feature is currently under development.")

    # ------------------------------------------------------------------
    def insert_battery_data(self, data):
        for idx, (parameter, value) in enumerate(data.items()):
            item_id = None
            for item in self.tree.get_children():
                if self.tree.item(item, "text") == parameter:
                    item_id = item
                    break

            if item_id:
                self.tree.item(item_id, values=(value,))
            else:
                tag = 'evenrow' if len(self.tree.get_children()) % 2 == 0 else 'oddrow'
                self.tree.insert("", "end", text=parameter, values=(value,), tags=(tag,))

    def copy_to_clipboard(self):
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("No Selection", "No rows selected to copy!")
            return

        rows = []
        for item in selected_items:
            parameter = self.tree.item(item, 'text')
            values = self.tree.item(item, 'values')
            rows.append('\t'.join([parameter] + [str(value) for value in values]))

        self.clipboard_clear()
        self.clipboard_append('\n'.join(rows))
        messagebox.showinfo("Copied", "Selected rows have been copied to the clipboard.")

    def clear_data(self):
        self.insert_battery_data(initial_data)
        for metric in self.metrics.values():
            metric.clear()
