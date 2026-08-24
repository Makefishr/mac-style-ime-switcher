"""Public keyboard-hook policy tests."""

import ctypes
import threading
import time
from ctypes import wintypes

from ime_switcher.caps_ime import CapsLockIME, IMEState, Win32NativeCapsLock
from ime_switcher.hook import (
    KeyboardHookProcessor,
    dispatch_keyboard_event,
    dispatch_hook_event,
    reset_capslock_state,
    reset_hook_state,
    send_replay,
    submit_replay,
)
from ime_switcher.shift_guard import KeyEvent, ShiftTapGuard
from ime_switcher.winapi import INPUT, KEYBDINPUT, user32


def test_generic_shift_tap_is_blocked_at_the_hook_boundary():
    guard = ShiftTapGuard()

    down = guard.process(
        KeyEvent(vk_code=0x10, scan_code=0x2A, flags=0, is_down=True),
    )
    up = guard.process(
        KeyEvent(vk_code=0x10, scan_code=0x2A, flags=0x80, is_down=False),
    )

    assert down.forward is False
    assert down.replay == ()
    assert up.forward is False
    assert up.replay == ()


def test_left_and_right_shift_repeats_are_blocked_without_replay():
    for vk_code, scan_code in ((0xA0, 0x2A), (0xA1, 0x36)):
        guard = ShiftTapGuard()

        decisions = [
            guard.process(
                KeyEvent(vk_code=vk_code, scan_code=scan_code, flags=0, is_down=True),
            ),
            guard.process(
                KeyEvent(vk_code=vk_code, scan_code=scan_code, flags=0, is_down=True),
            ),
            guard.process(
                KeyEvent(vk_code=vk_code, scan_code=scan_code, flags=0x80, is_down=False),
            ),
        ]

        assert all(decision.forward is False for decision in decisions)
        assert all(decision.replay == () for decision in decisions)


def test_injected_shift_events_are_forwarded_without_becoming_physical_state():
    guard = ShiftTapGuard()

    injected_down = guard.process(
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0x10, is_down=True),
    )
    injected_up = guard.process(
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0x90, is_down=False),
    )

    assert injected_down.forward is True
    assert injected_up.forward is True


def test_shift_and_letter_replay_shift_before_target_and_close_both_keys():
    guard = ShiftTapGuard()

    shift_down = guard.process(
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0, is_down=True),
    )
    letter_down = guard.process(
        KeyEvent(vk_code=0x41, scan_code=0x1E, flags=0, is_down=True),
    )
    letter_up = guard.process(
        KeyEvent(vk_code=0x41, scan_code=0x1E, flags=0x80, is_down=False),
    )
    shift_up = guard.process(
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0x80, is_down=False),
    )

    assert shift_down.forward is False
    assert shift_down.replay == ()
    assert letter_down.forward is False
    assert letter_down.replay == (
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0, is_down=True),
        KeyEvent(vk_code=0x41, scan_code=0x1E, flags=0, is_down=True),
    )
    assert letter_up.forward is True
    assert letter_up.replay == ()
    assert shift_up.forward is False
    assert shift_up.replay == (
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0x80, is_down=False),
    )


def test_ctrl_space_is_blocked_while_ctrl_keyup_still_passes():
    guard = ShiftTapGuard()

    ctrl_down = guard.process(
        KeyEvent(vk_code=0xA2, scan_code=0x1D, flags=0, is_down=True),
    )
    space_down = guard.process(
        KeyEvent(vk_code=0x20, scan_code=0x39, flags=0, is_down=True),
    )
    space_up = guard.process(
        KeyEvent(vk_code=0x20, scan_code=0x39, flags=0x80, is_down=False),
    )
    ctrl_up = guard.process(
        KeyEvent(vk_code=0xA2, scan_code=0x1D, flags=0x80, is_down=False),
    )

    assert ctrl_down.forward is True
    assert space_down.forward is False
    assert space_down.replay == ()
    assert space_up.forward is True
    assert ctrl_up.forward is True


def test_production_hook_processor_uses_shift_policy_at_public_boundary():
    processor = KeyboardHookProcessor()

    decision = processor.process(
        KeyEvent(vk_code=0xA1, scan_code=0x36, flags=0, is_down=True),
    )

    assert decision.forward is False
    assert decision.replay == ()


