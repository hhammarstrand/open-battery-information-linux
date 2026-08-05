import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from components.logging_frame import LoggingFrame
from core import makita
from core.makita import MakitaClient


def get_display_name():
    return "Makita LXT"


NO_INTERFACE_MESSAGE = ("No interface selected. Please select and connect an interface "
                        "from the sidebar.")

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


class ModuleApplication(tk.Frame):
    def __init__(self, parent, interface_module=None, obi_instance=None):
        super().__init__(parent)
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

    def create_widgets(self):
        label = tk.Label(self, text=get_display_name(), font=('Helvetica', 16))
        label.pack(pady=20)

        columns_frame = tk.Frame(self)
        columns_frame.pack(fill='both', padx=20, pady=10)

        columns_frame.grid_columnconfigure(0, weight=1)
        column_frame = tk.LabelFrame(columns_frame, text="Read data")
        column_frame.grid(row=0, column=0, sticky='nsew', padx=10, pady=10)

        # Store buttons in a list for easy management
        self.buttons = []

        button1 = tk.Button(column_frame, text="Read battery model", command=self.on_read_static_click)
        button1.pack(pady=10)
        button1.config(width=20)
        self.buttons.append(button1)

        button2 = tk.Button(column_frame, text="Read battery data", command=self.on_read_data_click, state=tk.DISABLED)
        button2.pack(pady=10)
        button2.config(width=20)
        self.buttons.append(button2)

        columns_frame.grid_columnconfigure(1, weight=1)
        column_frame = tk.LabelFrame(columns_frame, text="Function test")
        column_frame.grid(row=0, column=1, sticky='nsew', padx=10, pady=10)

        button3 = tk.Button(column_frame, text="LED test ON", command=self.on_all_leds_on_click, state=tk.DISABLED)
        button3.pack(pady=10)
        button3.config(width=20)
        self.buttons.append(button3)

        button4 = tk.Button(column_frame, text="LED test OFF", command=self.on_all_leds_off_click, state=tk.DISABLED)
        button4.pack(pady=10)
        button4.config(width=20)
        self.buttons.append(button4)

        columns_frame.grid_columnconfigure(2, weight=1)
        column_frame = tk.LabelFrame(columns_frame, text="Reset battery")
        column_frame.grid(row=0, column=2, sticky='nsew', padx=10, pady=10)

        button5 = tk.Button(column_frame, text="Clear errors", command=self.on_reset_errors_click, state=tk.DISABLED)
        button5.pack(pady=10)
        button5.config(width=20)
        self.buttons.append(button5)

        button6 = tk.Button(column_frame, text="Reset battery message", command=self.on_reset_message_click, state=tk.DISABLED)
        button6.pack(pady=10)
        button6.config(width=20)
        self.buttons.append(button6)

        tree_frame = tk.Frame(self)
        tree_frame.pack(pady=10, padx=20, fill='both', expand=True)

        tree_scroll_y = tk.Scrollbar(tree_frame, orient="vertical")
        tree_scroll_y.pack(side="right", fill="y")

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("Value"),
            yscrollcommand=tree_scroll_y.set,
        )

        tree_scroll_y.config(command=self.tree.yview)

        self.tree.heading("#0", text="Parameter")
        self.tree.heading("Value", text="Value")

        self.tree.tag_configure('evenrow', background='lightgrey', foreground="black")
        self.tree.tag_configure('oddrow', background='white', foreground="black")

        self.tree.pack(pady=1, padx=1, fill='both', expand=True)

        button_frame = tk.Frame(self)
        button_frame.pack(pady=4, padx=1, anchor='center')

        copy_button = tk.Button(button_frame, text="Copy", command=self.copy_to_clipboard)
        copy_button.pack(side="left", padx=5)

        clear_button = tk.Button(button_frame, text="Clear", command=self.clear_data)
        clear_button.pack(side="left", padx=5)

        self.logging_frame = LoggingFrame(
            self, self.prepare_logging, prefix="makita-lxt",
            obi_instance=self.obi_instance)
        self.logging_frame.pack(fill='x', padx=20, pady=(6, 10))

        self.pack(fill='both', expand=True)

        self.insert_battery_data(initial_data)

    def enable_all_buttons(self):
        """Enable all buttons."""
        for button in self.buttons:
            button.config(state=tk.NORMAL)

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def _check_interface(self):
        if not self.interface or not self.client:
            messagebox.showerror("Error", NO_INTERFACE_MESSAGE)
            return False
        return True

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
                "Capacity": "%sAh" % message["capacity_ah"],
                "Battery type": message["battery_type"],
            })
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
            self.buttons[1].config(state=tk.NORMAL)
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
                if idx % 2 == 0:
                    self.tree.insert("", "end", text=parameter, values=(value,), tags=('evenrow',))
                else:
                    self.tree.insert("", "end", text=parameter, values=(value,), tags=('oddrow',))

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

        self.parent.clipboard_clear()
        self.parent.clipboard_append('\n'.join(rows))
        messagebox.showinfo("Copied", "Selected rows have been copied to the clipboard.")

    def clear_data(self):
        self.insert_battery_data(initial_data)
