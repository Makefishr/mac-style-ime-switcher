"""Low-level keyboard hook and CapsLock handler."""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

from . import config
from . import toggle
from .caps_handler import CapsLockHandler
from .winapi import HOOKPROC, KBDLLHOOKSTRUCT, is_key_down, kernel32, user32

log = logging.getLogger(__name__)

# ── Internal state ────────────────────────────────────────
_mod_ctrl_down = False


# ── CapsLock dual-action callbacks ────────────────────────

def _do_short_press() -> None:
    """Toggle IME (runs in a thread — never blocks the hook)."""
    try:
        toggle.switch_ime()
        time.sleep(0.03)
        _force_capslock_off()
    except Exception:
        config.log_exception()


def _on_short_press() -> None:
    """Quick CapsLock press → toggle IME (non-blocking)."""
    threading.Thread(target=_do_short_press, daemon=True).start()


def _do_long_press() -> None:
    """Enable CapsLock uppercase (runs in a thread)."""
    time.sleep(0.01)  # let the physical UP clear the system key state
    user32.keybd_event(config.VK_CAPITAL, 0, 0, 0)
    user32.keybd_event(config.VK_CAPITAL, 0, config.KEYEVENTF_KEYUP, 0)
    log.info("CapsLock long-press → uppercase ON")


def _on_long_press() -> None:
    """Long CapsLock press (1 s) → enable uppercase (non-blocking)."""
    threading.Thread(target=_do_long_press, daemon=True).start()


_caps_handler = CapsLockHandler(
    on_short_press=_on_short_press,
    on_long_press=_on_long_press,
    long_press_seconds=1.0,
)


# ── CapsLock LED ──────────────────────────────────────────

def _force_capslock_off() -> None:
    """Send a synthetic CapsLock keystroke to turn the LED off.

    The keyboard hook passes injected events through automatically
    (``LLKHF_INJECTED`` flag), so this toggle is invisible to our
    CapsLock handler.
    """
    if user32.GetKeyState(config.VK_CAPITAL) & 1:
        user32.keybd_event(config.VK_CAPITAL, 0, 0, 0)
        user32.keybd_event(config.VK_CAPITAL, 0, config.KEYEVENTF_KEYUP, 0)


def force_capslock_off() -> None:
    """Public wrapper — same as internal version, kept for __main__.py."""
    _force_capslock_off()


# ── Keyboard hook ─────────────────────────────────────────

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

    # ── CapsLock (must handle both DOWN and UP) ──
    if vk == config.VK_CAPITAL:
        if is_down:
            capslock_on = bool(user32.GetKeyState(config.VK_CAPITAL) & 1)
            if _caps_handler.handle_down(capslock_is_on=capslock_on):
                return 1
        else:
            result = _caps_handler.handle_up()
            if result == "short":
                return 1                       # eat UP
            if result == "long":
                return user32.CallNextHookEx(  # pass UP through
                    config.hook_handle, nCode, wParam, lParam,
                )
        return user32.CallNextHookEx(
            config.hook_handle, nCode, wParam, lParam,
        )

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


# ── Hook thread ───────────────────────────────────────────

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