def test_sendinput_abi_uses_pointer_sized_input_structures():
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    expected_keyboard_size = 16 if pointer_size == 4 else 24
    expected_input_size = 28 if pointer_size == 4 else 40

    assert ctypes.sizeof(KEYBDINPUT) == expected_keyboard_size
    assert ctypes.sizeof(INPUT) == expected_input_size
    assert user32.SendInput.argtypes == [
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]
    assert user32.SendInput.restype is wintypes.UINT


def test_replay_is_one_external_sendinput_batch_in_shift_then_target_order():
    captured = []

    def send_input(count, inputs, cb_size):
        captured.append((count, inputs, cb_size))
        return count

    events = (
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0, is_down=True),
        KeyEvent(vk_code=0x41, scan_code=0x1E, flags=0, is_down=True),
    )

    assert submit_replay(events, send_input=send_input) is True
    assert len(captured) == 1

    count, inputs, cb_size = captured[0]
    assert count == 2
    assert cb_size == ctypes.sizeof(INPUT)
    assert [inputs[index].type for index in range(count)] == [1, 1]
    assert [inputs[index].ki.wScan for index in range(count)] == [0x2A, 0x1E]
    assert [inputs[index].ki.dwFlags for index in range(count)] == [
        0x0008,
        0x0008,
    ]


def test_production_processor_reset_clears_physical_shift_lifecycle_state():
    processor = KeyboardHookProcessor()
    shift_down = KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0, is_down=True)
    letter_down = KeyEvent(vk_code=0x41, scan_code=0x1E, flags=0, is_down=True)
    shift_up = KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0x80, is_down=False)

    processor.process(shift_down)
    processor.process(letter_down)
    processor.reset()
    release = processor.process(shift_up)

    assert release.forward is False
    assert release.replay == ()


def test_lifecycle_reset_submits_release_for_shift_already_replayed_down():
    processor = KeyboardHookProcessor()
    captured = []

    def send_input(count, inputs, cb_size):
        captured.append((count, inputs, cb_size))
        return count

    processor.process(
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0, is_down=True),
    )
    processor.process(
        KeyEvent(vk_code=0x41, scan_code=0x1E, flags=0, is_down=True),
    )

    assert reset_hook_state(processor, send_input=send_input) is True
    assert len(captured) == 1
    count, inputs, cb_size = captured[0]
    assert count == 1
    assert cb_size == ctypes.sizeof(INPUT)
    assert inputs[0].ki.wScan == 0x2A
    assert inputs[0].ki.dwFlags == 0x0008 | 0x0002


def test_capslock_remains_forwarded_after_a_pending_shift_at_hook_boundary():
    processor = KeyboardHookProcessor()
    processor.process(
        KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0, is_down=True),
    )

    caps_down = processor.process(
        KeyEvent(vk_code=0x14, scan_code=0x3A, flags=0, is_down=True),
    )

    assert caps_down.forward is True
    assert caps_down.replay == ()


def test_short_sendinput_releases_partial_shift_and_forwards_target_key():
    processor = KeyboardHookProcessor()
    calls = []

    def send_input(count, inputs, cb_size):
        calls.append((count, inputs, cb_size))
        return 1 if count == 2 else count

    shift_down = KeyEvent(vk_code=0xA0, scan_code=0x2A, flags=0, is_down=True)
    letter_down = KeyEvent(vk_code=0x41, scan_code=0x1E, flags=0, is_down=True)

    assert dispatch_keyboard_event(
        shift_down, processor=processor, send_input=send_input,
    ).forward is False
    target = dispatch_keyboard_event(
        letter_down, processor=processor, send_input=send_input,
    )

    assert target.forward is True
    assert target.replay == ()
    assert [call[0] for call in calls] == [2, 1]
    assert calls[1][1][0].ki.wScan == 0x2A
    assert calls[1][1][0].ki.dwFlags == 0x0008 | 0x0002


