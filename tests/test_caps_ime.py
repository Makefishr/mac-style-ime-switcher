"""Tests for CapsLockIME — the consolidated CapsLock→IME module."""
import time
import unittest

from ime_switcher.caps_ime import CapsLockIME, IMEState, VK_CAPITAL


# ── Fake adapters ────────────────────────────────────────────

class FakeIME:
    def __init__(self):
        self._state = IMEState.ENGLISH
        self.toggle_calls = 0

    def toggle(self) -> None:
        self.toggle_calls += 1
        self._state = (
            IMEState.CHINESE if self._state == IMEState.ENGLISH
            else IMEState.ENGLISH
        )

    def current_state(self) -> IMEState:
        return self._state


class FakeLED:
    def __init__(self):
        self.is_on = False
        self.off_calls = 0
        self.on_calls = 0

    def off(self) -> None:
        self.off_calls += 1
        self.is_on = False

    def on(self) -> None:
        self.on_calls += 1
        self.is_on = True


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


class TestShortPress(unittest.TestCase):
    def test_short_press_eats_both_events(self):
        engine = CapsLockIME()
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


class TestLongPress(unittest.TestCase):
    def test_long_press_eats_down_passes_up_through(self):
        engine = CapsLockIME(long_press_threshold=0.05)
        self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
        time.sleep(0.1)  # timer fires before UP
        self.assertFalse(engine.on_key_event(VK_CAPITAL, False))

    def test_long_press_enters_uppercase_mode(self):
        engine = CapsLockIME(long_press_threshold=0.05)
        engine.on_key_event(VK_CAPITAL, True)
        time.sleep(0.1)
        engine.on_key_event(VK_CAPITAL, False)
        time.sleep(0.05)

        self.assertFalse(engine.on_key_event(VK_CAPITAL, True))

    def test_long_press_turns_led_on(self):
        led = FakeLED()
        engine = CapsLockIME(long_press_threshold=0.05, led_adapter=led)
        engine.on_key_event(VK_CAPITAL, True)
        time.sleep(0.1)
        engine.on_key_event(VK_CAPITAL, False)
        time.sleep(0.05)

        self.assertEqual(led.on_calls, 1)
        self.assertTrue(led.is_on)


class TestUppercasePassThrough(unittest.TestCase):
    def test_press_when_uppercase_on_passes_down_through(self):
        engine = CapsLockIME(long_press_threshold=0.05)
        engine.on_key_event(VK_CAPITAL, True)
        time.sleep(0.1)
        engine.on_key_event(VK_CAPITAL, False)
        time.sleep(0.05)

        self.assertFalse(engine.on_key_event(VK_CAPITAL, True))

    def test_press_when_uppercase_on_turns_led_off(self):
        led = FakeLED()
        engine = CapsLockIME(long_press_threshold=0.05, led_adapter=led)
        engine.on_key_event(VK_CAPITAL, True)
        time.sleep(0.1)
        engine.on_key_event(VK_CAPITAL, False)
        time.sleep(0.05)

        led.off_calls = 0
        engine.on_key_event(VK_CAPITAL, True)
        time.sleep(0.05)

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
