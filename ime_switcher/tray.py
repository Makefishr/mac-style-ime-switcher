"""System-tray icon, menu, and registry auto-start."""

import logging
import os
import sys
import winreg

from . import config
from . import toggle

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


def _tray_label() -> str:
    is_cn = toggle.get_ime_status()
    return f"IME: {'中' if is_cn else 'En'}"


# ── Auto-start (registry) ─────────────────────────────────

def _exe_path() -> str:
    if getattr(sys, 'frozen', False):
        return sys.executable
    # Running from source — reconstruct the invocation.
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "__main__.py"))
    return f'"{sys.executable}" "{main}"'


def is_autostart_enabled() -> bool:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_READ,
        )
        winreg.QueryValueEx(key, config.APP_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False


def install_autostart() -> None:
    path = _exe_path()
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_SET_VALUE,
    )
    winreg.SetValueEx(key, config.APP_NAME, 0, winreg.REG_SZ, path)
    winreg.CloseKey(key)
    log.info("Auto-start installed: %s", path)
    print(f"已添加开机自启: {path}")


def remove_autostart() -> None:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, config.APP_NAME)
        winreg.CloseKey(key)
        log.info("Auto-start removed")
        print("已移除开机自启")
    except FileNotFoundError:
        pass


# ── Menu callbacks ────────────────────────────────────────

def _on_quit(icon, _item) -> None:
    config.running = False
    icon.stop()
    log.info("User quit from tray menu")


def _on_toggle_autostart(icon, _item) -> None:
    if is_autostart_enabled():
        remove_autostart()
        icon.notify("已关闭开机自启")
    else:
        install_autostart()
        icon.notify("已开启开机自启")


def _on_check_status(icon, _item) -> None:
    icon.notify(_tray_label())


# ── Menu builder ──────────────────────────────────────────

def build_menu():
    import pystray

    auto_on = is_autostart_enabled()
    return pystray.Menu(
        pystray.MenuItem("查看当前输入法状态", _on_check_status),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            f"开机自启: {'✓ 开' if auto_on else '✗ 关'}",
            _on_toggle_autostart,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _on_quit),
    )
