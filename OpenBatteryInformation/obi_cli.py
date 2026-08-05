#!/usr/bin/env python3
"""obi-log - read and log a Makita LXT pack from the command line.

Headless companion to the OBI Linux desktop app: same adapter, same protocol,
no display required. Handy for logging a battery over hours or days, over SSH,
or from a systemd service.

Examples::

    obi-log --list-ports
    obi-log --once
    obi-log --interval 60 --duration 8h --out ~/battery.csv
"""

import argparse
import sys
import time

import serial

from core import __version__
from core import makita
from core import sampling
from core import serial_ports
from core.obi_link import DEFAULT_BOOT_DELAY, DEFAULT_TIMEOUT, ObiLink, ObiLinkError

EXIT_OK = 0
EXIT_ERROR = 1


def parse_duration(value):
    """Accept ``90``, ``90s``, ``15m``, ``8h`` or ``2d`` and return seconds."""
    text = str(value).strip().lower()
    if not text:
        raise argparse.ArgumentTypeError("empty duration")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    multiplier = 1
    if text[-1] in units:
        multiplier = units[text[-1]]
        text = text[:-1]
    try:
        seconds = float(text) * multiplier
    except ValueError:
        raise argparse.ArgumentTypeError("invalid duration: %r" % (value,))
    if seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be positive")
    return seconds


def build_parser():
    parser = argparse.ArgumentParser(
        prog="obi-log",
        description="Read and log Makita LXT battery data via an ArduinoOBI adapter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Logs are written to %s by default." % sampling.log_dir())

    parser.add_argument("--version", action="version",
                        version="obi-log (OBI Linux) %s" % __version__)

    ports = parser.add_argument_group("adapter")
    ports.add_argument("-l", "--list-ports", action="store_true",
                       help="list serial ports and exit")
    ports.add_argument("--all-ports", action="store_true",
                       help="include built-in UARTs (/dev/ttyS*) when listing")
    ports.add_argument("-p", "--port",
                       help="serial device, e.g. /dev/ttyUSB0 (default: autodetect)")
    ports.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                       help="serial read timeout in seconds (default: %(default)s)")
    ports.add_argument("--boot-delay", type=float, default=DEFAULT_BOOT_DELAY,
                       help="wait after opening the port for the board to boot "
                            "(default: %(default)s, use 0 for native-USB boards)")

    logging_group = parser.add_argument_group("logging")
    logging_group.add_argument("-1", "--once", action="store_true",
                               help="take a single reading, print it and exit")
    logging_group.add_argument("-i", "--interval", type=float, default=sampling.DEFAULT_INTERVAL,
                               help="seconds between samples (default: %(default)s)")
    logging_group.add_argument("-d", "--duration", type=parse_duration,
                               help="stop after this long, e.g. 30m, 8h, 2d")
    logging_group.add_argument("-n", "--samples", type=int,
                               help="stop after this many samples")
    logging_group.add_argument("-o", "--out",
                               help="output file (default: timestamped file in the log dir)")
    logging_group.add_argument("-f", "--format", choices=("csv", "jsonl"), default="csv",
                               help="output format (default: %(default)s)")
    logging_group.add_argument("-a", "--append", action="store_true",
                               help="append to an existing log instead of overwriting")
    logging_group.add_argument("-s", "--include-status", action="store_true",
                               help="also log lock state, status code and charge count "
                                    "(one extra round trip per sample)")

    output = parser.add_argument_group("output")
    output.add_argument("-q", "--quiet", action="store_true",
                        help="only print errors")
    output.add_argument("-v", "--verbose", action="store_true",
                        help="print every protocol frame")

    return parser


def resolve_port(requested):
    if requested:
        return requested
    port = serial_ports.find_default_port()
    if port is not None:
        return port.stable_device
    raise SystemExit(
        "Could not decide which serial port to use.\n\n"
        + serial_ports.format_port_table(serial_ports.list_ports())
        + "\n\nPass one explicitly with --port /dev/ttyUSB0")


def open_link(args):
    device = resolve_port(args.port)
    trace = (lambda message: print(message, file=sys.stderr)) if args.verbose else None
    link = ObiLink(port=device, timeout=args.timeout,
                   boot_delay=args.boot_delay, trace=trace)
    try:
        link.open()
    except (serial.SerialException, ObiLinkError, OSError) as exc:
        raise SystemExit(serial_ports.diagnose(device, exc))
    return link, device


