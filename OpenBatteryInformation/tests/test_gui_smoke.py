"""End-to-end smoke test for the Tk application.

This is the test that would have caught the original port's assumptions about
Windows: it builds the real window, loads the real module and interface, and
exercises the threading rules the logger depends on.

Runs under Xvfb in CI (``xvfb-run -a python -m unittest ...``) and is skipped
automatically when there is no display.
"""

import os
import threading
import unittest

try:
    import tkinter
except ImportError:  # pragma: no cover - distro without python3-tk
    tkinter = None

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@unittest.skipIf(tkinter is None, "tkinter is not installed")
@unittest.skipUnless(HAS_DISPLAY, "no display available")
class GuiSmokeTest(unittest.TestCase):
    def setUp(self):
        import main
        self.app = main.OBI()
        self.app.update()

    def tearDown(self):
        try:
            self.app.on_close()
        except tkinter.TclError:  # pragma: no cover - already destroyed
            pass

    def test_window_and_plugins_load(self):
        self.assertEqual(self.app.title(), "OBI Linux")
        self.assertIn("Makita LXT", self.app.module_combobox["values"])
        self.assertIn("Arduino OBI", self.app.interface_combobox["values"])

    def test_helper_modules_are_not_offered_as_plugins(self):
        # core/ and components/ must never show up in the pickers.
        for value in self.app.module_combobox["values"]:
            self.assertNotIn("logging_frame", value)

    def test_selecting_the_module_builds_its_view(self):
        self.app.module_var.set("Makita LXT")
        self.app.display_module()
        self.app.update()

        self.assertIsNotNone(self.app.main_app)
        self.assertTrue(hasattr(self.app.main_app, "logging_frame"))
        self.assertTrue(hasattr(self.app.main_app, "tree"))

    def test_selecting_the_interface_builds_its_view(self):
        self.app.interface_var.set("Arduino OBI")
        self.app.display_interface_settings()
        self.app.update()

        self.assertIsNotNone(self.app.current_interface)
        self.assertFalse(self.app.current_interface.is_connected)

    def test_module_and_interface_are_wired_together(self):
        self.app.module_var.set("Makita LXT")
        self.app.display_module()
        self.app.interface_var.set("Arduino OBI")
        self.app.display_interface_settings()
        self.app.update()

        self.assertIs(self.app.main_app.interface, self.app.current_interface)
        self.assertIsNotNone(self.app.main_app.client)

    def test_logging_refuses_to_start_without_a_connection(self):
        self.app.module_var.set("Makita LXT")
        self.app.display_module()
        self.app.interface_var.set("Arduino OBI")
        self.app.display_interface_settings()
        self.app.update()

        with self.assertRaises(RuntimeError) as caught:
            self.app.main_app.prepare_logging({"include_status": False})

        self.assertIn("Connect", str(caught.exception))

    def test_debug_messages_from_worker_threads_reach_the_pane(self):
        def worker():
            self.app.update_debug("hello from a background thread")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)

        self.app._drain_debug_queue()
        self.app.update()

        contents = self.app.debug_text.get("1.0", "end")
        self.assertIn("hello from a background thread", contents)

    def test_debug_pane_is_bounded(self):
        import main

        for index in range(main.DEBUG_MAX_LINES + 50):
            self.app.update_debug("line %d" % index)

        lines = int(self.app.debug_text.index("end-1c").split(".")[0])
        self.assertLessEqual(lines, main.DEBUG_MAX_LINES)
        # The newest line must survive the trimming.
        self.assertIn("line %d" % (main.DEBUG_MAX_LINES + 49),
                      self.app.debug_text.get("1.0", "end"))

    def test_switching_modules_destroys_the_previous_view(self):
        self.app.module_var.set("Makita LXT")
        self.app.display_module()
        first = self.app.main_app
        self.app.display_module()
        self.app.update()

        self.assertIsNot(self.app.main_app, first)
        self.assertFalse(first.winfo_exists())


if __name__ == "__main__":
    unittest.main()
