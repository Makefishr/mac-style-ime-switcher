"""IME / keyboard-layout switching logic."""

import ctypes
import logging
import time
from ctypes import wintypes

from . import config
from .winapi import imm32, user32

log = logging.getLogger(__name__)


def _get_ime_conversion_mode(fg_hwnd: int) -> int | None:
    """Query the IME conversion mode for a foreground window's thread.

    Returns the raw DWORD, or ``None`` if the IME window is unavailable
    (modern TSF-only apps like new Notepad).
    """
    ime_wnd = imm32.ImmGetDefaultIMEWnd(fg_hwnd)
    if not ime_wnd:
        return None

    result = wintypes.DWORD()
    ok = user32.SendMessageTimeoutW(
        ime_wnd, config.WM_IME_CONTROL, config.IMC_GETCONVERSIONMODE, 0,
        config.SMTO_ABORTIFHUNG, 100, ctypes.byref(result),
    )
    if ok:
        return result.value
    return None


def _set_ime_conversion_mode(fg_hwnd: int, mode: int) -> bool:
    """Set the IME conversion mode. Returns ``True`` on success."""
    ime_wnd = imm32.ImmGetDefaultIMEWnd(fg_hwnd)
    if not ime_wnd:
        return False
    ok = user32.SendMessageTimeoutW(
        ime_wnd, config.WM_IME_CONTROL, config.IMC_SETCONVERSIONMODE, mode,
        config.SMTO_ABORTIFHUNG, 100, None,
    )
    return ok != 0


def switch_ime() -> None:
    """Toggle between English and Chinese input.

    Uses the keyboard layout for English, and the Chinese layout + IME
    conversion mode for Chinese.  Handles the case where Microsoft Pinyin
    is in English mode even though the Chinese layout is active.
    """
    fg = user32.GetForegroundWindow()
    if not fg:
        log.warning("switch_ime: no foreground window")
        return

    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    cur_hkl = user32.GetKeyboardLayout(fg_tid)
    cur_lang = cur_hkl & 0xFFFF

    if cur_lang == config.LANGID_ZH_CN:
        # Chinese layout is active — check the IME's internal mode.
        mode = _get_ime_conversion_mode(fg)
        if mode is not None and not (mode & config.IME_CMODE_NATIVE):
            # IME is in English mode — just open it to Chinese.
            _set_ime_conversion_mode(fg, mode | config.IME_CMODE_NATIVE)
            log.info("IME: opened conversion (was alphanumeric within Chinese layout)")
            return

        # IME is in Chinese mode (or we can't tell) → switch to English layout.
        new_hkl = user32.LoadKeyboardLayoutW(
            config.LAYOUT_EN_US, config.KLF_NOTELLSHELL,
        )
        if new_hkl:
            user32.PostMessageW(fg, config.WM_INPUTLANGCHANGEREQUEST, 0, new_hkl)
            log.info("IME switched: 中 → En")
    else:
        # Not Chinese → switch to Chinese layout and ensure IME is open.
        new_hkl = user32.LoadKeyboardLayoutW(
            config.LAYOUT_ZH_CN, config.KLF_NOTELLSHELL,
        )
        if not new_hkl:
            log.warning("switch_ime: LoadKeyboardLayout(%s) failed", config.LAYOUT_ZH_CN)
            return
        user32.PostMessageW(fg, config.WM_INPUTLANGCHANGEREQUEST, 0, new_hkl)
        log.info("IME switched: En → 中")
        # Give the layout switch a moment, then ensure IME is in native mode.
        time.sleep(0.05)
        _set_ime_conversion_mode(fg, config.IME_CMODE_NATIVE)


def get_ime_status() -> bool:
    """Return ``True`` if the user can currently type Chinese.

    Checks both the keyboard layout *and* the IME's internal conversion
    mode so that Microsoft Pinyin in English mode is reported correctly.
    """
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    tid = user32.GetWindowThreadProcessId(fg, None)
    hkl = user32.GetKeyboardLayout(tid)
    if (hkl & 0xFFFF) != config.LANGID_ZH_CN:
        return False
    mode = _get_ime_conversion_mode(fg)
    if mode is not None:
        return bool(mode & config.IME_CMODE_NATIVE)
    # Can't determine conversion mode — fall back to layout-only.
    return True