def test_replay_release_preserves_extended_scan_code_and_keyup_flag():
    captured = []

    def send_input(count, inputs, cb_size):
        captured.append((count, inputs, cb_size))
        return count

    release = KeyEvent(
        vk_code=0xA1,
        scan_code=0x36,
        flags=0x01,
        is_down=False,
    )

    assert send_replay((release,), send_input=send_input) == 1
    assert len(captured) == 1
    assert captured[0][1][0].ki.wVk == 0
    assert captured[0][1][0].ki.wScan == 0x36
    assert captured[0][1][0].ki.dwFlags == 0x0008 | 0x0001 | 0x0002


def test_ctrl_and_alt_shift_shortcuts_block_generic_and_left_right_shifts():
    for modifier_codes in (
        (0x11, 0x1D),
        (0xA2, 0x1D),
        (0xA3, 0x1D),
        (0x12, 0x38),
        (0xA4, 0x38),
        (0xA5, 0x38),
    ):
        modifier, modifier_scan = modifier_codes
        for shift, shift_scan in ((0x10, 0x2A), (0xA0, 0x2A), (0xA1, 0x36)):
            processor = KeyboardHookProcessor()
            assert processor.process(
                KeyEvent(modifier, modifier_scan, 0, True),
            ).forward is True
            shift_down = processor.process(
                KeyEvent(shift, shift_scan, 0, True),
            )
            shift_up = processor.process(
                KeyEvent(shift, shift_scan, 0x80, False),
            )
            assert shift_down.forward is False
            assert shift_down.replay == ()
            assert shift_up.forward is False
            assert shift_up.replay == ()
            assert processor.process(
                KeyEvent(modifier, modifier_scan, 0x80, False),
            ).forward is True


def test_win_space_and_ctrl_space_block_with_left_and_right_modifiers():
    for modifier in (0x5B, 0x5C, 0x11, 0xA2, 0xA3):
        processor = KeyboardHookProcessor()
        scan = 0x5B if modifier == 0x5B else 0x5C if modifier == 0x5C else 0x1D
        assert processor.process(KeyEvent(modifier, scan, 0, True)).forward is True
        space_down = processor.process(KeyEvent(0x20, 0x39, 0, True))
        space_up = processor.process(KeyEvent(0x20, 0x39, 0x80, False))
        modifier_up = processor.process(KeyEvent(modifier, scan, 0x80, False))
        assert space_down.forward is False
        assert space_down.replay == ()
        assert space_up.forward is True
        assert modifier_up.forward is True


def test_left_and_right_shift_letter_repeats_and_matching_releases():
    for shift, scan in ((0xA0, 0x2A), (0xA1, 0x36)):
        processor = KeyboardHookProcessor()
        calls = []

        def send_input(count, inputs, cb_size):
            calls.append((count, inputs, cb_size))
            return count

        shift_down = dispatch_keyboard_event(
            KeyEvent(shift, scan, 0, True),
            processor=processor,
            send_input=send_input,
        )
        first_down = dispatch_keyboard_event(
            KeyEvent(0x41, 0x1E, 0, True),
            processor=processor,
            send_input=send_input,
        )
        repeat_down = dispatch_keyboard_event(
            KeyEvent(0x41, 0x1E, 0, True),
            processor=processor,
            send_input=send_input,
        )
        letter_up = dispatch_keyboard_event(
            KeyEvent(0x41, 0x1E, 0x80, False),
            processor=processor,
            send_input=send_input,
        )
        shift_up = dispatch_keyboard_event(
            KeyEvent(shift, scan, 0x80, False),
            processor=processor,
            send_input=send_input,
        )

        assert shift_down.forward is False
        assert first_down.forward is False
        assert repeat_down.forward is True
        assert repeat_down.replay == ()
        assert letter_up.forward is True
        assert shift_up.forward is False
        assert len(calls) == 2
        assert [calls[0][1][i].ki.wScan for i in range(2)] == [scan, 0x1E]
        assert calls[1][1][0].ki.wScan == scan
        assert calls[1][1][0].ki.dwFlags == 0x0008 | 0x0002


