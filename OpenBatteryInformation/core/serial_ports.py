"""Serial port discovery and Linux-specific troubleshooting helpers.

Getting the adapter to show up and be writable is the single most common
problem when running OBI on Linux, so the diagnostics live here and are shared
by the GUI and the command line logger.
"""

import glob
import os

import serial.tools.list_ports

try:  # POSIX only
    import grp
except ImportError:  # pragma: no cover - Windows
    grp = None

#: USB vendor IDs of adapters people actually use with ArduinoOBI.
KNOWN_VENDORS = {
    0x2341: "Arduino",
    0x2A03: "Arduino",
    0x1A86: "CH340/CH341",
    0x0403: "FTDI",
    0x10C4: "CP210x",
    0x067B: "PL2303",
    0x303A: "Espressif",
    0x1B4F: "SparkFun",
    0x239A: "Adafruit",
}

UDEV_RULE_PATH = "/etc/udev/rules.d/90-arduino-obi.rules"


class PortInfo(object):
    """A serial port candidate with the extra detail Linux users need."""

    def __init__(self, device, description=None, manufacturer=None, vid=None,
                 pid=None, serial_number=None, by_id=None, manual=False):
        self.device = device
        self.description = description or ""
        self.manufacturer = manufacturer or ""
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number or ""
        #: Stable ``/dev/serial/by-id/...`` path, which survives replugging
        #: and reboots. Preferred for unattended logging.
        self.by_id = by_id
        #: Set for ports named in OBI_EXTRA_PORTS rather than enumerated.
        self.manual = manual

    @property
    def is_usb(self):
        return self.vid is not None

    @property
    def vendor_name(self):
        if self.vid is None:
            return ""
        return KNOWN_VENDORS.get(self.vid, self.manufacturer or "")

    @property
    def stable_device(self):
        """The path to prefer when opening this port."""
        return self.by_id or self.device

    @property
    def label(self):
        """Human readable one-liner for combo boxes and CLI listings."""
        parts = []
        vendor = self.vendor_name
        detail = self.description if self.description and self.description != "n/a" else ""
        if vendor and vendor.lower() not in detail.lower():
            parts.append(vendor)
        if detail:
            parts.append(detail)
        if not parts:
            parts.append("serial port")
        return "%s (%s)" % (self.device, ", ".join(parts))

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<PortInfo %s>" % self.label


def _extra_ports():
    """Devices from ``OBI_EXTRA_PORTS`` (colon separated).

    Pseudo terminals and socat/RFC2217 bridges are not enumerable, so this is
    how the port simulator in ``tools/fake_adapter.py`` and remote serial
    servers get into the picker.
    """
    raw = os.environ.get("OBI_EXTRA_PORTS", "")
    return [device for device in raw.split(":") if device]


def _by_id_map():
    """Map real device paths to their stable ``/dev/serial/by-id`` alias."""
    mapping = {}
    for path in glob.glob("/dev/serial/by-id/*"):
        try:
            mapping[os.path.realpath(path)] = path
        except OSError:  # pragma: no cover - transient
            continue
    return mapping


def list_ports(include_non_usb=False):
    """Return the available :class:`PortInfo` objects, USB adapters first.

    Built-in UARTs (``/dev/ttyS0`` and friends) are hidden by default: almost
    every PC reports a handful of them and none of them is ever the adapter.
    """
    aliases = _by_id_map()
    ports = []
    for device in _extra_ports():
        ports.append(PortInfo(device=device, description="manual entry", manual=True))
    for port in serial.tools.list_ports.comports():
        info = PortInfo(
            device=port.device,
            description=getattr(port, "description", ""),
            manufacturer=getattr(port, "manufacturer", ""),
            vid=getattr(port, "vid", None),
            pid=getattr(port, "pid", None),
            serial_number=getattr(port, "serial_number", ""),
            by_id=aliases.get(os.path.realpath(port.device)),
        )
        if not info.is_usb and not include_non_usb:
            continue
        ports.append(info)
    ports.sort(key=lambda p: (not (p.is_usb or p.manual), p.device))
    return ports


def find_default_port():
    """Return the obvious adapter, or ``None`` when it is ambiguous."""
    ports = list_ports()
    known = [p for p in ports if p.vid in KNOWN_VENDORS]
    if len(known) == 1:
        return known[0]
    if len(ports) == 1:
        return ports[0]
    return None


