"""Optional Windows elevation with a readiness-confirmed process handoff."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum

from . import config
from .winapi import SHELLEXECUTEINFOW, kernel32, shell32

log = logging.getLogger(__name__)

ERROR_CANCELLED = 1223
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_SHOWNORMAL = 1
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
HANDOFF_TIMEOUT_MS = 30_000
_READY_EVENT_PREFIX = f"Local\\{config.APP_NAME}.ElevationReady."


class HandoffStatus(Enum):
    READY = "ready"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CHILD_EXITED = "child_exited"


@dataclass(frozen=True)
class HandoffResult:
    status: HandoffStatus
    error_code: int | None = None


@dataclass(frozen=True)
class _LaunchResult:
    status: HandoffStatus
    process_handle: int | None = None
    error_code: int | None = None


def is_elevated() -> bool:
    """Return whether the current process has an elevated administrator token."""
    try:
        return bool(shell32.IsUserAnAdmin())
    except OSError:
        log.exception("Unable to query the current elevation state")
        return False


def parse_restart_event(arguments: list[str]) -> str | None:
    """Parse the private elevated-restart argument and validate its event name."""
    if not arguments or arguments[0] != "--elevated-restart":
        return None
    if len(arguments) != 2:
        raise ValueError("--elevated-restart requires exactly one event name")
    event_name = arguments[1]
    if not event_name.startswith(_READY_EVENT_PREFIX) or len(event_name) > 240:
        raise ValueError("invalid elevated-restart event name")
    return event_name


def notify_parent_ready(event_name: str) -> bool:
    """Signal that the elevated runtime has installed its hook and tray icon."""
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, event_name)
    if not handle:
        log.error(
            "OpenEventW(%s) failed with Windows error %d",
            event_name,
            ctypes.get_last_error(),
        )
        return False
    try:
        if kernel32.SetEvent(handle):
            return True
        log.error("SetEvent failed with Windows error %d", ctypes.get_last_error())
        return False
    finally:
        kernel32.CloseHandle(handle)


def handoff_to_elevated(
    release_current_instance: Callable[[], bool],
    timeout_ms: int = HANDOFF_TIMEOUT_MS,
) -> HandoffResult:
    """Launch an elevated copy, release the mutex, and await runtime readiness.

    The caller keeps running unless the child confirms that its keyboard hook
    and tray icon are ready. Once launch succeeds, ``release_current_instance``
    must make the named single-instance mutex available to the child.
    """
    event_name = _new_ready_event_name()
    ready_event = kernel32.CreateEventW(None, True, False, event_name)
    if not ready_event:
        return HandoffResult(HandoffStatus.FAILED, ctypes.get_last_error())

    launch = _launch_elevated(["--elevated-restart", event_name])
    if launch.status is not HandoffStatus.READY:
        kernel32.CloseHandle(ready_event)
        return HandoffResult(launch.status, launch.error_code)

    process_handle = launch.process_handle
    assert process_handle is not None
    try:
        try:
            released = release_current_instance()
        except Exception:
            log.exception("Unable to release the current instance for handoff")
            kernel32.TerminateProcess(process_handle, 1)
            kernel32.WaitForSingleObject(process_handle, 5_000)
            return HandoffResult(HandoffStatus.FAILED)
        if not released:
            kernel32.TerminateProcess(process_handle, 1)
            kernel32.WaitForSingleObject(process_handle, 5_000)
            return HandoffResult(HandoffStatus.FAILED)

        handles = (wintypes.HANDLE * 2)(ready_event, process_handle)
        wait_result = kernel32.WaitForMultipleObjects(
            2, handles, False, max(0, timeout_ms),
        )
        if wait_result == WAIT_OBJECT_0:
            if kernel32.WaitForSingleObject(process_handle, 0) == WAIT_OBJECT_0:
                return HandoffResult(HandoffStatus.CHILD_EXITED)
            return HandoffResult(HandoffStatus.READY)
        if wait_result == WAIT_OBJECT_0 + 1:
            return HandoffResult(HandoffStatus.CHILD_EXITED)
        if wait_result == WAIT_TIMEOUT:
            kernel32.TerminateProcess(process_handle, 1)
            kernel32.WaitForSingleObject(process_handle, 5_000)
            return HandoffResult(HandoffStatus.TIMED_OUT)
        error_code = ctypes.get_last_error() if wait_result == WAIT_FAILED else None
        return HandoffResult(HandoffStatus.FAILED, error_code)
    finally:
        kernel32.CloseHandle(process_handle)
        kernel32.CloseHandle(ready_event)


def _new_ready_event_name() -> str:
    return f"{_READY_EVENT_PREFIX}{os.getpid()}.{uuid.uuid4().hex}"


def _restart_command(arguments: list[str]) -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline(arguments)

    script = os.path.abspath(sys.argv[0])
    return sys.executable, subprocess.list2cmdline([script, *arguments])


def _launch_elevated(arguments: list[str]) -> _LaunchResult:
    executable, parameters = _restart_command(arguments)
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = parameters
    info.lpDirectory = str(config.APP_DIR)
    info.nShow = SW_SHOWNORMAL

    reset_key = "PYINSTALLER_RESET_ENVIRONMENT"
    previous_reset = os.environ.get(reset_key)
    if getattr(sys, "frozen", False):
        os.environ[reset_key] = "1"

    try:
        ctypes.set_last_error(0)
        launched = bool(shell32.ShellExecuteExW(ctypes.byref(info)))
        error_code = ctypes.get_last_error()
    finally:
        if getattr(sys, "frozen", False):
            if previous_reset is None:
                os.environ.pop(reset_key, None)
            else:
                os.environ[reset_key] = previous_reset

    if not launched:
        if error_code == ERROR_CANCELLED:
            return _LaunchResult(HandoffStatus.CANCELLED, error_code=error_code)
        return _LaunchResult(HandoffStatus.FAILED, error_code=error_code)
    if not info.hProcess:
        return _LaunchResult(HandoffStatus.FAILED)
    return _LaunchResult(HandoffStatus.READY, process_handle=info.hProcess)