def test_zero_sendinput_write_forwards_target_without_claiming_replay():
    processor = KeyboardHookProcessor()
    calls = []

    def send_input(count, inputs, cb_size):
        calls.append(count)
        return 0

    dispatch_keyboard_event(
        KeyEvent(0xA0, 0x2A, 0, True),
        processor=processor,
        send_input=send_input,
    )
    target = dispatch_keyboard_event(
        KeyEvent(0x41, 0x1E, 0, True),
        processor=processor,
        send_input=send_input,
    )

    assert target.forward is True
    assert target.replay == ()
    assert calls == [2]


def test_failed_cleanup_uses_injected_release_boundary_and_keeps_target_forwarded():
    processor = KeyboardHookProcessor()
    fallback_calls = []

    def send_input(count, inputs, cb_size):
        return 1 if count == 2 else 0

    def release_input(vk, scan, flags, extra_info):
        fallback_calls.append((vk, scan, flags, extra_info))

    dispatch_keyboard_event(
        KeyEvent(0xA0, 0x2A, 0, True),
        processor=processor,
        send_input=send_input,
    )
    target = dispatch_keyboard_event(
        KeyEvent(0x41, 0x1E, 0, True),
        processor=processor,
        send_input=send_input,
        release_input=release_input,
    )

    assert target.forward is True
    assert fallback_calls == [(0xA0, 0x2A, 0x0002, 0)]


def test_unavailable_release_fallback_blocks_when_shift_safety_cannot_be_proven():
    processor = KeyboardHookProcessor()

    def send_input(count, inputs, cb_size):
        return 1 if count == 2 else 0

    def release_input(vk, scan, flags, extra_info):
        raise OSError("release boundary unavailable")

    dispatch_keyboard_event(
        KeyEvent(0xA0, 0x2A, 0, True),
        processor=processor,
        send_input=send_input,
    )
    target = dispatch_keyboard_event(
        KeyEvent(0x41, 0x1E, 0, True),
        processor=processor,
        send_input=send_input,
        release_input=release_input,
    )

    assert target.forward is False
    assert target.replay == ()


def test_lifecycle_reset_uses_release_fallback_when_sendinput_cannot_release():
    processor = KeyboardHookProcessor()
    fallback_calls = []

    def send_input(count, inputs, cb_size):
        return 0

    def release_input(vk, scan, flags, extra_info):
        fallback_calls.append((vk, scan, flags, extra_info))

    processor.process(KeyEvent(0xA0, 0x2A, 0, True))
    processor.process(KeyEvent(0x41, 0x1E, 0, True))

    assert reset_hook_state(
        processor,
        send_input=send_input,
        release_input=release_input,
    ) is True
    assert fallback_calls == [(0xA0, 0x2A, 0x0002, 0)]


def test_ctrl_then_left_shift_and_a_replays_shift_before_target_in_one_batch():
    processor = KeyboardHookProcessor()
    batches = []

    def send_input(count, inputs, cb_size):
        batches.append(
            tuple(
                (inputs[index].ki.wScan, inputs[index].ki.dwFlags)
                for index in range(count)
            )
        )
        return count

    ctrl_down = dispatch_keyboard_event(
        KeyEvent(0xA2, 0x1D, 0, True),
        processor=processor,
        send_input=send_input,
    )
    shift_down = dispatch_keyboard_event(
        KeyEvent(0xA0, 0x2A, 0, True),
        processor=processor,
        send_input=send_input,
    )
    target_down = dispatch_keyboard_event(
        KeyEvent(0x41, 0x1E, 0, True),
        processor=processor,
        send_input=send_input,
    )

    assert ctrl_down.forward is True
    assert shift_down.forward is False
    assert target_down.forward is False
    assert batches == [((0x2A, 0x0008), (0x1E, 0x0008))]

    assert dispatch_keyboard_event(
        KeyEvent(0x41, 0x1E, 0x80, False),
        processor=processor,
        send_input=send_input,
    ).forward is True
    assert dispatch_keyboard_event(
        KeyEvent(0xA0, 0x2A, 0x80, False),
        processor=processor,
        send_input=send_input,
    ).forward is False
    assert dispatch_keyboard_event(
        KeyEvent(0xA2, 0x1D, 0x80, False),
        processor=processor,
        send_input=send_input,
    ).forward is True
    assert batches[-1] == ((0x2A, 0x0008 | 0x0002),)


