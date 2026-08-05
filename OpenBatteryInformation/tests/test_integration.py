"""End-to-end tests against the emulated adapter in tools/fake_adapter.py.

These exercise the whole Linux stack - pyserial on a pty, the framing, the
Makita decoders, the logger and (when a display is available) the Tk app -
without any hardware.
"""

import contextlib
import csv
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import makita  # noqa: E402
from core.obi_link import ObiLink  # noqa: E402
from tools.fake_adapter import FakeAdapter, FakeBattery, PtyAdapterServer  # noqa: E402

try:
    import tkinter
except ImportError:  # pragma: no cover - distro without python3-tk
    tkinter = None

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class FakeAdapterTestCase(unittest.TestCase):
    """Starts an emulated adapter on a pty for each test."""

    battery_kwargs = {}
    adapter_kwargs = {}

    def setUp(self):
        self.battery = FakeBattery(**self.battery_kwargs)
        self.server = PtyAdapterServer(FakeAdapter(self.battery, **self.adapter_kwargs))
        self.device = self.server.start()
        self.tmp = tempfile.mkdtemp(prefix="obi-int-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.server.stop)

    def open_link(self, **kwargs):
        kwargs.setdefault("boot_delay", 0)
        kwargs.setdefault("timeout", 2.0)
        link = ObiLink(port=self.device, **kwargs)
        link.open()
        self.addCleanup(link.close)
        return link


class LinkAgainstFakeAdapterTest(FakeAdapterTestCase):
    def test_reads_firmware_version(self):
        link = self.open_link()

        self.assertEqual(link.get_version(), "0.2.1")

    def test_reads_model_and_message(self):
        client = makita.MakitaClient(self.open_link())

        message = client.read_message()
        model, version = client.detect_model()

        self.assertEqual(model, "BL1850B")
        self.assertEqual(version, "")
        self.assertEqual(message["rom_id"], "24 07 0F AA BB CC DD EE")
        self.assertEqual(message["state"], "UNLOCKED")
        self.assertEqual(message["charge_count"], 564)
        self.assertEqual(message["capacity_ah"], 8.0)

    def test_reads_pack_data(self):
        client = makita.MakitaClient(self.open_link())
        client.detect_model()

        data = client.read_pack_data()

        self.assertAlmostEqual(data["pack_v"], 17.99, places=1)
        self.assertAlmostEqual(data["cell1_v"], 3.6, places=2)
        self.assertGreater(data["cell_delta_v"], 0)
        self.assertEqual(data["temp1_c"], 23.5)


class LockedBatteryTest(FakeAdapterTestCase):
    battery_kwargs = {"locked": True, "status": 0x1B}

    def test_lock_state_and_status_code_are_reported(self):
        client = makita.MakitaClient(self.open_link())

        message = client.read_message()

        self.assertEqual(message["state"], "LOCKED")
        self.assertTrue(message["locked"])
        self.assertEqual(message["status_code"], "1B")


class F0513BatteryTest(FakeAdapterTestCase):
    battery_kwargs = {"f0513": True}

    def test_detection_falls_back_to_the_limited_command_set(self):
        client = makita.MakitaClient(self.open_link())

        model, version = client.detect_model()

        self.assertEqual(version, makita.F0513)
        self.assertEqual(model, "BL185")

    def test_cell_voltages_come_from_individual_commands(self):
        client = makita.MakitaClient(self.open_link())
        client.detect_model()

        data = client.read_pack_data()

        self.assertAlmostEqual(data["pack_v"], sum(
            data["cell%d_v" % i] for i in range(1, 6)), places=3)
        self.assertIsNone(data["temp2_c"])


class CommandLineLoggingTest(FakeAdapterTestCase):
    battery_kwargs = {"drain_mv_per_s": 20.0}

    def test_logs_samples_to_csv(self):
        import obi_cli

        path = os.path.join(self.tmp, "log.csv")
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            exit_code = obi_cli.main([
                "--port", self.device, "--boot-delay", "0", "--interval", "0.2",
                "--samples", "3", "--include-status", "--out", path, "--quiet"])

        self.assertEqual(exit_code, 0)
        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["model"], "BL1850B")
        self.assertEqual(rows[0]["state"], "UNLOCKED")
        self.assertEqual(rows[0]["charge_count"], "564")
        self.assertEqual([row["sample"] for row in rows], ["1", "2", "3"])
        # The emulated pack drains, so the log must show a falling voltage.
        self.assertLess(float(rows[-1]["pack_v"]), float(rows[0]["pack_v"]))
        self.assertTrue(all(row["error"] == "" for row in rows))

    def test_single_reading_mode(self):
        import obi_cli

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = obi_cli.main(
                ["--port", self.device, "--boot-delay", "0", "--once", "--quiet"])

        self.assertEqual(exit_code, 0)
        self.assertIn("pack", output.getvalue())
        self.assertIn("delta", output.getvalue())


