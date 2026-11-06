import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from ime_switcher import settings_ui
from ime_switcher.settings_store import Preferences


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
