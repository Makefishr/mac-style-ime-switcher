"""Tests for CapsLockIME — the consolidated CapsLock→IME module."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ime_switcher.caps_ime import CapsLockIME, IMEState, VK_CAPITAL, _Win32IME


# ── Fake adapters ────────────────────────────────────────────

class FakeIME:
    def __init__(self):
        self._state = IMEState.ENGLISH
        self.toggle_calls = 0
        self.toggled = threading.Event()

    def toggle(self) -> bool:
        self.toggle_calls += 1
        self.toggled.set()
        self._state = (
            IMEState.CHINESE if self._state == IMEState.ENGLISH
            else IMEState.ENGLISH
        )
        return True

    def current_state(self) -> IMEState:
        return self._state

    def can_toggle(self) -> bool:
        return True


class ScopedFakeIME(FakeIME):
    def __init__(self, allowed):
        super().__init__()
        self.allowed = allowed

    def can_toggle(self) -> bool:
        return self.allowed


class ToggleResultIME(FakeIME):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def toggle(self):
        self.toggle_calls += 1
        self.toggled.set()
        if self.result is True:
            self._state = (
                IMEState.CHINESE if self._state == IMEState.ENGLISH
                else IMEState.ENGLISH
            )
        return self.result


class FakeLED:
    def __init__(self):
        self.is_on = False
        self.off_calls = 0
        self.on_calls = 0
        self.turned_off = threading.Event()
        self.turned_on = threading.Event()

    def off(self) -> None:
        self.off_calls += 1
        self.is_on = False
        self.turned_off.set()

    def on(self) -> None:
        self.on_calls += 1
        self.is_on = True
        self.turned_on.set()


class FakeNativeCapsLock:
    def __init__(self):
        self.tap_calls = 0
        self.tapped = threading.Event()

    def tap(self) -> None:
        self.tap_calls += 1
        self.tapped.set()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# ── Helpers ──────────────────────────────────────────────────

def quick_tap(engine):
    """Simulate a quick CapsLock press (< threshold)."""
    down_eaten = engine.on_key_event(VK_CAPITAL, True)
    up_eaten = engine.on_key_event(VK_CAPITAL, False)
    return down_eaten, up_eaten


# ── Tests ────────────────────────────────────────────────────

class TestNonCapsLockKey(unittest.TestCase):
    def test_non_capslock_key_returns_false(self):
        engine = CapsLockIME()
        self.assertFalse(engine.on_key_event(0x41, True))
        self.assertFalse(engine.on_key_event(0x41, False))


class TestWin32IMEAdapter(unittest.TestCase):
    def test_can_toggle_fails_closed_on_unexpected_scope_errors(self):
        adapter = _Win32IME()
        for error in (
            RuntimeError("private-adapter-scope-runtime-marker"),
            ValueError("private-adapter-scope-value-marker"),
        ):
            with self.subTest(error_type=type(error).__name__), \
                 patch(
                     "ime_switcher.settings.get_settings",
                     return_value=SimpleNamespace(mode="ime"),
                 ), \
                 patch(
                     "ime_switcher.toggle.can_switch",
                     side_effect=error,
                 ), \
                 self.assertLogs(
                     "ime_switcher.caps_ime",
                     level="ERROR",
                 ) as logs:
                self.assertFalse(adapter.can_toggle())

            log_output = "\n".join(logs.output)
            self.assertIn(type(error).__name__, log_output)
            self.assertNotIn(str(error), log_output)

    def test_toggle_fails_closed_on_unexpected_switch_errors(self):
        adapter = _Win32IME()
        for error in (
            RuntimeError("private-adapter-runtime-marker"),
            ValueError("private-adapter-value-marker"),
        ):
            with self.subTest(error_type=type(error).__name__), \
                 patch(
                     "ime_switcher.settings.get_settings",
                     return_value=SimpleNamespace(mode="layout"),
                 ), \
                 patch(
                     "ime_switcher.toggle.switch_ime",
                     side_effect=error,
                 ), \
                 self.assertLogs(
                     "ime_switcher.caps_ime",
                     level="ERROR",
                 ) as logs:
                self.assertFalse(adapter.toggle())

            log_output = "\n".join(logs.output)
            self.assertIn(type(error).__name__, log_output)
            self.assertNotIn(str(error), log_output)

    def test_toggle_does_not_swallow_process_control_exceptions(self):
        adapter = _Win32IME()
        for error in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(error_type=type(error).__name__), \
                 patch(
                     "ime_switcher.settings.get_settings",
                     return_value=SimpleNamespace(mode="layout"),
                 ), \
                 patch(
                     "ime_switcher.toggle.switch_ime",
                     side_effect=error,
                 ), \
                 self.assertRaises(type(error)):
                adapter.toggle()

    def test_can_toggle_does_not_swallow_process_control_exceptions(self):
        adapter = _Win32IME()
        for error in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(error_type=type(error).__name__), \
                 patch(
                     "ime_switcher.settings.get_settings",
                     return_value=SimpleNamespace(mode="ime"),
                 ), \
                 patch(
                     "ime_switcher.toggle.can_switch",
                     side_effect=error,
                 ), \
                 self.assertRaises(type(error)):
                adapter.can_toggle()


class TestShortPress(unittest.TestCase):
    def test_short_press_eats_both_events(self):
        engine = CapsLockIME(ime_adapter=FakeIME())
        down_eaten, up_eaten = quick_tap(engine)
        self.assertTrue(down_eaten)
        self.assertTrue(up_eaten)

    def test_short_press_toggles_ime(self):
        ime = FakeIME()
        led = FakeLED()
        engine = CapsLockIME(ime_adapter=ime, led_adapter=led)

        quick_tap(engine)
        time.sleep(0.05)

        self.assertEqual(ime.toggle_calls, 1)
        self.assertEqual(ime.current_state(), IMEState.CHINESE)

    def test_short_press_turns_led_off(self):
        ime = FakeIME()
        led = FakeLED()
        engine = CapsLockIME(ime_adapter=ime, led_adapter=led)

        quick_tap(engine)
        time.sleep(0.05)

        self.assertEqual(led.off_calls, 1)
        self.assertFalse(led.is_on)

    def test_failed_toggle_replays_native_once_and_worker_keeps_running(self):
        ime = ToggleResultIME(False)
        led = FakeLED()
        native = FakeNativeCapsLock()
        engine = CapsLockIME(
            ime_adapter=ime,
            led_adapter=led,
            native_adapter=native,
        )

        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(native.tapped.wait(0.5))
        self.assertTrue(led.turned_off.wait(0.5))
        self.assertEqual(native.tap_calls, 1)
        self.assertEqual(led.off_calls, 1)
        self.assertFalse(led.is_on)

        ime.result = True
        ime.toggled.clear()
        led.turned_off.clear()
        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(ime.toggled.wait(0.5))
        self.assertTrue(led.turned_off.wait(0.5))
        self.assertEqual(ime.toggle_calls, 2)
        self.assertEqual(native.tap_calls, 1)
        self.assertEqual(led.off_calls, 2)

    def test_only_literal_true_counts_as_toggle_success(self):
        for result in (None, "unexpected-success"):
            with self.subTest(result_type=type(result).__name__):
                ime = ToggleResultIME(result)
                led = FakeLED()
                native = FakeNativeCapsLock()
                engine = CapsLockIME(
                    ime_adapter=ime,
                    led_adapter=led,
                    native_adapter=native,
                )

                self.assertEqual(quick_tap(engine), (True, True))
                self.assertTrue(native.tapped.wait(0.5))
                self.assertTrue(led.turned_off.wait(0.5))
                self.assertEqual(native.tap_calls, 1)
                self.assertEqual(led.off_calls, 1)
                self.assertFalse(led.is_on)

    def test_toggle_exception_replays_native_and_worker_keeps_running(self):
        class RaisingOnceIME(FakeIME):
            def toggle(self):
                self.toggle_calls += 1
                self.toggled.set()
                if self.toggle_calls == 1:
                    raise RuntimeError("private-toggle-marker")
                return True

        ime = RaisingOnceIME()
        led = FakeLED()
        native = FakeNativeCapsLock()
        engine = CapsLockIME(
            ime_adapter=ime,
            led_adapter=led,
            native_adapter=native,
        )

        with self.assertLogs(
            "ime_switcher.caps_ime",
            level="ERROR",
        ) as logs:
            self.assertEqual(quick_tap(engine), (True, True))
            self.assertTrue(native.tapped.wait(0.5))
            self.assertTrue(led.turned_off.wait(0.5))

        log_output = "\n".join(logs.output)
        self.assertIn("RuntimeError", log_output)
        self.assertNotIn("private-toggle-marker", log_output)
        self.assertEqual(native.tap_calls, 1)

        ime.toggled.clear()
        led.turned_off.clear()
        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(ime.toggled.wait(0.5))
        self.assertTrue(led.turned_off.wait(0.5))
        self.assertEqual(ime.toggle_calls, 2)
        self.assertEqual(native.tap_calls, 1)

    def test_native_replay_exception_is_safe_and_worker_keeps_running(self):
        class FailingOnceNative:
            def __init__(self):
                self.tap_calls = 0
                self.failed = threading.Event()
                self.succeeded = threading.Event()

            def tap(self):
                self.tap_calls += 1
                if self.tap_calls == 1:
                    self.failed.set()
                    raise RuntimeError("private-native-marker")
                self.succeeded.set()

        ime = ToggleResultIME(False)
        led = FakeLED()
        native = FailingOnceNative()
        engine = CapsLockIME(
            ime_adapter=ime,
            led_adapter=led,
            native_adapter=native,
        )

        with self.assertLogs(
            "ime_switcher.caps_ime",
            level="ERROR",
        ) as logs:
            self.assertEqual(quick_tap(engine), (True, True))
            self.assertTrue(native.failed.wait(0.5))
            self.assertTrue(led.turned_off.wait(0.5))

        log_output = "\n".join(logs.output)
        self.assertIn("RuntimeError", log_output)
        self.assertNotIn("private-native-marker", log_output)
        self.assertEqual(native.tap_calls, 1)

        led.turned_off.clear()
        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(native.succeeded.wait(0.5))
        self.assertTrue(led.turned_off.wait(0.5))
        self.assertEqual(native.tap_calls, 2)


class TestOutOfScopeReplay(unittest.TestCase):
    def test_out_of_scope_press_is_intercepted_then_replayed_once(self):
        ime = ScopedFakeIME(False)
        native = FakeNativeCapsLock()
        engine = CapsLockIME(ime_adapter=ime, native_adapter=native)

        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(native.tapped.wait(0.5))
        self.assertEqual(ime.toggle_calls, 0)
        self.assertEqual(native.tap_calls, 1)

    def test_scope_exception_replays_safely_and_worker_keeps_running(self):
        class RaisingOnceScopeIME(FakeIME):
            def __init__(self):
                super().__init__()
                self.scope_calls = 0

            def can_toggle(self):
                self.scope_calls += 1
                if self.scope_calls == 1:
                    raise ValueError("private-engine-scope-marker")
                return True

        ime = RaisingOnceScopeIME()
        led = FakeLED()
        native = FakeNativeCapsLock()
        engine = CapsLockIME(
            ime_adapter=ime,
            led_adapter=led,
            native_adapter=native,
        )

        with self.assertLogs(
            "ime_switcher.caps_ime",
            level="ERROR",
        ) as logs:
            self.assertEqual(quick_tap(engine), (True, True))
            self.assertTrue(native.tapped.wait(0.5))

        log_output = "\n".join(logs.output)
        self.assertIn("ValueError", log_output)
        self.assertNotIn("private-engine-scope-marker", log_output)
        self.assertEqual(native.tap_calls, 1)

        ime.toggled.clear()
        led.turned_off.clear()
        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(ime.toggled.wait(0.5))
        self.assertTrue(led.turned_off.wait(0.5))
        self.assertEqual(ime.scope_calls, 2)
        self.assertEqual(native.tap_calls, 1)


class TestLongPress(unittest.TestCase):
    def test_supported_long_press_intercepts_both_events_and_enables_uppercase(self):
        clock = FakeClock()
        led = FakeLED()
        native = FakeNativeCapsLock()
        engine = CapsLockIME(
            long_press_threshold=1.0,
            ime_adapter=FakeIME(),
            led_adapter=led,
            native_adapter=native,
            clock=clock,
        )
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 1.01
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(led.turned_on.wait(0.5))
        self.assertEqual(led.on_calls, 1)
        self.assertEqual(native.tap_calls, 0)

    def test_long_press_enters_uppercase_mode(self):
        clock = FakeClock()
        led = FakeLED()
        engine = CapsLockIME(
            long_press_threshold=1.0,
            ime_adapter=FakeIME(),
            led_adapter=led,
            clock=clock,
        )

        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 1.01
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(led.turned_on.wait(0.5))
        self.assertTrue(led.is_on)

    def test_long_press_turns_led_on(self):
        led = FakeLED()
        engine = CapsLockIME(
            long_press_threshold=0.05,
            ime_adapter=FakeIME(),
            led_adapter=led,
        )
        engine.on_key_event(VK_CAPITAL, True)
        time.sleep(0.1)
        engine.on_key_event(VK_CAPITAL, False)
        time.sleep(0.05)

        self.assertEqual(led.on_calls, 1)
        self.assertTrue(led.is_on)


class TestUppercaseGesture(unittest.TestCase):
    def test_press_when_uppercase_on_is_intercepted_as_a_complete_gesture(self):
        clock = FakeClock()
        led = FakeLED()
        engine = CapsLockIME(
            long_press_threshold=1.0,
            ime_adapter=FakeIME(),
            led_adapter=led,
            clock=clock,
        )
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 1.01
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(led.turned_on.wait(0.5))

        clock.now = 2.0
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 2.1
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(led.turned_off.wait(0.5))

    def test_press_when_uppercase_on_turns_led_off(self):
        clock = FakeClock()
        led = FakeLED()
        engine = CapsLockIME(
            long_press_threshold=1.0,
            ime_adapter=FakeIME(),
            led_adapter=led,
            clock=clock,
        )
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 1.01
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(led.turned_on.wait(0.5))

        led.off_calls = 0
        led.turned_off.clear()
        clock.now = 2.0
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 2.1
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(led.turned_off.wait(0.5))

        self.assertEqual(led.off_calls, 1)
        self.assertFalse(led.is_on)


class TestImeState(unittest.TestCase):
    def test_ime_state_delegates_to_adapter(self):
        ime = FakeIME()
        engine = CapsLockIME(ime_adapter=ime)
        self.assertEqual(engine.ime_state, IMEState.ENGLISH)

        quick_tap(engine)
        time.sleep(0.05)
        self.assertEqual(engine.ime_state, IMEState.CHINESE)


class TestConsecutivePresses(unittest.TestCase):
    def test_two_quick_presses_each_toggle(self):
        ime = FakeIME()
        led = FakeLED()
        engine = CapsLockIME(long_press_threshold=0.05, ime_adapter=ime, led_adapter=led)

        quick_tap(engine)
        time.sleep(0.05)
        self.assertEqual(ime.current_state(), IMEState.CHINESE)
        self.assertEqual(led.off_calls, 1)

        quick_tap(engine)
        time.sleep(0.05)
        self.assertEqual(ime.current_state(), IMEState.ENGLISH)
        self.assertEqual(led.off_calls, 2)

    def test_blocked_actions_have_one_worker_and_preserve_pending_burst(self):
        class BlockingIME(FakeIME):
            def __init__(self):
                super().__init__()
                self.release = threading.Event()
                self.first_started = threading.Event()
                self.all_completed = threading.Event()
                self._lock = threading.Lock()
                self.active = 0
                self.max_active = 0
                self.started_count = 0
                self.completed_count = 0

            def toggle(self):
                with self._lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    self.started_count += 1
                    if self.started_count == 1:
                        self.first_started.set()
                self.release.wait(1.0)
                with self._lock:
                    self.active -= 1
                    self.completed_count += 1
                    if self.completed_count >= 4:
                        self.all_completed.set()
                return True

        ime = BlockingIME()
        engine = CapsLockIME(
            long_press_threshold=0.25,
            ime_adapter=ime,
            led_adapter=FakeLED(),
        )

        callback_times = []

        def submit_short_press():
            started = time.monotonic()
            down_eaten, up_eaten = quick_tap(engine)
            callback_times.append(time.monotonic() - started)
            self.assertTrue(down_eaten)
            self.assertTrue(up_eaten)

        submit_short_press()
        self.assertTrue(ime.first_started.wait(0.5))
        submit_short_press()
        submit_short_press()
        submit_short_press()
        time.sleep(0.05)

        self.assertLess(max(callback_times), 0.2)
        self.assertEqual(ime.max_active, 1)
        self.assertEqual(ime.started_count, 1)

        ime.release.set()
        self.assertTrue(ime.all_completed.wait(1.0))
        self.assertEqual(ime.started_count, 4)
        self.assertEqual(ime.completed_count, 4)
        self.assertEqual(ime.max_active, 1)


class TestLifecycleReset(unittest.TestCase):
    def test_reset_cancels_missing_keyup_and_late_keyup_starts_no_action(self):
        ime = FakeIME()
        led = FakeLED()
        engine = CapsLockIME(
            long_press_threshold=0.02,
            ime_adapter=ime,
            led_adapter=led,
        )

        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        engine.reset()
        self.assertTrue(led.turned_off.wait(0.5))

        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertEqual(ime.toggle_calls, 0)
        self.assertEqual(led.on_calls, 0)

        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(ime.toggled.wait(0.5))
        self.assertEqual(ime.toggle_calls, 1)

    def test_reset_safely_releases_entered_uppercase_state(self):
        clock = FakeClock()
        led = FakeLED()
        engine = CapsLockIME(
            long_press_threshold=1.0,
            ime_adapter=FakeIME(),
            led_adapter=led,
            clock=clock,
        )

        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 1.01
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(led.turned_on.wait(0.5))
        self.assertTrue(led.is_on)

        led.turned_off.clear()
        engine.reset()
        self.assertTrue(led.turned_off.wait(0.5))

        self.assertFalse(led.is_on)
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        engine.reset()


class TestUppercaseThreadOrdering(unittest.TestCase):
    def test_slow_long_scope_resolves_before_next_gesture_turns_uppercase_off(self):
        class BlockingIME(FakeIME):
            def __init__(self):
                super().__init__()
                self.started = threading.Event()
                self.release = threading.Event()
                self.can_toggle_calls = 0

            def can_toggle(self):
                self.can_toggle_calls += 1
                self.started.set()
                self.release.wait(1.0)
                return True

        clock = FakeClock()
        ime = BlockingIME()
        led = FakeLED()
        engine = CapsLockIME(
            long_press_threshold=1.0,
            ime_adapter=ime,
            led_adapter=led,
            clock=clock,
        )

        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 1.01
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
        self.assertTrue(ime.started.wait(0.5))

        clock.now = 2.0
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 2.1
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))

        ime.release.set()
        self.assertTrue(led.turned_off.wait(0.5))
        self.assertEqual(ime.can_toggle_calls, 1)
        self.assertEqual(ime.toggle_calls, 0)
        self.assertEqual(led.on_calls, 1)
        self.assertEqual(led.off_calls, 1)


class TestActionBurst(unittest.TestCase):
    def test_first_blocked_action_preserves_eight_pending_actions_in_order(self):
        events = []
        event_lock = threading.Lock()
        first_started = threading.Event()
        release_first = threading.Event()
        all_completed = threading.Event()

        class RecordingIME(FakeIME):
            def __init__(self):
                super().__init__()
                self.active = 0
                self.max_active = 0

            def toggle(self):
                with event_lock:
                    self.toggle_calls += 1
                    call_number = self.toggle_calls
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                if call_number == 1:
                    first_started.set()
                    release_first.wait(2.0)
                with event_lock:
                    events.append("toggle")
                    self.active -= 1
                return True

        class RecordingLED(FakeLED):
            def off(self):
                with event_lock:
                    self.off_calls += 1
                    self.is_on = False
                    events.append("off")
                    if self.off_calls == 9:
                        all_completed.set()

        ime = RecordingIME()
        led = RecordingLED()
        engine = CapsLockIME(ime_adapter=ime, led_adapter=led)

        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(first_started.wait(0.5))

        callback_times = []
        for _ in range(8):
            started = time.monotonic()
            self.assertEqual(quick_tap(engine), (True, True))
            callback_times.append(time.monotonic() - started)

        self.assertLess(max(callback_times), 0.2)
        release_first.set()
        self.assertTrue(all_completed.wait(1.0))

        self.assertEqual(ime.toggle_calls, 9)
        self.assertEqual(led.off_calls, 9)
        self.assertEqual(ime.max_active, 1)
        self.assertEqual(events, ["toggle", "off"] * 9)

    def test_reset_discards_full_old_generation_and_finishes_led_off(self):
        first_started = threading.Event()
        release_first = threading.Event()
        second_off_seen = threading.Event()
        counter_lock = threading.Lock()

        class BlockingIME(FakeIME):
            def toggle(self):
                with counter_lock:
                    self.toggle_calls += 1
                    call_number = self.toggle_calls
                if call_number == 1:
                    first_started.set()
                    release_first.wait(2.0)
                return True

        class RecordingLED(FakeLED):
            def off(self):
                super().off()
                if self.off_calls >= 2:
                    second_off_seen.set()

        ime = BlockingIME()
        led = RecordingLED()
        clock = FakeClock()
        engine = CapsLockIME(
            long_press_threshold=1.0,
            ime_adapter=ime,
            led_adapter=led,
            clock=clock,
        )

        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(first_started.wait(0.5))
        for _ in range(7):
            self.assertEqual(quick_tap(engine), (True, True))
        clock.now = 2.0
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        clock.now = 3.01
        self.assertTrue(engine.on_key_event(VK_CAPITAL, False))

        started = time.monotonic()
        engine.reset()
        reset_elapsed = time.monotonic() - started
        self.assertLess(reset_elapsed, 0.2)

        release_first.set()
        self.assertTrue(second_off_seen.wait(1.0))
        time.sleep(0.05)

        self.assertEqual(ime.toggle_calls, 1)
        self.assertEqual(led.on_calls, 0)
        self.assertFalse(led.is_on)

    def test_ninth_pending_action_is_observably_dropped_at_hard_limit(self):
        first_started = threading.Event()
        release_first = threading.Event()
        accepted_completed = threading.Event()
        count_lock = threading.Lock()

        class BlockingIME(FakeIME):
            def __init__(self):
                super().__init__()
                self.completed = 0

            def toggle(self):
                with count_lock:
                    self.toggle_calls += 1
                    call_number = self.toggle_calls
                if call_number == 1:
                    first_started.set()
                    release_first.wait(2.0)
                with count_lock:
                    self.completed += 1
                    if self.completed == 9:
                        accepted_completed.set()
                return True

        ime = BlockingIME()
        engine = CapsLockIME(ime_adapter=ime, led_adapter=FakeLED())

        self.assertEqual(quick_tap(engine), (True, True))
        self.assertTrue(first_started.wait(0.5))
        for _ in range(8):
            self.assertEqual(quick_tap(engine), (True, True))

        started = time.monotonic()
        with self.assertLogs("ime_switcher.caps_ime", level="INFO") as logs:
            self.assertEqual(quick_tap(engine), (True, True))
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(any("action dropped" in line for line in logs.output))

        release_first.set()
        self.assertTrue(accepted_completed.wait(1.0))
        self.assertEqual(ime.toggle_calls, 9)
