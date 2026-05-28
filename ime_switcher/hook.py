"""Low-level keyboard hook and CapsLock handler."""

import ctypes
import logging
import time
from ctypes import wintypes

from . import config
from .caps_ime import engine, VK_CAPITAL
from .winapi import HOOKPROC, KBDLLHOOKSTRUCT, is_key_down, kernel32, user32

log = logging.getLogger(__name__)


# ── Shortcut-blocking state ──────────────────────────────────
_mod_ctrl_down = False


# ── Keyboard hook ────────────────────────────────────────────


@HOOKPROC
def _keyboard_hook(nCode: int, wParam: int, lParam: int) -> int:
    global _mod_ctrl_down

    if nCode < 0:
        return user32.CallNextHookEx(
            config.hook_handle, nCode, wParam, lParam,
        )

    kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
    vk = kb.vkCode
    is_down = wParam in (config.WM_KEYDOWN, config.WM_SYSKEYDOWN)

    # Synthetic events (keybd_event / SendInput) always pass through.
    if kb.flags & config.LLKHF_INJECTED:
        return user32.CallNextHookEx(
            config.hook_handle, nCode, wParam, lParam,
        )

    # Track Ctrl state ourselves — more reliable than GetAsyncKeyState.
    if is_down and vk in (config.VK_CONTROL, config.VK_LCONTROL, config.VK_RCONTROL):
        _mod_ctrl_down = True
    elif not is_down and vk in (config.VK_CONTROL, config.VK_LCONTROL, config.VK_RCONTROL):
        _mod_ctrl_down = False

    # ── CapsLock: delegate to the deep module ──
    if engine.on_key_event(vk, is_down):
        return 1

    if not is_down:
        return user32.CallNextHookEx(
            config.hook_handle, nCode, wParam, lParam,
        )

    # ── Block Windows default IME shortcuts ──
    if vk == config.VK_SPACE and (
        is_key_down(config.VK_LWIN) or is_key_down(config.VK_RWIN)
    ):
        return 1
    if vk == config.VK_SPACE and _mod_ctrl_down:
        return 1
    if vk == config.VK_SHIFT and _mod_ctrl_down:
        return 1
    if vk == config.VK_SHIFT and is_key_down(config.VK_MENU):
        return 1

    return user32.CallNextHookEx(
        config.hook_handle, nCode, wParam, lParam,
    )


# ── Hook thread ──────────────────────────────────────────────


def hook_thread_main() -> None:
    """Install the low-level keyboard hook and run the message loop."""
    module = kernel32.GetModuleHandleW(None)
    ptr = user32.SetWindowsHookExW(
        config.WH_KEYBOARD_LL, _keyboard_hook, module, 0,
    )
    if not ptr:
        err = ctypes.get_last_error()
        log.error("SetWindowsHookExW failed (error %d)", err)
        return
    config.hook_handle = ptr
    log.info("Keyboard hook installed (handle=%s)", config.hook_handle)

    msg = wintypes.MSG()
    while config.running:
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, config.PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.005)

    user32.UnhookWindowsHookEx(config.hook_handle)
    config.hook_handle = None
    log.info("Keyboard hook uninstalled")