def test_ctrl_alt_shift_a_matrix_preserves_modifiers_and_matching_releases():
    modifiers = (
        (0x11, 0x1D),
        (0xA2, 0x1D),
        (0xA3, 0x1D),
        (0x12, 0x38),
        (0xA4, 0x38),
        (0xA5, 0x38),
    )
    shifts = ((0xA0, 0x2A), (0xA1, 0x36))

    for modifier, modifier_scan in modifiers:
        for shift, shift_scan in shifts:
            for shift_first in (False, True):
                processor = KeyboardHookProcessor()
                batches = []

                def send_input(count, inputs, cb_size):
                    batches.append(
                        tuple(
                            (inputs[index].ki.wScan, inputs[index].ki.dwFlags)
                            for index in range(count)
                        )
                    )
                    return count

                modifier_down = KeyEvent(modifier, modifier_scan, 0, True)
                shift_down = KeyEvent(shift, shift_scan, 0, True)
                ordered_downs = (
                    (shift_down, modifier_down)
                    if shift_first else (modifier_down, shift_down)
                )
                decisions = [
                    dispatch_keyboard_event(
                        event,
                        processor=processor,
                        send_input=send_input,
                    )
                    for event in ordered_downs
                ]

                assert any(decision.forward is True for decision in decisions)
                assert any(decision.forward is False for decision in decisions)
                target_down = dispatch_keyboard_event(
                    KeyEvent(0x41, 0x1E, 0, True),
                    processor=processor,
                    send_input=send_input,
                )
                assert target_down.forward is False
                assert batches == [
                    ((shift_scan, 0x0008), (0x1E, 0x0008)),
                ]

                assert dispatch_keyboard_event(
                    KeyEvent(0x41, 0x1E, 0x80, False),
                    processor=processor,
                    send_input=send_input,
                ).forward is True
                assert dispatch_keyboard_event(
                    KeyEvent(shift, shift_scan, 0x80, False),
                    processor=processor,
                    send_input=send_input,
                ).forward is False
                assert dispatch_keyboard_event(
                    KeyEvent(modifier, modifier_scan, 0x80, False),
                    processor=processor,
                    send_input=send_input,
                ).forward is True
                assert batches[-1] == (
                    (shift_scan, 0x0008 | 0x0002),
                )


def test_injected_shift_and_target_bypass_ctrl_alt_physical_state():
    for modifier, modifier_scan in ((0xA2, 0x1D), (0xA5, 0x38)):
        processor = KeyboardHookProcessor()
        batches = []

        def send_input(count, inputs, cb_size):
            batches.append(count)
            return count

        assert dispatch_keyboard_event(
            KeyEvent(modifier, modifier_scan, 0, True),
            processor=processor,
            send_input=send_input,
        ).forward is True
        assert dispatch_keyboard_event(
            KeyEvent(0xA0, 0x2A, 0x10, True),
            processor=processor,
            send_input=send_input,
        ).forward is True
        assert dispatch_keyboard_event(
            KeyEvent(0x41, 0x1E, 0x10, True),
            processor=processor,
            send_input=send_input,
        ).forward is True
        assert dispatch_keyboard_event(
            KeyEvent(0xA0, 0x2A, 0x90, False),
            processor=processor,
            send_input=send_input,
        ).forward is True
        assert batches == []


def test_capslock_hook_lifecycle_reset_uses_optional_engine_capability():
    class ResettableEngine:
        def __init__(self):
            self.reset_calls = 0

        def reset(self):
            self.reset_calls += 1

    class LegacyFakeEngine:
        pass

    resettable = ResettableEngine()
    assert reset_capslock_state(resettable) is True
    assert resettable.reset_calls == 1
    assert reset_capslock_state(LegacyFakeEngine()) is True


def test_injected_capslock_bypasses_engine_at_public_hook_seam():
    class RejectingCapsEngine:
        def __init__(self):
            self.called = False

        def on_key_event(self, vk_code, is_down):
            self.called = True
            raise AssertionError("injected CapsLock reached engine")

    caps_engine = RejectingCapsEngine()
    decision = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x10, True),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )

    assert decision.forward is True
    assert decision.replay == ()
    assert caps_engine.called is False


