"""CapsLock state machine for dual-action key: short=toggle IME, long=uppercase."""

import threading


class CapsLockHandler:
    """Detect short vs long CapsLock presses.

    - Short press (< *long_press_seconds*): calls ``on_short_press()``
    - Long press (>= *long_press_seconds*): calls ``on_long_press()``
    - When CapsLock is already ON, ``handle_down(True)`` returns False
      so the caller can pass the keystroke through to the system.

    ``handle_up`` returns a string the caller uses to decide whether
    to eat the key-up event:

    - ``"short"``  — eat the UP; short-press action was invoked
    - ``"long"``   — let UP pass through; long-press action was invoked
    - ``"none"``   — not in a detection cycle; let UP pass through

    Thread-safe.  The timer callback and ``handle_up`` race to resolve
    the press; a fresh ``threading.Lock`` per cycle ensures exactly one
    of the two actions is invoked.
    """

    def __init__(self, on_short_press, on_long_press, long_press_seconds=1.0):
        self._on_short_press = on_short_press
        self._on_long_press = on_long_press
        self._long_press_seconds = long_press_seconds

        self._main_lock = threading.Lock()
        self._resolve_lock: threading.Lock | None = None
        self._timer: threading.Timer | None = None

    # ── Public API ──────────────────────────────────────────

    def handle_down(self, capslock_is_on: bool) -> bool:
        """Handle CapsLock key-down.  Return True to eat the event."""
        if capslock_is_on:
            return False

        if not self._main_lock.acquire(blocking=False):
            return True  # already tracking a press

        self._resolve_lock = threading.Lock()
        self._timer = threading.Timer(
            self._long_press_seconds, self._on_timer,
        )
        self._timer.start()
        return True

    def handle_up(self) -> str:
        """Handle CapsLock key-up.

        Returns ``"short"``, ``"long"``, or ``"none"``.
        """
        if not self._main_lock.locked():
            return "none"

        if self._resolve_lock is not None and self._resolve_lock.acquire(blocking=False):
            # Short press — UP won the race.
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._on_short_press()
            self._main_lock.release()
            return "short"
        else:
            # Long press — timer already resolved.
            self._on_long_press()
            self._main_lock.release()
            return "long"

    # ── Timer callback ──────────────────────────────────────

    def _on_timer(self) -> None:
        """Timer callback — long press detected; action fires on release."""
        if self._resolve_lock is None:
            return
        if not self._resolve_lock.acquire(blocking=False):
            return  # already resolved as short press
        self._timer = None
        # _main_lock is kept → handle_up() will fire action and release it
