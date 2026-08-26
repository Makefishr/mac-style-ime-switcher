"""Tk/ttk settings window owned by the application main thread."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from . import config, elevation, tray
from .settings_store import SwitchingMethod, load_preferences

log = logging.getLogger(__name__)


class SettingsWindow:
    """Own and reuse the application's single settings window."""

    def __init__(
        self,
        root: tk.Tk,
        on_save: Callable[[bool, bool, SwitchingMethod], bool],
    ) -> None:
        self._root = root
        self._on_save = on_save
        self._window: tk.Toplevel | None = None
        self._run_as_admin = tk.BooleanVar(master=root)
        self._autostart = tk.BooleanVar(master=root)
        self._switching_method = tk.StringVar(master=root)
        self._status = tk.StringVar(master=root)
        self._save_button: ttk.Button | None = None

    def show(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            self._create_window()
        self.refresh_effective_state()
        assert self._window is not None
        self._window.deiconify()
        self._window.lift()
        self._window.focus_set()

    def hide(self) -> None:
        if self._window is not None and self._window.winfo_exists():
            self._window.withdraw()

    def refresh_effective_state(self) -> None:
        preferences = load_preferences()
        self._run_as_admin.set(preferences.run_as_administrator)
        self._autostart.set(tray.is_autostart_enabled())
        self._switching_method.set(preferences.switching_method.value)
        mode = "管理员模式" if elevation.is_elevated() else "普通模式"
        self._status.set(f"当前运行：{mode}")

    def _create_window(self) -> None:
        window = tk.Toplevel(self._root)
        self._window = window
        window.withdraw()
        window.title(f"{config.APP_TITLE} 设置")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._cancel)

        outer = ttk.Frame(window, padding=18)
        outer.grid(row=0, column=0, sticky="nsew")

        run_frame = ttk.LabelFrame(outer, text="运行模式", padding=(12, 10))
        run_frame.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            run_frame,
            text="以管理员身份运行",
            variable=self._run_as_admin,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            run_frame,
            text=(
                "启用后会请求 Windows 管理员权限，以便在管理员应用中切换输入法。\n"
                "若取消或启动失败，将继续以普通模式运行。"
            ),
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 4))
        ttk.Label(run_frame, textvariable=self._status).grid(
            row=2, column=0, sticky="w", pady=(2, 0)
        )

        switching_frame = ttk.LabelFrame(outer, text="输入法切换方式", padding=(12, 10))
        switching_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Radiobutton(
            switching_frame,
            text="切换键盘输入法",
            variable=self._switching_method,
            value=SwitchingMethod.KEYBOARD_LAYOUTS.value,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            switching_frame,
            text="在英文键盘和微软拼音之间切换。",
        ).grid(row=1, column=0, sticky="w", padx=(20, 0), pady=(2, 8))
        ttk.Radiobutton(
            switching_frame,
            text="仅切换微软拼音的中/英文模式",
            variable=self._switching_method,
            value=SwitchingMethod.MICROSOFT_PINYIN_MODE.value,
        ).grid(row=2, column=0, sticky="w")
        ttk.Label(
            switching_frame,
            text=(
                "CapsLock 不会更换键盘；使用前请先切到微软拼音。\n"
                "使用兼容接口切换，部分现代应用可能不支持。"
            ),
            justify="left",
        ).grid(row=3, column=0, sticky="w", padx=(20, 0), pady=(2, 0))

        startup_frame = ttk.LabelFrame(outer, text="启动", padding=(12, 10))
        startup_frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(
            startup_frame,
            text="开机时自动运行",
            variable=self._autostart,
        ).grid(row=0, column=0, sticky="w")

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self._cancel).grid(row=0, column=0)
        self._save_button = ttk.Button(buttons, text="保存", command=self._save)
        self._save_button.grid(row=0, column=1, padx=(8, 0))

        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 3)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _cancel(self) -> None:
        self.refresh_effective_state()
        self.hide()

    def _save(self) -> None:
        assert self._save_button is not None
        self._save_button.state(["disabled"])
        try:
            should_close = self._on_save(
                self._run_as_admin.get(),
                self._autostart.get(),
                SwitchingMethod(self._switching_method.get()),
            )
            self.refresh_effective_state()
            if should_close:
                self.hide()
        except Exception as exc:
            log.exception("Unable to save settings")
            messagebox.showerror(
                "无法保存设置",
                f"设置未能完整保存。请重试或查看日志。\n\n{exc}",
                parent=self._window,
            )
            self.refresh_effective_state()
        finally:
            if self._save_button.winfo_exists():
                self._save_button.state(["!disabled"])
