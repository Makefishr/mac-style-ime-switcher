"""Tk settings window owned by the application main thread.

The window is laid out like a Windows 11 settings page: grouped cards with
switches and radio rows. Controls are plain Tk widgets showing small
Pillow-rendered images, so they stay antialiased without another UI
dependency. Colors follow the system light/dark app theme.
"""

from __future__ import annotations

import ctypes
import logging
import os
import tkinter as tk
import winreg
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import messagebox

from . import config, elevation, tray
from .settings_store import SwitchingMethod, load_preferences

log = logging.getLogger(__name__)

_SUPERSAMPLE = 4
_WINDOW_WIDTH = 560  # logical pixels at 96 DPI
_WRAP = 410
_FONT_FAMILY = "Microsoft YaHei UI"

_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_CAPTION_COLOR = 35


@dataclass(frozen=True)
class _Palette:
    light: bool
    page: str
    card: str
    border: str
    divider: str
    text: str
    secondary: str
    accent: str
    accent_hover: str
    accent_pressed: str
    on_accent: str
    disabled: str
    on_disabled: str
    control: str
    button: str
    button_hover: str
    button_pressed: str
    button_border: str
    hint: str
    good: str
    good_text: str
    neutral: str
    neutral_text: str
    focus: str


_LIGHT = _Palette(
    light=True,
    page="#F3F3F3", card="#FFFFFF", border="#E5E5E5", divider="#EDEDED",
    text="#1A1A1A", secondary="#616161",
    accent="#0067C0", accent_hover="#1975C5", accent_pressed="#3183CA",
    on_accent="#FFFFFF", disabled="#C5C5C5", on_disabled="#FFFFFF",
    control="#8A8A8A",
    button="#FDFDFD", button_hover="#F6F6F6", button_pressed="#F0F0F0",
    button_border="#D5D5D5",
    hint="#E8F1FA",
    good="#DFF6DD", good_text="#0F7B0F",
    neutral="#E9E9E9", neutral_text="#5D5D5D",
    focus="#1A1A1A",
)

_DARK = _Palette(
    light=False,
    page="#202020", card="#2B2B2B", border="#1C1C1C", divider="#363636",
    text="#FFFFFF", secondary="#B5B5B5",
    accent="#4CC2FF", accent_hover="#48B2E9", accent_pressed="#42A1D2",
    on_accent="#000000", disabled="#434343", on_disabled="#A0A0A0",
    control="#A0A0A0",
    button="#2D2D2D", button_hover="#323232", button_pressed="#272727",
    button_border="#3C3C3C",
    hint="#26323C",
    good="#21361F", good_text="#6CCB5F",
    neutral="#383838", neutral_text="#C5C5C5",
    focus="#FFFFFF",
)


def _system_uses_light_theme() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except OSError:
        return True


def _colorref(color: str) -> int:
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


def _style_title_bar(window: tk.Toplevel, palette: _Palette) -> None:
    """Match the native title bar to the page; older Windows ignores this."""
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        dwmapi = ctypes.windll.dwmapi
        dark = ctypes.c_int(0 if palette.light else 1)
        dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark), ctypes.sizeof(dark),
        )
        caption = ctypes.c_uint(_colorref(palette.page))
        dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_CAPTION_COLOR, ctypes.byref(caption), ctypes.sizeof(caption),
        )
    except (AttributeError, OSError):
        log.debug("Unable to style the settings title bar", exc_info=True)


