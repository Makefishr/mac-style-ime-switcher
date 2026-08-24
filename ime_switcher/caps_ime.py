"""CapsLock-based IME switching — a single deep module.

The entire CapsLock decision tree (short-vs-long detection, IME toggle,
LED management, thread dispatch) sits behind the keyboard hook entry point.
"""
import enum
import logging
import queue
import threading
import time
from typing import Protocol


VK_CAPITAL = 0x14
KEYEVENTF_KEYUP = 0x0002
ACTION_QUEUE_CAPACITY = 8
log = logging.getLogger(__name__)


class IMEState(enum.Enum):
    ENGLISH = "En"
    CHINESE = "中"


# ── Adapter protocols ────────────────────────────────────────


class IMEAdapter(Protocol):
    def can_toggle(self) -> bool: ...
    def toggle(self) -> bool: ...
    def current_state(self) -> IMEState: ...


class LEDAdapter(Protocol):
    def off(self) -> None: ...
    def on(self) -> None: ...


class NativeCapsLockAdapter(Protocol):
    def tap(self) -> None: ...


# ── Production adapters ──────────────────────────────────────


class _Win32IME:
    def can_toggle(self) -> bool:
        from . import settings, toggle
        try:
            return toggle.can_switch(settings.get_settings().mode)
        except Exception as exc:
            log.error("IME scope adapter failed (%s)", type(exc).__name__)
            return False

    def toggle(self) -> bool:
        from . import settings, toggle
        try:
            return toggle.switch_ime(settings.get_settings().mode)
        except Exception as exc:
            log.error("IME switch adapter failed (%s)", type(exc).__name__)
            return False

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


class Win32NativeCapsLock:
    def __init__(self, key_event=None) -> None:
        self._key_event = key_event

    def tap(self) -> None:
        if self._key_event is None:
            from .winapi import user32
            key_event = user32.keybd_event
        else:
            key_event = self._key_event
        key_event(VK_CAPITAL, 0, 0, 0)
        key_event(VK_CAPITAL, 0, KEYEVENTF_KEYUP, 0)


# ── Engine ───────────────────────────────────────────────────


