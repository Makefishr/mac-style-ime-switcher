"""Entry point for Mac-style IME Switcher."""

import ctypes
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from enum import Enum
from tkinter import messagebox

# When running from source, ensure the project root is on sys.path.
if not getattr(sys, "frozen", False):
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

from ime_switcher import config, elevation, hook, toggle, tray
from ime_switcher.settings_store import (
    Preferences,
    SwitchingMethod,
    load_preferences,
    save_preferences,
)
from ime_switcher.settings_ui import SettingsWindow
from ime_switcher.autostart import WindowsStartup
from ime_switcher.system_hotkeys import LanguageHotkeys
from ime_switcher.winapi import kernel32

log = logging.getLogger(__name__)

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
HOOK_READY_TIMEOUT_SECONDS = 2.0
TRAY_READY_TIMEOUT_SECONDS = 5.0


class _RuntimeAction(Enum):
    QUIT = "quit"
    REQUEST_ELEVATION = "request_elevation"


def _mutex_name() -> str:
    return f"Global\\{config.APP_NAME}_SingleInstance"


def _acquire_single_instance(wait_ms: int = 0):
    handle = kernel32.CreateMutexW(None, False, _mutex_name())
    if not handle:
        log.error("CreateMutexW failed with Windows error %d", ctypes.get_last_error())
        return None

    result = kernel32.WaitForSingleObject(handle, max(0, wait_ms))
    if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
        log.info("Single-instance mutex acquired")
        return handle

    if result != WAIT_TIMEOUT:
        log.error("Waiting for the single-instance mutex returned 0x%08X", result)
    kernel32.CloseHandle(handle)
    return None


def _release_single_instance(handle) -> bool:
    if not handle:
        return True
    if not kernel32.ReleaseMutex(handle):
        log.error("ReleaseMutex failed with Windows error %d", ctypes.get_last_error())
        return False
    kernel32.CloseHandle(handle)
    return True


