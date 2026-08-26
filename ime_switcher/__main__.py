"""Entry point for Mac-style IME Switcher."""

import ctypes
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
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
    return f"Global\\{config.APP_NAME}_SingleInstance_{config.VERSION}"


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

    def run(
        self,
        notify_ready: Callable[[], bool] | None = None,
    ) -> _RuntimeAction:
        import pystray

        config.running = True
        config.hook_handle = None
        try:
            self._hook_thread = threading.Thread(
                target=hook.hook_thread_main,
                daemon=True,
                name="Hook",
            )
            self._hook_thread.start()
            hook_deadline = time.monotonic() + HOOK_READY_TIMEOUT_SECONDS
            while (
                not config.hook_handle
                and self._hook_thread.is_alive()
                and time.monotonic() < hook_deadline
            ):
                time.sleep(0.01)

            if not config.hook_handle:
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
    ) -> bool:
        try:
            tray.set_autostart_enabled(autostart)
        except OSError as exc:
            log.exception("Unable to update the HKCU Run entry")
            self._notify(f"无法更新开机启动设置（{exc}）。请重试或查看日志。")
            return False

        if run_as_administrator and not elevation.is_elevated():
            try:
                save_preferences(
                    Preferences(
                        run_as_administrator=True,
                        switching_method=switching_method,
                    )
                )
            except OSError as exc:
                log.exception("Unable to save administrator preference")
                self._notify(
                    f"无法保存管理员模式设置（{exc}）。MacStyleIME 继续以普通模式运行。"
                )
                return False
            toggle.set_switching_method(switching_method)
            self._request_elevation()
            return True

        save_preferences(
            Preferences(
                run_as_administrator=run_as_administrator,
                switching_method=switching_method,
            )
        )
        toggle.set_switching_method(switching_method)
        if elevation.is_elevated() and not run_as_administrator:
            self._notify("设置已保存；下次启动将使用普通模式。")
        return True


def _handle_cli(arguments: list[str]) -> bool:
    if not arguments:
        return False

    cmd = arguments[0].lower()
    if cmd == "--install":
        tray.install_autostart()
    elif cmd == "--uninstall":
        tray.remove_autostart()
    elif cmd in ("--help", "-h"):
        print(__doc__ or "")
        print(f"Mac-style IME Switcher v{config.VERSION}")
        print("  --install    添加开机自启")
        print("  --uninstall  移除开机自启")
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


def main() -> None:
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
                    Preferences(
                        run_as_administrator=False,
                        switching_method=load_preferences().switching_method,
                    )
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