def test_capslock_hook_returns_before_blocking_scope_resolution_finishes():
    class BlockingIME:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.returned = threading.Event()
            self.toggled = threading.Event()
            self.checker_thread = None
            self.toggle_calls = 0

        def can_toggle(self):
            self.checker_thread = threading.get_ident()
            self.entered.set()
            self.release.wait(1.0)
            self.returned.set()
            return True

        def toggle(self):
            self.toggle_calls += 1
            self.toggled.set()

        def current_state(self):
            return IMEState.ENGLISH

    class NoopLED:
        def on(self):
            pass

        def off(self):
            pass

    ime = BlockingIME()
    caps_engine = CapsLockIME(ime_adapter=ime, led_adapter=NoopLED())
    hook_thread = threading.get_ident()

    started = time.monotonic()
    down = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    down_elapsed = time.monotonic() - started
    started = time.monotonic()
    up = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    up_elapsed = time.monotonic() - started

    checker_entered = ime.entered.wait(0.2)
    checker_returned_before_release = ime.returned.is_set()
    ime.release.set()

    assert down.forward is False
    assert up.forward is False
    assert down_elapsed < 0.2
    assert up_elapsed < 0.2
    assert checker_entered is True
    assert checker_returned_before_release is False
    assert ime.checker_thread != hook_thread
    assert ime.toggled.wait(0.5)
    assert ime.toggle_calls == 1


def test_unsupported_short_press_replays_one_native_capslock_tap():
    class UnsupportedIME:
        def __init__(self):
            self.toggle_calls = 0

        def can_toggle(self):
            return False

        def toggle(self):
            self.toggle_calls += 1

        def current_state(self):
            return IMEState.ENGLISH

    class NoopLED:
        def on(self):
            pass

        def off(self):
            pass

    class NativeCapsLock:
        def __init__(self):
            self.tap_calls = 0
            self.tapped = threading.Event()

        def tap(self):
            self.tap_calls += 1
            self.tapped.set()

    ime = UnsupportedIME()
    native = NativeCapsLock()
    caps_engine = CapsLockIME(
        ime_adapter=ime,
        led_adapter=NoopLED(),
        native_adapter=native,
    )

    down = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    up = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )

    assert down.forward is False
    assert up.forward is False
    assert native.tapped.wait(0.5)
    assert native.tap_calls == 1
    assert ime.toggle_calls == 0


def test_scope_error_replays_native_capslock_instead_of_swallowing_press():
    class FailingIME:
        def __init__(self):
            self.toggle_calls = 0

        def can_toggle(self):
            raise OSError("scope unavailable")

        def toggle(self):
            self.toggle_calls += 1

        def current_state(self):
            return IMEState.ENGLISH

    class NoopLED:
        def on(self):
            pass

        def off(self):
            pass

    class NativeCapsLock:
        def __init__(self):
            self.tap_calls = 0
            self.tapped = threading.Event()

        def tap(self):
            self.tap_calls += 1
            self.tapped.set()

    ime = FailingIME()
    native = NativeCapsLock()
    caps_engine = CapsLockIME(
        ime_adapter=ime,
        led_adapter=NoopLED(),
        native_adapter=native,
    )

    down = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    up = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )

    assert down.forward is False
    assert up.forward is False
    assert native.tapped.wait(0.5)
    assert native.tap_calls == 1
    assert ime.toggle_calls == 0


def test_supported_long_press_uses_worker_scope_and_uppercase_without_sleep():
    class Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    class SupportedIME:
        def __init__(self):
            self.can_toggle_calls = 0
            self.toggle_calls = 0

        def can_toggle(self):
            self.can_toggle_calls += 1
            return True

        def toggle(self):
            self.toggle_calls += 1

        def current_state(self):
            return IMEState.ENGLISH

    class RecordingLED:
        def __init__(self):
            self.on_calls = 0
            self.turned_on = threading.Event()

        def on(self):
            self.on_calls += 1
            self.turned_on.set()

        def off(self):
            pass

    class RejectingNativeCapsLock:
        def tap(self):
            raise AssertionError("supported long press replayed native CapsLock")

    clock = Clock()
    ime = SupportedIME()
    led = RecordingLED()
    caps_engine = CapsLockIME(
        long_press_threshold=1.0,
        ime_adapter=ime,
        led_adapter=led,
        native_adapter=RejectingNativeCapsLock(),
        clock=clock,
    )

    down = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    clock.now = 1.01
    up = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )

    assert down.forward is False
    assert up.forward is False
    assert led.turned_on.wait(0.5)
    assert ime.can_toggle_calls == 1
    assert ime.toggle_calls == 0
    assert led.on_calls == 1


