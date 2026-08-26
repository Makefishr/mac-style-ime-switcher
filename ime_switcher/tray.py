"""System-tray icon, menu, and registry auto-start."""

import logging
import os
import sys
import winreg

from . import config

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


# ── Auto-start (registry) ─────────────────────────────────

def _exe_path() -> str:
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    # Running from source — reconstruct the invocation.
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "__main__.py"))
    return f'"{sys.executable}" "{main}"'


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_READ,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, config.APP_NAME)
        return value_type == winreg.REG_SZ and value == _exe_path()
    except FileNotFoundError:
        return False


def install_autostart() -> None:
    path = _exe_path()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, config.APP_NAME, 0, winreg.REG_SZ, path)
    log.info("Auto-start installed: %s", path)
    print(f"已添加开机自启: {path}")


def remove_autostart() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, config.APP_NAME)
        log.info("Auto-start removed")
        print("已移除开机自启")
    except FileNotFoundError:
        pass


# ── Menu builder ──────────────────────────────────────────

def set_autostart_enabled(enabled: bool) -> None:
    if enabled:
        install_autostart()
    else:
        remove_autostart()


def build_menu(on_open_settings, on_quit):
    import pystray

    return pystray.Menu(
        pystray.MenuItem("设置...", lambda _icon, _item: on_open_settings()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", lambda _icon, _item: on_quit()),
    )
