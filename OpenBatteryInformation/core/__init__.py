"""Platform independent core logic for OBI Linux.

Nothing in this package imports tkinter, which means the exact same code
powers the desktop application and the headless ``obi-log`` command line
logger. It also makes the protocol handling unit-testable without a display
or a battery attached.
"""

__version__ = "0.3.0"

__all__ = ["obi_link", "serial_ports", "makita", "sampling"]
