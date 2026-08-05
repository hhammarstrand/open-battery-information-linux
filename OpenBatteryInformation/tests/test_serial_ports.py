"""Port discovery and Linux troubleshooting helper tests."""

import os
import unittest
from unittest import mock

from core import serial_ports


class FakeComPort(object):
    def __init__(self, device, description="n/a", manufacturer="", vid=None,
                 pid=None, serial_number=""):
        self.device = device
        self.description = description
        self.manufacturer = manufacturer
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number


UNO = FakeComPort("/dev/ttyACM0", "Arduino Uno", "Arduino LLC", vid=0x2341,
                  pid=0x0043, serial_number="85035313437351F0F1F1")
CH340 = FakeComPort("/dev/ttyUSB0", "USB Serial", vid=0x1A86, pid=0x7523)
BUILTIN = FakeComPort("/dev/ttyS0")


def patch_ports(ports, by_id=None):
    return [
        mock.patch.object(serial_ports.serial.tools.list_ports, "comports",
                          return_value=ports),
        mock.patch.object(serial_ports, "_by_id_map", return_value=by_id or {}),
    ]


class ListPortsTest(unittest.TestCase):
    def run_list(self, ports, by_id=None, **kwargs):
        patches = patch_ports(ports, by_id)
        for patcher in patches:
            patcher.start()
        try:
            return serial_ports.list_ports(**kwargs)
        finally:
            for patcher in patches:
                patcher.stop()

    def test_builtin_uarts_are_hidden_by_default(self):
        found = self.run_list([BUILTIN, CH340])

        self.assertEqual([port.device for port in found], ["/dev/ttyUSB0"])

    def test_builtin_uarts_can_be_shown(self):
        found = self.run_list([BUILTIN, CH340], include_non_usb=True)

        # USB adapters sort first, the built-in UART comes last.
        self.assertEqual([port.device for port in found],
                         ["/dev/ttyUSB0", "/dev/ttyS0"])

    def test_label_names_the_known_chip(self):
        found = self.run_list([CH340])

        self.assertIn("CH340", found[0].label)
        self.assertIn("/dev/ttyUSB0", found[0].label)

    def test_stable_by_id_path_is_preferred_when_present(self):
        alias = "/dev/serial/by-id/usb-Arduino_Uno-if00"
        found = self.run_list([UNO], by_id={os.path.realpath("/dev/ttyACM0"): alias})

        self.assertEqual(found[0].by_id, alias)
        self.assertEqual(found[0].stable_device, alias)

    def test_stable_device_falls_back_to_the_plain_node(self):
        found = self.run_list([CH340])

        self.assertEqual(found[0].stable_device, "/dev/ttyUSB0")


class DefaultPortTest(unittest.TestCase):
    def find(self, ports):
        patches = patch_ports(ports)
        for patcher in patches:
            patcher.start()
        try:
            return serial_ports.find_default_port()
        finally:
            for patcher in patches:
                patcher.stop()

    def test_single_known_adapter_is_picked(self):
        self.assertEqual(self.find([UNO]).device, "/dev/ttyACM0")

    def test_known_adapter_wins_over_an_unknown_usb_device(self):
        odd = FakeComPort("/dev/ttyUSB9", "Some dongle", vid=0xDEAD, pid=0xBEEF)

        self.assertEqual(self.find([odd, UNO]).device, "/dev/ttyACM0")

    def test_ambiguous_setup_returns_nothing(self):
        self.assertIsNone(self.find([UNO, CH340]))

    def test_no_ports_returns_nothing(self):
        self.assertIsNone(self.find([]))


class DiagnoseTest(unittest.TestCase):
    def test_missing_device_suggests_checking_the_cable(self):
        advice = serial_ports.diagnose("/dev/definitely-not-here")

        self.assertIn("does not exist", advice)
        self.assertIn("dmesg", advice)

    def test_error_text_is_included(self):
        advice = serial_ports.diagnose("/dev/definitely-not-here",
                                       PermissionError("Permission denied"))

        self.assertIn("Permission denied", advice)

    @unittest.skipUnless(os.name == "posix", "POSIX only")
    def test_unwritable_device_explains_how_to_get_access(self):
        # /dev/null exists everywhere; pretend it is not writable.
        with mock.patch.object(serial_ports.os, "access", return_value=False), \
                mock.patch.object(serial_ports, "find_processes_using", return_value=[]), \
                mock.patch.object(serial_ports, "_user_groups", return_value=set()):
            advice = serial_ports.diagnose("/dev/null")

        self.assertIn("read/write access", advice)
        self.assertIn("usermod -aG", advice)

    @unittest.skipUnless(os.name == "posix", "POSIX only")
    def test_existing_group_membership_points_at_the_stale_session(self):
        with mock.patch.object(serial_ports.os, "access", return_value=False), \
                mock.patch.object(serial_ports, "find_processes_using", return_value=[]), \
                mock.patch.object(serial_ports, "_group_name", return_value="dialout"), \
                mock.patch.object(serial_ports, "_user_groups", return_value={"dialout"}):
            advice = serial_ports.diagnose("/dev/null")

        self.assertIn("Log out and back in", advice)
        self.assertNotIn("usermod -aG", advice)

    @unittest.skipUnless(os.name == "posix", "POSIX only")
    def test_busy_device_names_the_process(self):
        with mock.patch.object(serial_ports, "find_processes_using",
                               return_value=[(4711, "arduino-ide")]):
            advice = serial_ports.diagnose("/dev/null")

        self.assertIn("arduino-ide", advice)
        self.assertIn("4711", advice)


class FormatTableTest(unittest.TestCase):
    def test_table_shows_serial_number_and_stable_path(self):
        port = serial_ports.PortInfo(
            device="/dev/ttyACM0", description="Arduino Uno", vid=0x2341,
            serial_number="ABC123", by_id="/dev/serial/by-id/usb-Arduino_Uno-if00")

        table = serial_ports.format_port_table([port])

        self.assertIn("/dev/ttyACM0", table)
        self.assertIn("ABC123", table)
        self.assertIn("usb-Arduino_Uno-if00", table)

    def test_empty_list_explains_what_to_check(self):
        table = serial_ports.format_port_table([])

        self.assertIn("No USB serial adapters found", table)


if __name__ == "__main__":
    unittest.main()