def find_processes_using(device):
    """Best-effort list of ``(pid, name)`` holding the device open.

    Only processes owned by the current user are visible without root, which
    is exactly the common case: a forgotten Arduino IDE serial monitor.
    """
    holders = []
    try:
        target = os.path.realpath(device)
    except OSError:  # pragma: no cover - transient
        return holders
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        pid = fd_dir.split("/")[2]
        try:
            entries = os.listdir(fd_dir)
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                if os.path.realpath(os.path.join(fd_dir, entry)) == target:
                    holders.append((int(pid), _process_name(pid)))
                    break
            except (OSError, PermissionError):
                continue
    return holders


def _process_name(pid):
    try:
        with open("/proc/%s/comm" % pid) as handle:
            return handle.read().strip()
    except (OSError, IOError):  # pragma: no cover - race with process exit
        return "?"


def _group_name(gid):
    if grp is None:
        return str(gid)
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:  # pragma: no cover - unusual system
        return str(gid)


def _user_groups():
    if grp is None:
        return set()
    try:
        return set(grp.getgrgid(gid).gr_name for gid in os.getgroups())
    except (OSError, KeyError):  # pragma: no cover - unusual system
        return set()


def diagnose(device, error=None):
    """Return actionable advice for a failed ``open()`` of ``device``.

    The text is written to be pasted straight into a terminal by someone who
    has never touched udev.
    """
    lines = []
    if error is not None:
        lines.append("Could not open %s: %s" % (device, error))

    if not os.path.exists(device):
        lines.append("The device does not exist.")
        lines.append("  * Check the USB cable and that the adapter is plugged in.")
        lines.append("  * Run 'dmesg --follow' and replug it to see which device appears.")
        lines.append("  * Some cables are charge-only and carry no data lines.")
        return "\n".join(lines)

    if os.name != "posix":  # pragma: no cover - Windows
        return "\n".join(lines) if lines else ""

    try:
        stat = os.stat(device)
    except OSError as exc:  # pragma: no cover - race
        lines.append("Cannot stat the device: %s" % exc)
        return "\n".join(lines)

    owning_group = _group_name(stat.st_gid)
    if not os.access(device, os.R_OK | os.W_OK):
        lines.append("You do not have read/write access to %s (group '%s')."
                     % (device, owning_group))
        if owning_group in _user_groups():
            # Group membership is baked into the session at login, so this is
            # almost always someone who ran usermod and did not log out.
            lines.append("Your account IS in '%s', but this session started before that "
                         "was granted. Log out and back in (or reboot)." % owning_group)
        else:
            lines.append("Fix it with either of these, then replug the adapter:")
            if not os.path.exists(UDEV_RULE_PATH):
                lines.append("  sudo cp linux/90-arduino-obi.rules /etc/udev/rules.d/")
                lines.append("  sudo udevadm control --reload-rules && sudo udevadm trigger")
            lines.append("  sudo usermod -aG %s $USER    (then log out and back in)"
                         % owning_group)

    holders = find_processes_using(device)
    if holders:
        listed = ", ".join("%s (pid %d)" % (name, pid) for pid, name in holders)
        lines.append("The port is already open by: %s" % listed)
        lines.append("Close that program (a serial monitor, or another OBI instance).")

    if _modemmanager_may_interfere():
        lines.append("ModemManager is installed and may probe the adapter for ~10 s after "
                     "it is plugged in, which garbles the first commands.")
        lines.append("Installing linux/90-arduino-obi.rules tells it to leave the adapter "
                     "alone; otherwise just wait a few seconds and retry.")

    return "\n".join(lines)


def _modemmanager_may_interfere():
    if os.name != "posix":  # pragma: no cover - Windows
        return False
    if os.path.exists(UDEV_RULE_PATH):
        return False
    return any(os.path.exists(path) for path in (
        "/usr/sbin/ModemManager",
        "/usr/lib/ModemManager",
        "/run/ModemManager",
    ))


def format_port_table(ports):
    """Render ports for ``obi-log --list-ports``."""
    if not ports:
        return ("No USB serial adapters found.\n"
                + diagnose("/dev/ttyUSB0"))
    rows = ["Available serial ports:"]
    for port in ports:
        rows.append("  %s" % port.label)
        if port.serial_number:
            rows.append("      serial number : %s" % port.serial_number)
        if port.by_id:
            rows.append("      stable path   : %s" % port.by_id)
        if os.name == "posix" and os.path.exists(port.device):
            rows.append("      writable      : %s"
                        % ("yes" if os.access(port.device, os.R_OK | os.W_OK) else "NO"))
    return "\n".join(rows)