def test_unsupported_long_press_replays_exactly_one_native_capslock_tap():
    class Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    class UnsupportedIME:
        def __init__(self):
            self.can_toggle_calls = 0

        def can_toggle(self):
            self.can_toggle_calls += 1
            return False

        def toggle(self):
            raise AssertionError("unsupported long press toggled IME")

        def current_state(self):
            return IMEState.ENGLISH

    class RejectingLED:
        def on(self):
            raise AssertionError("unsupported long press enabled uppercase")

        def off(self):
            pass

    class NativeCapsLock:
        def __init__(self):
            self.tap_calls = 0
            self.tapped = threading.Event()

        def tap(self):
            self.tap_calls += 1
            self.tapped.set()

    clock = Clock()
    ime = UnsupportedIME()
    native = NativeCapsLock()
    caps_engine = CapsLockIME(
        long_press_threshold=1.0,
        ime_adapter=ime,
        led_adapter=RejectingLED(),
        native_adapter=native,
        clock=clock,
    )

    down = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    clock.now = 2.0
    up = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )

    assert down.forward is False
    assert up.forward is False
    assert native.tapped.wait(0.5)
    assert native.tap_calls == 1
    assert ime.can_toggle_calls == 1


def test_reset_cancels_gesture_whose_scope_check_is_already_blocked():
    class BlockingIME:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.toggle_calls = 0

        def can_toggle(self):
            self.entered.set()
            self.release.wait(1.0)
            return True

        def toggle(self):
            self.toggle_calls += 1

        def current_state(self):
            return IMEState.ENGLISH

    class RecordingLED:
        def __init__(self):
            self.off_calls = 0
            self.turned_off = threading.Event()

        def on(self):
            pass

        def off(self):
            self.off_calls += 1
            self.turned_off.set()

    class NativeCapsLock:
        def __init__(self):
            self.tap_calls = 0

        def tap(self):
            self.tap_calls += 1

    ime = BlockingIME()
    led = RecordingLED()
    native = NativeCapsLock()
    caps_engine = CapsLockIME(
        ime_adapter=ime,
        led_adapter=led,
        native_adapter=native,
    )

    dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=KeyboardHookProcessor(),
    )
    assert ime.entered.wait(0.5)

    caps_engine.reset()
    ime.release.set()

    assert led.turned_off.wait(0.5)
    assert ime.toggle_calls == 0
    assert native.tap_calls == 0
    assert led.off_calls == 1


def test_capslock_repeat_and_duplicate_up_are_blocked_as_one_gesture():
    class SupportedIME:
        def __init__(self):
            self.can_toggle_calls = 0
            self.toggle_calls = 0
            self.toggled = threading.Event()

        def can_toggle(self):
            self.can_toggle_calls += 1
            return True

        def toggle(self):
            self.toggle_calls += 1
            self.toggled.set()

        def current_state(self):
            return IMEState.ENGLISH

    class NoopLED:
        def on(self):
            pass

        def off(self):
            pass

    class RejectingNativeCapsLock:
        def tap(self):
            raise AssertionError("supported CapsLock gesture was replayed")

    ime = SupportedIME()
    caps_engine = CapsLockIME(
        ime_adapter=ime,
        led_adapter=NoopLED(),
        native_adapter=RejectingNativeCapsLock(),
    )
    processor = KeyboardHookProcessor()

    decisions = [
        dispatch_hook_event(
            KeyEvent(0x14, 0x3A, 0, True),
            caps_engine=caps_engine,
            processor=processor,
        ),
        dispatch_hook_event(
            KeyEvent(0x14, 0x3A, 0, True),
            caps_engine=caps_engine,
            processor=processor,
        ),
        dispatch_hook_event(
            KeyEvent(0x14, 0x3A, 0, True),
            caps_engine=caps_engine,
            processor=processor,
        ),
        dispatch_hook_event(
            KeyEvent(0x14, 0x3A, 0x80, False),
            caps_engine=caps_engine,
            processor=processor,
        ),
        dispatch_hook_event(
            KeyEvent(0x14, 0x3A, 0x80, False),
            caps_engine=caps_engine,
            processor=processor,
        ),
    ]

    assert all(decision.forward is False for decision in decisions)
    assert ime.toggled.wait(0.5)
    assert ime.can_toggle_calls == 1
    assert ime.toggle_calls == 1