class FlakyAdapterTest(FakeAdapterTestCase):
    adapter_kwargs = {"flaky": 1.0}  # drop every response

    def test_failed_samples_are_recorded_and_do_not_stop_the_log(self):
        from core import sampling

        client = makita.MakitaClient(self.open_link(timeout=0.2))
        client.command_version = ""
        path = os.path.join(self.tmp, "flaky.csv")
        writer = sampling.make_writer(path, makita.log_fieldnames())
        logger = sampling.SampleLogger(lambda: client.sample(trace=False), writer,
                                       interval=0.01, max_samples=2)
        logger.start()
        logger.join(timeout=30)

        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 2)
        self.assertEqual(logger.errors, 2)
        self.assertTrue(all(row["error"] for row in rows))
        self.assertIn("attempts", rows[0]["error"])


@unittest.skipIf(tkinter is None, "tkinter is not installed")
@unittest.skipUnless(HAS_DISPLAY, "no display available")
class GuiLoggingTest(FakeAdapterTestCase):
    """The GUI path: connect, read, log to disk, stop - all through widgets."""

    battery_kwargs = {"drain_mv_per_s": 20.0}

    def setUp(self):
        super().setUp()
        os.environ["OBI_EXTRA_PORTS"] = self.device
        self.addCleanup(os.environ.pop, "OBI_EXTRA_PORTS", None)

        import main
        self.app = main.OBI()
        self.addCleanup(self._close_app)

        self.app.module_var.set("Makita LXT")
        self.app.display_module()
        self.app.interface_var.set("Arduino OBI")
        self.app.display_interface_settings()
        self.pump(0.1)

        interface = self.app.current_interface
        interface.link.boot_delay = 0
        interface.conf_port.set(interface._ports[0].label)
        interface.open_serial_port()
        self.assertTrue(interface.is_connected, "could not connect to the fake adapter")

    def _close_app(self):
        try:
            self.app.on_close()
        except tkinter.TclError:  # pragma: no cover - already gone
            pass

    def pump(self, seconds):
        """Run the Tk event loop for a while without blocking on mainloop()."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.app.update()
            time.sleep(0.02)

    def test_reads_battery_into_the_tree(self):
        self.app.main_app.on_read_static_click()
        self.app.main_app.on_read_data_click()
        self.pump(0.1)

        values = {}
        tree = self.app.main_app.tree
        for item in tree.get_children():
            values[tree.item(item, "text")] = tree.item(item, "values")[0]

        self.assertEqual(values["Model"], "BL1850B")
        self.assertEqual(values["State"], "UNLOCKED")
        self.assertEqual(values["Charge count*"], "564")
        self.assertNotEqual(values["Pack Voltage"], "")
        self.assertNotEqual(values["Cell 3 Voltage"], "")

    def test_logging_panel_writes_a_csv(self):
        panel = self.app.main_app.logging_frame
        path = os.path.join(self.tmp, "gui.csv")
        panel.path_var.set(path)
        panel.interval_var.set("0.2")

        panel.start_logging()
        self.assertIsNotNone(panel.logger, "logging did not start")

        deadline = time.time() + 15
        while time.time() < deadline and panel.logger and panel.logger.samples < 3:
            self.pump(0.1)

        panel.stop_logging()
        self.pump(0.5)

        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(rows[0]["model"], "BL1850B")
        self.assertTrue(all(row["error"] == "" for row in rows))
        self.assertIn("samples", panel.status_label.cget("text"))

    def test_logging_stops_when_the_view_is_destroyed(self):
        panel = self.app.main_app.logging_frame
        panel.path_var.set(os.path.join(self.tmp, "destroy.csv"))
        panel.interval_var.set("0.2")
        panel.start_logging()
        logger = panel.logger
        self.assertTrue(logger.is_running)

        # Switching modules must not leave a logger running in the background.
        self.app.display_module()
        self.pump(0.3)

        self.assertFalse(logger.is_running)


if __name__ == "__main__":
    unittest.main()
