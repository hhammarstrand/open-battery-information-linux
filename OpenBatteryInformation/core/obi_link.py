"""Serial transport for the ArduinoOBI adapter.

Frame format, host to adapter::

    [0x01, data_len, response_len, command, *data]

Frame format, adapter to host::

    [command, response_len, *payload]      (response_len + 2 bytes in total)

The adapter firmware raises its ENABLE line and waits 400 ms before it starts
talking to the battery, and the F0513 command path (0x31/0x32) waits another
400 ms for test mode. A request can therefore easily take 900 ms before the
first byte comes back, which is why the default read timeout here is 2 s
rather than the 1 s the original Windows-only code used.

This module has no GUI dependency on purpose; see :mod:`core` for why.
"""

import os
import threading
import time

import serial

DEFAULT_BAUDRATE = 9600
DEFAULT_TIMEOUT = 2.0

# An Arduino Uno/Nano resets when the serial port is opened (DTR toggles the
# reset line) and then sits in the bootloader for a moment. Talking to it
# before the sketch is running produces a stream of confusing timeouts, so we
# wait it out once at connect time instead. Boards with native USB CDC
# (ESP32-C3) do not reset, but the short wait is harmless there.
DEFAULT_BOOT_DELAY = 2.0

INTERFACE_VERSION_CMD = [0x01, 0x00, 0x03, 0x01]

#: Response payload consisting only of 0xFF means the battery did not answer
#: the adapter, as opposed to the adapter not answering us.
_IDLE_BYTE = 0xFF


class ObiLinkError(ConnectionError):
    """Raised when the adapter cannot be reached.

    Subclasses :class:`ConnectionError` because callers (and the original
    upstream modules) catch ``ConnectionError`` around every request.
    """


class NotConnectedError(ObiLinkError):
    """Raised when a request is attempted before the port was opened."""


def _env_float(name, default):
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class ObiLink(object):
    """Thread-safe request/response channel to an ArduinoOBI adapter.

    Every request is serialised through a re-entrant lock so that the GUI
    thread and a background logging thread can share one adapter without
    interleaving their frames.
    """

    def __init__(self, port=None, baudrate=DEFAULT_BAUDRATE,
                 timeout=None, boot_delay=None, trace=None,
                 auto_reconnect=True):
        self._serial = serial.Serial()
        self._serial.baudrate = baudrate
        self._serial.timeout = timeout if timeout is not None else _env_float("OBI_TIMEOUT", DEFAULT_TIMEOUT)
        self._serial.write_timeout = self._serial.timeout
        if os.name == "posix":
            # Keeps a second OBI process (for example the GUI while obi-log
            # runs) from grabbing the same adapter and corrupting both streams.
            try:
                self._serial.exclusive = True
            except (AttributeError, ValueError):  # pragma: no cover - platform dependent
                pass
        self.port = port
        self.boot_delay = boot_delay if boot_delay is not None else _env_float("OBI_BOOT_DELAY", DEFAULT_BOOT_DELAY)
        self.auto_reconnect = auto_reconnect
        self._trace = trace
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # connection handling
    # ------------------------------------------------------------------
    def set_trace(self, callback):
        """Install a ``callback(message)`` used for protocol logging."""
        self._trace = callback

    def _log(self, message):
        callback = self._trace
        if callback is None:
            return
        try:
            callback(message)
        except Exception:  # pragma: no cover - a broken UI must not kill I/O
            pass

    @property
    def is_open(self):
        return bool(self._serial.is_open)

    @property
    def timeout(self):
        return self._serial.timeout

    def open(self, port=None):
        with self._lock:
            if port:
                self.port = port
            if self._serial.is_open:
                return
            if not self.port:
                raise ObiLinkError("No serial port selected.")
            self._serial.port = self.port
            self._serial.open()
            if self.boot_delay > 0:
                self._log("Waiting %.1f s for the adapter to start..." % self.boot_delay)
                time.sleep(self.boot_delay)
            self._discard_input()

    def close(self):
        with self._lock:
            if self._serial.is_open:
                self._serial.close()

    def reconnect(self):
        """Close and open the port again, e.g. after a USB re-enumeration."""
        with self._lock:
            try:
                self._serial.close()
            except Exception:  # pragma: no cover - best effort
                pass
            time.sleep(0.5)
            self._serial.port = self.port
            self._serial.open()
            if self.boot_delay > 0:
                time.sleep(self.boot_delay)
            self._discard_input()

    def _discard_input(self):
        try:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        except Exception:  # pragma: no cover - depends on driver
            pass

    # ------------------------------------------------------------------
    # protocol
    # ------------------------------------------------------------------
    def request(self, request, max_attempts=2, trace=True):
        """Send one frame and return the raw response bytes.

        Returns ``None`` for commands that declare a zero length response.
        Raises :class:`ObiLinkError` (a ``ConnectionError``) when no valid
        response was received within ``max_attempts`` tries.
        """
        if len(request) < 4:
            raise ValueError("Malformed request frame: %r" % (request,))

        expected_length = request[2] + 2
        frame = bytes(bytearray(request))

        with self._lock:
            if not self._serial.is_open:
                raise NotConnectedError(
                    "Serial port is not open. Please connect to the adapter first.")

            last_error = None
            for attempt in range(1, max_attempts + 1):
                if trace:
                    self._log(">> " + " ".join("%02X" % x for x in request[3:]))
                try:
                    self._serial.reset_input_buffer()
                    self._serial.write(frame)

                    response = self._serial.read(expected_length)
                    if trace:
                        self._log("<< " + " ".join("%02X" % x for x in response[2:]))

                    if request[2] == 0:
                        return None

                    if len(response) == 0:
                        raise TimeoutError(
                            "No response received from the adapter (expected %d bytes). "
                            "Check that a battery is connected." % expected_length)

                    if len(response) != expected_length:
                        raise ValueError(
                            "Incomplete response: received %d bytes, expected %d. "
                            "The battery may not be seated correctly."
                            % (len(response), expected_length))

                    if all(byte == _IDLE_BYTE for byte in response[2:]):
                        raise ValueError(
                            "Invalid response: all bytes are 0xFF. The battery may not "
                            "be communicating correctly.")

                    return response

                except (TimeoutError, ValueError) as exc:
                    last_error = exc
                    self._log("Attempt %d/%d failed: %s" % (attempt, max_attempts, exc))
                except serial.SerialException as exc:
                    last_error = exc
                    self._log("Attempt %d/%d serial error: %s. The adapter may have been "
                              "disconnected." % (attempt, max_attempts, exc))
                    if self.auto_reconnect and attempt < max_attempts:
                        self._attempt_reconnect()
                except Exception as exc:  # pragma: no cover - defensive
                    last_error = exc
                    self._log("Attempt %d/%d unexpected error: %s: %s"
                              % (attempt, max_attempts, type(exc).__name__, exc))

            raise ObiLinkError(
                "Failed to get a valid response after %d attempts (%s). Ensure the "
                "adapter is connected and a battery is inserted."
                % (max_attempts, last_error))

    def _attempt_reconnect(self):
        self._log("Trying to reopen %s..." % self.port)
        try:
            self.reconnect()
            self._log("Reopened %s" % self.port)
        except Exception as exc:
            self._log("Reconnect failed: %s: %s" % (type(exc).__name__, exc))

    # ------------------------------------------------------------------
    def get_version(self, max_attempts=5, trace=True):
        """Return the adapter firmware version, e.g. ``"0.2.1"``."""
        response = self.request(INTERFACE_VERSION_CMD, max_attempts=max_attempts, trace=trace)
        return ".".join(str(byte) for byte in response[2:])
