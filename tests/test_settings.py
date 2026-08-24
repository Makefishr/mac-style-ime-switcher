"""Tests for persisted application settings."""
import ctypes
import json
import os
import tempfile
import threading
import unittest
from ctypes import wintypes
from unittest.mock import patch
from pathlib import Path

from ime_switcher.settings import (
    MODE_IME,
    MODE_LAYOUT,
    Settings,
    _open_settings_window,
    get_settings,
    load_settings,
    save_settings,
)
from ime_switcher import settings, winapi


class FakeRoot:
    def __init__(self):
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1


class FakeThread:
    def __init__(self, **_kwargs):
        self.started = False
        self.alive = True

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive


class FakeRunRegistry:
    def __init__(
        self,
        *,
        path,
        value=None,
        apply_failure=None,
        restore_failure=None,
        snapshot_failure=False,
    ):
        self.path = path
        self.exists = value is not None
        self.value = value
        self.apply_failure = apply_failure
        self.restore_failure = restore_failure
        self.snapshot_failure = snapshot_failure
        self.events = []
        self.file_during_apply = None
        self.cache_during_apply = None

    def snapshot(self):
        self.events.append("snapshot")
        if self.snapshot_failure:
            raise OSError("private-snapshot-marker")
        return self.exists, self.value

    def apply(self, enabled):
        self.events.append(("apply", enabled))
        self.file_during_apply = (
            self.path.read_bytes() if self.path.exists() else None
        )
        self.cache_during_apply = get_settings(self.path)
        if self.apply_failure == "before":
            raise OSError("private-run-marker")
        self.exists = bool(enabled)
        self.value = "normalized-command" if enabled else None
        if self.apply_failure == "after":
            raise OSError("private-run-marker")

    def restore(self, snapshot):
        self.events.append("restore")
        if self.restore_failure == "before":
            raise OSError("private-rollback-marker")
        self.exists, self.value = snapshot
        if self.restore_failure == "after":
            raise OSError("private-rollback-marker")


