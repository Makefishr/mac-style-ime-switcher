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
VERSION   = "2.0.0"


def _running_under_tests() -> bool:
    """Return whether a test runner imported this module."""
    return "unittest" in sys.modules or "pytest" in sys.modules


def source_executable() -> str:
    """Return the source interpreter, preferring pythonw.exe beside it.

    python.exe opens a console window for elevated restarts and logon
    startup; pythonw.exe runs the same script without one.
    """
    executable = Path(sys.executable)
    if executable.name:
        windowless = executable.with_name("pythonw.exe")
        if windowless.exists():
            return str(windowless)
    return sys.executable


# ── Paths ─────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_TESTING = _running_under_tests()
_local_app_data = os.environ.get("LOCALAPPDATA")
if _TESTING:
    # Keep tests away from the real per-user settings, backup, and log.
    USER_DATA_DIR = Path(tempfile.gettempdir()) / f"{APP_NAME}-tests-{os.getpid()}"
elif _local_app_data:
    USER_DATA_DIR = Path(_local_app_data) / APP_NAME
else:
    USER_DATA_DIR = Path.home() / "AppData" / "Local" / APP_NAME
LOG_FILE = USER_DATA_DIR / "ime_switcher.log"

# ── Virtual-key codes ─────────────────────────────────────
VK_CAPITAL   = 0x14
VK_SHIFT     = 0x10
VK_CONTROL   = 0x11
VK_LCONTROL  = 0xA2
VK_RCONTROL  = 0xA3
VK_MENU      = 0x12   # Alt
VK_LWIN      = 0x5B
VK_RWIN      = 0x5C
VK_SPACE     = 0x20

# ── Window messages / hook constants ──────────────────────
WH_KEYBOARD_LL  = 13
WM_KEYDOWN      = 0x100
WM_SYSKEYDOWN   = 0x104
KEYEVENTF_KEYUP = 2
LLKHF_INJECTED  = 0x10
PM_REMOVE       = 1

# ── Keyboard-layout switching ─────────────────────────────
WM_INPUTLANGCHANGEREQUEST = 0x0050
KLF_NOTELLSHELL           = 0x0080

# ── Language identifiers ──────────────────────────────────
LANGID_EN_US = 0x0409
LANGID_ZH_CN = 0x0804
LAYOUT_EN_US = "00000409"
LAYOUT_ZH_CN = "00000804"

# ── IME conversion-mode control (WM_IME_CONTROL) ────────────
WM_IME_CONTROL       = 0x0283
IMC_GETCONVERSIONMODE = 0x0001
IMC_SETCONVERSIONMODE = 0x0002
IMC_GETOPENSTATUS      = 0x0005
IMC_SETOPENSTATUS      = 0x0006
SMTO_ABORTIFHUNG      = 0x0002
IME_CMODE_ALPHANUMERIC = 0x0000
IME_CMODE_NATIVE      = 0x0001   # bit 0 set = native (Chinese) conversion

# ── Misc ──────────────────────────────────────────────────
ERROR_ALREADY_EXISTS = 183

# ── Logging ───────────────────────────────────────────────
_log_format = "%(asctime)s [%(levelname)s] %(message)s"
if _TESTING:
    # Test runs emit log records; never write them to the user's real log.
    logging.basicConfig(
        level=logging.DEBUG,
        format=_log_format,
        handlers=[logging.NullHandler()],
    )
else:
    try:
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        _log_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        logging.basicConfig(
            level=logging.DEBUG,
            format=_log_format,
            handlers=[_log_handler],
        )
    except OSError:
        # Logging must never prevent the keyboard hook from starting.
        logging.basicConfig(level=logging.DEBUG, format=_log_format)

# Pillow logs every plugin it imports at DEBUG level. Keep our own DEBUG
# records while dropping that one-time startup noise.
logging.getLogger("PIL").setLevel(logging.INFO)


def log_exception() -> None:
    logging.error(traceback.format_exc())


# ── Global state ──────────────────────────────────────────
running     = True
hook_handle = None
