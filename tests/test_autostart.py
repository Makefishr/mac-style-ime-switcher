import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from ime_switcher import config
from ime_switcher.autostart import (
    WindowsStartup,
    STARTUP_APPROVED_RUN_KEY,
    launch_command,
    run_command,
    startup_method,
    task_xml,
)
from tests.fakes import Registry, Scheduler


class StartupTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(config, "USER_DATA_DIR", Path(folder)))
        self.registry = Registry()
        self.scheduler = Scheduler()
        self.backend = WindowsStartup(self.registry, self.scheduler, lambda: True)
        self.task_name = "MacStyleIME-Autostart-S-1-5-21-123-456-789-1001"

    def test_startup_choice_for_all_combinations(self):
        for enabled, administrator, expected in (
            (False, False, "disabled"), (False, True, "disabled"),
            (True, False, "run"), (True, True, "task"),
        ):
            with self.subTest(enabled=enabled, administrator=administrator):
                self.assertEqual(startup_method(enabled, administrator), expected)

    def test_standard_mode_writes_run_and_removes_owned_task(self):
        self.scheduler.tasks.update({self.task_name, "OtherProgram"})
        self.backend.apply(True, False)
        self.assertEqual(self.registry.entries[config.REG_RUN_KEY][config.APP_NAME], (run_command(), 1))
        self.assertEqual(self.scheduler.tasks, {"OtherProgram"})
        self.assertTrue(self.backend.is_enabled())

    def test_admin_mode_creates_on_demand_task_and_writes_run(self):
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: ("old", 1)}
        self.backend.apply(True, True)
        self.assertEqual(self.scheduler.tasks, {self.task_name})
        self.assertEqual(self.registry.entries[config.REG_RUN_KEY][config.APP_NAME], (run_command(), 1))
        self.assertTrue(run_command().endswith(" --autostart"))
        root = ET.fromstring(self.scheduler.xml)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        for name, expected in (
            ("LogonType", "InteractiveToken"), ("RunLevel", "HighestAvailable"),
            ("DisallowStartIfOnBatteries", "false"), ("StopIfGoingOnBatteries", "false"),
            ("ExecutionTimeLimit", "PT0S"), ("MultipleInstancesPolicy", "IgnoreNew"),
        ):
            self.assertEqual(root.find(f".//t:{name}", ns).text, expected)
        self.assertIsNone(root.find(".//t:LogonTrigger", ns))
        self.assertEqual(len(root.find("t:Triggers", ns)), 0)
        self.assertEqual(root.find(".//t:Principal/t:UserId", ns).text, self.scheduler.username)
        self.assertEqual(root.find(".//t:Command", ns).text, launch_command()[0])
        self.assertTrue(root.find(".//t:Arguments", ns).text.endswith("--autostart-task"))
        create = next(call for call in self.scheduler.calls if "/Create" in call)
        self.assertNotIn("/RP", create)
        self.assertNotIn("/RU", create)

    def test_disable_removes_both_but_not_other_applications(self):
        self.scheduler.tasks.update({self.task_name, "OtherProgram"})
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: ("old", 1), "Other": ("keep", 1)}
        self.registry.entries[STARTUP_APPROVED_RUN_KEY] = {
            config.APP_NAME: (b"\x03" + bytes(11), 3), "Other": (b"\x03" + bytes(11), 3),
        }
        self.backend.apply(False, True)
        self.assertEqual(self.scheduler.tasks, {"OtherProgram"})
        self.assertEqual(self.registry.entries[config.REG_RUN_KEY], {"Other": ("keep", 1)})
        self.assertEqual(self.registry.entries[STARTUP_APPROVED_RUN_KEY], {"Other": (b"\x03" + bytes(11), 3)})

    def test_failed_task_creation_leaves_previous_run_entry(self):
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: ("old", 1)}
        self.scheduler.fail = "create"
        with self.assertRaises(OSError):
            self.backend.apply(True, True)
        self.assertEqual(self.registry.entries[config.REG_RUN_KEY][config.APP_NAME], ("old", 1))

    def test_failed_task_deletion_preserves_working_run_entry(self):
        self.scheduler.tasks.add(self.task_name)
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: ("old", 1)}
        self.scheduler.fail = "delete"
        with self.assertRaises(OSError):
            self.backend.apply(False, False)
        self.assertIn(config.APP_NAME, self.registry.entries[config.REG_RUN_KEY])

    def test_nonadmin_defers_creation_and_task_cleanup_until_elevated(self):
        backend = WindowsStartup(self.registry, self.scheduler, lambda: False)
        self.assertTrue(backend.needs_elevation(True, True))
        with self.assertRaises(PermissionError):
            backend.apply(True, True)
        self.scheduler.tasks.add(self.task_name)
        for enabled, administrator in ((True, False), (False, False)):
            self.assertTrue(backend.needs_elevation(enabled, administrator))
            with self.assertRaises(PermissionError):
                backend.apply(enabled, administrator)
        self.assertFalse(any("/Create" in call or "/Delete" in call for call in self.scheduler.calls))
        self.assertEqual(self.registry.writes, [])

    def test_standard_run_registration_needs_no_elevation(self):
        backend = WindowsStartup(self.registry, self.scheduler, lambda: False)
        self.assertFalse(backend.needs_elevation(True, False))
        backend.apply(True, False)
        self.assertTrue(backend.is_enabled())

    def test_matching_task_is_reused_without_requiring_elevation(self):
        self.backend.apply(True, True)
        backend = WindowsStartup(self.registry, self.scheduler, lambda: False)
        self.assertTrue(backend.is_configured(True, True))
        backend.apply(True, True)
        self.assertEqual(sum("/Create" in call for call in self.scheduler.calls), 1)

    def test_changed_or_disabled_task_is_repaired(self):
        self.backend.apply(True, True)
        original = self.scheduler.xml
        for modified in (original.replace("HighestAvailable", "LeastPrivilege"),
                         original.replace("<Settings>", "<Settings><Enabled>false</Enabled>"),
                         original.replace("</Actions>", "<Exec><Command>other.exe</Command></Exec></Actions>")):
            self.scheduler.xml = modified
            self.assertFalse(self.backend.is_configured(True, True))
            self.backend.apply(True, True)
            self.assertEqual(self.scheduler.xml, original)

    def test_scheduler_added_defaults_do_not_force_task_recreation(self):
        self.backend.apply(True, True)
        self.scheduler.xml = self.scheduler.xml.replace("<Settings>", "<Settings><Priority>7</Priority>")
        self.assertTrue(self.backend.is_configured(True, True))
        self.backend.apply(True, True)
        self.assertEqual(sum("/Create" in call for call in self.scheduler.calls), 1)

    def test_scheduler_omitted_empty_triggers_does_not_force_task_recreation(self):
        self.backend.apply(True, True)
        self.scheduler.xml = self.scheduler.xml.replace("<Triggers />", "")
        self.assertTrue(self.backend.is_configured(True, True))
        self.backend.apply(True, True)
        self.assertEqual(sum("/Create" in call for call in self.scheduler.calls), 1)

    def test_scheduler_normalized_user_sid_matches_original_account(self):
        self.backend.apply(True, True)
        root = ET.fromstring(self.scheduler.xml)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        root.find(".//t:Principal/t:UserId", ns).text = self.scheduler.sid
        self.scheduler.xml = ET.tostring(root, encoding="unicode")
        self.assertTrue(self.backend.is_configured(True, True))

    def test_disable_removes_run_entry_even_when_command_has_changed(self):
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: ("old.exe", 1)}
        self.backend.apply(False, False)
        self.assertNotIn(config.APP_NAME, self.registry.entries[config.REG_RUN_KEY])

    def test_scheduler_failure_is_not_treated_as_task_absent(self):
        def failed(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 1, "", "service unavailable")
        backend = WindowsStartup(self.registry, failed, lambda: True)
        with self.assertRaises(OSError):
            backend.task_exists()

    def test_timeout_is_an_actionable_startup_error(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 20)
        backend = WindowsStartup(self.registry, timeout, lambda: True)
        with self.assertRaises(OSError):
            backend.task_exists()

    def test_legacy_quoted_run_command_is_recognized(self):
        executable, arguments = launch_command()
        legacy = " ".join(f'"{item}"' for item in [executable, *arguments])
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: (legacy, 1)}
        self.assertTrue(self.backend.run_exists())
        self.assertFalse(self.backend.is_configured(True, False))
        self.backend.apply(True, False)
        self.assertEqual(self.registry.entries[config.REG_RUN_KEY][config.APP_NAME], (run_command(), 1))

    def test_legacy_run_without_autostart_flag_remains_enabled_but_needs_migration(self):
        executable, arguments = launch_command()
        self.registry.entries[config.REG_RUN_KEY] = {
            config.APP_NAME: (subprocess.list2cmdline([executable, *arguments]), 1),
        }
        self.assertTrue(self.backend.is_enabled())
        self.assertFalse(self.backend.is_configured(True, False))
        self.backend.apply(True, False)
        self.assertTrue(self.registry.entries[config.REG_RUN_KEY][config.APP_NAME][0].endswith(" --autostart"))

    def test_windows_disabled_startup_is_recognized_and_explicitly_reenabled(self):
        for administrator in (False, True):
            for status in (3, 7):
                with self.subTest(administrator=administrator, status=status):
                    self.backend.apply(True, administrator)
                    self.registry.entries[STARTUP_APPROVED_RUN_KEY] = {
                        config.APP_NAME: (bytes([status]) + bytes(11), self.registry.REG_BINARY),
                        "Other": (b"\x03" + bytes(11), self.registry.REG_BINARY),
                    }
                    self.assertTrue(self.backend.is_disabled())
                    self.assertFalse(self.backend.is_enabled())
                    self.assertTrue(self.backend.is_configured(True, administrator))
                    self.backend.apply(True, administrator)
                    self.assertTrue(self.backend.is_enabled())
                    self.assertNotIn(config.APP_NAME, self.registry.entries[STARTUP_APPROVED_RUN_KEY])
                    self.assertIn("Other", self.registry.entries[STARTUP_APPROVED_RUN_KEY])

    def test_disable_removes_leftover_approval_even_without_run_or_task(self):
        self.registry.entries[STARTUP_APPROVED_RUN_KEY] = {
            config.APP_NAME: (b"\x02" + bytes(11), self.registry.REG_BINARY),
        }
        self.backend.apply(False, False)
        self.assertNotIn(config.APP_NAME, self.registry.entries[STARTUP_APPROVED_RUN_KEY])

    def test_windows_enabled_or_invalid_approval_does_not_disable_startup(self):
        self.backend.apply(True, False)
        for value, kind in ((b"\x02" + bytes(11), 3), (b"\x06" + bytes(11), 3),
                            (b"", 3), ("invalid", 1)):
            with self.subTest(value=value):
                self.registry.entries[STARTUP_APPROVED_RUN_KEY] = {config.APP_NAME: (value, kind)}
                self.assertFalse(self.backend.is_disabled())
                self.assertTrue(self.backend.is_enabled())

    def test_old_logon_trigger_is_replaced_while_preserving_windows_disable(self):
        self.backend.apply(True, True)
        original = self.scheduler.xml
        self.scheduler.xml = original.replace("<Triggers />", "<Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>")
        approval = (b"\x03" + bytes(11), self.registry.REG_BINARY)
        self.registry.entries[STARTUP_APPROVED_RUN_KEY] = {config.APP_NAME: approval}
        self.assertFalse(self.backend.is_configured(True, True))
        self.backend.apply(True, True, preserve_approval=True)
        self.assertEqual(self.scheduler.xml, original)
        self.assertTrue(self.backend.is_configured(True, True))
        self.assertFalse(self.backend.is_enabled())
        self.assertEqual(self.registry.entries[STARTUP_APPROVED_RUN_KEY][config.APP_NAME], approval)
        self.backend.apply(True, True, preserve_approval=True)
        self.assertEqual(sum("/Create" in call for call in self.scheduler.calls), 2)
        self.assertEqual(self.registry.entries[STARTUP_APPROVED_RUN_KEY][config.APP_NAME], approval)

    def test_source_task_keeps_executable_and_quoted_script_arguments_separate(self):
        root = ET.fromstring(task_xml(r"PC\User",
                                     r"D:\Python Tools\pythonw.exe",
                                     [r"D:\My Projects\IME & Tools\__main__.py"]))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:Command", ns).text, r"D:\Python Tools\pythonw.exe")
        self.assertEqual(root.find(".//t:Arguments", ns).text,
                         '"D:\\My Projects\\IME & Tools\\__main__.py" --autostart-task')


class StartupLoggingTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(config, "USER_DATA_DIR", Path(folder)))
        self.registry = Registry()
        self.scheduler = Scheduler()
        self.backend = WindowsStartup(self.registry, self.scheduler, lambda: True)
        self.task_name = "MacStyleIME-Autostart-S-1-5-21-123-456-789-1001"

    def test_task_creation_and_run_write_are_logged(self):
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: ("old", 1)}
        with self.assertLogs("ime_switcher.autostart", level="INFO") as captured:
            self.backend.apply(True, True)
        output = "\n".join(captured.output)
        self.assertIn(f"Creating on-demand task {self.task_name}", output)
        self.assertIn(run_command(), output)
        self.assertIn(f"Writing Run entry {config.APP_NAME}", output)

    def test_task_deletion_and_run_removal_are_logged(self):
        self.scheduler.tasks.add(self.task_name)
        self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: ("old", 1)}
        with self.assertLogs("ime_switcher.autostart", level="INFO") as captured:
            self.backend.apply(False, False)
        output = "\n".join(captured.output)
        self.assertIn(f"Deleting startup task {self.task_name}", output)
        self.assertIn(f"Removed Run entry {config.APP_NAME}", output)

    def test_run_write_is_logged(self):
        with self.assertLogs("ime_switcher.autostart", level="INFO") as captured:
            self.backend.apply(True, False)
        output = "\n".join(captured.output)
        self.assertIn(f"Writing Run entry {config.APP_NAME}", output)
        self.assertIn(run_command(), output)

    def test_unchanged_registration_only_logs_at_debug(self):
        self.backend.apply(True, False)
        with self.assertLogs("ime_switcher.autostart", level="DEBUG") as captured:
            self.backend.apply(True, False)
        output = "\n".join(captured.output)
        self.assertIn("already matches", output)
        self.assertNotIn("Writing Run entry", output)


class SourceExecutableTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(config, "USER_DATA_DIR", Path(folder)))
        self.registry = Registry()
        self.scheduler = Scheduler()
        self.backend = WindowsStartup(self.registry, self.scheduler, lambda: True)
        self.task_name = "MacStyleIME-Autostart-S-1-5-21-123-456-789-1001"
        self.script = str(Path(config.__file__).with_name("__main__.py").resolve())

    def test_source_launch_prefers_pythonw_next_to_interpreter(self):
        with (
            patch.object(config.sys, "executable", r"C:\Python312\python.exe"),
            patch.object(Path, "exists", return_value=True),
        ):
            self.assertEqual(config.source_executable(), r"C:\Python312\pythonw.exe")
            executable, arguments = launch_command()
        self.assertEqual(executable, r"C:\Python312\pythonw.exe")
        self.assertEqual(arguments, [self.script])

    def test_source_launch_falls_back_without_pythonw(self):
        python = r"C:\Python312\python.exe"
        with (
            patch.object(config.sys, "executable", python),
            patch.object(Path, "exists", return_value=False),
        ):
            self.assertEqual(config.source_executable(), python)
            self.assertEqual(launch_command()[0], python)

    def test_frozen_launch_keeps_executable_and_arguments(self):
        executable = r"D:\Apps\MacStyleIME.exe"
        with (
            patch.object(config.sys, "frozen", True, create=True),
            patch.object(config.sys, "executable", executable),
        ):
            self.assertEqual(launch_command(), (executable, []))
            self.assertEqual(run_command(), r'D:\Apps\MacStyleIME.exe --autostart')

    def test_source_run_command_quotes_paths_and_passes_autostart_to_script(self):
        with patch("ime_switcher.autostart.launch_command", return_value=(
            r"D:\Python Tools\pythonw.exe", [r"D:\My Projects\IME\__main__.py"],
        )):
            self.assertEqual(run_command(),
                             r'"D:\Python Tools\pythonw.exe" "D:\My Projects\IME\__main__.py" --autostart')

    def test_console_run_entry_is_rebuilt_for_pythonw(self):
        python = r"C:\Python312\python.exe"
        pythonw = r"C:\Python312\pythonw.exe"
        with (
            patch.object(config.sys, "executable", python),
            patch.object(Path, "exists", return_value=True),
        ):
            old = subprocess.list2cmdline([python, self.script])
            self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: (old, 1)}
            self.assertFalse(self.backend.run_exists())
            self.backend.apply(True, False)
        expected = subprocess.list2cmdline([pythonw, self.script, "--autostart"])
        self.assertEqual(
            self.registry.entries[config.REG_RUN_KEY][config.APP_NAME], (expected, 1),
        )

    def test_console_task_is_rebuilt_for_pythonw(self):
        python = r"C:\Python312\python.exe"
        with (
            patch.object(config.sys, "executable", python),
            patch.object(Path, "exists", return_value=True),
        ):
            self.scheduler.tasks.add(self.task_name)
            self.scheduler.xml = task_xml(
                self.scheduler.username, python, [self.script],
            )
            self.registry.entries[config.REG_RUN_KEY] = {config.APP_NAME: (run_command(), 1)}
            self.assertFalse(self.backend.is_configured(True, True))
            self.backend.apply(True, True)
        root = ET.fromstring(self.scheduler.xml)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:Command", ns).text, r"C:\Python312\pythonw.exe")