class TestSettings(unittest.TestCase):
    def test_defaults_use_layout_mode_and_detect_autostart(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = load_settings(
                Path(directory) / "settings.json",
                autostart_detector=lambda: True,
            )

        self.assertEqual(settings.mode, MODE_LAYOUT)
        self.assertTrue(settings.autostart)
        self.assertFalse(settings.admin)

    def test_explicit_boolean_strings_are_parsed_without_truthiness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"autostart": " false ", "admin": "TrUe"}),
                encoding="utf-8",
            )

            restored = load_settings(
                path,
                autostart_detector=lambda: True,
            )

        self.assertEqual(
            restored,
            Settings(mode=MODE_LAYOUT, autostart=False, admin=True),
        )

    def test_unknown_boolean_string_uses_default_without_logging_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"autostart": "enabled-sensitive"}),
                encoding="utf-8",
            )

            with self.assertLogs(
                "ime_switcher.settings",
                level="WARNING",
            ) as logs:
                restored = load_settings(
                    path,
                    autostart_detector=lambda: False,
                )

        self.assertFalse(restored.autostart)
        self.assertNotIn("enabled-sensitive", "\n".join(logs.output))

    def test_non_boolean_json_types_use_each_field_default(self):
        invalid_values = (0, 1, -1, 1.0, None, [], {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for value in invalid_values:
                with self.subTest(value=value):
                    path.write_text(
                        json.dumps({"autostart": value, "admin": value}),
                        encoding="utf-8",
                    )
                    with self.assertLogs(
                        "ime_switcher.settings",
                        level="WARNING",
                    ):
                        restored = load_settings(
                            path,
                            autostart_detector=lambda: True,
                        )

                    self.assertEqual(
                        restored,
                        Settings(
                            mode=MODE_LAYOUT,
                            autostart=True,
                            admin=False,
                        ),
                    )

    def test_effective_defaults_and_cache_stay_consistent_for_one_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"autostart": "invalid"}),
                encoding="utf-8",
            )
            with self.assertLogs(
                "ime_switcher.settings",
                level="WARNING",
            ):
                first = get_settings(
                    path,
                    autostart_detector=lambda: True,
                )

            path.write_text(
                json.dumps({"autostart": False, "admin": True}),
                encoding="utf-8",
            )
            cached = get_settings(
                path,
                autostart_detector=lambda: False,
            )

        self.assertIs(cached, first)
        self.assertEqual(
            cached,
            Settings(mode=MODE_LAYOUT, autostart=True, admin=False),
        )

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = Settings(mode=MODE_IME, autostart=True, admin=True)
            save_settings(original, path)

            restored = load_settings(path, autostart_detector=lambda: False)

        self.assertEqual(restored, original)

    def test_settings_writeback_always_uses_json_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(
                Settings(autostart=" false ", admin="TRUE"),
                path,
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertIs(persisted["autostart"], False)
        self.assertIs(persisted["admin"], True)

    def test_save_settings_uses_synced_same_directory_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_bytes(b"old")
            real_fsync = os.fsync
            real_replace = os.replace
            observed = {}

            def replace(source, destination):
                observed["source"] = Path(source)
                observed["destination"] = Path(destination)
                observed["old_bytes"] = path.read_bytes()
                return real_replace(source, destination)

            with patch.object(
                settings.os,
                "fsync",
                wraps=real_fsync,
            ) as fsync, patch.object(
                settings.os,
                "replace",
                side_effect=replace,
            ):
                save_settings(Settings(autostart=True), path)

            self.assertGreaterEqual(fsync.call_count, 1)
            self.assertEqual(observed["source"].parent, path.parent)
            self.assertNotEqual(observed["source"], path)
            self.assertEqual(observed["destination"], path)
            self.assertEqual(observed["old_bytes"], b"old")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_install_autostart_encodes_frozen_and_source_commands(self):
        class FakeRegistryKey:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

        key = FakeRegistryKey()

        def installed_value(executable, frozen):
            with patch.object(
                settings.winreg, "CreateKeyEx", return_value=key,
            ), patch.object(settings.winreg, "SetValueEx") as set_value, patch.object(
                settings.sys, "executable", executable,
            ), patch.object(settings.sys, "frozen", frozen, create=True):
                settings.install_autostart()
            return set_value.call_args.args[-1]

        self.assertEqual(
            installed_value(
                r"C:\Program Files\MacStyleIME\MacStyleIME.exe",
                True,
            ),
            r'"C:\Program Files\MacStyleIME\MacStyleIME.exe"',
        )
        self.assertEqual(
            installed_value(r"C:\MacStyleIME\MacStyleIME.exe", True),
            r"C:\MacStyleIME\MacStyleIME.exe",
        )

        source_script = str(
            Path(settings.__file__).resolve().with_name("__main__.py")
        )
        self.assertEqual(
            installed_value(r"C:\Python312\python.exe", False),
            f'"C:\\Python312\\python.exe" "{source_script}"',
        )


class TestSettingsTransaction(unittest.TestCase):
    def test_file_snapshot_failure_stops_before_registry_or_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
            )
            real_read_bytes = Path.read_bytes

            def fail_target_read(candidate):
                if candidate == path:
                    raise OSError("private-file-snapshot-marker")
                return real_read_bytes(candidate)

            with patch.object(
                Path,
                "read_bytes",
                new=fail_target_read,
            ), self.assertLogs(
                "ime_switcher.settings",
                level="WARNING",
            ) as logs:
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(registry.events, [])
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            log_output = "\n".join(logs.output)
            self.assertNotIn("private-file-snapshot-marker", log_output)
            self.assertNotIn(directory, log_output)

    def test_run_snapshot_failure_stops_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
                snapshot_failure=True,
            )

            with self.assertLogs(
                "ime_switcher.settings",
                level="WARNING",
            ) as logs:
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(registry.events, ["snapshot"])
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            log_output = "\n".join(logs.output)
            self.assertNotIn("private-snapshot-marker", log_output)
            self.assertNotIn(directory, log_output)

    def test_success_commits_file_registry_and_cache_together(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = (
                b'{"mode":"layout","autostart":false,"admin":false}\n'
            )
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
            )
            new_settings = Settings(
                mode=MODE_IME,
                autostart=True,
                admin=True,
            )

            result = settings.save_user_settings(
                new_settings,
                path=path,
                registry_adapter=registry,
            )

            self.assertEqual(result, settings.SaveResult.SUCCESS)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                new_settings.to_dict(),
            )
            self.assertEqual((registry.exists, registry.value), (True, "normalized-command"))
            self.assertEqual(registry.file_during_apply, old_bytes)
            self.assertIs(registry.cache_during_apply, old_cache)
            self.assertEqual(get_settings(path), new_settings)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_success_creates_missing_file_and_run_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(path=path)
            new_settings = Settings(
                mode=MODE_IME,
                autostart=True,
                admin=True,
            )

            result = settings.save_user_settings(
                new_settings,
                path=path,
                registry_adapter=registry,
            )

            self.assertEqual(result, settings.SaveResult.SUCCESS)
            self.assertIsNone(registry.file_during_apply)
            self.assertIs(registry.cache_during_apply, old_cache)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "normalized-command"),
            )
            self.assertEqual(get_settings(path), new_settings)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_consecutive_saves_commit_each_complete_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(Settings().to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: False)
            registry = FakeRunRegistry(path=path)
            first = Settings(
                mode=MODE_IME,
                autostart=True,
                admin=True,
            )
            second = Settings(
                mode=MODE_LAYOUT,
                autostart=False,
                admin=False,
            )

            first_result = settings.save_user_settings(
                first,
                path=path,
                registry_adapter=registry,
            )
            second_result = settings.save_user_settings(
                second,
                path=path,
                registry_adapter=registry,
            )

            self.assertEqual(
                (first_result, second_result),
                (
                    settings.SaveResult.SUCCESS,
                    settings.SaveResult.SUCCESS,
                ),
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                second.to_dict(),
            )
            self.assertEqual((registry.exists, registry.value), (False, None))
            self.assertEqual(get_settings(path), second)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_fsync_failure_leaves_all_committed_state_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
            )

            with patch.object(
                settings.os,
                "fsync",
                side_effect=OSError("private-stage-marker"),
            ), self.assertLogs(
                "ime_switcher.settings",
                level="WARNING",
            ) as logs:
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "custom-old-command"),
            )
            self.assertEqual(registry.events, ["snapshot"])
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            log_output = "\n".join(logs.output)
            self.assertNotIn("private-stage-marker", log_output)
            self.assertNotIn(directory, log_output)

    def test_run_failure_before_modification_restores_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
                apply_failure="before",
            )

            with self.assertLogs(
                "ime_switcher.settings",
                level="WARNING",
            ) as logs:
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "custom-old-command"),
            )
            self.assertEqual(
                registry.events,
                ["snapshot", ("apply", True), "restore"],
            )
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            log_output = "\n".join(logs.output)
            self.assertNotIn("private-run-marker", log_output)
            self.assertNotIn(directory, log_output)

    def test_run_failure_after_modification_restores_custom_old_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value='"custom old command" --private-flag',
                apply_failure="after",
            )

            result = settings.save_user_settings(
                Settings(autostart=True),
                path=path,
                registry_adapter=registry,
            )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, '"custom old command" --private-flag'),
            )
            self.assertEqual(
                registry.events,
                ["snapshot", ("apply", True), "restore"],
            )
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_rollback_delete_reports_partial_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                apply_failure="after",
                restore_failure="before",
            )

            with self.assertLogs(
                "ime_switcher.settings",
                level="ERROR",
            ) as logs:
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.PARTIAL_FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "normalized-command"),
            )
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            log_output = "\n".join(logs.output)
            self.assertNotIn("private-rollback-marker", log_output)
            self.assertNotIn("normalized-command", log_output)
            self.assertNotIn(directory, log_output)

    def test_run_failure_restores_absent_run_value_by_deleting_new_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                apply_failure="after",
            )

            result = settings.save_user_settings(
                Settings(autostart=True),
                path=path,
                registry_adapter=registry,
            )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual((registry.exists, registry.value), (False, None))
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_replace_failure_restores_run_and_leaves_file_and_cache_old(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
            )

            with patch.object(
                settings.os,
                "replace",
                side_effect=OSError("private-replace-marker"),
            ), self.assertLogs(
                "ime_switcher.settings",
                level="WARNING",
            ) as logs:
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "custom-old-command"),
            )
            self.assertEqual(
                registry.events,
                ["snapshot", ("apply", True), "restore"],
            )
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            log_output = "\n".join(logs.output)
            self.assertNotIn("private-replace-marker", log_output)
            self.assertNotIn(directory, log_output)

    def test_replace_failure_after_effect_restores_exact_old_file_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{ "admin": false, "custom": "preserve" }\r\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
            )
            real_replace = os.replace

            def replace_then_fail(source, destination):
                real_replace(source, destination)
                raise OSError("private-post-replace-marker")

            with patch.object(
                settings.os,
                "replace",
                side_effect=replace_then_fail,
            ):
                result = settings.save_user_settings(
                    Settings(autostart=True, admin=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "custom-old-command"),
            )
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_replace_failure_removes_file_that_did_not_exist_before(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            registry = FakeRunRegistry(
                path=path,
                value="custom-old-command",
            )
            real_replace = os.replace

            def replace_then_fail(source, destination):
                real_replace(source, destination)
                raise OSError("private-post-replace-marker")

            with patch.object(
                settings.os,
                "replace",
                side_effect=replace_then_fail,
            ):
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertFalse(path.exists())
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "custom-old-command"),
            )
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_failed_rollback_writeback_reports_partial_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_bytes = b'{"mode":"layout","autostart":false}\n'
            path.write_bytes(old_bytes)
            old_cache = get_settings(
                path,
                autostart_detector=lambda: False,
            )
            old_run = '"private custom command" --old'
            registry = FakeRunRegistry(
                path=path,
                value=old_run,
                restore_failure="before",
            )

            with patch.object(
                settings.os,
                "replace",
                side_effect=OSError("private-replace-marker"),
            ), self.assertLogs(
                "ime_switcher.settings",
                level="ERROR",
            ) as logs:
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.PARTIAL_FAILURE)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "normalized-command"),
            )
            self.assertIs(get_settings(path), old_cache)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            log_output = "\n".join(logs.output)
            self.assertNotIn(old_run, log_output)
            self.assertNotIn("private-replace-marker", log_output)
            self.assertNotIn(directory, log_output)

    def test_default_registry_adapter_restores_exact_value_and_type(self):
        class RegistryKey:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(Settings().to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: False)
            old_run = '"custom executable" --preserve-exactly'
            key = RegistryKey()

            with patch.object(
                settings.winreg,
                "OpenKey",
                return_value=key,
            ), patch.object(
                settings.winreg,
                "QueryValueEx",
                return_value=(old_run, settings.winreg.REG_EXPAND_SZ),
            ), patch.object(
                settings.winreg,
                "CreateKeyEx",
                return_value=key,
            ), patch.object(
                settings.winreg,
                "SetValueEx",
            ) as set_value, patch.object(
                settings.os,
                "replace",
                side_effect=OSError("replace failed"),
            ):
                result = settings.save_user_settings(
                    Settings(autostart=True),
                    path=path,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertGreaterEqual(set_value.call_count, 2)
            self.assertEqual(
                set_value.call_args.args[1:],
                (
                    settings.config.APP_NAME,
                    0,
                    settings.winreg.REG_EXPAND_SZ,
                    old_run,
                ),
            )


class TestSettingsTransactionConcurrency(unittest.TestCase):
    def test_failed_save_finishes_rollback_before_next_save_takes_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = Settings(mode=MODE_LAYOUT, autostart=True, admin=False)
            first = Settings(mode=MODE_IME, autostart=False, admin=True)
            second = Settings(mode=MODE_LAYOUT, autostart=True, admin=True)
            path.write_text(
                json.dumps(original.to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: True)

            rendezvous = threading.Barrier(2)
            first_replaced = threading.Event()
            second_finished = threading.Event()
            state_lock = threading.Lock()
            timeline = []

            class ConcurrentRunRegistry:
                def __init__(self):
                    self.exists = True
                    self.value = "custom-original-command"

                def snapshot(self):
                    label = threading.current_thread().name
                    with state_lock:
                        snapshot = self.exists, self.value
                        timeline.append((label, "snapshot"))
                    if label == "settings-B":
                        try:
                            rendezvous.wait(timeout=1.0)
                        except threading.BrokenBarrierError:
                            pass
                    return snapshot

                def apply(self, enabled):
                    with state_lock:
                        timeline.append((threading.current_thread().name, "apply"))
                        self.exists = bool(enabled)
                        self.value = "normalized-command" if enabled else None

                def restore(self, snapshot):
                    with state_lock:
                        timeline.append((threading.current_thread().name, "restore"))
                        self.exists, self.value = snapshot

            registry = ConcurrentRunRegistry()
            real_replace = os.replace
            first_primary_replace_seen = False

            def replace_then_coordinate(source, destination):
                nonlocal first_primary_replace_seen
                is_first_primary = False
                if threading.current_thread().name == "settings-A":
                    with state_lock:
                        if not first_primary_replace_seen:
                            first_primary_replace_seen = True
                            is_first_primary = True
                result = real_replace(source, destination)
                if is_first_primary:
                    first_replaced.set()
                    try:
                        rendezvous.wait(timeout=1.0)
                    except threading.BrokenBarrierError:
                        pass
                    else:
                        second_finished.wait(timeout=1.0)
                    raise OSError("private-first-replace-marker")
                return result

            results = {}
            errors = {}

            def save(label, value):
                try:
                    results[label] = settings.save_user_settings(
                        value,
                        path=path,
                        registry_adapter=registry,
                    )
                except BaseException as exc:
                    errors[label] = exc
                finally:
                    with state_lock:
                        timeline.append((threading.current_thread().name, "return"))
                    if label == "B":
                        second_finished.set()

            with patch.object(
                settings.os,
                "replace",
                side_effect=replace_then_coordinate,
            ):
                thread_a = threading.Thread(
                    target=save,
                    args=("A", first),
                    name="settings-A",
                )
                thread_b = threading.Thread(
                    target=save,
                    args=("B", second),
                    name="settings-B",
                )
                thread_a.start()
                self.assertTrue(first_replaced.wait(timeout=1.0))
                thread_b.start()
                thread_a.join(timeout=3.0)
                thread_b.join(timeout=3.0)

            self.assertFalse(thread_a.is_alive(), "first save did not finish")
            self.assertFalse(thread_b.is_alive(), "second save did not finish")
            self.assertEqual(errors, {})
            self.assertEqual(
                results,
                {
                    "A": settings.SaveResult.FAILURE,
                    "B": settings.SaveResult.SUCCESS,
                },
            )
            self.assertLess(
                timeline.index(("settings-A", "return")),
                timeline.index(("settings-B", "snapshot")),
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                second.to_dict(),
            )
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "normalized-command"),
            )
            self.assertEqual(get_settings(path), second)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_next_save_acquires_transaction_after_every_save_result(self):
        scenarios = (
            (
                "success",
                {},
                settings.SaveResult.SUCCESS,
            ),
            (
                "failure",
                {"apply_failure": "before"},
                settings.SaveResult.FAILURE,
            ),
            (
                "partial_failure",
                {
                    "apply_failure": "after",
                    "restore_failure": "before",
                },
                settings.SaveResult.PARTIAL_FAILURE,
            ),
        )
        for label, registry_options, expected_first_result in scenarios:
            with self.subTest(result=label), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                path.write_text(
                    json.dumps(Settings().to_dict()),
                    encoding="utf-8",
                )
                get_settings(path, autostart_detector=lambda: False)
                registry = FakeRunRegistry(
                    path=path,
                    value="custom-original-command",
                    **registry_options,
                )

                first_result = settings.save_user_settings(
                    Settings(mode=MODE_IME, autostart=True, admin=True),
                    path=path,
                    registry_adapter=registry,
                )
                self.assertIs(first_result, expected_first_result)

                registry.apply_failure = None
                registry.restore_failure = None
                second = Settings(
                    mode=MODE_LAYOUT,
                    autostart=False,
                    admin=True,
                )
                second_finished = threading.Event()
                outcomes = {}

                def save_second():
                    try:
                        outcomes["result"] = settings.save_user_settings(
                            second,
                            path=path,
                            registry_adapter=registry,
                        )
                    except BaseException as exc:
                        outcomes["error"] = exc
                    finally:
                        second_finished.set()

                thread = threading.Thread(target=save_second)
                thread.start()
                self.assertTrue(second_finished.wait(timeout=2.0))
                thread.join(timeout=2.0)

                self.assertFalse(thread.is_alive(), "next save did not finish")
                self.assertNotIn("error", outcomes)
                self.assertIs(outcomes.get("result"), settings.SaveResult.SUCCESS)
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8")),
                    second.to_dict(),
                )
                self.assertEqual((registry.exists, registry.value), (False, None))
                self.assertEqual(get_settings(path), second)

    def test_process_control_exception_propagates_and_releases_transaction(self):
        for process_error in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(
                error_type=type(process_error).__name__,
            ), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                path.write_text(
                    json.dumps(Settings().to_dict()),
                    encoding="utf-8",
                )
                get_settings(path, autostart_detector=lambda: False)

                class ProcessControlRegistry:
                    def snapshot(self):
                        raise process_error

                    def apply(self, _enabled):
                        raise AssertionError("apply followed a failed snapshot")

                    def restore(self, _snapshot):
                        raise AssertionError("restore followed a failed snapshot")

                with self.assertRaises(type(process_error)):
                    settings.save_user_settings(
                        Settings(autostart=True),
                        path=path,
                        registry_adapter=ProcessControlRegistry(),
                    )

                second = Settings(
                    mode=MODE_IME,
                    autostart=True,
                    admin=True,
                )
                registry = FakeRunRegistry(path=path)
                second_finished = threading.Event()
                outcomes = {}

                def save_second():
                    try:
                        outcomes["result"] = settings.save_user_settings(
                            second,
                            path=path,
                            registry_adapter=registry,
                        )
                    except BaseException as exc:
                        outcomes["error"] = exc
                    finally:
                        second_finished.set()

                thread = threading.Thread(target=save_second)
                thread.start()
                self.assertTrue(second_finished.wait(timeout=2.0))
                thread.join(timeout=2.0)

                self.assertFalse(thread.is_alive(), "next save did not finish")
                self.assertNotIn("error", outcomes)
                self.assertIs(outcomes.get("result"), settings.SaveResult.SUCCESS)
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8")),
                    second.to_dict(),
                )
                self.assertEqual(
                    (registry.exists, registry.value),
                    (True, "normalized-command"),
                )
                self.assertEqual(get_settings(path), second)

    def test_unhandled_ordinary_exception_releases_transaction(self):
        class InvalidPath:
            def __fspath__(self):
                raise RuntimeError("private-path-marker")

        with self.assertRaises(RuntimeError):
            settings.save_user_settings(
                Settings(autostart=True),
                path=InvalidPath(),
                registry_adapter=object(),
            )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(Settings().to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: False)
            expected = Settings(mode=MODE_IME, autostart=True, admin=True)
            registry = FakeRunRegistry(path=path)
            finished = threading.Event()
            outcomes = {}

            def save_after_exception():
                try:
                    outcomes["result"] = settings.save_user_settings(
                        expected,
                        path=path,
                        registry_adapter=registry,
                    )
                except BaseException as exc:
                    outcomes["error"] = exc
                finally:
                    finished.set()

            thread = threading.Thread(target=save_after_exception)
            thread.start()
            self.assertTrue(finished.wait(timeout=2.0))
            thread.join(timeout=2.0)

            self.assertFalse(thread.is_alive(), "next save did not finish")
            self.assertNotIn("error", outcomes)
            self.assertIs(outcomes.get("result"), settings.SaveResult.SUCCESS)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                expected.to_dict(),
            )
            self.assertEqual(get_settings(path), expected)

    def test_legacy_and_transactional_saves_share_one_serial_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(Settings().to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: False)
            legacy_value = Settings(
                mode=MODE_IME,
                autostart=False,
                admin=False,
            )
            user_value = Settings(
                mode=MODE_LAYOUT,
                autostart=True,
                admin=True,
            )

            rendezvous = threading.Barrier(2)
            legacy_replaced = threading.Event()
            user_finished = threading.Event()
            timeline_lock = threading.Lock()
            timeline = []

            class CoordinatedRegistry(FakeRunRegistry):
                def snapshot(self):
                    with timeline_lock:
                        timeline.append("user-snapshot")
                    try:
                        rendezvous.wait(timeout=1.0)
                    except threading.BrokenBarrierError:
                        pass
                    return super().snapshot()

            registry = CoordinatedRegistry(path=path)
            real_replace = os.replace
            legacy_primary_seen = False

            def coordinate_legacy_replace(source, destination):
                nonlocal legacy_primary_seen
                is_legacy_primary = False
                if threading.current_thread().name == "legacy-save":
                    with timeline_lock:
                        if not legacy_primary_seen:
                            legacy_primary_seen = True
                            is_legacy_primary = True
                result = real_replace(source, destination)
                if is_legacy_primary:
                    legacy_replaced.set()
                    try:
                        rendezvous.wait(timeout=1.0)
                    except threading.BrokenBarrierError:
                        pass
                    else:
                        user_finished.wait(timeout=1.0)
                return result

            results = {}
            errors = {}

            def save_legacy():
                try:
                    results["legacy"] = save_settings(legacy_value, path)
                except BaseException as exc:
                    errors["legacy"] = exc
                finally:
                    with timeline_lock:
                        timeline.append("legacy-return")

            def save_user():
                try:
                    results["user"] = settings.save_user_settings(
                        user_value,
                        path=path,
                        registry_adapter=registry,
                    )
                except BaseException as exc:
                    errors["user"] = exc
                finally:
                    user_finished.set()

            with patch.object(
                settings.os,
                "replace",
                side_effect=coordinate_legacy_replace,
            ):
                legacy_thread = threading.Thread(
                    target=save_legacy,
                    name="legacy-save",
                )
                user_thread = threading.Thread(
                    target=save_user,
                    name="user-save",
                )
                legacy_thread.start()
                self.assertTrue(legacy_replaced.wait(timeout=1.0))
                user_thread.start()
                legacy_thread.join(timeout=3.0)
                user_thread.join(timeout=3.0)

            self.assertFalse(legacy_thread.is_alive(), "legacy save did not finish")
            self.assertFalse(user_thread.is_alive(), "user save did not finish")
            self.assertEqual(errors, {})
            self.assertIsNone(results.get("legacy"))
            self.assertIs(results.get("user"), settings.SaveResult.SUCCESS)
            self.assertLess(
                timeline.index("legacy-return"),
                timeline.index("user-snapshot"),
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                user_value.to_dict(),
            )
            self.assertEqual(
                (registry.exists, registry.value),
                (True, "normalized-command"),
            )
            self.assertEqual(get_settings(path), user_value)


