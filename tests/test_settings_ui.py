import tkinter as tk
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from ime_switcher import settings_ui
from ime_switcher.settings_store import Preferences, SwitchingMethod


def _window_with_values(values, baseline, elevated):
    window = settings_ui.SettingsWindow.__new__(settings_ui.SettingsWindow)
    names = ("_run_as_admin", "_autostart", "_switching_method",
             "_disable_system_hotkeys", "_block_ctrl_space")
    for name, value in zip(names, values):
        setattr(window, name, Mock(get=Mock(return_value=value)))
    window._baseline = baseline
    window._elevated = elevated
    return window


class SettingsWindowTests(unittest.TestCase):
    def test_refresh_uses_saved_autostart_intent_without_querying_scheduler(self):
        window = settings_ui.SettingsWindow.__new__(settings_ui.SettingsWindow)
        for name in ("_run_as_admin", "_autostart", "_switching_method",
                     "_disable_system_hotkeys", "_block_ctrl_space", "_status"):
            setattr(window, name, Mock())
        with (
            patch.object(settings_ui, "load_preferences", return_value=replace(Preferences(), autostart=True)),
            patch.object(settings_ui.tray, "is_autostart_enabled") as actual,
            patch.object(settings_ui.elevation, "is_elevated", return_value=False),
        ):
            window.refresh_effective_state()
        window._autostart.set.assert_called_once_with(True)
        actual.assert_not_called()


class ChangeHintTests(unittest.TestCase):
    METHOD = SwitchingMethod.KEYBOARD_LAYOUTS.value

    def hint(self, values, baseline, elevated=False):
        return _window_with_values(values, baseline, elevated)._change_hint()

    def test_enabling_administrator_mode_warns_about_uac_restart(self):
        hint = self.hint(
            (True, False, self.METHOD, True, True),
            (False, False, self.METHOD, True, True),
        )
        self.assertIn("UAC", hint)

    def test_disabling_administrator_mode_while_elevated_applies_next_start(self):
        hint = self.hint(
            (False, True, self.METHOD, True, True),
            (True, True, self.METHOD, True, True),
            elevated=True,
        )
        self.assertIn("下次启动", hint)

    def test_enabling_autostart_in_administrator_mode_mentions_logon_task(self):
        hint = self.hint(
            (True, True, self.METHOD, True, True),
            (True, False, self.METHOD, True, True),
            elevated=True,
        )
        self.assertIn("计划任务", hint)

    def test_restoring_system_shortcuts_mentions_sign_out(self):
        hint = self.hint(
            (False, False, self.METHOD, False, True),
            (False, False, self.METHOD, True, True),
        )
        self.assertIn("注销", hint)

    def test_change_without_side_effect_has_no_hint(self):
        hint = self.hint(
            (False, False, self.METHOD, True, False),
            (False, False, self.METHOD, True, True),
        )
        self.assertEqual("", hint)


class SettingsWindowSmokeTests(unittest.TestCase):
    """Build the real window to catch layout and image-rendering errors."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        for target, value in (
            ("load_preferences", Preferences(autostart=False)),
            ("_system_uses_light_theme", True),
        ):
            patcher = patch.object(settings_ui, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(settings_ui.elevation, "is_elevated", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(settings_ui, "_style_title_bar")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_enables_only_after_a_change_and_passes_values(self):
        on_save = Mock(return_value=True)
        window = settings_ui.SettingsWindow(self.root, on_save)
        window.show()
        self.root.update_idletasks()
        self.assertFalse(window._save_button.enabled)
        self.assertFalse(window._hint_frame.winfo_ismapped())

        window._run_as_admin.set(True)
        self.root.update_idletasks()
        self.assertTrue(window._save_button.enabled)
        self.assertIn("UAC", window._hint.get())

        window._save_button.invoke()
        on_save.assert_called_once_with(
            True, False, SwitchingMethod.KEYBOARD_LAYOUTS, True, True,
        )
        self.assertFalse(window._window.winfo_viewable())

    def test_cancel_discards_pending_changes(self):
        on_save = Mock(return_value=True)
        window = settings_ui.SettingsWindow(self.root, on_save)
        window.show()
        window._block_ctrl_space.set(False)
        window._cancel()
        on_save.assert_not_called()
        self.assertTrue(window._block_ctrl_space.get())
        self.assertFalse(window._save_button.enabled)

    def test_dark_theme_rebuilds_window_on_next_show(self):
        window = settings_ui.SettingsWindow(self.root, Mock(return_value=True))
        window.show()
        first = window._window
        window.hide()
        with patch.object(settings_ui, "_system_uses_light_theme", return_value=False):
            window.show()
        self.assertIsNot(first, window._window)
        self.assertFalse(window._art.palette.light)


if __name__ == "__main__":
    unittest.main()