def describe_battery(client, quiet=False):
    """Identify the pack and print a short header."""
    message = client.read_message(trace=False)
    model, version = client.detect_model(trace=False)
    if not quiet:
        print("Battery : %s%s" % (model, " (diagnostics only)" if version == makita.F0513 else ""))
        print("ROM ID  : %s" % message["rom_id"])
        print("State   : %s (status code %s, %d charges)"
              % (message["state"], message["status_code"], message["charge_count"]))
        print("Made    : %s, %s Ah"
              % (message["manufacturing_date"], message["capacity_ah"]))
    return message


def format_sample(row):
    cells = " ".join("%.3f" % row["cell%d_v" % i] for i in range(1, makita.CELL_COUNT + 1)
                     if row.get("cell%d_v" % i) is not None)
    text = "pack %.3f V | cells %s | delta %.3f V | t1 %s C" % (
        row["pack_v"], cells, row["cell_delta_v"], row["temp1_c"])
    if row.get("temp2_c") is not None:
        text += " | t2 %s C" % row["temp2_c"]
    if row.get("state"):
        text += " | %s (%s, %s charges)" % (row["state"], row["status_code"], row["charge_count"])
    return text


def run_once(client):
    row = client.sample(include_status=True, trace=False)
    print(format_sample(row))
    return EXIT_OK


def run_logger(client, args):
    fieldnames = makita.log_fieldnames(args.include_status)
    path = args.out or sampling.default_log_path("makita-lxt", args.format)
    writer = sampling.make_writer(path, fieldnames, fmt=args.format, append=args.append)

    def on_event(event):
        if args.quiet:
            if event["type"] == "error":
                print("sample %d failed: %s" % (event["index"], event["message"]),
                      file=sys.stderr)
            return
        if event["type"] == "sample":
            row = event["row"]
            if row.get(sampling.ERROR_FIELD):
                print("[%s] #%d ERROR %s" % (row["timestamp"], row["sample"],
                                             row[sampling.ERROR_FIELD]))
            else:
                print("[%s] #%d %s" % (row["timestamp"], row["sample"], format_sample(row)))
            sys.stdout.flush()

    logger = sampling.SampleLogger(
        lambda: client.sample(include_status=args.include_status, trace=False),
        writer,
        interval=args.interval,
        max_samples=args.samples,
        max_duration=args.duration,
        on_event=on_event)

    try:
        logger.start()
    except (OSError, ValueError) as exc:
        raise SystemExit("Could not open %s:\n%s" % (path, exc))

    if not args.quiet:
        print("Logging every %gs to %s (Ctrl-C to stop)" % (args.interval, path))

    try:
        while logger.is_running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        if not args.quiet:
            print("\nStopping...")
        logger.stop()
    logger.join(timeout=30)

    print("Wrote %d samples (%d errors) to %s" % (logger.samples, logger.errors, path),
          file=sys.stderr if args.quiet else sys.stdout)
    return EXIT_OK if logger.samples else EXIT_ERROR


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.list_ports:
        print(serial_ports.format_port_table(
            serial_ports.list_ports(include_non_usb=args.all_ports)))
        return EXIT_OK

    if args.interval <= 0:
        raise SystemExit("--interval must be positive")

    link, device = open_link(args)
    if not args.quiet:
        print("Adapter : %s" % device)
    try:
        try:
            version = link.get_version(trace=False)
            if not args.quiet:
                print("Firmware: %s" % version)
        except ConnectionError as exc:
            raise SystemExit("The adapter did not respond: %s\n\n"
                             "Is the ArduinoOBI firmware flashed on this board?" % exc)

        client = makita.MakitaClient(link)
        try:
            describe_battery(client, quiet=args.quiet)
        except ConnectionError as exc:
            raise SystemExit("Could not read the battery: %s\n\n"
                             "Check that the pack is seated on the adapter contacts." % exc)
        except ValueError as exc:
            raise SystemExit("Unsupported battery: %s" % exc)

        if args.once:
            return run_once(client)
        return run_logger(client, args)
    finally:
        link.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:  # pragma: no cover - user abort before logging starts
        sys.exit(130)