class TestSettingsWindowLifecycle(unittest.TestCase):
    def test_successful_save_closes_without_success_dialog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(Settings().to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: False)
            registry = FakeRunRegistry(path=path)
            root = FakeRoot()
            errors = []

            result = settings.save_settings_and_close(
                Settings(mode=MODE_IME, autostart=True, admin=True),
                root=root,
                show_error_fn=lambda *args, **kwargs: errors.append(
                    (args, kwargs)
                ),
                path=path,
                registry_adapter=registry,
            )

        self.assertEqual(result, settings.SaveResult.SUCCESS)
        self.assertEqual(root.destroy_calls, 1)
        self.assertEqual(errors, [])

    def test_failed_save_shows_error_and_keeps_window_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(Settings().to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: False)
            registry = FakeRunRegistry(path=path)
            root = FakeRoot()
            errors = []

            with patch.object(
                settings.os,
                "fsync",
                side_effect=OSError("private-stage-marker"),
            ):
                result = settings.save_settings_and_close(
                    Settings(autostart=True),
                    root=root,
                    show_error_fn=lambda *args, **kwargs: errors.append(
                        (args, kwargs)
                    ),
                    path=path,
                    registry_adapter=registry,
                )

            self.assertEqual(result, settings.SaveResult.FAILURE)
            self.assertEqual(root.destroy_calls, 0)
            self.assertEqual(
                errors,
                [(
                    ("保存失败", "设置保存失败，请重试。"),
                    {"parent": root},
                )],
            )
            root.destroy()
            self.assertEqual(root.destroy_calls, 1)

    def test_partial_failure_keeps_window_open_and_names_autostart_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(Settings().to_dict()),
                encoding="utf-8",
            )
            get_settings(path, autostart_detector=lambda: False)
            registry = FakeRunRegistry(
                path=path,
                apply_failure="after",
                restore_failure="before",
            )
            root = FakeRoot()
            errors = []

            result = settings.save_settings_and_close(
                Settings(autostart=True),
                root=root,
                show_error_fn=lambda *args, **kwargs: errors.append(
                    (args, kwargs)
                ),
                path=path,
                registry_adapter=registry,
            )

            self.assertEqual(result, settings.SaveResult.PARTIAL_FAILURE)
            self.assertEqual(root.destroy_calls, 0)
            self.assertEqual(
                errors,
                [(
                    (
                        "保存失败",
                        "设置未完全保存，请重新检查开机自启。",
                    ),
                    {"parent": root},
                )],
            )
            root.destroy()
            self.assertEqual(root.destroy_calls, 1)

    def test_settings_window_is_not_started_twice_while_alive(self):
        created = []

        def factory(**kwargs):
            thread = FakeThread(**kwargs)
            created.append(thread)
            return thread

        with patch("ime_switcher.settings._settings_thread", None):
            self.assertTrue(_open_settings_window(thread_factory=factory))
            self.assertFalse(_open_settings_window(thread_factory=factory))
            created[0].alive = False
            self.assertTrue(_open_settings_window(thread_factory=factory))

        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].started)


