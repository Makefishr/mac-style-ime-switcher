import io
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ime_switcher import __main__ as app
from ime_switcher import elevation
from ime_switcher.settings_store import Preferences, SwitchingMethod
from ime_switcher.autostart import (
    WindowsStartup, STARTUP_APPROVED_RUN_KEY, launch_command, run_command, task_xml,
)
from tests.fakes import Registry, Scheduler


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.runtime = app._DesktopRuntime.__new__(app._DesktopRuntime)
        self.runtime._startup_notice = None
        self.runtime._root = Mock()
        self.runtime._notify = Mock()
        self.runtime._request_elevation = Mock()
        self.startup = self.enterContext(patch.object(app, "WindowsStartup")).return_value
        self.startup.needs_elevation.return_value = False
        self.startup.is_configured.return_value = False
        self.startup.is_disabled.return_value = False
        self.hotkeys = self.enterContext(patch.object(app, "LanguageHotkeys")).return_value
        self.save = self.enterContext(patch.object(app, "save_preferences"))
        self.load = self.enterContext(patch.object(app, "load_preferences", return_value=Preferences()))
        self.enterContext(patch.object(app.toggle, "set_switching_method"))
        self.ctrl = self.enterContext(patch.object(app.hook, "set_block_ctrl_space"))
        self.elevated = self.enterContext(patch.object(app.elevation, "is_elevated", return_value=False))
        self.warning = self.enterContext(patch.object(app.messagebox, "showwarning"))

    def test_admin_autostart_is_deferred_to_elevated_runtime(self):
        self.assertTrue(self.runtime._apply_settings(
            True, True, SwitchingMethod.KEYBOARD_LAYOUTS, False, False,
        ))
        self.startup.apply.assert_not_called()
        self.runtime._request_elevation.assert_called_once_with()
        saved = self.save.call_args.args[0]
        self.assertTrue(saved.autostart)
        self.assertTrue(saved.run_as_administrator)
        self.assertFalse(saved.block_ctrl_space)
        self.assertFalse(saved.disable_system_hotkeys)

    def test_removing_highest_task_requests_elevation_even_if_admin_mode_is_off(self):
        self.startup.needs_elevation.return_value = True
        self.runtime._apply_settings(False, False, SwitchingMethod.KEYBOARD_LAYOUTS, True, True)
        self.runtime._request_elevation.assert_called_once_with()
        self.startup.apply.assert_not_called()

    def test_elevated_runtime_applies_autostart_directly(self):
        self.elevated.return_value = True
        self.runtime._apply_settings(True, True, SwitchingMethod.KEYBOARD_LAYOUTS, True, False)
        self.startup.apply.assert_called_once_with(True, True, preserve_approval=True)
        self.ctrl.assert_called_once_with(False)
        self.runtime._request_elevation.assert_not_called()

    def test_settings_save_failure_does_not_change_system_state(self):
        self.save.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            self.runtime._apply_settings(False, True, SwitchingMethod.KEYBOARD_LAYOUTS, True, False)
        self.hotkeys.apply.assert_not_called()
        self.startup.apply.assert_not_called()

    def test_legacy_startup_state_is_migrated_before_application(self):
        self.startup.is_registered.return_value = True
        self.runtime._prepare_settings()
        self.assertTrue(self.save.call_args.args[0].autostart)
        self.startup.apply.assert_called_once_with(True, False, preserve_approval=True)

    def test_elevated_child_uses_saved_desired_startup(self):
        self.load.return_value = replace(Preferences(), autostart=True, run_as_administrator=True)
        self.runtime._prepare_settings()
        self.startup.is_enabled.assert_not_called()
        self.startup.apply.assert_called_once_with(True, True, preserve_approval=True)

    def test_startup_failure_is_shown_without_preventing_hook_start(self):
        self.load.return_value = replace(Preferences(), autostart=True)
        self.startup.apply.side_effect = OSError("registration failed")
        with self.assertLogs("ime_switcher.__main__", level="ERROR"):
            self.runtime._prepare_settings()
        self.assertIn("登录自启尚未确认应用", self.runtime._startup_notice)

    def test_shortcut_failure_does_not_skip_startup_or_ctrl_space(self):
        self.hotkeys.apply.side_effect = ValueError("backup missing")
        with self.assertLogs("ime_switcher.__main__", level="ERROR"):
            self.runtime._apply_settings(False, True, SwitchingMethod.KEYBOARD_LAYOUTS, False, False)
        self.ctrl.assert_called_once_with(False)
        self.startup.apply.assert_called_once_with(True, False, preserve_approval=True)
        self.assertIn("系统语言快捷键", self.warning.call_args.args[1])
        self.assertIn("backup missing", self.warning.call_args.args[1])

    def test_scheduler_failure_keeps_preferences_and_still_requests_admin_mode(self):
        self.startup.is_configured.side_effect = OSError("service unavailable")
        with self.assertLogs("ime_switcher.__main__", level="ERROR"):
            self.runtime._apply_settings(True, True, SwitchingMethod.KEYBOARD_LAYOUTS, True, False)
        self.assertTrue(self.save.call_args.args[0].autostart)
        self.runtime._request_elevation.assert_called_once_with()
        self.assertIn("登录自启尚未确认应用", self.warning.call_args.args[1])

    def test_existing_matching_task_is_not_recreated_or_reported_as_unapplied(self):
        self.load.return_value = replace(Preferences(), autostart=True, run_as_administrator=True)
        self.startup.is_configured.return_value = True
        self.runtime._prepare_settings()
        self.startup.apply.assert_not_called()
        self.startup.needs_elevation.assert_not_called()
        self.assertIsNone(self.runtime._startup_notice)

    def test_startup_repair_preserves_windows_disable_until_explicit_enable(self):
        self.load.return_value = replace(Preferences(), autostart=True, run_as_administrator=True)
        self.startup.is_disabled.return_value = True
        self.startup.is_configured.return_value = True
        self.runtime._prepare_settings()
        self.startup.apply.assert_not_called()
        self.startup.is_configured.assert_called_once_with(True, True)
        self.startup.reset_approval.assert_not_called()
        self.runtime._request_elevation.assert_not_called()
        self.startup.is_configured.return_value = False
        self.runtime._apply_settings(False, True, SwitchingMethod.KEYBOARD_LAYOUTS, True, False,
                                     autostart_changed=True)
        self.startup.apply.assert_called_once_with(True, False, preserve_approval=True)
        self.startup.reset_approval.assert_called_once_with()

    def test_legacy_migration_preserves_windows_disabled_registration(self):
        for administrator in (False, True):
            with self.subTest(administrator=administrator):
                registry = Registry({
                    app.config.REG_RUN_KEY: {app.config.APP_NAME: (run_command(), Registry.REG_SZ)},
                    STARTUP_APPROVED_RUN_KEY: {
                        app.config.APP_NAME: (b"\x03" + bytes(11), Registry.REG_BINARY),
                    },
                })
                backend = WindowsStartup(registry, Scheduler(), lambda: False)
                with patch.object(app, "WindowsStartup", return_value=backend):
                    self.load.return_value = replace(Preferences(), run_as_administrator=administrator)
                    self.runtime._prepare_settings()
                self.assertTrue(self.save.call_args.args[0].autostart)
                self.assertTrue(backend.run_exists())
                self.assertTrue(backend.is_disabled())
                self.runtime._request_elevation.assert_not_called()

    def test_disabled_startup_mode_changes_repair_registration_without_reenabling(self):
        registry = Registry()
        scheduler = Scheduler()
        backend = WindowsStartup(registry, scheduler, lambda: True)
        backend.apply(True, False)
        approval = (b"\x03" + bytes(11), Registry.REG_BINARY)
        registry.entries[STARTUP_APPROVED_RUN_KEY] = {app.config.APP_NAME: approval}
        self.elevated.return_value = True
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(app.config, "USER_DATA_DIR", Path(folder)),
            patch.object(app, "WindowsStartup", return_value=backend),
        ):
            for administrator in (True, False):
                with self.subTest(administrator=administrator):
                    self.load.return_value = replace(
                        Preferences(), autostart=True, run_as_administrator=not administrator,
                    )
                    self.runtime._apply_settings(
                        administrator, False, SwitchingMethod.KEYBOARD_LAYOUTS, True, True,
                        autostart_changed=False,
                    )
                    self.assertTrue(self.save.call_args.args[0].autostart)
                    self.assertTrue(backend.is_configured(True, administrator))
                    self.assertEqual(bool(scheduler.tasks), administrator)
                    self.assertFalse(backend.is_enabled())
                    self.assertEqual(registry.entries[STARTUP_APPROVED_RUN_KEY][app.config.APP_NAME], approval)
        self.runtime._request_elevation.assert_not_called()

    def test_disabled_old_logon_task_is_migrated_without_reenabling_startup(self):
        for desired in (True, None):
            with self.subTest(autostart=desired):
                approval = (b"\x03" + bytes(11), Registry.REG_BINARY)
                registry = Registry({STARTUP_APPROVED_RUN_KEY: {app.config.APP_NAME: approval}})
                scheduler = Scheduler()
                scheduler.tasks.add(f"{app.config.APP_NAME}-Autostart-{scheduler.sid}")
                executable, arguments = launch_command()
                scheduler.xml = task_xml(scheduler.username, executable, arguments).replace(
                    "<Triggers />",
                    "<Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>",
                ).replace(" --autostart-task", "")
                backend = WindowsStartup(registry, scheduler, lambda: True)
                with (
                    tempfile.TemporaryDirectory() as folder,
                    patch.object(app.config, "USER_DATA_DIR", Path(folder)),
                    patch.object(app, "WindowsStartup", return_value=backend),
                ):
                    self.load.return_value = replace(
                        Preferences(), autostart=desired, run_as_administrator=True,
                    )
                    self.runtime._prepare_settings()
                    self.assertNotIn("LogonTrigger", scheduler.xml)
                    self.assertIn("--autostart-task", scheduler.xml)
                    self.assertTrue(backend.run_exists(allow_legacy=False))
                    self.assertTrue(backend.is_configured(True, True))
                    self.assertFalse(backend.is_enabled())
                    self.assertEqual(registry.entries[STARTUP_APPROVED_RUN_KEY][app.config.APP_NAME], approval)
                    self.assertIsNone(self.runtime._startup_notice)
                    self.runtime._prepare_settings()
                    self.assertEqual(sum("/Create" in call for call in scheduler.calls), 1)
                self.runtime._request_elevation.assert_not_called()

    def test_saving_other_settings_preserves_windows_disable(self):
        self.load.return_value = replace(Preferences(), autostart=True)
        self.startup.is_disabled.return_value = True
        self.startup.is_configured.return_value = True
        self.runtime._apply_settings(False, True, SwitchingMethod.KEYBOARD_LAYOUTS, True, False)
        self.startup.reset_approval.assert_not_called()
        self.startup.apply.assert_not_called()

    def test_unchanged_disabled_ui_switch_preserves_desired_registration(self):
        self.load.return_value = replace(Preferences(), autostart=True)
        self.startup.is_disabled.return_value = True
        self.startup.is_configured.return_value = True
        self.runtime._apply_settings(False, False, SwitchingMethod.KEYBOARD_LAYOUTS, True, False,
                                     autostart_changed=False)
        self.assertTrue(self.save.call_args.args[0].autostart)
        self.startup.reset_approval.assert_not_called()
        self.startup.apply.assert_not_called()

    def test_explicit_enable_survives_handoff_to_elevated_runtime(self):
        registry = Registry({STARTUP_APPROVED_RUN_KEY: {
            app.config.APP_NAME: (b"\x03" + bytes(11), Registry.REG_BINARY),
        }})
        scheduler = Scheduler()
        backend = WindowsStartup(registry, scheduler, lambda: self.elevated.return_value)
        self.enterContext(patch.object(app, "WindowsStartup", return_value=backend))
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app.config, "USER_DATA_DIR", Path(folder)))
        self.runtime._apply_settings(True, True, SwitchingMethod.KEYBOARD_LAYOUTS, True, False)
        self.runtime._request_elevation.assert_called_once_with()
        self.elevated.return_value = True
        self.load.return_value = self.save.call_args.args[0]
        self.runtime._prepare_settings()
        self.assertTrue(backend.is_enabled())
        self.assertTrue(backend.is_configured(True, True))

    def test_saved_disable_cleans_registration_in_elevated_runtime(self):
        self.load.return_value = replace(Preferences(), autostart=False, run_as_administrator=True)
        self.startup.is_disabled.return_value = True
        self.elevated.return_value = True
        self.runtime._prepare_settings()
        self.startup.apply.assert_called_once_with(False, True, preserve_approval=True)

    def test_reload_rejection_is_shown_in_settings(self):
        self.hotkeys.apply.return_value = False
        self.runtime._apply_settings(False, False, SwitchingMethod.KEYBOARD_LAYOUTS, True, True)
        self.assertIn("注销后重新登录", self.warning.call_args.args[1])

    def test_shortcut_failure_notice_does_not_double_full_stop(self):
        self.hotkeys.apply.side_effect = ValueError("备份不存在。")
        with self.assertLogs("ime_switcher.__main__", level="ERROR"):
            self.runtime._apply_settings(False, True, SwitchingMethod.KEYBOARD_LAYOUTS, False, False)
        message = self.warning.call_args.args[1]
        self.assertNotIn("。。", message)
        self.assertIn("备份不存在。请修复", message)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.startup = self.enterContext(patch.object(app, "WindowsStartup")).return_value
        self.startup.needs_elevation.return_value = False
        self.startup.is_configured.return_value = False
        self.load = self.enterContext(patch.object(app, "load_preferences", return_value=Preferences()))
        self.install = self.enterContext(patch.object(app.tray, "install_autostart"))
        self.remove = self.enterContext(patch.object(app.tray, "remove_autostart"))
        self.elevated_command = self.enterContext(patch.object(app.elevation, "run_elevated_command"))
        self.stdout = self.enterContext(patch.object(app.sys, "stdout", new_callable=io.StringIO))

    def test_install_and_uninstall_use_same_startup_manager(self):
        self.assertTrue(app._handle_cli(["--install"]))
        self.install.assert_called_once_with()
        self.assertTrue(app._handle_cli(["--uninstall"]))
        self.remove.assert_called_once_with()

    def test_install_reports_standard_run_method(self):
        app._handle_cli(["--install"])
        self.assertEqual(self.stdout.getvalue(), "已添加登录自启（普通模式：启动项）。\n")

    def test_install_reports_administrator_task_method(self):
        self.load.return_value = replace(Preferences(), run_as_administrator=True)
        app._handle_cli(["--install"])
        self.assertEqual(self.stdout.getvalue(), "已添加登录自启（管理员模式：启动项与按需任务）。\n")

    def test_uninstall_reports_that_running_instance_is_unchanged(self):
        app._handle_cli(["--uninstall"])
        self.assertEqual(
            self.stdout.getvalue(),
            "已移除登录自启。正在运行的 MacStyleIME 不受影响，"
            '如需退出请在托盘图标菜单中选择"退出"。\n',
        )

    def test_elevated_child_does_not_repeat_success_message(self):
        app._handle_cli(["--install", elevation.CLI_CHILD_FLAG])
        self.elevated_command.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")

    def test_uac_failure_prints_no_success_line(self):
        self.startup.needs_elevation.return_value = True
        self.elevated_command.side_effect = OSError("管理员命令失败")
        with (patch.object(app.sys, "stderr", new_callable=io.StringIO),
              self.assertLogs("ime_switcher.__main__", level="ERROR"),
              self.assertRaises(SystemExit) as result):
            app._handle_cli(["--install"])
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(self.stdout.getvalue(), "")

    def test_elevated_child_failure_does_not_print_success_or_show_dialog(self):
        self.install.side_effect = OSError("task creation failed")
        with (
            patch.object(app.sys, "frozen", True, create=True),
            patch.object(app.messagebox, "showerror") as showerror,
            patch.object(app.sys, "stderr", new_callable=io.StringIO),
            self.assertLogs("ime_switcher.__main__", level="ERROR"),
            self.assertRaises(SystemExit) as result,
        ):
            app._handle_cli(["--install", elevation.CLI_CHILD_FLAG])
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(self.stdout.getvalue(), "")
        showerror.assert_not_called()

    def test_frozen_parent_failure_shows_one_dialog(self):
        self.startup.needs_elevation.return_value = True
        self.elevated_command.side_effect = OSError("cancelled")
        with (
            patch.object(app.sys, "frozen", True, create=True),
            patch.object(app.messagebox, "showerror") as showerror,
            patch.object(app.sys, "stderr", new_callable=io.StringIO) as stderr,
            self.assertLogs("ime_switcher.__main__", level="ERROR"),
            self.assertRaises(SystemExit),
        ):
            app._handle_cli(["--install"])
        self.assertEqual(self.stdout.getvalue(), "")
        showerror.assert_called_once()
        self.assertIn("登录自启操作未完成", stderr.getvalue())

    def test_uac_path_prints_the_success_message_once_in_the_parent(self):
        self.startup.needs_elevation.return_value = True
        self.load.return_value = replace(Preferences(), run_as_administrator=True)
        app._handle_cli(["--install"])
        self.elevated_command.assert_called_once_with(["--install"])
        self.install.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "已添加登录自启（管理员模式：启动项与按需任务）。\n")

    def test_tray_autostart_helpers_stay_silent(self):
        with (
            patch.object(app.tray, "load_preferences", return_value=Preferences()),
            patch.object(app.tray, "save_preferences"),
            patch.object(app.tray, "set_autostart_enabled"),
        ):
            app.tray.install_autostart()
            app.tray.remove_autostart()
        self.assertEqual(self.stdout.getvalue(), "")

    def test_startup_cli_requests_uac_when_required(self):
        self.startup.needs_elevation.return_value = True
        app._handle_cli(["--install"])
        self.elevated_command.assert_called_once_with(["--install"])
        self.install.assert_not_called()

    def test_cli_uac_cancellation_reports_failure_without_writing_run(self):
        self.startup.needs_elevation.return_value = True
        self.elevated_command.side_effect = OSError("cancelled")
        with (patch.object(app.sys, "stderr", new_callable=io.StringIO) as output,
              self.assertLogs("ime_switcher.__main__", level="ERROR"),
              self.assertRaises(SystemExit) as result):
            app._handle_cli(["--uninstall"])
        self.assertEqual(result.exception.code, 1)
        self.assertIn("登录自启操作未完成", output.getvalue())
        self.assertEqual(self.stdout.getvalue(), "")
        self.remove.assert_not_called()


