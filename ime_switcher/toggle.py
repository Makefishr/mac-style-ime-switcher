"""IME / keyboard-layout switching logic."""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass

from . import config
from .settings_store import SwitchingMethod
from .winapi import DWORD_PTR, GUITHREADINFO, imm32, user32

log = logging.getLogger(__name__)

_LEGACY_CONSOLE_WINDOW_CLASS = "ConsoleWindowClass"
_DIALOG_WINDOW_CLASS = "#32770"
_LANGUAGE_CHANGE_TIMEOUT_SECONDS = 0.5
_LANGUAGE_CHANGE_POLL_SECONDS = 0.01
_IME_MODE_SETTLE_SECONDS = 0.02
_active_switching_method = SwitchingMethod.KEYBOARD_LAYOUTS


@dataclass(frozen=True)
class _InputState:
    foreground: int
    target: int
    legacy_console: bool
    language: int | None
    conversion_mode: int | None
    open_status: bool | None

    @property
    def can_type_chinese(self) -> bool:
        if self.language != config.LANGID_ZH_CN:
            return False
        if self.open_status is False:
            return False
        if self.conversion_mode is not None:
            return bool(self.conversion_mode & config.IME_CMODE_NATIVE)
        return True


def _get_ime_window(hwnd: int) -> int | None:
    ime_wnd = imm32.ImmGetDefaultIMEWnd(hwnd)
    return ime_wnd or None


def _get_window_class(hwnd: int) -> str | None:
    class_name = ctypes.create_unicode_buffer(256)
    length = user32.GetClassNameW(hwnd, class_name, len(class_name))
    return class_name.value if length > 0 else None


def _get_input_message_target(fg_hwnd: int) -> int:
    """Return the actual focused control for dialogs, or the top-level window."""
    if _get_window_class(fg_hwnd) != _DIALOG_WINDOW_CLASS:
        return fg_hwnd

    tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
    if not tid:
        return fg_hwnd
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
        return info.hwndFocus
    return fg_hwnd


def _get_window_language(hwnd: int) -> int | None:
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    if not tid:
        return None
    hkl = user32.GetKeyboardLayout(tid)
    return (hkl & 0xFFFF) if hkl else None


def _get_ime_window_language(hwnd: int) -> int | None:
    ime_wnd = _get_ime_window(hwnd)
    if not ime_wnd:
        return None
    return _get_window_language(ime_wnd)


def _query_ime_control(hwnd: int, command: int) -> int | None:
    ime_wnd = _get_ime_window(hwnd)
    if not ime_wnd:
        return None

    result = DWORD_PTR()
    ok = user32.SendMessageTimeoutW(
        ime_wnd,
        config.WM_IME_CONTROL,
        command,
        0,
        config.SMTO_ABORTIFHUNG,
        100,
        ctypes.byref(result),
    )
    return result.value if ok else None


def _set_ime_control(hwnd: int, command: int, value: int) -> bool:
    ime_wnd = _get_ime_window(hwnd)
    if not ime_wnd:
        return False
    ok = user32.SendMessageTimeoutW(
        ime_wnd,
        config.WM_IME_CONTROL,
        command,
        value,
        config.SMTO_ABORTIFHUNG,
        100,
        None,
    )
    return ok != 0


def _resolve_input_state(fg_hwnd: int | None = None) -> _InputState | None:
    foreground = fg_hwnd or user32.GetForegroundWindow()
    if not foreground:
        return None

    legacy_console = _get_window_class(foreground) == _LEGACY_CONSOLE_WINDOW_CLASS
    target = foreground if legacy_console else _get_input_message_target(foreground)
    language = _get_window_language(target)
    ime_language = _get_ime_window_language(target)
    if legacy_console and ime_language is not None:
        language = ime_language
    elif language is None:
        language = ime_language

    conversion_mode = _query_ime_control(
        target, config.IMC_GETCONVERSIONMODE,
    )
    open_value = _query_ime_control(target, config.IMC_GETOPENSTATUS)
    return _InputState(
        foreground=foreground,
        target=target,
        legacy_console=legacy_console,
        language=language,
        conversion_mode=conversion_mode,
        open_status=bool(open_value) if open_value is not None else None,
    )


def _set_chinese_mode(state: _InputState) -> bool:
    mode = state.conversion_mode or 0
    opened = _set_ime_control(
        state.target, config.IMC_SETOPENSTATUS, 1,
    )
    converted = _set_ime_control(
        state.target,
        config.IMC_SETCONVERSIONMODE,
        mode | config.IME_CMODE_NATIVE,
    )
    return opened and converted


