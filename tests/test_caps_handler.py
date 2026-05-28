"""Unit tests for CapsLockHandler state machine."""

import time
import unittest

from ime_switcher.caps_handler import CapsLockHandler


class TestCapsLockHandler(unittest.TestCase):
    def setUp(self):
        self.short_count = 0
        self.long_count = 0
        self.handler = CapsLockHandler(
            on_short_press=self._on_short,
            on_long_press=self._on_long,
            long_press_seconds=0.05,
        )

    def _on_short(self):
        self.short_count += 1

    def _on_long(self):
        self.long_count += 1

    # ── Quick press ─────────────────────────────────────────

    def test_quick_press_returns_short_and_triggers_short_callback(self):
        eaten = self.handler.handle_down(capslock_is_on=False)
        self.assertTrue(eaten)
        time.sleep(0.01)
        result = self.handler.handle_up()
        self.assertEqual(result, "short")
        time.sleep(0.1)

        self.assertEqual(self.short_count, 1)
        self.assertEqual(self.long_count, 0)

    # ── Long press ──────────────────────────────────────────

    def test_long_press_returns_long_on_release_and_triggers_callback(self):
        self.handler.handle_down(capslock_is_on=False)
        time.sleep(0.1)  # timer fires

        self.assertEqual(self.short_count, 0)
        self.assertEqual(self.long_count, 0)  # deferred

        result = self.handler.handle_up()
        self.assertEqual(result, "long")

        self.assertEqual(self.short_count, 0)
        self.assertEqual(self.long_count, 1)

    # ── CapsLock already ON ─────────────────────────────────

    def test_down_when_capslock_on_passes_through(self):
        eaten = self.handler.handle_down(capslock_is_on=True)
        self.assertFalse(eaten)

    def test_up_when_not_tracking_returns_none(self):
        result = self.handler.handle_up()
        self.assertEqual(result, "none")

    # ── Second press while tracking ─────────────────────────

    def test_second_down_while_waiting_is_eaten_no_extra_action(self):
        self.handler.handle_down(capslock_is_on=False)
        eaten = self.handler.handle_down(capslock_is_on=False)
        self.assertTrue(eaten)

        result = self.handler.handle_up()
        self.assertEqual(result, "short")
        time.sleep(0.1)

        self.assertEqual(self.short_count, 1)
        self.assertEqual(self.long_count, 0)

    # ── Full lifecycle: long → off → short ──────────────────

    def test_full_lifecycle_long_then_short(self):
        # Cycle 1: long press
        self.handler.handle_down(capslock_is_on=False)
        time.sleep(0.1)
        result = self.handler.handle_up()
        self.assertEqual(result, "long")
        self.assertEqual(self.long_count, 1)
        self.assertEqual(self.short_count, 0)

        # Cycle 2: CapsLock is ON, press to turn off
        eaten = self.handler.handle_down(capslock_is_on=True)
        self.assertFalse(eaten)
        result = self.handler.handle_up()
        self.assertEqual(result, "none")

        # Cycle 3: CapsLock is OFF, short press
        self.handler.handle_down(capslock_is_on=False)
        time.sleep(0.01)
        result = self.handler.handle_up()
        self.assertEqual(result, "short")
        time.sleep(0.1)

        self.assertEqual(self.short_count, 1)
        self.assertEqual(self.long_count, 1)

    # ── Multiple cycles ─────────────────────────────────────

    def test_two_quick_presses(self):
        for _ in range(2):
            self.handler.handle_down(capslock_is_on=False)
            time.sleep(0.01)
            result = self.handler.handle_up()
            self.assertEqual(result, "short")
            time.sleep(0.1)

        self.assertEqual(self.short_count, 2)
        self.assertEqual(self.long_count, 0)

    # ── Race: UP wins against timer ─────────────────────────

    def test_race_up_wins_returns_short(self):
        self.handler.handle_down(capslock_is_on=False)
        result = self.handler.handle_up()  # UP before timer
        self.assertEqual(result, "short")
        time.sleep(0.1)

        self.assertEqual(self.short_count, 1)
        self.assertEqual(self.long_count, 0)

    # ── Race: timer wins against UP ─────────────────────────

    def test_race_timer_wins_returns_long(self):
        self.handler.handle_down(capslock_is_on=False)
        time.sleep(0.1)  # timer fires first
        result = self.handler.handle_up()
        self.assertEqual(result, "long")

        self.assertEqual(self.short_count, 0)
        self.assertEqual(self.long_count, 1)