def test_unknown_scope_replays_one_injected_tap_without_recursion():
    class UnknownIME:
        def __init__(self):
            self.can_toggle_calls = 0
            self.toggle_calls = 0

        def can_toggle(self):
            self.can_toggle_calls += 1
            return "unknown"

        def toggle(self):
            self.toggle_calls += 1

        def current_state(self):
            return IMEState.ENGLISH

    class NoopLED:
        def on(self):
            pass

        def off(self):
            pass

    class LoopbackNativeCapsLock:
        def __init__(self):
            self.caps_engine = None
            self.processor = KeyboardHookProcessor()
            self.tap_calls = 0
            self.replay_decisions = []
            self.tapped = threading.Event()

        def tap(self):
            self.tap_calls += 1
            self.replay_decisions.extend(
                (
                    dispatch_hook_event(
                        KeyEvent(0x14, 0x3A, 0x10, True),
                        caps_engine=self.caps_engine,
                        processor=self.processor,
                    ),
                    dispatch_hook_event(
                        KeyEvent(0x14, 0x3A, 0x90, False),
                        caps_engine=self.caps_engine,
                        processor=self.processor,
                    ),
                ),
            )
            self.tapped.set()

    ime = UnknownIME()
    native = LoopbackNativeCapsLock()
    caps_engine = CapsLockIME(
        ime_adapter=ime,
        led_adapter=NoopLED(),
        native_adapter=native,
    )
    native.caps_engine = caps_engine
    processor = KeyboardHookProcessor()

    physical_down = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=processor,
    )
    physical_up = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=processor,
    )

    assert physical_down.forward is False
    assert physical_up.forward is False
    assert native.tapped.wait(0.5)
    assert native.tap_calls == 1
    assert [decision.forward for decision in native.replay_decisions] == [True, True]
    assert ime.can_toggle_calls == 1
    assert ime.toggle_calls == 0


def test_missing_scope_resolver_falls_back_to_native_capslock():
    class UnresolvedIME:
        def __init__(self):
            self.toggle_calls = 0

        def toggle(self):
            self.toggle_calls += 1

        def current_state(self):
            return IMEState.ENGLISH

    class NoopLED:
        def on(self):
            pass

        def off(self):
            pass

    class NativeCapsLock:
        def __init__(self):
            self.tap_calls = 0
            self.tapped = threading.Event()

        def tap(self):
            self.tap_calls += 1
            self.tapped.set()

    ime = UnresolvedIME()
    native = NativeCapsLock()
    caps_engine = CapsLockIME(
        ime_adapter=ime,
        led_adapter=NoopLED(),
        native_adapter=native,
    )
    processor = KeyboardHookProcessor()

    down = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0, True),
        caps_engine=caps_engine,
        processor=processor,
    )
    up = dispatch_hook_event(
        KeyEvent(0x14, 0x3A, 0x80, False),
        caps_engine=caps_engine,
        processor=processor,
    )

    assert down.forward is False
    assert up.forward is False
    assert native.tapped.wait(0.5)
    assert native.tap_calls == 1
    assert ime.toggle_calls == 0


def test_native_capslock_boundary_submits_one_complete_injected_tap():
    submitted = []
    replay = Win32NativeCapsLock(
        key_event=lambda vk, scan, flags, extra: submitted.append(
            (vk, scan, flags, extra),
        ),
    )

    replay.tap()

    assert submitted == [
        (0x14, 0, 0, 0),
        (0x14, 0, 0x0002, 0),
    ]