class MainCliParsingTests(unittest.TestCase):
    def test_elevated_cli_child_flag_is_handled_by_the_cli_branch(self):
        arguments = ["--install", elevation.CLI_CHILD_FLAG]
        with (
            patch.object(app.sys, "argv", ["MacStyleIME.exe", *arguments]),
            patch.object(app, "_handle_cli", return_value=True) as handle,
        ):
            app.main()
        handle.assert_called_once_with(arguments)


class AutostartLaunchTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.scheduler = Scheduler()
        self.backend = WindowsStartup(self.registry, self.scheduler, lambda: False)
        self.task_name = "MacStyleIME-Autostart-S-1-5-21-123-456-789-1001"
        self.scheduler.tasks.add(self.task_name)
        self.enterContext(patch.object(app, "WindowsStartup", return_value=self.backend))
        self.enterContext(patch.object(app.sys, "argv", ["MacStyleIME.exe", "--autostart"]))
        self.enterContext(patch.object(app, "_enable_dpi_awareness"))
        self.load = self.enterContext(patch.object(app, "load_preferences", return_value=
            replace(Preferences(), run_as_administrator=True, autostart=True)))
        self.elevated = self.enterContext(patch.object(app.elevation, "is_elevated", return_value=False))
        self.handoff = self.enterContext(patch.object(app.elevation, "handoff_to_elevated"))
        self.lock = self.enterContext(patch.object(app, "_acquire_single_instance", return_value=123))
        self.release = self.enterContext(patch.object(app, "_release_single_instance", return_value=True))
        self.runtime = self.enterContext(patch.object(app, "_DesktopRuntime"))
        self.runtime.return_value.run.return_value = app._RuntimeAction.QUIT
        self.save = self.enterContext(patch.object(app, "save_preferences"))
        self.enterContext(patch.object(app.toggle, "set_switching_method"))
        self.stdout = self.enterContext(patch.object(app.sys, "stdout", new_callable=io.StringIO))

    def test_admin_autostart_runs_task_and_exits_before_acquiring_lock(self):
        def require_no_lock(command, **kwargs):
            self.lock.assert_not_called()
            return self.scheduler(command, **kwargs)
        self.backend._runner = require_no_lock
        app.main()
        runs = [call for call in self.scheduler.calls if "/Run" in call]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][1:], ["/Run", "/TN", self.task_name])
        self.lock.assert_not_called()
        self.runtime.assert_not_called()
        self.handoff.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")

    def test_failed_or_missing_task_runs_standard_without_uac_or_preference_rollback(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.scheduler.tasks.clear()
                self.scheduler.fail = "run"
                with self.assertLogs("ime_switcher.__main__", level="ERROR"):
                    app.main()
                self.handoff.assert_not_called()
                self.runtime.return_value.run.assert_called()
                self.lock.assert_called_with(0)
                self.save.assert_not_called()
                self.assertNotIn("未知命令", self.stdout.getvalue())

    def test_standard_or_already_elevated_autostart_does_not_run_task(self):
        for administrator, elevated in ((False, False), (True, True)):
            with self.subTest(administrator=administrator, elevated=elevated):
                self.load.return_value = replace(Preferences(), run_as_administrator=administrator)
                self.elevated.return_value = elevated
                app.main()
                self.handoff.assert_not_called()
                self.runtime.return_value.run.assert_called()
                self.assertFalse(any("/Run" in call for call in self.scheduler.calls))

    def test_task_timeout_runs_standard_without_uac(self):
        def timeout(command, **kwargs):
            if "/Run" in command:
                raise subprocess.TimeoutExpired(command, 5)
            return self.scheduler(command, **kwargs)
        self.backend._runner = timeout
        with self.assertLogs("ime_switcher.__main__", level="ERROR"):
            app.main()
        self.handoff.assert_not_called()
        self.runtime.return_value.run.assert_called_once_with()
        self.save.assert_not_called()

    def test_task_child_without_elevated_token_never_requests_uac_or_relaunches_task(self):
        with patch.object(app.sys, "argv", ["MacStyleIME.exe", "--autostart-task"]):
            app.main()
        self.handoff.assert_not_called()
        self.runtime.return_value.run.assert_called_once_with()
        self.assertFalse(any("/Run" in call for call in self.scheduler.calls))
        self.assertNotIn("未知命令", self.stdout.getvalue())


class HandoffRollbackTests(unittest.TestCase):
    def test_cancelled_uac_reconciles_actual_run_registration_on_resume(self):
        for initial, desired in ((False, True), (True, False)):
            with self.subTest(initial=initial, desired=desired):
                registry = Registry()
                if initial:
                    registry.entries[app.config.REG_RUN_KEY] = {app.config.APP_NAME: (run_command(), 1)}
                scheduler = Scheduler()
                backend = WindowsStartup(registry, scheduler, lambda: False)
                state = [replace(Preferences(), autostart=initial)]
                runtime = app._DesktopRuntime.__new__(app._DesktopRuntime)
                runtime._root = Mock()
                runtime._startup_notice = None
                runtime._notify = Mock()
                runtime._request_elevation = Mock()

                def first_run():
                    runtime._apply_settings(True, desired, SwitchingMethod.KEYBOARD_LAYOUTS, False, False)
                    runtime._request_elevation.assert_called_once_with()
                    return app._RuntimeAction.REQUEST_ELEVATION

                def resumed_run():
                    runtime._prepare_settings()
                    return app._RuntimeAction.QUIT

                with (
                    patch.object(app.sys, "argv", ["MacStyleIME.exe"]),
                    patch.object(app, "_acquire_single_instance", return_value=1),
                    patch.object(app, "_release_single_instance", return_value=True),
                    patch.object(app, "_DesktopRuntime", side_effect=[
                        SimpleNamespace(run=first_run), SimpleNamespace(run=resumed_run),
                    ]),
                    patch.object(app, "WindowsStartup", return_value=backend),
                    patch.object(app, "LanguageHotkeys"),
                    patch.object(app, "load_preferences", side_effect=lambda: state[0]),
                    patch.object(app, "save_preferences", side_effect=lambda value: state.__setitem__(0, value)),
                    patch.object(app.toggle, "set_switching_method"),
                    patch.object(app.hook, "set_block_ctrl_space"),
                    patch.object(app.elevation, "is_elevated", return_value=False),
                    patch.object(app.elevation, "handoff_to_elevated", return_value=
                                 elevation.HandoffResult(elevation.HandoffStatus.CANCELLED)),
                ):
                    app.main()
                self.assertEqual(backend.run_exists(), desired)
                self.assertEqual(scheduler.tasks, set())
                self.assertEqual(state[0].autostart, desired)
                self.assertFalse(state[0].run_as_administrator)
                self.assertFalse(state[0].block_ctrl_space)
                self.assertFalse(state[0].disable_system_hotkeys)

    def test_cancelled_uac_preserves_shortcut_options_and_startup_intent(self):
        saved_during_settings = replace(Preferences(), run_as_administrator=True,
                                       autostart=True, block_ctrl_space=False,
                                       disable_system_hotkeys=False)
        first = SimpleNamespace(run=Mock(return_value=app._RuntimeAction.REQUEST_ELEVATION))
        second = SimpleNamespace(run=Mock(return_value=app._RuntimeAction.QUIT))
        with (
            patch.object(app.sys, "argv", ["MacStyleIME.exe"]),
            patch.object(app, "_acquire_single_instance", return_value=1),
            patch.object(app, "_release_single_instance", return_value=True),
            patch.object(app, "_DesktopRuntime", side_effect=[first, second]) as runtime,
            patch.object(app, "load_preferences", side_effect=[Preferences(), saved_during_settings]),
            patch.object(app, "save_preferences") as save,
            patch.object(app.toggle, "set_switching_method"),
            patch.object(app.elevation, "is_elevated", return_value=False),
            patch.object(app.elevation, "handoff_to_elevated",
                         return_value=elevation.HandoffResult(elevation.HandoffStatus.CANCELLED)),
        ):
            app.main()
        self.assertEqual(save.call_args.args[0], replace(saved_during_settings, run_as_administrator=False))
        self.assertIn("管理员模式未启用", runtime.call_args.args[0])


class RuntimeShutdownTests(unittest.TestCase):
    def make_runtime(self):
        runtime = app._DesktopRuntime.__new__(app._DesktopRuntime)
        runtime._root = Mock()
        runtime._icon = None
        runtime._hook_thread = None
        runtime._tray_thread = None
        runtime._tray_started = Mock(wait=Mock(return_value=True))
        runtime._tray_started_ok = True
        runtime._action = app._RuntimeAction.QUIT
        runtime._settings = SimpleNamespace(show=Mock())
        runtime._hook_loop = Mock(installed=True)
        runtime._prepare_settings = Mock()
        return runtime

    def test_finally_stops_hook_before_joining_thread(self):
        runtime = self.make_runtime()
        order = []
        runtime._hook_loop.stop.side_effect = lambda: order.append("stop")
        fake_thread = Mock(join=Mock(side_effect=lambda **kwargs: order.append("join")))
        with (
            patch.dict("sys.modules", {"pystray": Mock()}),
            patch.object(app.threading, "Thread", return_value=fake_thread),
            patch.object(app.tray, "_make_tray_image"),
            patch.object(app.tray, "build_menu"),
        ):
            self.assertIs(runtime.run(), app._RuntimeAction.QUIT)
        self.assertEqual(order, ["stop", "join", "join"])
        runtime._root.destroy.assert_called_once_with()

    def test_hook_startup_timeout_also_sends_stop(self):
        runtime = self.make_runtime()
        runtime._hook_loop.ready.wait.return_value = False
        with (
            patch.dict("sys.modules", {"pystray": Mock()}),
            patch.object(app.threading, "Thread"),
            patch.object(app.messagebox, "showerror"),
            self.assertLogs("ime_switcher.__main__", level="ERROR"),
        ):
            runtime.run()
        runtime._hook_loop.stop.assert_called_once_with()


class RestartCommandTests(unittest.TestCase):
    def test_source_restart_prefers_pythonw(self):
        python = r"C:\Python312\python.exe"
        script = r"D:\Apps\MacStyleIME\__main__.py"
        with (
            patch.object(elevation.sys, "executable", python),
            patch.object(elevation.sys, "argv", [script]),
            patch.object(Path, "exists", return_value=True),
        ):
            executable, parameters = elevation._restart_command(["--install"])
        self.assertEqual(executable, r"C:\Python312\pythonw.exe")
        self.assertEqual(parameters, subprocess.list2cmdline([script, "--install"]))

    def test_source_restart_falls_back_without_pythonw(self):
        python = r"C:\Python312\python.exe"
        script = r"D:\Apps\MacStyleIME\__main__.py"
        with (
            patch.object(elevation.sys, "executable", python),
            patch.object(elevation.sys, "argv", [script]),
            patch.object(Path, "exists", return_value=False),
        ):
            self.assertEqual(elevation._restart_command(["--install"])[0], python)


class ElevatedCommandTests(unittest.TestCase):
    def setUp(self):
        self.launch = self.enterContext(patch.object(elevation, "_launch_elevated"))
        self.launch.return_value = SimpleNamespace(status=elevation.HandoffStatus.READY, process_handle=123)
        self.kernel = self.enterContext(patch.object(elevation, "kernel32"))
        self.kernel.WaitForSingleObject.return_value = elevation.WAIT_OBJECT_0

    def test_cancelled_cli_elevation_does_not_wait_on_nonexistent_process(self):
        self.launch.return_value = SimpleNamespace(status=elevation.HandoffStatus.CANCELLED, error_code=1223)
        with self.assertRaises(OSError):
            elevation.run_elevated_command(["--install"])
        self.kernel.WaitForSingleObject.assert_not_called()

    def test_child_failure_is_reported_and_handle_is_closed(self):
        def exit_code(handle, code):
            code._obj.value = 1
            return True
        self.kernel.GetExitCodeProcess.side_effect = exit_code
        with self.assertRaises(OSError):
            elevation.run_elevated_command(["--uninstall"])
        self.kernel.CloseHandle.assert_called_once_with(123)

    def test_child_timeout_is_not_reported_as_success(self):
        self.kernel.WaitForSingleObject.return_value = elevation.WAIT_TIMEOUT
        with self.assertRaises(OSError):
            elevation.run_elevated_command(["--install"])
        self.kernel.CloseHandle.assert_called_once_with(123)

    def test_success_closes_child_handle(self):
        self.kernel.GetExitCodeProcess.return_value = True
        elevation.run_elevated_command(["--install"])
        self.kernel.CloseHandle.assert_called_once_with(123)
