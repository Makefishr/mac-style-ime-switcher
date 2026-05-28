"""Entry point for Mac-style IME Switcher."""

import ctypes
import logging
import os
import sys
import threading
import time

# When running from source, ensure the project root is on sys.path.
if not getattr(sys, 'frozen', False):
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

from ime_switcher import config
from ime_switcher import hook
from ime_switcher import tray
from ime_switcher.winapi import kernel32

log = logging.getLogger(__name__)


def _check_single_instance() -> bool:
    mutex_name = f"Global\\{config.APP_NAME}_SingleInstance_{config.VERSION}"
    handle = kernel32.CreateMutexW(None, True, mutex_name)
    if kernel32.GetLastError() == config.ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return False
    log.info("Single-instance mutex acquired")
    return True


def run_background() -> None:
    import pystray

    # Start the keyboard-hook thread.
    t = threading.Thread(target=hook.hook_thread_main, daemon=True, name="Hook")
    t.start()
    time.sleep(0.2)

    if not config.hook_handle:
        log.error("Hook installation failed — exiting")
        print("错误: 键盘钩子安装失败，请尝试以管理员身份运行")
        return

    # System tray.
    icon = pystray.Icon(
        config.APP_NAME,
        tray._make_tray_image(),
        config.APP_TITLE,
        tray.build_menu(),
    )

    log.info("Entering tray-icon main loop")
    try:
        icon.run()
    except KeyboardInterrupt:
        pass
    finally:
        config.running = False
        log.info("%s exiting", config.APP_NAME)


def main() -> None:
    if not _check_single_instance():
        print("程序已在运行中")
        log.warning("Duplicate instance rejected")
        return

    log.info("=== %s v%s starting ===", config.APP_NAME, config.VERSION)

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "--install":
            tray.install_autostart()
        elif cmd == "--uninstall":
            tray.remove_autostart()
        elif cmd in ("--help", "-h"):
            print(__doc__ or "")
            print(f"Mac-style IME Switcher v{config.VERSION}")
            print("  --install    添加开机自启")
            print("  --uninstall  移除开机自启")
            print("  --help       显示此帮助")
        else:
            print(f"未知命令: {cmd}")
            print("用法: MacStyleIME.exe [--install|--uninstall|--help]")
        return

    run_background()


if __name__ == "__main__":
    main()
