"""Adwaita-inspired theming for the Tk interface.

Modern Linux desktops (GNOME, and by extension Pop!_OS and Ubuntu) settled on
a visual language that Tk can approximate honestly: flat surfaces, one pixel
borders instead of bevels, cards on a slightly darker window background, an
8 px spacing grid, the system font, a single accented primary action - and a
dark variant that follows the desktop preference.

None of this is a GTK integration. Tk draws its own widgets, so the palette
here is a deliberate reimplementation of the Adwaita colours rather than
something read from the running theme. What *is* read from the desktop is the
colour scheme preference and the text scaling factor.

Modules should not hardcode colours; use ``theme.current().colors[...]`` and
the ready-made style names below.
"""

import os
import subprocess
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

#: Colours follow libadwaita's named palette as closely as Tk allows.
PALETTES = {
    "light": {
        "window": "#fafafa",
        "sidebar": "#ebebeb",
        "header": "#ebebeb",
        "card": "#ffffff",
        "view": "#ffffff",
        "row_alt": "#f6f5f4",
        "border": "#cdc7c2",
        "text": "#202020",
        "dim": "#5e5c64",
        "button": "#f6f5f4",
        "button_hover": "#ebebeb",
        "button_active": "#dedcda",
        "accent": "#3584e4",
        "accent_hover": "#2b76d4",
        "accent_text": "#ffffff",
        "success": "#26a269",
        "warning": "#c07f00",
        "error": "#c01c28",
        "shadow": "#e0ded9",
    },
    "dark": {
        "window": "#242424",
        "sidebar": "#2c2c2c",
        "header": "#303030",
        "card": "#303030",
        "view": "#1e1e1e",
        "row_alt": "#262626",
        "border": "#3d3d3d",
        "text": "#ffffff",
        "dim": "#b5b5b5",
        "button": "#383838",
        "button_hover": "#454545",
        "button_active": "#4f4f4f",
        "accent": "#3584e4",
        "accent_hover": "#5093ea",
        "accent_text": "#ffffff",
        "success": "#57e389",
        "warning": "#f5c211",
        "error": "#ff7b63",
        "shadow": "#1c1c1c",
    },
}

#: Surfaces that widgets can sit on. Each gets its own label styles, because
#: ttk labels do not inherit the background of their parent.
SURFACES = ("Window", "Card", "Sidebar", "Header")

#: 8 px spacing grid.
PAD_XS, PAD_S, PAD_M, PAD_L = 4, 8, 12, 20

_current = None


def detect_scheme():
    """Return ``"dark"`` or ``"light"``.

    ``OBI_THEME`` wins, then the GNOME/GTK desktop preference, then light.
    """
    override = (os.environ.get("OBI_THEME") or "").strip().lower()
    if override in ("dark", "light"):
        return override

    value = _gsettings("org.gnome.desktop.interface", "color-scheme")
    if value:
        if "prefer-dark" in value:
            return "dark"
        if "prefer-light" in value or "default" in value:
            # 'default' means the user never chose; GTK renders light.
            return "light"

    theme_name = (_gsettings("org.gnome.desktop.interface", "gtk-theme") or "")
    if "dark" in theme_name.lower():
        return "dark"
    if "dark" in (os.environ.get("GTK_THEME") or "").lower():
        return "dark"
    return "light"


def detect_text_scaling():
    """Desktop text scaling factor (1.0 when unset or unknown)."""
    override = os.environ.get("OBI_SCALING")
    if override:
        try:
            return float(override)
        except ValueError:
            return 1.0
    value = _gsettings("org.gnome.desktop.interface", "text-scaling-factor")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _gsettings(schema, key):
    """Read one gsettings key, or None when unavailable.

    Wrapped tightly: this runs at start-up on desktops that may not have
    gsettings at all, and a hang here would freeze the splash-free app before
    it ever draws.
    """
    try:
        output = subprocess.run(
            ["gsettings", "get", schema, key],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=1.5)
    except (OSError, subprocess.SubprocessError):
        return None
    if output.returncode != 0:
        return None
    return output.stdout.decode("utf-8", "replace").strip().strip("'")


