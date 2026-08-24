"""System-tray icon and the small settings menu."""

import logging
import os
import sys

from . import config, settings

log = logging.getLogger(__name__)


def _make_tray_image():
    from PIL import Image

    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ico = Image.open(os.path.join(base, "app.ico"))
    return ico.resize((64, 64), Image.LANCZOS)


# Keep the CLI helpers available while keeping registry details out of tray UI.
def is_autostart_enabled() -> bool:
    return settings.is_autostart_enabled()


def install_autostart() -> None:
    settings.install_autostart()


def remove_autostart() -> None:
    settings.remove_autostart()


def _on_settings(_icon, _item) -> None:
    settings.show_settings()


def _on_quit(icon, _item) -> None:
    config.running = False
    icon.stop()
    log.info("用户从托盘退出")


def build_menu():
    import pystray

    return pystray.Menu(
        pystray.MenuItem("设置", _on_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _on_quit),
    )
