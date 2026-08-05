#!/usr/bin/env python3
"""Emulate an ArduinoOBI adapter with a battery on it, over a pseudo terminal.

Lets you develop and demo OBI Linux - including the data logger - without any
hardware. It speaks the same frame format as ArduinoOBI/src/main.cpp.

    $ python3 tools/fake_adapter.py
    Fake ArduinoOBI ready on /dev/pts/5
    Point OBI at it with:
        obi-log --port /dev/pts/5 --boot-delay 0 --interval 5
        OBI_EXTRA_PORTS=/dev/pts/5 python3 main.py

The emulated pack slowly discharges, so a log written against it shows a real
curve instead of a flat line.
"""

import argparse
import os
import pty
import random
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import makita  # noqa: E402  (path juggling above is deliberate)


def _u16le(value):
    return int(max(0, min(0xFFFF, round(value)))).to_bytes(2, "little")


class FakeBattery(object):
    """A Makita LXT pack with slowly draining cells."""

    def __init__(self, model="BL1850B", f0513=False, cells=None, drain_mv_per_s=0.0,
                 charge_count=564, locked=False, status=0x00):
        self.model = model
        self.f0513 = f0513
        self.cells_mv = list(cells or [3600, 3610, 3590, 3605, 3595])
        self.drain_mv_per_s = drain_mv_per_s
        self.charge_count = charge_count
        self.locked = locked
        self.status = status
        self.temp1_c = 23.5
        self.temp2_c = 29.0
        self._last = time.monotonic()

    def step(self):
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        if self.drain_mv_per_s:
            for index in range(len(self.cells_mv)):
                # Cell 3 is a little weaker, which is what makes logging
                # interesting: the delta grows as the pack drains.
                factor = 1.3 if index == 2 else 1.0
                self.cells_mv[index] = max(2500, self.cells_mv[index]
                                           - self.drain_mv_per_s * elapsed * factor)

    # ------------------------------------------------------------------
    def message_payload(self):
        """40 bytes: the answer to READ_MSG_CMD."""
        payload = bytearray(40)
        payload[0] = 0x24                      # year  -> 2036
        payload[1] = 0x07                      # month
        payload[2] = 0x0F                      # day
        payload[3:8] = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
        payload[19] = makita.nibble_swap(0x50)  # battery type
        payload[24] = makita.nibble_swap(0x50)  # capacity -> 8.0 Ah
        payload[27] = self.status
        payload[28] = 0x01 if self.locked else 0x00
        # The decoder reads bytes 36/37 of the frame as swapped nibbles, high
        # byte first, masked to 12 bits.
        count = self.charge_count & 0x0FFF
        payload[34] = makita.nibble_swap((count >> 8) & 0x0F)
        payload[35] = makita.nibble_swap(count & 0xFF)
        return bytes(payload)

    def pack_payload(self):
        """29 bytes: the answer to READ_DATA_REQUEST."""
        self.step()
        payload = bytearray(29)
        payload[0:2] = _u16le(sum(self.cells_mv))
        for index, millivolts in enumerate(self.cells_mv):
            payload[2 + 2 * index:4 + 2 * index] = _u16le(millivolts)
        payload[14:16] = _u16le(self.temp1_c * 100)
        payload[16:18] = _u16le(self.temp2_c * 100)
        return bytes(payload)

    def cell_payload(self, index):
        self.step()
        return _u16le(self.cells_mv[index])

    def temp_payload(self):
        return _u16le(self.temp1_c * 100)


class FakeAdapter(object):
    """Turns request frames into response frames, like the Arduino sketch."""

    def __init__(self, battery, flaky=0.0, latency=0.0):
        self.battery = battery
        self.flaky = flaky
        self.latency = latency

    def respond(self, command, data, response_length):
        if self.latency:
            time.sleep(self.latency)
        if self.flaky and random.random() < self.flaky:
            return None  # simulate a dropped answer

        payload = self._payload(command, data, response_length)
        payload = (payload + bytes(response_length))[:response_length]
        return bytes(bytearray([command, response_length])) + payload

    def _payload(self, command, data, response_length):
        battery = self.battery
        first = data[0] if data else None

        if command == 0x01:                                   # adapter version
            return bytes([0, 2, 1])

        if command == 0x31:                                   # F0513 model
            if not battery.f0513:
                return b"\xff" * response_length
            return bytes([0x18, 0x05])

        if command == 0x32:                                   # F0513 firmware
            return bytes([0x01, 0x00]) if battery.f0513 else b"\xff" * response_length

        if command == 0x33:
            if first == 0xAA:                                 # read message
                return battery.message_payload()
            return bytes(response_length)                     # test mode, LEDs, ...

        if command == 0xCC:
            if first == 0xDC:                                 # model
                if battery.f0513:
                    return b"\xff" * response_length
                return battery.model.encode("utf-8")
            if first == 0xD7:                                 # read data
                if battery.f0513:
                    return b"\xff" * response_length
                return battery.pack_payload()
            if first in (0x31, 0x32, 0x33, 0x34, 0x35):       # F0513 cell voltage
                return battery.cell_payload(first - 0x31)
            if first == 0x52:                                 # F0513 temperature
                return battery.temp_payload()
            return bytes(response_length)

        return bytes(response_length)


