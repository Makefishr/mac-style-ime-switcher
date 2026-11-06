"""System-tray icon, menu, and startup-management entry points."""

import logging
import os
import sys
from dataclasses import replace

from . import config
from .autostart import WindowsStartup
from .settings_store import load_preferences, save_preferences

log = logging.getLogger(__name__)


# ── Tray icon (PIL) ───────────────────────────────────────

def _make_tray_image():
    from PIL import Image

    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ico = Image.open(os.path.join(base, "app.ico"))
    return ico.resize((64, 64), Image.LANCZOS)


# ── Auto-start ───────────────────────────────────────────

def is_autostart_enabled() -> bool:
    return WindowsStartup().is_enabled()


def install_autostart() -> None:
    preferences = load_preferences()
    set_autostart_enabled(True, preferences.run_as_administrator)
    save_preferences(replace(preferences, autostart=True))


def remove_autostart() -> None:
    preferences = load_preferences()
    set_autostart_enabled(False, preferences.run_as_administrator)
    save_preferences(replace(preferences, autostart=False))


# ── Menu builder ──────────────────────────────────────────

def set_autostart_enabled(enabled: bool, administrator: bool = False) -> None:
    WindowsStartup().apply(enabled, administrator)


def build_menu(on_open_settings, on_quit):
    import pystray

    return pystray.Menu(
        pystray.MenuItem("设置...", lambda _icon, _item: on_open_settings()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", lambda _icon, _item: on_quit()),
    )