class TestKnownFolderBoundary(unittest.TestCase):
    def test_known_folder_abi_matches_windows_declarations(self):
        self.assertEqual(ctypes.sizeof(winapi.GUID), 16)
        self.assertEqual(
            winapi.shell32.SHGetKnownFolderPath.argtypes,
            [
                ctypes.POINTER(winapi.GUID),
                wintypes.DWORD,
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.LPWSTR),
            ],
        )
        self.assertIs(
            winapi.shell32.SHGetKnownFolderPath.restype,
            ctypes.HRESULT,
        )
        self.assertEqual(
            winapi.ole32.CoTaskMemFree.argtypes,
            [ctypes.c_void_p],
        )
        self.assertIsNone(winapi.ole32.CoTaskMemFree.restype)
        self.assertEqual(
            (
                winapi.FOLDERID_PROGRAM_FILES.Data1,
                winapi.FOLDERID_PROGRAM_FILES.Data2,
                winapi.FOLDERID_PROGRAM_FILES.Data3,
                bytes(winapi.FOLDERID_PROGRAM_FILES.Data4),
            ),
            (
                0x905E63B6,
                0xC1BF,
                0x494E,
                bytes.fromhex("b2 9c 65 b7 32 d3 d2 1a"),
            ),
        )
        self.assertEqual(
            (
                winapi.FOLDERID_PROGRAM_FILES_X86.Data1,
                winapi.FOLDERID_PROGRAM_FILES_X86.Data2,
                winapi.FOLDERID_PROGRAM_FILES_X86.Data3,
                bytes(winapi.FOLDERID_PROGRAM_FILES_X86.Data4),
            ),
            (
                0x7C5A40EF,
                0xA0FB,
                0x4BFC,
                bytes.fromhex("87 4a c0 f2 e0 b9 fa 8e"),
            ),
        )

    def test_successful_known_folder_query_releases_buffer_once(self):
        buffer = ctypes.create_unicode_buffer(r"C:\Program Files")
        freed = []

        def query(_folder_id, flags, token, output):
            self.assertEqual(flags, 0)
            self.assertIsNone(token)
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = (
                ctypes.addressof(buffer)
            )
            return 0

        value = winapi.get_known_folder_path(
            winapi.FOLDERID_PROGRAM_FILES,
            query=query,
            free=lambda pointer: freed.append(pointer.value),
        )

        self.assertEqual(value, r"C:\Program Files")
        self.assertEqual(freed, [ctypes.addressof(buffer)])

    def test_failed_known_folder_queries_fail_closed_and_free_once(self):
        for case in ("hresult", "exception", "empty", "allocated-empty"):
            with self.subTest(case=case):
                buffer = ctypes.create_unicode_buffer(
                    "" if case == "allocated-empty" else r"C:\private-marker"
                )
                freed = []

                def query(_folder_id, _flags, _token, output):
                    if case != "empty":
                        ctypes.cast(
                            output,
                            ctypes.POINTER(ctypes.c_void_p),
                        )[0] = ctypes.addressof(buffer)
                    if case == "exception":
                        raise OSError("private-marker")
                    return 1 if case == "hresult" else 0

                with self.assertLogs(winapi.log, level="WARNING") as captured:
                    value = winapi.get_known_folder_path(
                        winapi.FOLDERID_PROGRAM_FILES,
                        query=query,
                        free=lambda pointer: freed.append(pointer.value),
                    )

                self.assertIsNone(value)
                expected_frees = (
                    [ctypes.addressof(buffer)] if case != "empty" else []
                )
                self.assertEqual(freed, expected_frees)
                flattened = "\n".join(captured.output)
                self.assertNotIn("private-marker", flattened)
                self.assertNotIn(r"C:\private-marker", flattened)
                if case == "exception":
                    self.assertIn("OSError", flattened)