class CapsLockIME:
    """Consume each physical CapsLock press as one macOS-style gesture.

    A supported short press toggles IME; a supported long press enables
    uppercase. Unsupported, unknown, or failed gestures replay one native
    CapsLock tap after keyup. Pressing CapsLock while uppercase is active
    disables uppercase.

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
        native_adapter: NativeCapsLockAdapter | None = None,
        clock=None,
    ):
        self._threshold = long_press_threshold
        self._clock = clock or time.monotonic
        self._ime = ime_adapter or _Win32IME()
        self._led = led_adapter or _Win32LED()
        self._native = native_adapter or Win32NativeCapsLock()
        self._main_lock = threading.Lock()
        self._press_started_at: float | None = None
        self._mode_lock = threading.Lock()
        self._uppercase_mode = False
        self._action_queue: queue.Queue = queue.Queue(
            maxsize=ACTION_QUEUE_CAPACITY,
        )
        self._action_state_lock = threading.Lock()
        self._action_generation = 0
        self._worker_start_lock = threading.Lock()
        self._worker_started = False

    # ── Public ────────────────────────────────────────────────

    def on_key_event(self, vk_code: int, is_down: bool) -> bool:
        if vk_code != VK_CAPITAL:
            return False

        if is_down:
            return self._on_down()
        else:
            return self._on_up()

    def reset(self) -> None:
        """Cancel an incomplete CapsLock gesture at a hook lifecycle edge."""
        self._press_started_at = None
        with self._mode_lock:
            self._uppercase_mode = False
        if self._main_lock.locked():
            self._main_lock.release()
        self._reset_actions(self._led.off)

    @property
    def ime_state(self) -> IMEState:
        return self._ime.current_state()

    # ── Internal: down / up ───────────────────────────────────

    def _on_down(self) -> bool:
        if not self._main_lock.acquire(blocking=False):
            return True  # already tracking a press

        self._press_started_at = self._clock()
        return True

    def _on_up(self) -> bool:
        if not self._main_lock.locked():
            return True

        started_at = self._press_started_at
        self._press_started_at = None
        self._main_lock.release()
        elapsed = 0.0 if started_at is None else self._clock() - started_at
        if elapsed >= self._threshold:
            self._dispatch(self._long_press_action)
        else:
            self._dispatch(self._short_press_action)
        return True

    # ── Actions (run on daemon thread) ────────────────────────

    def _can_toggle(self) -> bool:
        checker = getattr(self._ime, "can_toggle", None)
        if checker is None:
            return False
        try:
            result = checker()
        except Exception as exc:
            log.error(
                "Unable to determine IME scope (%s)",
                type(exc).__name__,
            )
            return False
        if result is True:
            return True
        if result is False:
            return False
        log.warning("Unrecognized IME scope result; using native CapsLock")
        return False

    def _short_press_action(self, generation: int) -> None:
        if self._turn_off_uppercase(generation):
            return
        can_toggle = self._can_toggle()
        if not self._is_generation_current(generation):
            return
        if can_toggle:
            try:
                toggled = self._ime.toggle()
            except Exception as exc:
                log.error("IME toggle failed (%s)", type(exc).__name__)
                toggled = False
            if toggled is not True:
                self._replay_native()
            self._led.off()
        else:
            self._replay_native()

    def _long_press_action(self, generation: int) -> None:
        if self._turn_off_uppercase(generation):
            return
        can_toggle = self._can_toggle()
        if not self._is_generation_current(generation):
            return
        if can_toggle:
            with self._mode_lock:
                self._uppercase_mode = True
            self._led.on()
        else:
            self._replay_native()

    def _turn_off_uppercase(self, generation: int) -> bool:
        with self._mode_lock:
            if not self._uppercase_mode:
                return False
            self._uppercase_mode = False
        if self._is_generation_current(generation):
            self._led.off()
        return True

    # ── Helpers ───────────────────────────────────────────────

    def _replay_native(self) -> bool:
        try:
            self._native.tap()
            return True
        except Exception as exc:
            log.error(
                "Native CapsLock replay failed (%s)",
                type(exc).__name__,
            )
            return False

    def _dispatch(self, action) -> None:
        self._ensure_worker()
        with self._action_state_lock:
            item = (self._action_generation, action)
            try:
                self._action_queue.put_nowait(item)
            except queue.Full:
                log.info("CapsLock action dropped while previous actions are busy")

    def _reset_actions(self, final_action) -> None:
        with self._action_state_lock:
            self._action_generation += 1
            while True:
                try:
                    self._action_queue.get_nowait()
                except queue.Empty:
                    break
                else:
                    self._action_queue.task_done()
            self._action_queue.put_nowait(
                (
                    self._action_generation,
                    lambda _generation: final_action(),
                ),
            )
        self._ensure_worker()

    def _is_generation_current(self, generation: int) -> bool:
        with self._action_state_lock:
            return generation == self._action_generation

    def _ensure_worker(self) -> None:
        with self._worker_start_lock:
            if self._worker_started:
                return
            worker = threading.Thread(
                target=self._worker_main,
                name="CapsLockAction",
                daemon=True,
            )
            worker.start()
            self._worker_started = True

    def _worker_main(self) -> None:
        while True:
            generation, action = self._action_queue.get()
            try:
                with self._action_state_lock:
                    is_current = generation == self._action_generation
                if is_current:
                    self._safe_run(action, generation)
            finally:
                self._action_queue.task_done()

    def _safe_run(self, action, generation: int) -> None:
        try:
            action(generation)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Action failed")


# ── Module-level singleton ───────────────────────────────────
engine = CapsLockIME()
