"""Constants, logging, and global state for Mac-style IME Switcher."""
import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

# ── App identity ──────────────────────────────────────────
APP_NAME  = "MacStyleIME"
APP_TITLE = "Mac-style IME Switcher"
VERSION   = "1.4.0"

# ── Paths ─────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LOG_FILE    = Path(tempfile.gettempdir()) / APP_NAME / "ime_switcher.log"
SETTINGS_FILE = APP_DIR / "ime_switcher.json"

# ── Virtual-key codes ─────────────────────────────────────
VK_CAPITAL   = 0x14
VK_SHIFT     = 0x10
VK_LSHIFT    = 0xA0
VK_RSHIFT    = 0xA1
VK_CONTROL   = 0x11
VK_LCONTROL  = 0xA2
VK_RCONTROL  = 0xA3
VK_MENU      = 0x12   # Alt
VK_LMENU     = 0xA4
VK_RMENU     = 0xA5
VK_LWIN      = 0x5B
VK_RWIN      = 0x5C
VK_SPACE     = 0x20

# ── Window messages / hook constants ──────────────────────
WH_KEYBOARD_LL  = 13
WM_KEYDOWN      = 0x100
WM_SYSKEYDOWN   = 0x104
KEYEVENTF_KEYUP = 2
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008
LLKHF_INJECTED  = 0x10
LLKHF_EXTENDED  = 0x01
INPUT_KEYBOARD  = 1
PM_REMOVE       = 1

# ── Keyboard-layout switching ─────────────────────────────
WM_INPUTLANGCHANGEREQUEST = 0x0050
KLF_NOTELLSHELL           = 0x0080

# ── Language identifiers ──────────────────────────────────
LANGID_ZH_CN = 0x0804
LANGID_EN_US = 0x0409
LAYOUT_EN_US = "00000409"
LAYOUT_ZH_CN = "00000804"
MICROSOFT_PINYIN_DESCRIPTION_ALLOWLIST = frozenset({
    "microsoft pinyin",
    "微软拼音",
})

# ── IME conversion-mode control (WM_IME_CONTROL) ────────────
WM_IME_CONTROL       = 0x0283
IMC_GETCONVERSIONMODE = 0x0001
IMC_SETCONVERSIONMODE = 0x0002
IMC_GETOPENSTATUS      = 0x0005
IMC_SETOPENSTATUS      = 0x0006
SMTO_ABORTIFHUNG      = 0x0002
IME_CONTROL_TIMEOUT_MS = 100
IME_CMODE_NATIVE      = 0x0001   # bit 0 set = native (Chinese) conversion

# ── Misc ──────────────────────────────────────────────────
ERROR_ALREADY_EXISTS = 183

# ── Logging ───────────────────────────────────────────────
def _log_file_candidates():
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        yield Path(local_appdata) / APP_NAME / "ime_switcher.log"
    yield Path(tempfile.gettempdir()) / APP_NAME / "ime_switcher.log"


def _configure_logging() -> None:
    global LOG_FILE
    for candidate in _log_file_candidates():
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                filename=str(candidate),
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(message)s",
            )
        except (OSError, ValueError):
            continue
        LOG_FILE = candidate
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


_configure_logging()


def log_exception() -> None:
    logging.error(traceback.format_exc())


# ── Global state ──────────────────────────────────────────
running     = True
hook_handle = None