class _Art:
    """Render and cache antialiased control images for one window."""

    def __init__(self, master: tk.Misc, palette: _Palette) -> None:
        self._master = master
        self.palette = palette
        self.scale = master.winfo_fpixels("1i") / 96
        self._cache: dict[tuple, object] = {}

    def px(self, value: float) -> int:
        return max(1, round(value * self.scale))

    def _render(self, key: tuple, width: int, height: int, draw: Callable) -> object:
        if key not in self._cache:
            from PIL import Image, ImageDraw, ImageTk

            k = _SUPERSAMPLE
            image = Image.new("RGBA", (width * k, height * k), (0, 0, 0, 0))
            draw(ImageDraw.Draw(image), k)
            image = image.resize((width, height), Image.LANCZOS)
            self._cache[key] = ImageTk.PhotoImage(image, master=self._master)
        return self._cache[key]

    def switch(self, on: bool, focused: bool) -> object:
        p = self.palette
        width, height = self.px(48), self.px(28)

        def draw(d, k):
            m, w, h = self.px(4) * k, self.px(40) * k, self.px(20) * k
            if focused:
                g = self.px(3) * k
                d.rounded_rectangle(
                    (m - g, m - g, m + w + g - 1, m + h + g - 1),
                    radius=h / 2 + g, outline=p.focus, width=max(k, self.px(1.5) * k),
                )
            box = (m, m, m + w - 1, m + h - 1)
            knob = self.px(12) * k
            cy = m + h / 2
            if on:
                d.rounded_rectangle(box, radius=h / 2, fill=p.accent)
                cx = m + w - h / 2
                fill = p.on_accent
            else:
                d.rounded_rectangle(box, radius=h / 2, outline=p.control, width=k)
                cx = m + h / 2
                fill = p.control
            d.ellipse((cx - knob / 2, cy - knob / 2, cx + knob / 2, cy + knob / 2), fill=fill)

        return self._render(("switch", on, focused), width, height, draw)

    def radio(self, selected: bool, focused: bool) -> object:
        p = self.palette
        size = self.px(28)

        def draw(d, k):
            m, r = self.px(4) * k, self.px(10) * k
            c = m + r
            if focused:
                g = self.px(3) * k
                d.ellipse(
                    (c - r - g, c - r - g, c + r + g, c + r + g),
                    outline=p.focus, width=max(k, self.px(1.5) * k),
                )
            if selected:
                d.ellipse((c - r, c - r, c + r, c + r), fill=p.accent)
                dot = self.px(4) * k
                d.ellipse((c - dot, c - dot, c + dot, c + dot), fill=p.on_accent)
            else:
                d.ellipse((c - r, c - r, c + r, c + r), outline=p.control, width=k)

        return self._render(("radio", selected, focused), size, size, draw)

    def corners(self, fill: str, outline: str | None) -> tuple[object, object, object, object]:
        """Return nw, ne, sw, se images that round a panel's square corners."""
        p = self.palette
        radius = self.px(8)
        key = ("corners", fill, outline)
        if key not in self._cache:
            from PIL import Image, ImageDraw, ImageTk

            k = _SUPERSAMPLE
            full = Image.new("RGB", (radius * 2 * k, radius * 2 * k), p.page)
            ImageDraw.Draw(full).rounded_rectangle(
                (0, 0, radius * 2 * k - 1, radius * 2 * k - 1),
                radius=radius * k, fill=fill, outline=outline, width=k if outline else 0,
            )
            full = full.resize((radius * 2, radius * 2), Image.LANCZOS)
            boxes = (
                (0, 0, radius, radius),
                (radius, 0, radius * 2, radius),
                (0, radius, radius, radius * 2),
                (radius, radius, radius * 2, radius * 2),
            )
            self._cache[key] = tuple(
                ImageTk.PhotoImage(full.crop(box), master=self._master) for box in boxes
            )
        return self._cache[key]

    def pill(self, width: int, height: int, fill: str, dot: str) -> object:
        def draw(d, k):
            d.rounded_rectangle(
                (0, 0, width * k - 1, height * k - 1), radius=height * k / 2, fill=fill,
            )
            r = self.px(3) * k
            cx, cy = self.px(12) * k, height * k / 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=dot)

        return self._render(("pill", width, height, fill, dot), width, height, draw)

    def button(self, accent: bool, state: str, focused: bool) -> object:
        p = self.palette
        width, height = self.px(96), self.px(36)

        def draw(d, k):
            m = self.px(2) * k
            box = (m, m, width * k - m - 1, height * k - m - 1)
            radius = self.px(4) * k
            if focused:
                d.rounded_rectangle(
                    (0, 0, width * k - 1, height * k - 1), radius=radius + m,
                    outline=p.focus, width=max(k, self.px(1.5) * k),
                )
            if accent:
                fill = {
                    "normal": p.accent, "hover": p.accent_hover,
                    "pressed": p.accent_pressed, "disabled": p.disabled,
                }[state]
                d.rounded_rectangle(box, radius=radius, fill=fill)
            else:
                fill = {
                    "normal": p.button, "hover": p.button_hover,
                    "pressed": p.button_pressed, "disabled": p.button,
                }[state]
                d.rounded_rectangle(box, radius=radius, fill=fill, outline=p.button_border, width=k)

        return self._render(("button", accent, state, focused), width, height, draw)


