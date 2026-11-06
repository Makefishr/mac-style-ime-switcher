import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ime_switcher.system_hotkeys import LanguageHotkeys, TOGGLE_KEY, VALUE_NAMES
from tests.fakes import Registry


class LanguageHotkeysTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.backup = Path(folder) / "language_hotkeys_backup.json"
        self.original = {"Hotkey": ("1", 1), "Language Hotkey": ("2", 1)}
        self.registry = Registry({TOGGLE_KEY: self.original.copy()})
        self.refresh = Mock(return_value=True)
        self.hotkeys = LanguageHotkeys(self.registry, self.refresh, self.backup)

    def test_disable_saves_backup_before_first_system_write(self):
        self.registry.before_write = lambda: self.assertTrue(self.backup.exists())
        self.assertTrue(self.hotkeys.apply(True))
        self.assertEqual(self.registry.entries[TOGGLE_KEY],
                         {name: ("3", 1) for name in VALUE_NAMES})
        self.assertIsNone(json.loads(self.backup.read_text())["Layout Hotkey"])
        self.refresh.assert_called_once_with()

    def test_repeated_enable_and_process_restart_preserve_first_values(self):
        self.hotkeys.apply(True)
        first_backup = self.backup.read_bytes()
        restarted = LanguageHotkeys(self.registry, self.refresh, self.backup)
        restarted.apply(True)
        self.assertEqual(self.backup.read_bytes(), first_backup)
        restarted.apply(False)
        self.assertEqual(self.registry.entries[TOGGLE_KEY], self.original)
        self.assertFalse(self.backup.exists())

    def test_original_absent_key_restores_absent_values(self):
        self.registry.entries.clear()
        self.hotkeys.apply(True)
        self.hotkeys.apply(False)
        self.assertEqual(self.registry.entries[TOGGLE_KEY], {})

    def test_restore_keeps_original_types_including_binary(self):
        original = {"Hotkey": (b"\x00\xff", 3), "Layout Hotkey": (2, 4)}
        self.registry.entries[TOGGLE_KEY] = original.copy()
        self.hotkeys.apply(True)
        self.hotkeys.apply(False)
        self.assertEqual(self.registry.entries[TOGGLE_KEY], original)

    def test_disabled_option_without_backup_does_not_touch_registry(self):
        self.assertTrue(self.hotkeys.apply(False))
        self.assertEqual(self.registry.writes, [])
        self.refresh.assert_not_called()

    def test_failed_backup_write_does_not_change_registry(self):
        with patch("ime_switcher.system_hotkeys.save_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.hotkeys.apply(True)
        self.assertEqual(self.registry.writes, [])

    def test_missing_backup_leaves_already_disabled_shortcuts_untouched(self):
        self.registry.entries[TOGGLE_KEY] = {
            name: ("3", self.registry.REG_SZ) for name in VALUE_NAMES
        }
        with self.assertLogs("ime_switcher.system_hotkeys", level="INFO") as logs:
            self.assertTrue(self.hotkeys.apply(False))
        self.assertTrue(any("No language shortcut backup" in line for line in logs.output))
        self.assertEqual(self.registry.writes, [])
        self.refresh.assert_not_called()

    def test_failed_reload_after_restore_retains_backup_until_retry(self):
        self.hotkeys.apply(True)
        self.refresh.return_value = False
        with self.assertLogs("ime_switcher.system_hotkeys", level="WARNING"):
            self.assertFalse(self.hotkeys.apply(False))
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.registry.entries[TOGGLE_KEY], self.original)
        self.refresh.return_value = True
        self.assertTrue(self.hotkeys.apply(False))
        self.assertFalse(self.backup.exists())

    def test_partial_disable_can_still_restore_original_values(self):
        calls = 0

        def fail_second_write():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("registry write denied")

        self.registry.before_write = fail_second_write
        with self.assertRaises(PermissionError):
            self.hotkeys.apply(True)
        self.registry.before_write = lambda: None
        self.hotkeys.apply(False)
        self.assertEqual(self.registry.entries[TOGGLE_KEY], self.original)

    def test_failed_restore_retains_backup_for_retry(self):
        self.hotkeys.apply(True)
        self.registry.before_write = Mock(side_effect=PermissionError("denied"))
        with self.assertRaises(PermissionError):
            self.hotkeys.apply(False)
        self.assertTrue(self.backup.exists())

    def test_corrupt_backup_is_not_overwritten_or_applied(self):
        self.backup.write_text('{"Hotkey": null}')
        with self.assertRaises(ValueError):
            self.hotkeys.apply(True)
        self.assertEqual(self.registry.writes, [])

    def test_reload_failure_is_reported_without_losing_recovery_state(self):
        self.refresh.return_value = False
        with self.assertLogs("ime_switcher.system_hotkeys", level="WARNING"):
            self.assertFalse(self.hotkeys.apply(True))
        self.assertTrue(self.backup.exists())