class TestAdministratorRelaunch(unittest.TestCase):
    def test_forged_program_files_environment_cannot_request_runas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attacker_root = root / "attacker-controlled"
            attacker_executable = attacker_root / "MacStyleIME.exe"
            attacker_root.mkdir()
            attacker_executable.write_bytes(b"test")
            real_program_files = root / "real-program-files"
            real_program_files_x86 = root / "real-program-files-x86"
            real_program_files.mkdir()
            real_program_files_x86.mkdir()

            with patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(attacker_root),
                    "ProgramFiles(x86)": str(attacker_root),
                },
                clear=False,
            ), patch.object(
                settings.sys, "frozen", True, create=True,
            ), patch.object(
                settings.sys, "executable", str(attacker_executable),
            ), patch.object(
                settings, "get_settings", return_value=Settings(admin=True),
            ), patch.object(
                settings, "is_admin", return_value=False,
            ), patch(
                "ime_switcher.winapi.get_known_folder_path",
                side_effect=[
                    str(real_program_files),
                    str(real_program_files_x86),
                ],
            ), patch.object(
                settings.ctypes.windll.shell32,
                "ShellExecuteW",
                return_value=33,
            ) as shell_execute:
                should_continue = settings.maybe_relaunch_as_admin()

            self.assertFalse(should_continue)
            shell_execute.assert_not_called()

    def test_official_program_files_roots_can_request_runas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_files = root / "Program Files"
            program_files_x86 = root / "Program Files (x86)"
            program_files.mkdir()
            program_files_x86.mkdir()

            for protected_root in (program_files, program_files_x86):
                with self.subTest(protected_root=protected_root.name):
                    executable = (
                        protected_root / "MacStyleIME" / "MacStyleIME.exe"
                    )
                    executable.parent.mkdir(parents=True)
                    executable.write_bytes(b"test")

                    with patch.object(
                        settings.sys, "frozen", True, create=True,
                    ), patch.object(
                        settings.sys, "executable", str(executable),
                    ), patch.object(
                        settings,
                        "get_settings",
                        return_value=Settings(admin=True),
                    ), patch.object(
                        settings, "is_admin", return_value=False,
                    ), patch(
                        "ime_switcher.winapi.get_known_folder_path",
                        side_effect=[
                            str(program_files).swapcase(),
                            str(program_files_x86).swapcase(),
                        ],
                    ), patch.object(
                        settings.ctypes.windll.shell32,
                        "ShellExecuteW",
                        return_value=33,
                    ) as shell_execute:
                        should_continue = settings.maybe_relaunch_as_admin()

                    self.assertFalse(should_continue)
                    shell_execute.assert_called_once()
                    self.assertEqual(
                        Path(shell_execute.call_args.args[2]),
                        executable.resolve(),
                    )

    def test_sibling_prefix_and_portable_paths_cannot_request_runas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_files = root / "Program Files"
            program_files_x86 = root / "Program Files (x86)"
            program_files.mkdir()
            program_files_x86.mkdir()
            sibling_executable = (
                root / "Program Files-Evil" / "MacStyleIME.exe"
            )
            portable_executable = root / "portable" / "MacStyleIME.exe"
            for executable in (sibling_executable, portable_executable):
                executable.parent.mkdir()
                executable.write_bytes(b"test")

                with self.subTest(parent=executable.parent.name), patch.object(
                    settings.sys, "frozen", True, create=True,
                ), patch.object(
                    settings.sys, "executable", str(executable),
                ), patch.object(
                    settings,
                    "get_settings",
                    return_value=Settings(admin=True),
                ), patch.object(
                    settings, "is_admin", return_value=False,
                ), patch(
                    "ime_switcher.winapi.get_known_folder_path",
                    side_effect=[str(program_files), str(program_files_x86)],
                ), patch.object(
                    settings.ctypes.windll.shell32,
                    "ShellExecuteW",
                    return_value=33,
                ) as shell_execute:
                    should_continue = settings.maybe_relaunch_as_admin()

                self.assertFalse(should_continue)
                shell_execute.assert_not_called()

    def test_one_known_folder_failure_does_not_hide_the_other_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_files_x86 = root / "Program Files (x86)"
            executable = (
                program_files_x86 / "MacStyleIME" / "MacStyleIME.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test")

            with patch.object(
                settings.sys, "frozen", True, create=True,
            ), patch.object(
                settings.sys, "executable", str(executable),
            ), patch.object(
                settings, "get_settings", return_value=Settings(admin=True),
            ), patch.object(
                settings, "is_admin", return_value=False,
            ), patch(
                "ime_switcher.winapi.get_known_folder_path",
                side_effect=[None, str(program_files_x86)],
            ), patch.object(
                settings.ctypes.windll.shell32,
                "ShellExecuteW",
                return_value=33,
            ) as shell_execute:
                should_continue = settings.maybe_relaunch_as_admin()

            self.assertFalse(should_continue)
            shell_execute.assert_called_once()

    def test_all_known_folder_failures_cannot_request_runas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "forged-protected-root" / "MacStyleIME.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"test")
            query_calls = 0

            def failing_query(_folder_id, _flags, _token, _output):
                nonlocal query_calls
                query_calls += 1
                if query_calls == 1:
                    return 1
                raise OSError("private-marker")

            with patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(executable.parent),
                    "ProgramFiles(x86)": str(executable.parent),
                },
                clear=False,
            ), patch.object(
                settings.sys, "frozen", True, create=True,
            ), patch.object(
                settings.sys, "executable", str(executable),
            ), patch.object(
                settings, "get_settings", return_value=Settings(admin=True),
            ), patch.object(
                settings, "is_admin", return_value=False,
            ), patch.object(
                winapi.shell32,
                "SHGetKnownFolderPath",
                side_effect=failing_query,
            ), patch.object(
                winapi.ole32, "CoTaskMemFree",
            ) as free, patch.object(
                settings.ctypes.windll.shell32,
                "ShellExecuteW",
                return_value=33,
            ) as shell_execute, self.assertLogs(level="WARNING") as captured:
                should_continue = settings.maybe_relaunch_as_admin()

            self.assertFalse(should_continue)
            self.assertEqual(query_calls, 2)
            free.assert_not_called()
            shell_execute.assert_not_called()
            flattened = "\n".join(captured.output)
            self.assertNotIn("private-marker", flattened)
            self.assertNotIn(str(executable), flattened)

    def test_admin_controls_do_not_query_or_request_runas(self):
        cases = (
            ("admin-disabled", False, False, True, True),
            ("already-admin", True, True, True, True),
            ("not-frozen", True, False, False, False),
        )
        for name, admin, elevated, frozen, expected in cases:
            with self.subTest(name=name), patch.object(
                settings.sys, "frozen", frozen, create=True,
            ), patch.object(
                settings,
                "get_settings",
                return_value=Settings(admin=admin),
            ), patch.object(
                settings, "is_admin", return_value=elevated,
            ), patch(
                "ime_switcher.winapi.get_known_folder_path",
            ) as get_known_folder, patch.object(
                settings.ctypes.windll.shell32,
                "ShellExecuteW",
                return_value=33,
            ) as shell_execute:
                should_continue = settings.maybe_relaunch_as_admin()

            self.assertIs(should_continue, expected)
            get_known_folder.assert_not_called()
            shell_execute.assert_not_called()

    def test_uac_cancel_preserves_argument_forwarding_and_stops_current_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_files = root / "Program Files"
            executable = program_files / "MacStyleIME" / "MacStyleIME.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test")
            arguments = ["--mode", "value with space"]

            with patch.object(
                settings.sys, "frozen", True, create=True,
            ), patch.object(
                settings.sys, "executable", str(executable),
            ), patch.object(
                settings.sys, "argv", [str(executable), *arguments],
            ), patch.object(
                settings, "get_settings", return_value=Settings(admin=True),
            ), patch.object(
                settings, "is_admin", return_value=False,
            ), patch(
                "ime_switcher.winapi.get_known_folder_path",
                side_effect=[str(program_files), None],
            ), patch.object(
                settings.ctypes.windll.shell32,
                "ShellExecuteW",
                return_value=5,
            ) as shell_execute:
                should_continue = settings.maybe_relaunch_as_admin()

            self.assertFalse(should_continue)
            shell_execute.assert_called_once_with(
                None,
                "runas",
                str(executable.resolve()),
                '--mode "value with space"',
                str(settings.config.APP_DIR),
                1,
            )
