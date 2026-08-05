"""Theme palette and desktop-preference detection tests (no display needed)."""

import os
import unittest
from unittest import mock

from components import theme


class PaletteTest(unittest.TestCase):
    def test_both_palettes_define_the_same_colours(self):
        light = set(theme.PALETTES["light"])
        dark = set(theme.PALETTES["dark"])

        self.assertEqual(light, dark)
        # Anything the styles reference must exist in both.
        for required in ("window", "card", "view", "border", "text", "dim",
                         "accent", "success", "warning", "error"):
            self.assertIn(required, light)

    def test_every_colour_is_a_hex_value(self):
        for name, palette in theme.PALETTES.items():
            for key, value in palette.items():
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$",
                                 "%s.%s is not a hex colour" % (name, key))

    def test_unknown_theme_name_falls_back_to_light(self):
        self.assertEqual(theme.Theme("chartreuse").name, "light")
        self.assertEqual(theme.Theme("dark").name, "dark")


class SchemeDetectionTest(unittest.TestCase):
    def detect(self, environ, gsettings=None):
        with mock.patch.dict(os.environ, environ, clear=True):
            with mock.patch.object(theme, "_gsettings",
                                   side_effect=lambda schema, key: (gsettings or {}).get(key)):
                return theme.detect_scheme()

    def test_env_override_wins(self):
        self.assertEqual(self.detect({"OBI_THEME": "dark"},
                                     {"color-scheme": "default"}), "dark")
        self.assertEqual(self.detect({"OBI_THEME": "light"},
                                     {"color-scheme": "prefer-dark"}), "light")

    def test_follows_the_gnome_colour_scheme(self):
        self.assertEqual(self.detect({}, {"color-scheme": "prefer-dark"}), "dark")
        self.assertEqual(self.detect({}, {"color-scheme": "prefer-light"}), "light")
        self.assertEqual(self.detect({}, {"color-scheme": "default"}), "light")

    def test_falls_back_to_the_gtk_theme_name(self):
        self.assertEqual(self.detect({}, {"gtk-theme": "Yaru-blue-dark"}), "dark")
        self.assertEqual(self.detect({}, {"gtk-theme": "Yaru"}), "light")

    def test_gtk_theme_environment_variable_is_honoured(self):
        self.assertEqual(self.detect({"GTK_THEME": "Adwaita:dark"}), "dark")

    def test_light_when_nothing_is_known(self):
        self.assertEqual(self.detect({}), "light")

    def test_missing_gsettings_does_not_raise(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(theme.subprocess, "run", side_effect=OSError):
                self.assertEqual(theme.detect_scheme(), "light")


class TextScalingTest(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"OBI_SCALING": "1.5"}, clear=True):
            self.assertEqual(theme.detect_text_scaling(), 1.5)

    def test_invalid_override_is_ignored(self):
        with mock.patch.dict(os.environ, {"OBI_SCALING": "huge"}, clear=True):
            self.assertEqual(theme.detect_text_scaling(), 1.0)

    def test_reads_the_desktop_factor(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(theme, "_gsettings", return_value="1.25"):
                self.assertEqual(theme.detect_text_scaling(), 1.25)

    def test_unset_factor_means_no_scaling(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(theme, "_gsettings", return_value=None):
                self.assertEqual(theme.detect_text_scaling(), 1.0)


class GsettingsTest(unittest.TestCase):
    def test_failed_command_returns_none(self):
        result = mock.Mock(returncode=1, stdout=b"")
        with mock.patch.object(theme.subprocess, "run", return_value=result):
            self.assertIsNone(theme._gsettings("org.example", "key"))

    def test_value_is_unquoted(self):
        result = mock.Mock(returncode=0, stdout=b"'prefer-dark'\n")
        with mock.patch.object(theme.subprocess, "run", return_value=result):
            self.assertEqual(theme._gsettings("org.example", "key"), "prefer-dark")

    def test_a_hanging_gsettings_cannot_block_startup(self):
        with mock.patch.object(theme.subprocess, "run",
                               side_effect=theme.subprocess.TimeoutExpired("gsettings", 1.5)):
            self.assertIsNone(theme._gsettings("org.example", "key"))


if __name__ == "__main__":
    unittest.main()