def _switch_legacy_console_ime(state: _InputState) -> bool:
    if not state.legacy_console or state.language != config.LANGID_ZH_CN:
        return False
    if state.conversion_mode is None or state.open_status is None:
        return False

    if state.can_type_chinese:
        # conhost must be closed once so its next automatic reopen retains
        # alphanumeric mode instead of restoring native conversion.
        _set_ime_control(
            state.target,
            config.IMC_SETCONVERSIONMODE,
            state.conversion_mode & ~config.IME_CMODE_NATIVE,
        )
        if _set_ime_control(state.target, config.IMC_SETOPENSTATUS, 0):
            log.info("IME console mode change requested: Chinese to English")
            return True
        return False

    if _set_chinese_mode(state):
        log.info("IME console mode change requested: English to Chinese")
        return True
    return False


def _current_language(hwnd: int) -> int | None:
    return _get_window_language(hwnd) or _get_ime_window_language(hwnd)


def _wait_for_language(hwnd: int, expected_language: int) -> bool:
    deadline = time.monotonic() + _LANGUAGE_CHANGE_TIMEOUT_SECONDS
    while True:
        if _current_language(hwnd) == expected_language:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_LANGUAGE_CHANGE_POLL_SECONDS, remaining))


def _request_layout_change(
    target: int,
    layout_name: str,
    expected_language: int,
    description: str,
) -> bool:
    new_hkl = user32.LoadKeyboardLayoutW(
        layout_name, config.KLF_NOTELLSHELL,
    )
    if not new_hkl:
        log.warning("LoadKeyboardLayout(%s) failed", layout_name)
        return False
    if not user32.PostMessageW(
        target, config.WM_INPUTLANGCHANGEREQUEST, 0, new_hkl,
    ):
        log.warning("IME layout change request failed: %s", description)
        return False
    if _wait_for_language(target, expected_language):
        log.info("IME layout changed: %s", description)
        return True
    log.warning("IME layout change timed out: %s", description)
    return False


def set_switching_method(method: SwitchingMethod) -> None:
    """Select the behavior used by subsequent CapsLock presses."""
    global _active_switching_method
    _active_switching_method = method


def switch_ime() -> None:
    """Apply the configured CapsLock switching behavior."""
    if _active_switching_method is SwitchingMethod.MICROSOFT_PINYIN_MODE:
        _switch_microsoft_pinyin_mode()
    else:
        _switch_keyboard_layouts()


def _switch_microsoft_pinyin_mode() -> None:
    state = _resolve_input_state()
    if state is None:
        log.warning("Pinyin mode switch skipped: no foreground window")
        return
    if state.language != config.LANGID_ZH_CN:
        log.warning(
            "Pinyin mode switch skipped: Simplified Chinese is not active"
        )
        return
    if state.conversion_mode is None:
        log.warning(
            "Pinyin mode switch unsupported: conversion mode is unavailable"
        )
        return

    was_native = bool(state.conversion_mode & config.IME_CMODE_NATIVE)
    if was_native:
        # Microsoft Pinyin uses 0x401 for Chinese on current Windows builds.
        # Clearing only IME_CMODE_NATIVE produces 0x400, which it rejects and
        # normalizes back to Chinese. English must be requested as mode 0.
        requested_mode = config.IME_CMODE_ALPHANUMERIC
        description = "Chinese to English"
    else:
        requested_mode = config.IME_CMODE_NATIVE
        description = "English to Chinese"

    if not _set_ime_control(
        state.target,
        config.IMC_SETCONVERSIONMODE,
        requested_mode,
    ):
        log.warning("Pinyin mode switch request failed: %s", description)
        return

    # SendMessage is synchronous, but Microsoft Pinyin normalizes conversion
    # modes asynchronously. Verify after that normalization can occur.
    time.sleep(_IME_MODE_SETTLE_SECONDS)
    confirmed_mode = _query_ime_control(
        state.target, config.IMC_GETCONVERSIONMODE,
    )
    if confirmed_mode is None:
        log.warning("Pinyin mode switch could not be verified: %s", description)
        return
    if bool(confirmed_mode & config.IME_CMODE_NATIVE) == was_native:
        log.warning("Pinyin mode switch was not applied: %s", description)
        return
    log.info("Microsoft Pinyin mode changed: %s", description)


def _switch_keyboard_layouts() -> None:
    """Toggle the effective foreground input state between English and Chinese."""
    state = _resolve_input_state()
    if state is None:
        log.warning("switch_ime: no foreground window")
        return
    if state.language is None:
        log.warning("switch_ime: unable to determine the foreground input language")
        return
    if _switch_legacy_console_ime(state):
        return

    if state.language == config.LANGID_ZH_CN:
        if not state.can_type_chinese and state.conversion_mode is not None:
            if _set_chinese_mode(state):
                log.info("IME mode set to Chinese")
            return
        _request_layout_change(
            state.target,
            config.LAYOUT_EN_US,
            config.LANGID_EN_US,
            "Chinese to English",
        )
        return

    if _request_layout_change(
        state.target,
        config.LAYOUT_ZH_CN,
        config.LANGID_ZH_CN,
        "English to Chinese",
    ):
        refreshed = _resolve_input_state(state.foreground)
        if refreshed is not None:
            _set_chinese_mode(refreshed)