class _DesktopRuntime:
    def __init__(self, startup_notice: str | None = None) -> None:
        self._startup_notice = startup_notice
        self._commands: queue.SimpleQueue = queue.SimpleQueue()
        self._root = tk.Tk()
        self._root.withdraw()
        self._icon = None
        self._hook_thread: threading.Thread | None = None
        self._tray_thread: threading.Thread | None = None
        self._tray_started = threading.Event()
        self._tray_started_ok = False
        self._action = _RuntimeAction.QUIT
        self._settings = SettingsWindow(self._root, self._apply_settings)
        self._hook_loop = hook.HookLoop()

    def _prepare_settings(self) -> None:
        preferences = load_preferences()
        if preferences.autostart is None:
            try:
                preferences = replace(preferences, autostart=WindowsStartup().is_enabled())
                save_preferences(preferences)
            except OSError as exc:
                log.exception("Unable to migrate startup registration")
                self._append_startup_notice(f"无法读取原有登录自启（{exc}），请在设置中重新保存。")
        for notice in self._apply_preferences(preferences, request_elevation=False):
            self._append_startup_notice(notice)

    def _apply_preferences(self, preferences: Preferences, *, request_elevation: bool) -> list[str]:
        """Apply independent preferences and report precisely which parts failed."""
        notices = []
        toggle.set_switching_method(preferences.switching_method)
        hook.set_block_ctrl_space(preferences.block_ctrl_space)
        try:
            if not LanguageHotkeys().apply(preferences.disable_system_hotkeys):
                notices.append("系统语言快捷键已写入，但 Windows 未能重新载入；请注销后重新登录。")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.exception("Unable to update system language shortcuts")
            notices.append(f"系统语言快捷键未能完整应用：{str(exc).rstrip('。')}。请修复后重新保存设置。")
        try:
            startup = WindowsStartup()
            needs_elevation = request_elevation and preferences.run_as_administrator and not elevation.is_elevated()
            if preferences.autostart is not None:
                if not startup.is_configured(preferences.autostart, preferences.run_as_administrator):
                    if needs_elevation or startup.needs_elevation(preferences.autostart, preferences.run_as_administrator):
                        needs_elevation = True
                    else:
                        startup.apply(preferences.autostart, preferences.run_as_administrator)
            if needs_elevation:
                if request_elevation:
                    self._request_elevation()
                else:
                    notices.append("登录自启设置尚未应用：需要管理员权限，请在设置中重新保存。")
        except OSError as exc:
            log.exception("Unable to update startup registration")
            notices.append(f"登录自启尚未确认应用：{exc}。偏好已保留，请在设置中重新保存。")
            # A scheduler failure must not prevent the independently requested mode change.
            if request_elevation and preferences.run_as_administrator and not elevation.is_elevated():
                self._request_elevation()
        return notices

    def _append_startup_notice(self, message: str) -> None:
        self._startup_notice = f"{self._startup_notice or ''} {message}".strip()

    def run(
        self,
        notify_ready: Callable[[], bool] | None = None,
    ) -> _RuntimeAction:
        import pystray

        config.running = True
        config.hook_handle = None
        try:
            self._prepare_settings()
            self._hook_thread = threading.Thread(
                target=self._hook_loop.run,
                daemon=True,
                name="Hook",
            )
            self._hook_thread.start()
            hook_ready = self._hook_loop.ready.wait(HOOK_READY_TIMEOUT_SECONDS)
            if not hook_ready or not self._hook_loop.installed:
                log.error("Hook installation failed — exiting")
                messagebox.showerror(
                    "Mac-style IME Switcher",
                    "键盘钩子未能启动。MacStyleIME 尚未接管输入法切换；请重试或查看日志。",
                    parent=self._root,
                )
                return self._action

            self._icon = pystray.Icon(
                config.APP_NAME,
                tray._make_tray_image(),
                config.APP_TITLE,
                tray.build_menu(
                    lambda: self._post(self._settings.show),
                    lambda: self._post(self._request_quit),
                ),
            )
            self._tray_thread = threading.Thread(
                target=self._run_tray,
                daemon=True,
                name="Tray",
            )
            self._tray_thread.start()

            if (
                not self._tray_started.wait(TRAY_READY_TIMEOUT_SECONDS)
                or not self._tray_started_ok
            ):
                log.error("Tray icon did not become ready")
                messagebox.showerror(
                    "Mac-style IME Switcher",
                    "系统托盘未能启动。MacStyleIME 尚未完成启动；请重试或查看日志。",
                    parent=self._root,
                )
                return self._action

            if notify_ready is not None and not notify_ready():
                log.error("Unable to confirm elevated runtime readiness to parent")
                return self._action

            self._root.after(50, self._drain_commands)
            log.info("Entering Tk main loop")
            self._root.mainloop()
            return self._action
        finally:
            config.running = False
            self._hook_loop.stop()
            if self._icon is not None:
                self._icon.stop()
            if self._hook_thread is not None:
                self._hook_thread.join(timeout=1.0)
            if self._tray_thread is not None:
                self._tray_thread.join(timeout=1.0)
            try:
                self._root.destroy()
            except tk.TclError:
                pass
            log.info("%s exiting", config.APP_NAME)

    def _run_tray(self) -> None:
        def setup(icon) -> None:
            icon.visible = True
            if self._startup_notice:
                self._notify(self._startup_notice)
            self._tray_started_ok = True
            self._tray_started.set()

        try:
            self._icon.run(setup=setup)
        except Exception:
            log.exception("Tray icon loop failed")
            self._tray_started.set()
            self._post(self._request_quit)

    def _post(self, command) -> None:
        self._commands.put(command)

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            try:
                command()
            except Exception:
                log.exception("Desktop command failed")
        if config.running:
            self._root.after(50, self._drain_commands)

    def _notify(self, message: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(message)
        except Exception:
            log.exception("Unable to show tray notification: %s", message)

    def _request_quit(self) -> None:
        config.running = False
        self._root.quit()

    def _request_elevation(self) -> None:
        self._action = _RuntimeAction.REQUEST_ELEVATION
        self._request_quit()

    def _apply_settings(
        self,
        run_as_administrator: bool,
        autostart: bool,
        switching_method: SwitchingMethod,
        disable_system_hotkeys: bool,
        block_ctrl_space: bool,
    ) -> bool:
        preferences = Preferences(
            run_as_administrator=run_as_administrator,
            switching_method=switching_method,
            disable_system_hotkeys=disable_system_hotkeys,
            block_ctrl_space=block_ctrl_space,
            autostart=autostart,
        )
        save_preferences(preferences)
        notices = self._apply_preferences(preferences, request_elevation=True)
        if notices:
            messagebox.showwarning(
                "设置已保存，部分尚未生效", "\n\n".join(notices), parent=self._root,
            )
        if elevation.is_elevated() and not run_as_administrator:
            self._notify("设置已保存；下次启动将使用普通模式。")
        return True


_UNINSTALL_MESSAGE = (
    "已移除登录自启。正在运行的 MacStyleIME 不受影响，"
    '如需退出请在托盘图标菜单中选择"退出"。'
)


def _startup_cli_message(cmd: str, preferences: Preferences) -> str:
    """Return the success line for an applied --install/--uninstall command."""
    if cmd == "--uninstall":
        return _UNINSTALL_MESSAGE
    if preferences.run_as_administrator:
        return "已添加登录自启（管理员模式：计划任务）。"
    return "已添加登录自启（普通模式：启动项）。"


def _handle_cli(arguments: list[str]) -> bool:
    if not arguments:
        return False

    cmd = arguments[0].lower()
    if cmd in ("--install", "--uninstall"):
        # The elevated child has no console and its parent prints the result.
        report_result = elevation.CLI_CHILD_FLAG not in arguments[1:]
        try:
            preferences = load_preferences()
            enabled = cmd == "--install"
            startup = WindowsStartup()
            if (not startup.is_configured(enabled, preferences.run_as_administrator)
                    and startup.needs_elevation(enabled, preferences.run_as_administrator)):
                elevation.run_elevated_command([cmd])
            elif enabled:
                tray.install_autostart()
            else:
                tray.remove_autostart()
        except OSError as exc:
            log.error("Startup command failed: %s", exc)
            message = f"登录自启操作未完成：{exc}。请重试或查看日志。"
            print(message, file=sys.stderr)
            # The waiting parent shows the single dialog; the elevated child
            # must not duplicate it.
            if getattr(sys, "frozen", False) and report_result:
                messagebox.showerror("登录自启操作未完成", message)
            raise SystemExit(1) from None
        if report_result:
            print(_startup_cli_message(cmd, preferences))
    elif cmd in ("--help", "-h"):
        print(__doc__ or "")
        print(f"Mac-style IME Switcher v{config.VERSION}")
        print("  --install    按管理员模式设置添加登录自启（必要时请求 UAC）")
        print("  --uninstall  移除启动项和登录任务（必要时请求 UAC）")
        print("  --help       显示此帮助")
    else:
        print(f"未知命令: {cmd}")
        print("用法: MacStyleIME.exe [--install|--uninstall|--help]")
    return True


def _handoff_notice(
    result: elevation.HandoffResult,
    *,
    retry_next_start: bool,
) -> str:
    if result.status is elevation.HandoffStatus.CANCELLED:
        reason = "已取消管理员权限请求"
    elif result.status is elevation.HandoffStatus.TIMED_OUT:
        reason = "管理员实例未能在 30 秒内完成启动"
    elif result.status is elevation.HandoffStatus.CHILD_EXITED:
        reason = "管理员实例在完成启动前退出"
    elif result.error_code is not None:
        reason = f"管理员模式启动失败（Windows 错误 {result.error_code}）"
    else:
        reason = "管理员模式启动失败"

    if retry_next_start:
        return f"{reason}；本次以普通模式运行，下次启动仍会重试管理员模式。"
    return f"{reason}；管理员模式未启用，MacStyleIME 继续以普通模式运行。"


def _enable_dpi_awareness() -> None:
    """Draw Tk windows sharply on scaled displays, as IDLE does on Windows."""
    try:
        ctypes.OleDLL("shcore").SetProcessDpiAwareness(1)  # system DPI aware
    except (AttributeError, OSError):
        log.debug("Unable to enable DPI awareness", exc_info=True)


def main() -> None:
    _enable_dpi_awareness()
    arguments = sys.argv[1:]
    try:
        restart_event = elevation.parse_restart_event(arguments)
    except ValueError as exc:
        log.error("Invalid internal restart arguments: %s", exc)
        return

    if restart_event is not None:
        if not elevation.is_elevated():
            log.error("Elevated restart did not receive an elevated token")
            return
    elif _handle_cli(arguments):
        return

    mutex_handle = _acquire_single_instance(
        elevation.HANDOFF_TIMEOUT_MS if restart_event is not None else 0
    )
    if mutex_handle is None:
        print("程序已在运行中")
        log.warning("Duplicate instance rejected")
        return

    def release_current_instance() -> bool:
        nonlocal mutex_handle
        if mutex_handle is None:
            return True
        if _release_single_instance(mutex_handle):
            mutex_handle = None
            return True
        return False

    def reacquire_after_failed_handoff() -> bool:
        nonlocal mutex_handle
        if mutex_handle is not None:
            return True
        mutex_handle = _acquire_single_instance(5_000)
        return mutex_handle is not None

    preferences = load_preferences()
    toggle.set_switching_method(preferences.switching_method)

    try:
        if restart_event is not None:
            log.info(
                "=== %s v%s starting (elevated handoff) ===",
                config.APP_NAME,
                config.VERSION,
            )
            _DesktopRuntime().run(
                lambda: elevation.notify_parent_ready(restart_event)
            )
            return

        startup_notice = None
        if preferences.run_as_administrator and not elevation.is_elevated():
            result = elevation.handoff_to_elevated(release_current_instance)
            if result.status is elevation.HandoffStatus.READY:
                return
            if not reacquire_after_failed_handoff():
                log.error("Unable to resume the standard instance after handoff failure")
                return
            startup_notice = _handoff_notice(result, retry_next_start=True)

        while True:
            log.info(
                "=== %s v%s starting (%s) ===",
                config.APP_NAME,
                config.VERSION,
                "elevated" if elevation.is_elevated() else "standard",
            )
            action = _DesktopRuntime(startup_notice).run()
            startup_notice = None
            if action is not _RuntimeAction.REQUEST_ELEVATION:
                return

            result = elevation.handoff_to_elevated(release_current_instance)
            if result.status is elevation.HandoffStatus.READY:
                return

            rollback_failed = False
            try:
                save_preferences(
                    replace(load_preferences(), run_as_administrator=False)
                )
            except OSError:
                rollback_failed = True
                log.exception("Unable to roll back administrator preference")

            if not reacquire_after_failed_handoff():
                log.error("Unable to resume the standard instance after handoff failure")
                return
            startup_notice = _handoff_notice(result, retry_next_start=False)
            if rollback_failed:
                startup_notice += " 管理员偏好未能回滚；下次启动可能再次请求权限。"
    finally:
        release_current_instance()


if __name__ == "__main__":
    main()
