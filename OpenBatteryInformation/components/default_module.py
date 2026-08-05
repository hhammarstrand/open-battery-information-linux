import tkinter as tk
from tkinter import ttk

from components.theme import PAD_L, PAD_M, PAD_S


class DefaultModule(ttk.Frame):
    """Empty state shown before a module is picked.

    Modern desktop applications explain the first step rather than showing a
    blank pane, so this doubles as a three step quick start.
    """

    STEPS = (
        ("1", "Pick a module", "Choose the battery family in the sidebar."),
        ("2", "Pick an interface", "Select your ArduinoOBI adapter's serial port and connect."),
        ("3", "Read the battery", "Read the model, then the cell data - or start logging over time."),
    )

    def __init__(self, parent):
        super().__init__(parent, style="Window.TFrame")
        self.parent = parent
        self.create_widgets()

    def create_widgets(self):
        centre = ttk.Frame(self, style="Window.TFrame")
        centre.place(relx=0.5, rely=0.42, anchor="center")

        ttk.Label(centre, text="Open Battery Information",
                  style="WindowTitle.TLabel").pack(anchor="center")
        ttk.Label(centre, text="Diagnose and repair battery packs instead of scrapping them.",
                  style="WindowSubtitle.TLabel").pack(anchor="center", pady=(PAD_S, PAD_L))

        for number, title, detail in self.STEPS:
            row = ttk.Frame(centre, style="Window.TFrame")
            row.pack(fill="x", pady=PAD_S)
            ttk.Label(row, text=number, style="WindowAccent.TLabel",
                      width=2).pack(side="left", padx=(0, PAD_M))
            text = ttk.Frame(row, style="Window.TFrame")
            text.pack(side="left", fill="x")
            ttk.Label(text, text=title, style="WindowHeading.TLabel").pack(anchor="w")
            ttk.Label(text, text=detail, style="WindowDim.TLabel").pack(anchor="w")