class Theme(object):
    def __init__(self, name):
        self.name = name if name in PALETTES else "light"
        self.colors = PALETTES[self.name]

    # ------------------------------------------------------------------
    def apply(self, root):
        colors = self.colors
        style = ttk.Style(root)
        try:
            style.theme_use("clam")  # the most configurable theme on Linux
        except tk.TclError:  # pragma: no cover - depends on the Tk build
            pass

        self._apply_fonts(root)
        self._apply_base(style, colors)
        self._apply_labels(style, colors)
        self._apply_buttons(style, colors)
        self._apply_inputs(style, colors)
        self._apply_treeview(style, colors)
        self._apply_scrollbars(style, colors)
        self._apply_options(root, colors)

        root.configure(background=colors["window"])
        return self

    # ------------------------------------------------------------------
    def _apply_fonts(self, root):
        """Respect the system UI font; only set sizes and weights."""
        scaling = detect_text_scaling()
        if scaling and abs(scaling - 1.0) > 0.01:
            try:
                # Tk's baseline on X11 is 96/72; fold the desktop's factor in.
                root.tk.call("tk", "scaling", (96.0 / 72.0) * scaling)
            except tk.TclError:  # pragma: no cover - depends on the Tk build
                pass

        base = tkfont.nametofont("TkDefaultFont")
        # Derive from the system font's family, never from a hardcoded one.
        # Note: Font(font=other, size=...) silently ignores every other option,
        # so the family has to be passed explicitly.
        family = base.actual("family")
        size = abs(int(base.actual("size") or 10))

        def derive(delta=0, weight="normal"):
            return tkfont.Font(root=root, family=family,
                               size=size + delta, weight=weight)

        self.fonts = {
            "body": base,
            "title": derive(8, "bold"),
            "subtitle": derive(2),
            "heading": derive(0, "bold"),
            "caption": derive(-1),
            "metric": derive(7, "bold"),
            "mono": tkfont.nametofont("TkFixedFont"),
        }

    def _apply_base(self, style, colors):
        style.configure(".",
                        background=colors["window"],
                        foreground=colors["text"],
                        fieldbackground=colors["view"],
                        bordercolor=colors["border"],
                        darkcolor=colors["window"],
                        lightcolor=colors["window"],
                        troughcolor=colors["window"],
                        focuscolor=colors["accent"],
                        selectbackground=colors["accent"],
                        selectforeground=colors["accent_text"],
                        borderwidth=0,
                        relief="flat")

        for surface in SURFACES:
            style.configure("%s.TFrame" % surface,
                            background=colors[surface.lower()])
        style.configure("Separator.TFrame", background=colors["border"])
        style.configure("TSeparator", background=colors["border"])

    def _apply_labels(self, style, colors):
        kinds = {
            "": ("text", "body"),
            "Title": ("text", "title"),
            "Subtitle": ("dim", "subtitle"),
            "Heading": ("text", "heading"),
            "Dim": ("dim", "body"),
            "Caption": ("dim", "caption"),
            "Metric": ("text", "metric"),
            "Accent": ("accent", "heading"),
            "Success": ("success", "heading"),
            "Warning": ("warning", "heading"),
            "Error": ("error", "heading"),
            # Headline numbers keep their size when they carry a status colour.
            "MetricAccent": ("accent", "metric"),
            "MetricSuccess": ("success", "metric"),
            "MetricWarning": ("warning", "metric"),
            "MetricError": ("error", "metric"),
        }
        for surface in SURFACES:
            background = colors[surface.lower()]
            for kind, (color_key, font_key) in kinds.items():
                name = "%s%s.TLabel" % (surface, kind)
                style.configure(name,
                                background=background,
                                foreground=colors[color_key],
                                font=self.fonts[font_key])

    def _apply_buttons(self, style, colors):
        style.configure("TButton",
                        background=colors["button"],
                        foreground=colors["text"],
                        bordercolor=colors["border"],
                        lightcolor=colors["button"],
                        darkcolor=colors["button"],
                        borderwidth=1,
                        focusthickness=0,
                        relief="flat",
                        anchor="center",
                        padding=(PAD_M, PAD_S))
        style.map("TButton",
                  background=[("disabled", colors["window"]),
                              ("pressed", colors["button_active"]),
                              ("active", colors["button_hover"])],
                  foreground=[("disabled", colors["dim"])],
                  bordercolor=[("focus", colors["accent"])])

        style.configure("Accent.TButton",
                        background=colors["accent"],
                        foreground=colors["accent_text"],
                        bordercolor=colors["accent"],
                        lightcolor=colors["accent"],
                        darkcolor=colors["accent"])
        style.map("Accent.TButton",
                  background=[("disabled", colors["button"]),
                              ("pressed", colors["accent_hover"]),
                              ("active", colors["accent_hover"])],
                  foreground=[("disabled", colors["dim"])])

        # Toolbar buttons sit directly on the header, with no frame at rest.
        style.configure("Header.TButton",
                        background=colors["header"],
                        bordercolor=colors["header"],
                        lightcolor=colors["header"],
                        darkcolor=colors["header"],
                        padding=(PAD_S, PAD_XS))
        style.map("Header.TButton",
                  background=[("pressed", colors["button_active"]),
                              ("active", colors["button_hover"])])

        for surface in SURFACES:
            style.configure("%s.TCheckbutton" % surface,
                            background=colors[surface.lower()],
                            foreground=colors["text"],
                            focuscolor=colors[surface.lower()],
                            indicatorbackground=colors["view"],
                            indicatorforeground=colors["accent_text"],
                            bordercolor=colors["border"],
                            padding=(PAD_XS, 0))
            style.map("%s.TCheckbutton" % surface,
                      background=[("active", colors[surface.lower()])],
                      foreground=[("disabled", colors["dim"])],
                      indicatorbackground=[("selected", colors["accent"]),
                                           ("disabled", colors["window"])])

    def _apply_inputs(self, style, colors):
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            style.configure(name,
                            fieldbackground=colors["view"],
                            background=colors["button"],
                            foreground=colors["text"],
                            bordercolor=colors["border"],
                            lightcolor=colors["view"],
                            darkcolor=colors["view"],
                            arrowcolor=colors["dim"],
                            insertcolor=colors["text"],
                            borderwidth=1,
                            padding=(PAD_S, PAD_XS))
            style.map(name,
                      fieldbackground=[("disabled", colors["window"]),
                                       ("readonly", colors["view"])],
                      foreground=[("disabled", colors["dim"])],
                      bordercolor=[("focus", colors["accent"])],
                      arrowcolor=[("disabled", colors["border"])])

    def _apply_treeview(self, style, colors):
        row_height = max(int(self.fonts["body"].metrics("linespace") * 1.9), 26)
        style.configure("Treeview",
                        background=colors["view"],
                        fieldbackground=colors["view"],
                        foreground=colors["text"],
                        bordercolor=colors["border"],
                        borderwidth=0,
                        relief="flat",
                        rowheight=row_height)
        style.map("Treeview",
                  background=[("selected", colors["accent"])],
                  foreground=[("selected", colors["accent_text"])])
        style.configure("Treeview.Heading",
                        background=colors["sidebar"],
                        foreground=colors["dim"],
                        bordercolor=colors["border"],
                        lightcolor=colors["sidebar"],
                        darkcolor=colors["sidebar"],
                        relief="flat",
                        font=self.fonts["caption"],
                        padding=(PAD_S, PAD_S))
        style.map("Treeview.Heading",
                  background=[("active", colors["button_hover"])])

    def _apply_scrollbars(self, style, colors):
        # Modern scrollbars have no stepper arrows; drop them from the layout.
        for orient in ("Vertical", "Horizontal"):
            name = "%s.TScrollbar" % orient
            try:
                style.layout(name, [
                    ("%s.Scrollbar.trough" % name.split(".")[0], {
                        "children": [("%s.Scrollbar.thumb" % name.split(".")[0],
                                      {"expand": "1", "sticky": "nswe"})],
                        "sticky": "ns" if orient == "Vertical" else "ew",
                    }),
                ])
            except tk.TclError:  # pragma: no cover - layout name differences
                pass
            style.configure(name,
                            background=colors["border"],
                            troughcolor=colors["window"],
                            bordercolor=colors["window"],
                            lightcolor=colors["border"],
                            darkcolor=colors["border"],
                            arrowcolor=colors["dim"],
                            borderwidth=0,
                            width=10)
            style.map(name, background=[("active", colors["dim"])])

    def _apply_options(self, root, colors):
        """Classic widgets (Text, Listbox) need the option database."""
        root.option_add("*TCombobox*Listbox.background", colors["view"])
        root.option_add("*TCombobox*Listbox.foreground", colors["text"])
        root.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
        root.option_add("*TCombobox*Listbox.selectForeground", colors["accent_text"])
        root.option_add("*Text.background", colors["view"])
        root.option_add("*Text.foreground", colors["text"])
        root.option_add("*Toplevel.background", colors["window"])
        root.option_add("*Dialog.msg.background", colors["window"])

    # ------------------------------------------------------------------
    def style_text(self, widget):
        """Apply the palette to a classic tk.Text."""
        colors = self.colors
        widget.configure(background=colors["view"],
                         foreground=colors["text"],
                         insertbackground=colors["text"],
                         selectbackground=colors["accent"],
                         selectforeground=colors["accent_text"],
                         relief="flat",
                         borderwidth=0,
                         highlightthickness=1,
                         highlightbackground=colors["border"],
                         highlightcolor=colors["border"],
                         font=self.fonts["mono"],
                         padx=PAD_S, pady=PAD_XS)


def apply_theme(root, name=None):
    """Apply (and remember) the theme for this process."""
    global _current
    _current = Theme(name or detect_scheme()).apply(root)
    return _current


def current():
    """The theme in use. Falls back to light before apply_theme() runs."""
    return _current if _current is not None else Theme("light")


class Card(tk.Frame):
    """A flat surface with a hairline border, the libadwaita 'card' idiom.

    Built on a classic frame because ``highlightthickness`` gives an exact one
    pixel border in a colour we choose, which ttk frames do not.
    """

    def __init__(self, parent, padding=PAD_M, **kwargs):
        colors = current().colors
        super().__init__(parent,
                         background=colors["card"],
                         highlightthickness=1,
                         highlightbackground=colors["border"],
                         highlightcolor=colors["border"],
                         bd=0,
                         padx=padding, pady=padding,
                         **kwargs)

    def title(self, text, **kwargs):
        """Add a section heading in the card's own style."""
        label = ttk.Label(self, text=text, style="CardHeading.TLabel", **kwargs)
        label.pack(anchor="w", pady=(0, PAD_S))
        return label