class _Switch(tk.Canvas):
    """A Windows 11 style on/off switch bound to a BooleanVar."""

    def __init__(self, master: tk.Misc, art: _Art, variable: tk.BooleanVar, bg: str) -> None:
        super().__init__(
            master, width=art.px(48), height=art.px(28), bg=bg,
            highlightthickness=0, bd=0, takefocus=1,
        )
        self._art = art
        self._variable = variable
        self._focused = False
        self._image = self.create_image(0, 0, anchor="nw")
        self.bind("<Button-1>", lambda _event: self.toggle())
        self.bind("<space>", lambda _event: self.toggle())
        self.bind("<FocusIn>", lambda _event: self._set_focus(True))
        self.bind("<FocusOut>", lambda _event: self._set_focus(False))
        self._trace = variable.trace_add("write", lambda *_args: self.redraw())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.redraw()

    def toggle(self) -> None:
        self._variable.set(not self._variable.get())

    def redraw(self) -> None:
        self.itemconfigure(
            self._image, image=self._art.switch(bool(self._variable.get()), self._focused),
        )

    def _set_focus(self, focused: bool) -> None:
        self._focused = focused
        self.redraw()

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            try:
                self._variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass


class _Radio(tk.Canvas):
    """A radio indicator that selects ``value`` in a StringVar."""

    def __init__(
        self, master: tk.Misc, art: _Art, variable: tk.StringVar, value: str, bg: str,
    ) -> None:
        super().__init__(
            master, width=art.px(28), height=art.px(28), bg=bg,
            highlightthickness=0, bd=0, takefocus=1,
        )
        self._art = art
        self._variable = variable
        self._value = value
        self._focused = False
        self._image = self.create_image(0, 0, anchor="nw")
        self.bind("<Button-1>", lambda _event: self.select())
        self.bind("<space>", lambda _event: self.select())
        self.bind("<FocusIn>", lambda _event: self._set_focus(True))
        self.bind("<FocusOut>", lambda _event: self._set_focus(False))
        self._trace = variable.trace_add("write", lambda *_args: self.redraw())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.redraw()

    def select(self) -> None:
        self._variable.set(self._value)

    def redraw(self) -> None:
        selected = self._variable.get() == self._value
        self.itemconfigure(self._image, image=self._art.radio(selected, self._focused))

    def _set_focus(self, focused: bool) -> None:
        self._focused = focused
        self.redraw()

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            try:
                self._variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass


class _Button(tk.Canvas):
    """A rounded push button with hover, pressed, focus and disabled states."""

    def __init__(
        self, master: tk.Misc, art: _Art, text: str, command: Callable[[], None],
        *, accent: bool, font: tkfont.Font, bg: str,
    ) -> None:
        super().__init__(
            master, width=art.px(96), height=art.px(36), bg=bg,
            highlightthickness=0, bd=0, takefocus=1,
        )
        self._art = art
        self._accent = accent
        self._command = command
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focused = False
        self._image = self.create_image(0, 0, anchor="nw")
        self._text = self.create_text(art.px(48), art.px(18), text=text, font=font)
        self.bind("<Enter>", lambda _event: self._set(hover=True))
        self.bind("<Leave>", lambda _event: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _event: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<space>", lambda _event: self.invoke())
        self.bind("<Return>", self._activate)
        self.bind("<FocusIn>", lambda _event: self._set(focused=True))
        self.bind("<FocusOut>", lambda _event: self._set(focused=False))
        self._redraw()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(takefocus=1 if enabled else 0)
        self._redraw()

    def invoke(self) -> None:
        if self._enabled:
            self._command()

    def _activate(self, _event) -> str:
        # Stop the window-wide Return binding from also pressing Save.
        self.invoke()
        return "break"

    def _release(self, event) -> None:
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        was_pressed = self._pressed
        self._set(pressed=False)
        if was_pressed and inside:
            self.invoke()

    def _set(self, **state: bool) -> None:
        for name, value in state.items():
            setattr(self, f"_{name}", value)
        self._redraw()

    def _redraw(self) -> None:
        p = self._art.palette
        if not self._enabled:
            state = "disabled"
        elif self._pressed:
            state = "pressed"
        elif self._hover:
            state = "hover"
        else:
            state = "normal"
        self.itemconfigure(
            self._image,
            image=self._art.button(self._accent, state, self._focused and self._enabled),
        )
        if self._accent:
            color = p.on_disabled if state == "disabled" else p.on_accent
        else:
            color = p.text
        self.itemconfigure(self._text, fill=color)


class SettingsWindow:
    """Own and reuse the application's single settings window."""

    _window: tk.Toplevel | None = None
    _save_button: _Button | None = None
    _baseline: tuple | None = None
    _art: _Art | None = None
    _pill: tk.Canvas | None = None
    _hint_frame: tk.Frame | None = None
    _elevated = False

    def __init__(
        self,
        root: tk.Tk,
        on_save: Callable[[bool, bool, SwitchingMethod, bool, bool], bool],
    ) -> None:
        self._root = root
        self._on_save = on_save
        self._run_as_admin = tk.BooleanVar(master=root)
        self._autostart = tk.BooleanVar(master=root)
        self._disable_system_hotkeys = tk.BooleanVar(master=root)
        self._block_ctrl_space = tk.BooleanVar(master=root)
        self._switching_method = tk.StringVar(master=root)
        self._status = tk.StringVar(master=root)
        self._hint = tk.StringVar(master=root)
        for variable in (
            self._run_as_admin, self._autostart, self._disable_system_hotkeys,
            self._block_ctrl_space, self._switching_method,
        ):
            variable.trace_add("write", lambda *_args: self._on_change())

    def show(self) -> None:
        if (
            self._window is not None
            and self._window.winfo_exists()
            and self._art is not None
            and self._art.palette.light != _system_uses_light_theme()
        ):
            self._window.destroy()
            self._window = None
        if self._window is None or not self._window.winfo_exists():
            self._create_window()
        self.refresh_effective_state()
        assert self._window is not None
        self._window.deiconify()
        self._window.update_idletasks()
        _style_title_bar(self._window, self._art.palette)
        self._window.lift()
        self._window.focus_force()

    def hide(self) -> None:
        if self._window is not None and self._window.winfo_exists():
            self._window.withdraw()

    def refresh_effective_state(self) -> None:
        preferences = load_preferences()
        self._run_as_admin.set(preferences.run_as_administrator)
        self._autostart.set(
            tray.is_autostart_enabled() if preferences.autostart is None
            else preferences.autostart
        )
        self._switching_method.set(preferences.switching_method.value)
        self._disable_system_hotkeys.set(preferences.disable_system_hotkeys)
        self._block_ctrl_space.set(preferences.block_ctrl_space)
        self._elevated = elevation.is_elevated()
        mode = "管理员模式" if self._elevated else "普通模式"
        self._status.set(f"当前运行：{mode}")
        self._baseline = self._values()
        self._on_change()
        self._draw_pill()

    def _values(self) -> tuple:
        return (
            self._run_as_admin.get(),
            self._autostart.get(),
            self._switching_method.get(),
            self._disable_system_hotkeys.get(),
            self._block_ctrl_space.get(),
        )

    def _on_change(self) -> None:
        if self._window is None or self._baseline is None:
            return
        dirty = self._values() != self._baseline
        if self._save_button is not None:
            self._save_button.set_enabled(dirty)
        self._hint.set(self._change_hint() if dirty else "")
        if self._hint_frame is not None:
            if self._hint.get():
                self._hint_frame.grid()
            else:
                self._hint_frame.grid_remove()

    def _change_hint(self) -> str:
        """Explain the side effect that saving the pending change will have."""
        admin, autostart, _method, disable_hotkeys, _ctrl_space = self._values()
        old_admin, old_autostart, _old_method, old_disable_hotkeys, _old_ctrl_space = (
            self._baseline
        )
        if admin and not self._elevated:
            return "保存后会弹出 UAC，并以管理员身份重新启动。取消 UAC 则继续以普通模式运行。"
        if not admin and self._elevated and old_admin:
            return "保存后，下次启动将以普通模式运行；本次仍保持管理员模式。"
        if autostart and not old_autostart and admin:
            return "将创建登录计划任务，以后登录时自动以管理员身份运行，不再弹出 UAC。"
        if old_disable_hotkeys and not disable_hotkeys:
            return "将恢复系统原有的语言切换快捷键；若未生效，请注销后重新登录。"
        return ""

    def _draw_pill(self) -> None:
        if self._pill is None or self._art is None or not self._pill.winfo_exists():
            return
        art, p = self._art, self._art.palette
        text = "管理员模式" if self._elevated else "普通模式"
        fill, color = (p.good, p.good_text) if self._elevated else (p.neutral, p.neutral_text)
        font = self._fonts["small"]
        height = art.px(26)
        width = art.px(30) + font.measure(text)
        self._pill.delete("all")
        self._pill.configure(width=width, height=height)
        self._pill.create_image(0, 0, anchor="nw", image=art.pill(width, height, fill, color))
        self._pill.create_text(
            art.px(20), height // 2, anchor="w", text=text, fill=color, font=font,
        )

    # ── Layout ────────────────────────────────────────────

    def _create_window(self) -> None:
        palette = _LIGHT if _system_uses_light_theme() else _DARK
        window = tk.Toplevel(self._root, bg=palette.page)
        self._window = window
        window.withdraw()
        window.title(f"{config.APP_TITLE} 设置")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._cancel)
        icon = tray.app_icon_path()
        try:
            window.iconbitmap(icon)
        except tk.TclError:
            log.debug("Unable to set settings window icon %s", icon, exc_info=True)

        art = _Art(window, palette)
        self._art = art
        px = art.px
        self._fonts = {
            "title": tkfont.Font(root=self._root, family=_FONT_FAMILY, size=14, weight="bold"),
            "section": tkfont.Font(root=self._root, family=_FONT_FAMILY, size=10, weight="bold"),
            "body": tkfont.Font(root=self._root, family=_FONT_FAMILY, size=10),
            "small": tkfont.Font(root=self._root, family=_FONT_FAMILY, size=9),
        }

        window.columnconfigure(0, minsize=px(_WINDOW_WIDTH))
        outer = tk.Frame(window, bg=palette.page, padx=px(24), pady=px(18))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        self._build_header(outer, art).grid(row=0, column=0, sticky="ew")

        self._section(outer, 1, "切换方式")
        card = self._card(outer, 2)
        self._radio_row(
            card, 0, "切换键盘输入法",
            "CapsLock 在英文键盘和微软拼音之间切换。",
            SwitchingMethod.KEYBOARD_LAYOUTS.value,
        )
        self._divider(card, 1)
        self._radio_row(
            card, 2, "仅切换微软拼音的中/英文模式",
            "始终停留在微软拼音，CapsLock 只切换中/英文。\n"
            "使用前请先切到微软拼音；部分现代应用可能不支持。",
            SwitchingMethod.MICROSOFT_PINYIN_MODE.value,
        )
        self._round_corners(card)

        self._section(outer, 3, "快捷键")
        card = self._card(outer, 4)
        self._switch_row(
            card, 0, "关闭系统语言切换快捷键",
            "Ctrl+Shift 和 Alt+Shift 不再切换输入法，避免与 CapsLock 冲突。\n"
            "关闭此项会恢复原来的系统设置。",
            self._disable_system_hotkeys,
        )
        self._divider(card, 1)
        self._switch_row(
            card, 2, "拦截 Ctrl+Space",
            "关闭后，编辑器里的 Ctrl+Space 代码补全可以正常使用。\n"
            "Win+Space 始终拦截。",
            self._block_ctrl_space,
        )
        self._round_corners(card)

        self._section(outer, 5, "运行与启动")
        card = self._card(outer, 6)
        self._switch_row(
            card, 0, "以管理员身份运行",
            "在以管理员身份运行的程序中也能切换输入法。",
            self._run_as_admin,
        )
        self._divider(card, 1)
        self._switch_row(
            card, 2, "开机时自动运行",
            "管理员模式下使用登录计划任务，登录时不会弹出 UAC。",
            self._autostart,
        )
        self._round_corners(card)

        hint = tk.Frame(outer, bg=palette.hint, padx=px(14), pady=px(10))
        hint.grid(row=7, column=0, sticky="ew", pady=(px(14), 0))
        hint.columnconfigure(1, weight=1)
        tk.Label(
            hint, text="ⓘ", bg=palette.hint, fg=palette.accent, font=self._fonts["body"],
        ).grid(row=0, column=0, sticky="n", padx=(0, px(8)))
        tk.Label(
            hint, textvariable=self._hint, bg=palette.hint, fg=palette.text,
            font=self._fonts["small"], justify="left", anchor="w",
            wraplength=px(_WINDOW_WIDTH - 110),
        ).grid(row=0, column=1, sticky="w")
        self._round_corners(hint, palette.hint)
        hint.grid_remove()
        self._hint_frame = hint

        self._build_footer(outer, art).grid(row=8, column=0, sticky="ew", pady=(px(16), 0))

        window.bind("<Escape>", lambda _event: self._cancel())
        window.bind("<Return>", lambda _event: self._save_button and self._save_button.invoke())

        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 3)
        window.geometry(f"+{x}+{y}")

    def _build_header(self, parent: tk.Frame, art: _Art) -> tk.Frame:
        p, px = art.palette, art.px
        header = tk.Frame(parent, bg=p.page)
        header.columnconfigure(1, weight=1)
        logo = self._logo_image(art)
        if logo is not None:
            tk.Label(header, image=logo, bg=p.page).grid(
                row=0, column=0, rowspan=2, padx=(0, px(14)),
            )
        tk.Label(
            header, text=config.APP_TITLE, bg=p.page, fg=p.text, font=self._fonts["title"],
        ).grid(row=0, column=1, sticky="sw")
        tk.Label(
            header, text=f"v{config.VERSION}  ·  用 CapsLock 切换中英文", bg=p.page,
            fg=p.secondary, font=self._fonts["small"],
        ).grid(row=1, column=1, sticky="nw")
        self._pill = tk.Canvas(header, bg=p.page, highlightthickness=0, bd=0)
        self._pill.grid(row=0, column=2, rowspan=2, sticky="e")
        return header

    def _logo_image(self, art: _Art) -> object | None:
        try:
            from PIL import Image, ImageTk

            with Image.open(tray.app_icon_path()) as icon:
                sizes = icon.info.get("sizes")
                if sizes:
                    icon.size = max(sizes)  # pick the largest ICO frame
                image = icon.convert("RGBA").resize((art.px(44), art.px(44)), Image.LANCZOS)
            self._logo = ImageTk.PhotoImage(image, master=self._window)
            return self._logo
        except (OSError, ValueError):
            log.debug("Unable to load settings window logo", exc_info=True)
            return None

    def _build_footer(self, parent: tk.Frame, art: _Art) -> tk.Frame:
        p, px = art.palette, art.px
        footer = tk.Frame(parent, bg=p.page)
        footer.columnconfigure(0, weight=1)
        link = tk.Label(
            footer, text="打开日志文件夹", bg=p.page, fg=p.accent,
            font=self._fonts["small"], cursor="hand2",
        )
        link.grid(row=0, column=0, sticky="w")
        link.bind("<Button-1>", lambda _event: self._open_data_folder())
        _Button(
            footer, art, "取消", self._cancel, accent=False,
            font=self._fonts["body"], bg=p.page,
        ).grid(row=0, column=1, padx=(0, px(4)))
        self._save_button = _Button(
            footer, art, "保存", self._save, accent=True,
            font=self._fonts["body"], bg=p.page,
        )
        self._save_button.grid(row=0, column=2)
        return footer

    def _section(self, parent: tk.Frame, row: int, text: str) -> None:
        p, px = self._art.palette, self._art.px
        tk.Label(
            parent, text=text, bg=p.page, fg=p.text, font=self._fonts["section"],
        ).grid(row=row, column=0, sticky="w", padx=(px(2), 0), pady=(px(16), px(6)))

    def _card(self, parent: tk.Frame, row: int) -> tk.Frame:
        p = self._art.palette
        card = tk.Frame(
            parent, bg=p.card, highlightthickness=1,
            highlightbackground=p.border, highlightcolor=p.border,
        )
        card.grid(row=row, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        return card

    def _round_corners(
        self, panel: tk.Frame, fill: str | None = None, outline: str | None = None,
    ) -> None:
        p = self._art.palette
        if fill is None:
            fill, outline = p.card, p.border
        anchors = (("nw", 0, 0), ("ne", 1, 0), ("sw", 0, 1), ("se", 1, 1))
        for image, (anchor, relx, rely) in zip(self._art.corners(fill, outline), anchors):
            corner = tk.Label(panel, image=image, bd=0, highlightthickness=0)
            corner.place(relx=relx, rely=rely, anchor=anchor, bordermode="outside")
            corner.lift()

    def _divider(self, card: tk.Frame, row: int) -> None:
        p, px = self._art.palette, self._art.px
        tk.Frame(card, bg=p.divider, height=1).grid(
            row=row, column=0, sticky="ew", padx=px(16),
        )

    def _texts(self, row: tk.Frame, column: int, title: str, description: str) -> list:
        p, px = self._art.palette, self._art.px
        heading = tk.Label(
            row, text=title, bg=p.card, fg=p.text, font=self._fonts["body"], anchor="w",
        )
        heading.grid(row=0, column=column, sticky="w")
        detail = tk.Label(
            row, text=description, bg=p.card, fg=p.secondary, font=self._fonts["small"],
            justify="left", anchor="w", wraplength=px(_WRAP),
        )
        detail.grid(row=1, column=column, sticky="w", pady=(px(2), 0))
        return [heading, detail]

    def _switch_row(
        self, card: tk.Frame, row: int, title: str, description: str,
        variable: tk.BooleanVar,
    ) -> None:
        p, px = self._art.palette, self._art.px
        # The switch image carries a 4 px focus margin, so trim the right pad.
        frame = tk.Frame(card, bg=p.card, padx=0, pady=px(10))
        frame.grid(row=row, column=0, sticky="ew", padx=(px(16), px(12)))
        frame.columnconfigure(0, weight=1)
        labels = self._texts(frame, 0, title, description)
        switch = _Switch(frame, self._art, variable, p.card)
        switch.grid(row=0, column=1, rowspan=2, padx=(px(12), 0))
        for widget in (frame, *labels):
            widget.bind("<Button-1>", lambda _event: switch.toggle())

    def _radio_row(
        self, card: tk.Frame, row: int, title: str, description: str, value: str,
    ) -> None:
        p, px = self._art.palette, self._art.px
        frame = tk.Frame(card, bg=p.card, padx=px(12), pady=px(10))
        frame.grid(row=row, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)
        radio = _Radio(frame, self._art, self._switching_method, value, p.card)
        radio.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, px(8)))
        labels = self._texts(frame, 1, title, description)
        for widget in (frame, *labels):
            widget.bind("<Button-1>", lambda _event: radio.select())

    # ── Actions ───────────────────────────────────────────

    def _open_data_folder(self) -> None:
        try:
            os.startfile(config.USER_DATA_DIR)
        except OSError as exc:
            log.exception("Unable to open %s", config.USER_DATA_DIR)
            messagebox.showerror("无法打开文件夹", str(exc), parent=self._window)

    def _cancel(self) -> None:
        self.refresh_effective_state()
        self.hide()

    def _save(self) -> None:
        assert self._save_button is not None
        self._save_button.set_enabled(False)
        try:
            should_close = self._on_save(
                self._run_as_admin.get(),
                self._autostart.get(),
                SwitchingMethod(self._switching_method.get()),
                self._disable_system_hotkeys.get(),
                self._block_ctrl_space.get(),
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