class PtyAdapterServer(object):
    """Serve a :class:`FakeAdapter` on a pseudo terminal.

    Usable both from the command line wrapper below and directly from tests::

        server = PtyAdapterServer(FakeAdapter(FakeBattery()))
        device = server.start()
        ...
        server.stop()
    """

    def __init__(self, adapter, verbose=False):
        self.adapter = adapter
        self.verbose = verbose
        self.device = None
        self._master = None
        self._slave = None
        self._thread = None
        self._running = False

    def start(self):
        self._master, self._slave = pty.openpty()
        self.device = os.ttyname(self._slave)
        self._running = True
        self._thread = threading.Thread(target=self._serve, name="fake-obi-adapter")
        self._thread.daemon = True
        self._thread.start()
        return self.device

    def stop(self):
        self._running = False
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except (OSError, TypeError):
                pass
        self._master = self._slave = None
        if self._thread is not None:
            self._thread.join(timeout=2)

    def serve_forever(self):
        """Blocking variant for the command line tool."""
        self.start()
        try:
            while self._thread.is_alive():
                self._thread.join(0.2)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            self.stop()

    def _serve(self):
        while self._running:
            header = _read_exactly(self._master, 4)
            if header is None:
                break
            start, data_length, response_length, command = header
            if start != 0x01:
                continue
            data = _read_exactly(self._master, data_length) if data_length else b""
            if data is None:
                break

            response = self.adapter.respond(command, data, response_length)
            if self.verbose:
                print(">> cmd %02X data %s" % (command, data.hex() if data else "-"))
                print("<< %s" % (response.hex() if response else "(dropped)"))
                sys.stdout.flush()
            if response:
                try:
                    os.write(self._master, response)
                except OSError:
                    break


def serve(adapter, verbose=False):
    """Run the adapter on a new pty and block until interrupted."""
    server = PtyAdapterServer(adapter, verbose=verbose)
    device = server.start()

    print("Fake ArduinoOBI ready on %s" % device)
    print("Point OBI at it with:")
    print("    obi-log --port %s --boot-delay 0 --interval 5" % device)
    print("    OBI_EXTRA_PORTS=%s python3 main.py" % device)
    print("Ctrl-C to stop.")
    sys.stdout.flush()

    try:
        while server._thread.is_alive():
            server._thread.join(0.2)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.stop()


def _read_exactly(fd, count):
    chunks = b""
    while len(chunks) < count:
        try:
            chunk = os.read(fd, count - len(chunks))
        except OSError:
            return None
        if not chunk:
            return None
        chunks += chunk
    return chunks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="BL1850B", help="reported model name")
    parser.add_argument("--f0513", action="store_true",
                        help="emulate the diagnostics-only F0513 command set")
    parser.add_argument("--locked", action="store_true", help="report a locked BMS")
    parser.add_argument("--status", type=lambda v: int(v, 0), default=0x00,
                        help="status code byte, e.g. 0x1B")
    parser.add_argument("--charge-count", type=int, default=564)
    parser.add_argument("--drain", type=float, default=2.0,
                        help="cell drain in mV per second (default: %(default)s)")
    parser.add_argument("--flaky", type=float, default=0.0,
                        help="fraction of requests to drop, 0.0-1.0")
    parser.add_argument("--latency", type=float, default=0.0,
                        help="seconds to wait before answering")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    battery = FakeBattery(model=args.model, f0513=args.f0513,
                          drain_mv_per_s=args.drain, locked=args.locked,
                          status=args.status, charge_count=args.charge_count)
    serve(FakeAdapter(battery, flaky=args.flaky, latency=args.latency),
          verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
