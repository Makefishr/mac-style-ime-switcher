import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ime_switcher.settings_store import Preferences, SwitchingMethod, load_preferences, save_preferences


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(folder) / "settings.json"
        self.enterContext(patch("ime_switcher.settings_store.settings_path", return_value=self.path))

    def test_missing_file_defaults_enable_both_shortcut_options(self):
        preferences = load_preferences()
        self.assertTrue(preferences.disable_system_hotkeys)
        self.assertTrue(preferences.block_ctrl_space)
        self.assertIsNone(preferences.autostart)

    def test_old_settings_keep_mode_and_get_new_defaults(self):
        self.path.write_text(json.dumps({"run_as_administrator": True,
                                        "switching_method": "microsoft_pinyin_mode"}))
        preferences = load_preferences()
        self.assertTrue(preferences.run_as_administrator)
        self.assertEqual(preferences.switching_method, SwitchingMethod.MICROSOFT_PINYIN_MODE)
        self.assertTrue(preferences.disable_system_hotkeys)
        self.assertTrue(preferences.block_ctrl_space)

    def test_options_and_autostart_round_trip(self):
        expected = replace(Preferences(), disable_system_hotkeys=False,
                           block_ctrl_space=False, autostart=True)
        save_preferences(expected)
        self.assertEqual(load_preferences(), expected)

    def test_invalid_boolean_does_not_turn_string_false_into_true(self):
        self.path.write_text('{"block_ctrl_space": "false"}')
        with self.assertLogs("ime_switcher.settings_store", level="ERROR"):
            self.assertEqual(load_preferences(), Preferences())
