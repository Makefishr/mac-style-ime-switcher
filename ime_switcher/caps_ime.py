"""CapsLock-based IME switching — a single deep module.

The entire CapsLock decision tree (short-vs-long detection, IME toggle,
LED management, thread dispatch) sits behind two public entry points:
``on_key_event`` for the keyboard hook and ``ime_state`` for the tray.
"""
import enum
import threading
from typing import Protocol


VK_CAPITAL = 0x14


class IMEState(enum.Enum):
    ENGLISH = "En"
    CHINESE = "中"


# ── Adapter protocols ────────────────────────────────────────


class IMEAdapter(Protocol):
    def toggle(self) -> None: ...
    def current_state(self) -> IMEState: ...


class LEDAdapter(Protocol):
    def off(self) -> None: ...
    def on(self) -> None: ...


# ── Production adapters ──────────────────────────────────────


class _Win32IME:
    def toggle(self) -> None:
        from . import toggle
        toggle.switch_ime()

    def current_state(self) -> IMEState:
        from . import toggle
        return IMEState.CHINESE if toggle.get_ime_status() else IMEState.ENGLISH


class _Win32LED:
    def off(self) -> None:
        from .winapi import user32
        from . import config
        if user32.GetKeyState(config.VK_CAPITAL) & 1:
            user32.keybd_event(config.VK_CAPITAL, 0, 0, 0)
            user32.keybd_event(config.VK_CAPITAL, 0, config.KEYEVENTF_KEYUP, 0)

    def on(self) -> None:
        from .winapi import user32
        from . import config
        if not (user32.GetKeyState(config.VK_CAPITAL) & 1):
            user32.keybd_event(config.VK_CAPITAL, 0, 0, 0)
            user32.keybd_event(config.VK_CAPITAL, 0, config.KEYEVENTF_KEYUP, 0)


# ── Engine ───────────────────────────────────────────────────


class CapsLockIME:
    """CapsLock IME switcher with macOS-style dual-action.

    Short press (< 1 s*)  → toggle IME, LED stays off.
    Long press (>= 1 s*) → enable uppercase, LED on.
    When uppercase is already on, pressing CapsLock passes through.

    All I/O runs on a daemon thread so ``on_key_event`` never blocks.
    The *threshold is configurable but defaults to 1.0 seconds.

    Thread-safe: the hook thread calls ``on_key_event``; a daemon
    thread runs IME/LED side-effects.
    """

    def __init__(
        self,
        *,
        long_press_threshold: float = 1.0,
        ime_adapter: IMEAdapter | None = None,
        led_adapter: LEDAdapter | None = None,
    ):
        self._threshold = long_press_threshold
        self._ime = ime_adapter or _Win32IME()
        self._led = led_adapter or _Win32LED()
        self._main_lock = threading.Lock()
        self._resolve_lock: threading.Lock | None = None
        self._timer: threading.Timer | None = None
        self._uppercase_mode = False

    # ── Public ────────────────────────────────────────────────

    def on_key_event(self, vk_code: int, is_down: bool) -> bool:
        if vk_code != VK_CAPITAL:
            return False

        if is_down:
            return self._on_down()
        else:
            return self._on_up()

    @property
    def ime_state(self) -> IMEState:
        return self._ime.current_state()

    # ── Internal: down / up ───────────────────────────────────

    def _on_down(self) -> bool:
        if self._uppercase_mode:
            self._uppercase_mode = False
            self._dispatch(self._led.off)
            return False  # pass through — let OS turn CapsLock off

        if not self._main_lock.acquire(blocking=False):
            return True  # already tracking a press

        self._resolve_lock = threading.Lock()
        self._timer = threading.Timer(self._threshold, self._on_timer_expiry)
        self._timer.start()
        return True

    def _on_up(self) -> bool:
        if not self._main_lock.locked():
            return False

        if self._resolve_lock is not None and self._resolve_lock.acquire(blocking=False):
            # UP won the race → short press
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._main_lock.release()
            self._dispatch(self._short_press_action)
            return True  # eat UP
        else:
            # Timer already fired → long press
            self._main_lock.release()
            self._dispatch(self._long_press_action)
            return False  # pass UP through

    def _on_timer_expiry(self) -> None:
        if self._resolve_lock is None:
            return
        if not self._resolve_lock.acquire(blocking=False):
            return  # short press resolved first
        self._timer = None
        # _main_lock stays held → _on_up will fire the long action

    # ── Actions (run on daemon thread) ────────────────────────

    def _short_press_action(self) -> None:
        self._ime.toggle()
        self._led.off()

    def _long_press_action(self) -> None:
        self._uppercase_mode = True
        self._led.on()

    # ── Helpers ───────────────────────────────────────────────

    def _dispatch(self, action) -> None:
        threading.Thread(target=self._safe_run, args=(action,), daemon=True).start()

    def _safe_run(self, action) -> None:
        try:
            action()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Action failed")


# ── Module-level singleton ───────────────────────────────────
engine = CapsLockIME()
