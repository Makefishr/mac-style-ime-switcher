"""IME / keyboard-layout switching logic."""

import ctypes
import logging

from . import config
from .winapi import user32

log = logging.getLogger(__name__)


def switch_ime() -> None:
    """Query the foreground window's keyboard layout and flip it.

    ZH-CN (0x0804) → EN-US (0x0409), and vice versa.
    Uses ``WM_INPUTLANGCHANGEREQUEST`` so the switch is silent —
    no input-method flyout, no visual noise.
    """
    fg = user32.GetForegroundWindow()
    if not fg:
        log.warning("switch_ime: no foreground window")
        return

    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    cur_hkl = user32.GetKeyboardLayout(fg_tid)
    cur_lang = cur_hkl & 0xFFFF

    if cur_lang == config.LANGID_ZH_CN:
        target_layout = config.LAYOUT_EN_US
    else:
        target_layout = config.LAYOUT_ZH_CN

    new_hkl = user32.LoadKeyboardLayoutW(target_layout, config.KLF_NOTELLSHELL)
    if not new_hkl:
        log.warning("switch_ime: LoadKeyboardLayout(%s) failed", target_layout)
        return

    ok = user32.PostMessageW(
        fg, config.WM_INPUTLANGCHANGEREQUEST, 0, new_hkl,
    )
    if ok:
        log.info("IME switched: %s → %s",
                 "中" if cur_lang == config.LANGID_ZH_CN else "En",
                 "En" if cur_lang == config.LANGID_ZH_CN else "中")
    else:
        log.warning("switch_ime: PostMessage failed (err=%d)",
                    ctypes.get_last_error())


def get_ime_status() -> bool:
    """Return ``True`` if the foreground window is using a Chinese layout."""
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    tid = user32.GetWindowThreadProcessId(fg, None)
    hkl = user32.GetKeyboardLayout(tid)
    return (hkl & 0xFFFF) == config.LANGID_ZH_CN
