"""Transport tests using a fake serial port - no adapter required."""

import unittest
from unittest import mock

import serial

from core import obi_link
from core.obi_link import NotConnectedError, ObiLink, ObiLinkError

VERSION_RESPONSE = bytes(bytearray([0x01, 0x03, 0x00, 0x02, 0x01]))
MODEL_CMD = [0x01, 0x02, 0x10, 0xCC, 0xDC, 0x0C]
ZERO_LENGTH_CMD = [0x01, 0x02, 0x00, 0xCC, 0xF0, 0x00]


class FakeSerial(object):
    """Minimal stand-in for :class:`serial.Serial`."""

    #: Responses handed out by successive read() calls. An Exception instance
    #: is raised instead of returned.
    script = []

    def __init__(self):
        self.is_open = False
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.write_timeout = None
        self.written = []
        self.open_count = 0
        self.responses = list(self.script)

    def open(self):
        self.is_open = True
        self.open_count += 1

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size):
        if not self.responses:
            return b""
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_link(script, **kwargs):
    """Build an ObiLink backed by a scripted fake port, already open."""
    fake_type = type("ScriptedSerial", (FakeSerial,), {"script": script})
    patcher = mock.patch.object(obi_link.serial, "Serial", fake_type)
    patcher.start()
    try:
        kwargs.setdefault("boot_delay", 0)
        link = ObiLink(port="/dev/fake", **kwargs)
    finally:
        patcher.stop()
    link.open()
    return link


class RequestTest(unittest.TestCase):
    def test_sends_the_frame_and_returns_the_response(self):
        response = bytes(bytearray([0xCC, 0x10]) + b"BL1850B" + bytes(9))
        link = make_link([response])

        result = link.request(MODEL_CMD)

        self.assertEqual(result, response)
        self.assertEqual(link._serial.written, [bytes(bytearray(MODEL_CMD))])

    def test_zero_length_command_returns_none(self):
        link = make_link([b"\xcc\x00"])

        self.assertIsNone(link.request(ZERO_LENGTH_CMD))

    def test_no_response_is_retried_then_reported(self):
        link = make_link([b"", b""])

        with self.assertRaises(ObiLinkError) as caught:
            link.request(MODEL_CMD, max_attempts=2)

        self.assertIn("2 attempts", str(caught.exception))
        self.assertEqual(len(link._serial.written), 2)

    def test_all_ff_payload_is_rejected(self):
        idle = bytes(bytearray([0xCC, 0x10]) + b"\xff" * 16)
        link = make_link([idle, idle])

        with self.assertRaises(ObiLinkError):
            link.request(MODEL_CMD, max_attempts=2)

    def test_short_response_is_rejected(self):
        link = make_link([b"\xcc\x10\x01\x02", b"\xcc\x10\x01\x02"])

        with self.assertRaises(ObiLinkError):
            link.request(MODEL_CMD, max_attempts=2)

    def test_second_attempt_can_succeed(self):
        good = bytes(bytearray([0xCC, 0x10]) + b"BL1850B" + bytes(9))
        link = make_link([b"", good])

        self.assertEqual(link.request(MODEL_CMD, max_attempts=2), good)

    def test_request_before_connecting_raises_connection_error(self):
        link = make_link([])
        link.close()

        with self.assertRaises(NotConnectedError):
            link.request(MODEL_CMD)
        # Modules catch ConnectionError, so the subclass must stay compatible.
        self.assertTrue(issubclass(NotConnectedError, ConnectionError))

    def test_malformed_request_is_a_programming_error(self):
        link = make_link([])

        with self.assertRaises(ValueError):
            link.request([0x01, 0x00])

    def test_trace_can_be_silenced_for_logging(self):
        response = bytes(bytearray([0xCC, 0x10]) + b"BL1850B" + bytes(9))
        seen = []
        link = make_link([response, response])
        link.set_trace(seen.append)

        link.request(MODEL_CMD, trace=False)
        self.assertEqual(seen, [])

        link.request(MODEL_CMD, trace=True)
        self.assertEqual(len(seen), 2)  # one ">>" and one "<<"


class ReconnectTest(unittest.TestCase):
    def test_serial_error_triggers_a_reconnect_attempt(self):
        good = bytes(bytearray([0xCC, 0x10]) + b"BL1850B" + bytes(9))
        link = make_link([serial.SerialException("device disappeared"), good])

        with mock.patch.object(obi_link.time, "sleep"):
            result = link.request(MODEL_CMD, max_attempts=2)

        self.assertEqual(result, good)
        self.assertEqual(link._serial.open_count, 2)

    def test_reconnect_can_be_disabled(self):
        link = make_link([serial.SerialException("boom"),
                          serial.SerialException("boom")],
                         auto_reconnect=False)

        with self.assertRaises(ObiLinkError):
            link.request(MODEL_CMD, max_attempts=2)
        self.assertEqual(link._serial.open_count, 1)


class VersionTest(unittest.TestCase):
    def test_parses_the_firmware_version(self):
        link = make_link([VERSION_RESPONSE])

        self.assertEqual(link.get_version(), "0.2.1")


class BootDelayTest(unittest.TestCase):
    def test_waits_for_the_board_after_opening(self):
        fake_type = type("ScriptedSerial", (FakeSerial,), {"script": []})
        with mock.patch.object(obi_link.serial, "Serial", fake_type):
            link = ObiLink(port="/dev/fake", boot_delay=1.5)
        with mock.patch.object(obi_link.time, "sleep") as sleep:
            link.open()

        sleep.assert_called_once_with(1.5)

    def test_opening_without_a_port_is_reported(self):
        fake_type = type("ScriptedSerial", (FakeSerial,), {"script": []})
        with mock.patch.object(obi_link.serial, "Serial", fake_type):
            link = ObiLink(boot_delay=0)

        with self.assertRaises(ObiLinkError):
            link.open()


if __name__ == "__main__":
    unittest.main()
