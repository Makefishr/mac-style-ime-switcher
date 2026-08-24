"""Tests for the two supported switching scopes."""
import ctypes
from contextlib import ExitStack, contextmanager
import threading
import time
from ctypes import wintypes
import unittest
from unittest.mock import patch

from ime_switcher import config, toggle
from ime_switcher.caps_ime import CapsLockIME, VK_CAPITAL
from ime_switcher.settings import MODE_IME, MODE_LAYOUT


def fill_microsoft_pinyin_description(_hkl, buffer, _size):
    buffer.value = "Microsoft Pinyin"
    return len(buffer.value)


def can_switch_in_active_context(mode, *, language, description):
    def describe(_hkl, buffer, _size):
        if isinstance(description, Exception):
            raise description
        buffer.value = description
        return len(description)

    with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
         patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
         patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
         patch.object(toggle.user32, "GetKeyboardLayout", return_value=language), \
         patch.object(toggle.imm32, "ImmGetDescriptionW", side_effect=describe):
        return toggle.can_switch(mode)


class MessageIMEBoundary:
    def __init__(
        self,
        *,
        conversion,
        open_status,
        conversion_reads=None,
        open_reads=None,
        fail_controls=(),
        raise_controls=(),
        raise_error=None,
        apply_conversion=True,
        open_sets_native=False,
    ):
        self.conversion = conversion
        self.open_status = open_status
        self.conversion_reads = list(conversion_reads or ())
        self.open_reads = list(open_reads or ())
        self.fail_controls = set(fail_controls)
        self.raise_controls = set(raise_controls)
        self.raise_error = raise_error or OSError("IME control unavailable")
        self.apply_conversion = apply_conversion
        self.open_sets_native = open_sets_native
        self.calls = []

    def send_message_timeout(
        self,
        hwnd,
        message,
        control,
        value,
        flags,
        timeout,
        result,
    ):
        self.calls.append((control, value, flags, timeout))
        if control in self.raise_controls:
            raise self.raise_error
        if control in self.fail_controls:
            return 0

        if control == config.IMC_GETCONVERSIONMODE:
            observed = (
                self.conversion_reads.pop(0)
                if self.conversion_reads
                else self.conversion
            )
            if isinstance(observed, Exception):
                raise observed
            if observed is None:
                return 0
            result._obj.value = observed
        elif control == config.IMC_SETCONVERSIONMODE:
            if self.apply_conversion:
                self.conversion = value
            result._obj.value = 1
        elif control == config.IMC_GETOPENSTATUS:
            observed = (
                self.open_reads.pop(0)
                if self.open_reads
                else self.open_status
            )
            if isinstance(observed, Exception):
                raise observed
            if observed is None:
                return 0
            result._obj.value = int(observed)
        elif control == config.IMC_SETOPENSTATUS:
            self.open_status = bool(value)
            if self.open_status and self.open_sets_native:
                self.conversion |= config.IME_CMODE_NATIVE
            result._obj.value = 1
        else:
            raise AssertionError(f"unexpected IME control {control:#x}")
        return 1


@contextmanager
def active_message_ime(boundary, *, description="Microsoft Pinyin"):
    def describe(_hkl, buffer, _size):
        buffer.value = description
        return len(description)

    with ExitStack() as stack:
        stack.enter_context(patch.object(
            toggle.user32,
            "GetForegroundWindow",
            return_value=100,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "GetWindowThreadProcessId",
            return_value=77,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "GetGUIThreadInfo",
            return_value=False,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "GetKeyboardLayout",
            return_value=config.LANGID_ZH_CN,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetDescriptionW",
            side_effect=describe,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetContext",
            return_value=0,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetDefaultIMEWnd",
            return_value=300,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "SendMessageTimeoutW",
            side_effect=boundary.send_message_timeout,
            create=True,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "LoadKeyboardLayoutW",
            side_effect=AssertionError("IME mode loaded a keyboard layout"),
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "PostMessageW",
            side_effect=AssertionError("IME mode posted a layout change"),
        ))
        yield boundary


@contextmanager
def active_direct_ime(*, failing_operation, failure=None):
    failure = failure or OSError("do-not-log-value")
    state = {
        "conversion": config.IME_CMODE_NATIVE,
        "open": True,
        "sentence": 0x0008,
    }

    def get_conversion(_himc, conversion, sentence):
        if failing_operation == "get_conversion":
            raise failure
        conversion._obj.value = state["conversion"]
        sentence._obj.value = state["sentence"]
        return True

    def set_conversion(_himc, mode, _sentence):
        if failing_operation == "set_conversion":
            raise failure
        state["conversion"] = mode
        return True

    def get_open(_himc):
        if failing_operation == "get_open":
            raise failure
        return state["open"]

    def set_open(_himc, value):
        if failing_operation == "set_open":
            raise failure
        state["open"] = bool(value)
        return True

    with ExitStack() as stack:
        stack.enter_context(patch.object(
            toggle.user32,
            "GetForegroundWindow",
            return_value=100,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "GetWindowThreadProcessId",
            return_value=77,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "GetGUIThreadInfo",
            return_value=False,
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "GetKeyboardLayout",
            return_value=config.LANGID_ZH_CN,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetDescriptionW",
            side_effect=fill_microsoft_pinyin_description,
        ))
        get_context = stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetContext",
            return_value=300,
        ))
        release_context = stack.enter_context(patch.object(
            toggle.imm32,
            "ImmReleaseContext",
            return_value=True,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetConversionStatus",
            side_effect=get_conversion,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmSetConversionStatus",
            side_effect=set_conversion,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetOpenStatus",
            side_effect=get_open,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmSetOpenStatus",
            side_effect=set_open,
        ))
        stack.enter_context(patch.object(
            toggle.imm32,
            "ImmGetDefaultIMEWnd",
            side_effect=AssertionError("direct HIMC exception used message fallback"),
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "LoadKeyboardLayoutW",
            side_effect=AssertionError("IME mode loaded a layout"),
        ))
        stack.enter_context(patch.object(
            toggle.user32,
            "PostMessageW",
            side_effect=AssertionError("IME mode posted a layout change"),
        ))
        yield state, get_context, release_context


class TestSwitchScopes(unittest.TestCase):
    def test_ime_mode_rejects_unknown_and_fuzzy_pinyin_descriptions(self):
        for description in (
            "Some Other IME",
            "Microsoft Pinyin Extra",
            "微软拼音输入法测试",
        ):
            with self.subTest(description=description):
                self.assertFalse(can_switch_in_active_context(
                    MODE_IME,
                    language=config.LANGID_ZH_CN,
                    description=description,
                ))

    def test_ime_mode_accepts_only_normalized_microsoft_pinyin_names(self):
        for description in (
            "微软拼音",
            "Microsoft Pinyin",
            "  MICROSOFT   PINYIN  ",
            "Ｍｉｃｒｏｓｏｆｔ　Ｐｉｎｙｉｎ",
        ):
            with self.subTest(description=description):
                self.assertTrue(can_switch_in_active_context(
                    MODE_IME,
                    language=config.LANGID_ZH_CN,
                    description=description,
                ))

    def test_ime_mode_fails_closed_when_description_query_raises(self):
        self.assertFalse(can_switch_in_active_context(
            MODE_IME,
            language=config.LANGID_ZH_CN,
            description=OSError("description unavailable"),
        ))

    def test_scope_errors_log_only_safe_exception_context(self):
        error = RuntimeError("private-scope-marker")
        with self.assertLogs(
            "ime_switcher.toggle",
            level="WARNING",
        ) as logs:
            self.assertFalse(can_switch_in_active_context(
                MODE_IME,
                language=config.LANGID_ZH_CN,
                description=error,
            ))

        log_output = "\n".join(logs.output)
        self.assertIn("RuntimeError", log_output)
        self.assertNotIn(str(error), log_output)

    def test_ime_mode_fails_closed_when_window_thread_query_fails(self):
        with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
             patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=0), \
             patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
             patch.object(
                 toggle.user32,
                 "GetKeyboardLayout",
                 return_value=config.LANGID_ZH_CN,
             ), \
             patch.object(
                 toggle.imm32,
                 "ImmGetDescriptionW",
                 side_effect=fill_microsoft_pinyin_description,
             ):
            self.assertFalse(toggle.can_switch(MODE_IME))

    def test_ime_mode_rejects_an_empty_description(self):
        self.assertFalse(can_switch_in_active_context(
            MODE_IME,
            language=config.LANGID_ZH_CN,
            description="",
        ))

    def test_layout_mode_uses_only_the_explicit_language_allowlist(self):
        for language, expected in (
            (config.LANGID_ZH_CN, True),
            (config.LANGID_EN_US, True),
            (0x0411, False),
        ):
            with self.subTest(language=language):
                self.assertEqual(
                    can_switch_in_active_context(
                        MODE_LAYOUT,
                        language=language,
                        description=AssertionError(
                            "layout mode queried the IME description",
                        ),
                    ),
                    expected,
                )

    def test_layout_mode_switches_zh_cn_to_en_us_without_conversion_io(self):
        with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
             patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
             patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
             patch.object(
                 toggle.user32,
                 "GetKeyboardLayout",
                 return_value=config.LANGID_ZH_CN,
             ), \
             patch.object(
                 toggle.imm32,
                 "ImmGetContext",
                 side_effect=AssertionError("layout mode queried conversion mode"),
             ), \
             patch.object(
                 toggle.user32,
                 "LoadKeyboardLayoutW",
                 return_value=config.LANGID_EN_US,
             ) as load_layout, \
             patch.object(toggle.user32, "PostMessageW", return_value=True) as post:
            self.assertTrue(toggle.switch_ime(MODE_LAYOUT))

        load_layout.assert_called_once_with(
            config.LAYOUT_EN_US,
            config.KLF_NOTELLSHELL,
        )
        post.assert_called_once_with(
            100,
            config.WM_INPUTLANGCHANGEREQUEST,
            0,
            config.LANGID_EN_US,
        )

    def test_layout_mode_switches_en_us_to_zh_cn_without_conversion_io(self):
        with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
             patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
             patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
             patch.object(
                 toggle.user32,
                 "GetKeyboardLayout",
                 return_value=config.LANGID_EN_US,
             ), \
             patch.object(
                 toggle.imm32,
                 "ImmGetContext",
                 side_effect=AssertionError("layout mode wrote conversion mode"),
             ), \
             patch.object(
                 toggle.user32,
                 "LoadKeyboardLayoutW",
                 return_value=config.LANGID_ZH_CN,
             ) as load_layout, \
             patch.object(toggle.user32, "PostMessageW", return_value=True) as post:
            self.assertTrue(toggle.switch_ime(MODE_LAYOUT))

        load_layout.assert_called_once_with(
            config.LAYOUT_ZH_CN,
            config.KLF_NOTELLSHELL,
        )
        post.assert_called_once_with(
            100,
            config.WM_INPUTLANGCHANGEREQUEST,
            0,
            config.LANGID_ZH_CN,
        )

    def test_layout_mode_rejects_other_languages_without_loading_or_posting(self):
        self.assertFalse(can_switch_in_active_context(
            MODE_LAYOUT,
            language=0x0411,
            description=AssertionError("layout mode queried IME description"),
        ))

        with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
             patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
             patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
             patch.object(toggle.user32, "GetKeyboardLayout", return_value=0x0411), \
             patch.object(
                 toggle.user32,
                 "LoadKeyboardLayoutW",
                 side_effect=AssertionError("unsupported language loaded a layout"),
             ), \
             patch.object(
                 toggle.user32,
                 "PostMessageW",
                 side_effect=AssertionError("unsupported language posted a layout"),
             ):
            self.assertFalse(toggle.switch_ime(MODE_LAYOUT))

    def test_layout_mode_returns_false_when_target_layout_cannot_load(self):
        for language, target_layout in (
            (config.LANGID_ZH_CN, config.LAYOUT_EN_US),
            (config.LANGID_EN_US, config.LAYOUT_ZH_CN),
        ):
            with self.subTest(language=language), \
                 patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
                 patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
                 patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
                 patch.object(toggle.user32, "GetKeyboardLayout", return_value=language), \
                 patch.object(
                     toggle.user32,
                     "LoadKeyboardLayoutW",
                     return_value=0,
                 ) as load_layout, \
                 patch.object(
                     toggle.user32,
                     "PostMessageW",
                     side_effect=AssertionError("posted an unavailable layout"),
                 ):
                self.assertFalse(toggle.switch_ime(MODE_LAYOUT))

            load_layout.assert_called_once_with(
                target_layout,
                config.KLF_NOTELLSHELL,
            )

    def test_layout_mode_returns_false_when_post_fails_or_raises(self):
        for post_effect in (0, OSError("post unavailable")):
            with self.subTest(post_effect=post_effect), \
                 patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
                 patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
                 patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
                 patch.object(
                     toggle.user32,
                     "GetKeyboardLayout",
                     return_value=config.LANGID_ZH_CN,
                 ), \
                 patch.object(
                     toggle.user32,
                     "LoadKeyboardLayoutW",
                     return_value=config.LANGID_EN_US,
                 ), \
                 patch.object(
                     toggle.user32,
                     "PostMessageW",
                     side_effect=(
                         post_effect
                         if isinstance(post_effect, Exception)
                         else None
                     ),
                     return_value=(
                         post_effect
                         if not isinstance(post_effect, Exception)
                         else None
                     ),
                ):
                self.assertFalse(toggle.switch_ime(MODE_LAYOUT))

    def test_public_switch_fails_closed_on_unexpected_layout_boundary_errors(self):
        for error in (
            RuntimeError("private-runtime-marker"),
            ValueError("private-value-marker"),
        ):
            with self.subTest(error_type=type(error).__name__), \
                 patch.object(toggle, "_input_targets", return_value=(100, 100)), \
                 patch.object(
                     toggle,
                     "_current_language",
                     return_value=config.LANGID_ZH_CN,
                 ), \
                 patch.object(
                     toggle.user32,
                     "LoadKeyboardLayoutW",
                     side_effect=error,
                 ), \
                 self.assertLogs(
                     "ime_switcher.toggle",
                     level="WARNING",
                 ) as logs:
                self.assertFalse(toggle.switch_ime(MODE_LAYOUT))

            log_output = "\n".join(logs.output)
            self.assertIn(type(error).__name__, log_output)
            self.assertNotIn(str(error), log_output)

    def test_public_switch_does_not_swallow_process_control_exceptions(self):
        for error in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(error_type=type(error).__name__), \
                 patch.object(toggle, "_input_targets", return_value=(100, 100)), \
                 patch.object(
                     toggle,
                     "_current_language",
                     return_value=config.LANGID_ZH_CN,
                 ), \
                 patch.object(
                     toggle.user32,
                     "LoadKeyboardLayoutW",
                     side_effect=error,
                 ), \
                 self.assertRaises(type(error)):
                toggle.switch_ime(MODE_LAYOUT)

    def test_post_exceptions_fail_closed_without_logging_exception_values(self):
        for error in (
            OSError("private-post-os-marker"),
            RuntimeError("private-post-runtime-marker"),
            ValueError("private-post-value-marker"),
        ):
            with self.subTest(error_type=type(error).__name__), \
                 patch.object(toggle, "_input_targets", return_value=(100, 100)), \
                 patch.object(
                     toggle,
                     "_current_language",
                     return_value=config.LANGID_ZH_CN,
                 ), \
                 patch.object(
                     toggle.user32,
                     "LoadKeyboardLayoutW",
                     return_value=config.LANGID_EN_US,
                 ), \
                 patch.object(
                     toggle.user32,
                     "PostMessageW",
                     side_effect=error,
                 ), \
                 self.assertLogs(
                     "ime_switcher.toggle",
                     level="WARNING",
                 ) as logs:
                self.assertFalse(toggle.switch_ime(MODE_LAYOUT))

            log_output = "\n".join(logs.output)
            self.assertIn(type(error).__name__, log_output)
            self.assertNotIn(str(error), log_output)

    def test_ime_mode_changes_conversion_without_loading_a_layout(self):
        boundary = MessageIMEBoundary(
            conversion=config.IME_CMODE_NATIVE,
            open_status=True,
        )

        with active_message_ime(boundary):
            self.assertTrue(toggle.switch_ime(MODE_IME))

        self.assertEqual(boundary.conversion, 0)
        self.assertEqual(
            [control for control, *_rest in boundary.calls],
            [
                config.IMC_GETCONVERSIONMODE,
                config.IMC_GETOPENSTATUS,
                config.IMC_SETCONVERSIONMODE,
                config.IMC_SETOPENSTATUS,
                config.IMC_GETCONVERSIONMODE,
                config.IMC_GETOPENSTATUS,
            ],
        )

    def test_ime_mode_confirms_english_to_chinese_conversion_by_readback(self):
        boundary = MessageIMEBoundary(conversion=0, open_status=False)

        with active_message_ime(boundary):
            self.assertTrue(toggle.switch_ime(MODE_IME))

        self.assertEqual(boundary.conversion, config.IME_CMODE_NATIVE)
        self.assertTrue(boundary.open_status)
        self.assertEqual(
            [control for control, *_rest in boundary.calls],
            [
                config.IMC_GETCONVERSIONMODE,
                config.IMC_GETOPENSTATUS,
                config.IMC_SETOPENSTATUS,
                config.IMC_SETCONVERSIONMODE,
                config.IMC_GETCONVERSIONMODE,
                config.IMC_GETOPENSTATUS,
            ],
        )

    def test_ime_mode_rejects_success_when_conversion_set_fails_or_raises(self):
        for failure_kind in ("timeout", "exception"):
            boundary = MessageIMEBoundary(
                conversion=0,
                open_status=False,
                fail_controls=(
                    (config.IMC_SETCONVERSIONMODE,)
                    if failure_kind == "timeout"
                    else ()
                ),
                raise_controls=(
                    (config.IMC_SETCONVERSIONMODE,)
                    if failure_kind == "exception"
                    else ()
                ),
                open_sets_native=True,
            )

            with self.subTest(failure_kind=failure_kind), \
                 self.assertLogs("ime_switcher.toggle", level="WARNING") as logs, \
                 active_message_ime(boundary):
                self.assertFalse(toggle.switch_ime(MODE_IME))

            self.assertEqual(boundary.conversion, config.IME_CMODE_NATIVE)
            self.assertIn(
                config.IMC_SETCONVERSIONMODE,
                [control for control, *_rest in boundary.calls],
            )
            self.assertTrue(any(
                "WM_IME_CONTROL" in entry
                for entry in logs.output
            ))

    def test_ime_mode_rejects_success_when_open_status_set_fails_or_raises(self):
        for failure_kind in ("timeout", "exception"):
            boundary = MessageIMEBoundary(
                conversion=config.IME_CMODE_NATIVE,
                open_status=True,
                fail_controls=(
                    (config.IMC_SETOPENSTATUS,)
                    if failure_kind == "timeout"
                    else ()
                ),
                raise_controls=(
                    (config.IMC_SETOPENSTATUS,)
                    if failure_kind == "exception"
                    else ()
                ),
            )

            with self.subTest(failure_kind=failure_kind), \
                 self.assertLogs("ime_switcher.toggle", level="WARNING") as logs, \
                 active_message_ime(boundary):
                self.assertFalse(toggle.switch_ime(MODE_IME))

            self.assertTrue(any(
                "WM_IME_CONTROL" in entry
                for entry in logs.output
            ))

    def test_ime_mode_fails_closed_when_initial_state_get_fails_or_raises(self):
        for control in (
            config.IMC_GETCONVERSIONMODE,
            config.IMC_GETOPENSTATUS,
        ):
            for failure_kind in ("timeout", "exception"):
                boundary = MessageIMEBoundary(
                    conversion=config.IME_CMODE_NATIVE,
                    open_status=True,
                    fail_controls=(
                        (control,)
                        if failure_kind == "timeout"
                        else ()
                    ),
                    raise_controls=(
                        (control,)
                        if failure_kind == "exception"
                        else ()
                    ),
                )

                with self.subTest(control=control, failure_kind=failure_kind), \
                     self.assertLogs("ime_switcher.toggle", level="WARNING") as logs, \
                     active_message_ime(boundary):
                    self.assertFalse(toggle.switch_ime(MODE_IME))

                self.assertTrue(any(
                    "WM_IME_CONTROL" in entry
                    for entry in logs.output
                ))

    def test_message_boundary_exceptions_log_type_without_values(self):
        for error in (
            OSError("private-message-os-marker"),
            RuntimeError("private-message-runtime-marker"),
            ValueError("private-message-value-marker"),
        ):
            boundary = MessageIMEBoundary(
                conversion=config.IME_CMODE_NATIVE,
                open_status=True,
                raise_controls=(config.IMC_GETCONVERSIONMODE,),
                raise_error=error,
            )
            with self.subTest(error_type=type(error).__name__), self.assertLogs(
                "ime_switcher.toggle",
                level="WARNING",
            ) as logs, active_message_ime(boundary):
                self.assertFalse(toggle.switch_ime(MODE_IME))

            log_output = "\n".join(logs.output)
            self.assertIn(type(error).__name__, log_output)
            self.assertNotIn(str(error), log_output)

    def test_ime_mode_fails_closed_when_readback_fails_or_raises(self):
        for readback_name in ("conversion", "open"):
            for failure in (None, OSError("readback unavailable")):
                boundary = MessageIMEBoundary(
                    conversion=config.IME_CMODE_NATIVE,
                    open_status=True,
                    conversion_reads=(
                        [config.IME_CMODE_NATIVE, failure]
                        if readback_name == "conversion"
                        else None
                    ),
                    open_reads=(
                        [True, failure]
                        if readback_name == "open"
                        else None
                    ),
                )

                with self.subTest(
                    readback_name=readback_name,
                    failure=failure,
                ), self.assertLogs(
                    "ime_switcher.toggle",
                    level="WARNING",
                ) as logs, active_message_ime(boundary):
                    self.assertFalse(toggle.switch_ime(MODE_IME))

                self.assertTrue(any(
                    "WM_IME_CONTROL" in entry
                    for entry in logs.output
                ))

    def test_ime_mode_returns_false_when_readback_native_bit_is_unchanged(self):
        boundary = MessageIMEBoundary(
            conversion=config.IME_CMODE_NATIVE,
            open_status=True,
            conversion_reads=(
                config.IME_CMODE_NATIVE,
                config.IME_CMODE_NATIVE,
            ),
        )

        with self.assertLogs(
            "ime_switcher.toggle",
            level="WARNING",
        ) as logs, active_message_ime(boundary):
            self.assertFalse(toggle.switch_ime(MODE_IME))

        flattened_logs = " ".join("\n".join(logs.output).split())
        self.assertIn("切换结果未确认，已取消本次切换", flattened_logs)
        self.assertNotIn("Shift 回退", flattened_logs)

    def test_ime_mode_readback_ignores_unrelated_conversion_bits(self):
        initial_unrelated = 0x0020
        observed_unrelated = 0x0040
        boundary = MessageIMEBoundary(
            conversion=config.IME_CMODE_NATIVE | initial_unrelated,
            open_status=True,
            conversion_reads=(
                config.IME_CMODE_NATIVE | initial_unrelated,
                observed_unrelated,
            ),
        )

        with active_message_ime(boundary):
            self.assertTrue(toggle.switch_ime(MODE_IME))

    def test_direct_himc_get_conversion_exception_fails_closed_and_releases(self):
        with self.assertLogs(
            "ime_switcher.toggle",
            level="WARNING",
        ) as logs, active_direct_ime(
            failing_operation="get_conversion",
        ) as (_state, get_context, release_context):
            self.assertFalse(toggle.switch_ime(MODE_IME))

        self.assertEqual(release_context.call_count, get_context.call_count)
        self.assertNotIn("do-not-log-value", "\n".join(logs.output))

    def test_direct_himc_set_conversion_exception_fails_closed_and_releases(self):
        with self.assertLogs(
            "ime_switcher.toggle",
            level="WARNING",
        ) as logs, active_direct_ime(
            failing_operation="set_conversion",
        ) as (_state, get_context, release_context):
            self.assertFalse(toggle.switch_ime(MODE_IME))

        self.assertEqual(release_context.call_count, get_context.call_count)
        self.assertNotIn("do-not-log-value", "\n".join(logs.output))

    def test_direct_himc_get_open_exception_fails_closed_and_releases(self):
        with self.assertLogs(
            "ime_switcher.toggle",
            level="WARNING",
        ) as logs, active_direct_ime(
            failing_operation="get_open",
        ) as (_state, get_context, release_context):
            self.assertFalse(toggle.switch_ime(MODE_IME))

        self.assertEqual(release_context.call_count, get_context.call_count)
        self.assertNotIn("do-not-log-value", "\n".join(logs.output))

    def test_direct_himc_set_open_exception_fails_closed_and_releases(self):
        with self.assertLogs(
            "ime_switcher.toggle",
            level="WARNING",
        ) as logs, active_direct_ime(
            failing_operation="set_open",
        ) as (_state, get_context, release_context):
            self.assertFalse(toggle.switch_ime(MODE_IME))

        self.assertEqual(release_context.call_count, get_context.call_count)
        self.assertNotIn("do-not-log-value", "\n".join(logs.output))

    def test_direct_himc_unexpected_exceptions_fail_closed_and_release(self):
        cases = (
            ("get_conversion", RuntimeError("private-get-conversion-marker")),
            ("set_conversion", ValueError("private-set-conversion-marker")),
            ("get_open", RuntimeError("private-get-open-marker")),
            ("set_open", ValueError("private-set-open-marker")),
        )
        for operation, error in cases:
            with self.subTest(operation=operation), self.assertLogs(
                "ime_switcher.toggle",
                level="WARNING",
            ) as logs, active_direct_ime(
                failing_operation=operation,
                failure=error,
            ) as (_state, get_context, release_context):
                self.assertFalse(toggle.switch_ime(MODE_IME))

            self.assertEqual(release_context.call_count, get_context.call_count)
            log_output = "\n".join(logs.output)
            self.assertIn(type(error).__name__, log_output)
            self.assertNotIn(str(error), log_output)

    def test_ime_control_timeout_is_bounded_and_does_not_use_send_message(self):
        def timed_out(*args):
            args[-1]._obj.value = 0
            return 0

        with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
             patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
             patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
             patch.object(toggle.user32, "GetKeyboardLayout", return_value=0x0804), \
             patch.object(
                 toggle.imm32,
                 "ImmGetDescriptionW",
                 side_effect=fill_microsoft_pinyin_description,
             ), \
             patch.object(toggle.imm32, "ImmGetContext", return_value=0), \
             patch.object(toggle.imm32, "ImmGetDefaultIMEWnd", return_value=300), \
             patch.object(
                 toggle.user32,
                 "SendMessageTimeoutW",
                 side_effect=timed_out,
                 create=True,
             ) as send_timeout, \
             patch.object(
                 toggle.user32,
                 "SendMessageW",
                 side_effect=AssertionError("unbounded SendMessageW called"),
                 create=True,
             ) as send_message:
            self.assertFalse(toggle.switch_ime(MODE_IME))

        send_message.assert_not_called()
        self.assertGreaterEqual(send_timeout.call_count, 1)
        timeout_call = send_timeout.call_args
        self.assertTrue(timeout_call.args[4] & config.SMTO_ABORTIFHUNG)
        self.assertLessEqual(timeout_call.args[5], 100)

    def test_ime_mode_uses_focus_control_default_ime_window(self):
        """The public IME switch must target the focused child, not its owner."""
        class ExpectedGUIThreadInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        state = {"conversion": config.IME_CMODE_NATIVE, "open": True}

        def get_gui_thread_info(_thread_id, info_ptr):
            info = info_ptr._obj
            # Model the Win32 ABI: the API rejects the old undersized struct.
            if ctypes.sizeof(info) != ctypes.sizeof(ExpectedGUIThreadInfo):
                return False
            info.hwndActive = 100
            info.hwndFocus = 200
            info.hwndCaret = 200
            return True

        def send_message(hwnd, message, control, value):
            self.assertEqual(hwnd, 300)
            self.assertEqual(message, config.WM_IME_CONTROL)
            if control == config.IMC_GETCONVERSIONMODE:
                return state["conversion"]
            if control == config.IMC_SETCONVERSIONMODE:
                state["conversion"] = value
                return 1
            if control == 0x0005:  # IMC_GETOPENSTATUS
                return int(state["open"])
            if control == 0x0006:  # IMC_SETOPENSTATUS
                state["open"] = bool(value)
                return 1
            self.fail(f"unexpected WM_IME_CONTROL={control:#x}")

        def send_message_timeout(
            hwnd, message, control, value, _flags, _timeout, result,
        ):
            result._obj.value = send_message(hwnd, message, control, value)
            return 1

        with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
             patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
             patch.object(toggle.user32, "GetGUIThreadInfo", side_effect=get_gui_thread_info), \
             patch.object(toggle.user32, "GetKeyboardLayout", return_value=0x0804), \
             patch.object(
                 toggle.imm32,
                 "ImmGetDescriptionW",
                 side_effect=fill_microsoft_pinyin_description,
             ), \
             patch.object(toggle.imm32, "ImmGetContext", return_value=0), \
             patch.object(
                 toggle.imm32,
                 "ImmGetDefaultIMEWnd",
                 side_effect=lambda hwnd: 300 if hwnd == 200 else 0,
             ) as default_ime, \
             patch.object(
                 toggle.user32,
                 "SendMessageTimeoutW",
                 side_effect=send_message_timeout,
                 create=True,
             ):
            self.assertTrue(toggle.switch_ime(MODE_IME))

        self.assertEqual(state, {"conversion": 0, "open": False})
        self.assertTrue(default_ime.call_count)
        self.assertTrue(all(item.args == (200,) for item in default_ime.call_args_list))

    def test_ime_mode_falls_back_to_foreground_when_focus_api_fails(self):
        """A missing GUI-thread focus must retain the foreground compatibility path."""
        state = {"conversion": config.IME_CMODE_NATIVE, "open": True}

        def send_message(hwnd, message, control, value):
            self.assertEqual(hwnd, 301)
            self.assertEqual(message, config.WM_IME_CONTROL)
            if control == config.IMC_GETCONVERSIONMODE:
                return state["conversion"]
            if control == config.IMC_SETCONVERSIONMODE:
                state["conversion"] = value
                return 1
            if control == 0x0005:
                return int(state["open"])
            if control == 0x0006:
                state["open"] = bool(value)
                return 1
            self.fail(f"unexpected WM_IME_CONTROL={control:#x}")

        def send_message_timeout(
            hwnd, message, control, value, _flags, _timeout, result,
        ):
            result._obj.value = send_message(hwnd, message, control, value)
            return 1

        with patch.object(toggle.user32, "GetForegroundWindow", return_value=100), \
             patch.object(toggle.user32, "GetWindowThreadProcessId", return_value=77), \
             patch.object(toggle.user32, "GetGUIThreadInfo", return_value=False), \
             patch.object(toggle.user32, "GetKeyboardLayout", return_value=0x0804), \
             patch.object(
                 toggle.imm32,
                 "ImmGetDescriptionW",
                 side_effect=fill_microsoft_pinyin_description,
             ), \
             patch.object(toggle.imm32, "ImmGetContext", return_value=0), \
             patch.object(
                 toggle.imm32,
                 "ImmGetDefaultIMEWnd",
                 side_effect=lambda hwnd: 301 if hwnd == 100 else 0,
             ) as default_ime, \
             patch.object(
                 toggle.user32,
                 "SendMessageTimeoutW",
                 side_effect=send_message_timeout,
                 create=True,
             ):
            self.assertTrue(toggle.switch_ime(MODE_IME))

        self.assertEqual(state, {"conversion": 0, "open": False})
        self.assertTrue(default_ime.call_count)
        self.assertTrue(all(item.args == (100,) for item in default_ime.call_args_list))

    def test_ime_mode_accepts_zh_cn_even_when_mode_is_unreadable(self):
        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(toggle, "_get_mode_with_fallback", return_value=None):
            self.assertTrue(toggle.can_switch(MODE_IME))

        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=0x0409), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(toggle, "_get_mode_with_fallback", return_value=0):
            self.assertFalse(toggle.can_switch(MODE_IME))

    def test_missing_description_is_rejected_but_allowlisted_name_is_accepted(self):
        self.assertFalse(can_switch_in_active_context(
            MODE_IME,
            language=config.LANGID_ZH_CN,
            description="",
        ))
        self.assertTrue(can_switch_in_active_context(
            MODE_IME,
            language=config.LANGID_ZH_CN,
            description="微软拼音",
        ))

    def test_layout_mode_accepts_only_zh_cn_or_en_us(self):
        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN):
            self.assertTrue(toggle.can_switch(MODE_LAYOUT))

        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=0x0409):
            self.assertTrue(toggle.can_switch(MODE_LAYOUT))

        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=0x0411):
            self.assertFalse(toggle.can_switch(MODE_LAYOUT))

    def test_ime_mode_does_not_report_success_when_mode_did_not_change(self):
        """A successful control message is not proof that Pinyin changed."""
        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(
                 toggle, "_get_mode_with_fallback",
                 return_value=config.IME_CMODE_NATIVE,
             ), \
             patch.object(toggle, "_set_mode_with_fallback", return_value=True):
            self.assertFalse(toggle.switch_ime(MODE_IME))

    def test_ime_mode_reopens_pinyin_when_conversion_bit_is_stale(self):
        """A closed Pinyin context can retain native conversion=1."""
        state = {"open": False, "conversion": config.IME_CMODE_NATIVE, "sentence": 0x8}

        def get_conversion(_himc, conversion, sentence):
            conversion._obj.value = state["conversion"]
            sentence._obj.value = state["sentence"]
            return True

        def set_conversion(_himc, mode, _sentence):
            # Modern Pinyin reports success but does not apply conversion
            # writes while the IME is closed.
            if state["open"]:
                state["conversion"] = mode
            return True

        def set_open(_himc, value):
            state["open"] = bool(value)
            return True

        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(toggle.imm32, "ImmGetContext", return_value=30), \
             patch.object(toggle.imm32, "ImmReleaseContext", return_value=True), \
             patch.object(toggle.imm32, "ImmGetOpenStatus", side_effect=lambda _himc: state["open"]), \
             patch.object(toggle.imm32, "ImmSetOpenStatus", side_effect=set_open), \
             patch.object(toggle.imm32, "ImmGetConversionStatus", side_effect=get_conversion), \
             patch.object(toggle.imm32, "ImmSetConversionStatus", side_effect=set_conversion):
            self.assertTrue(toggle.switch_ime(MODE_IME))

        self.assertEqual(state, {"open": True, "conversion": config.IME_CMODE_NATIVE, "sentence": 0x8})

    def test_ime_status_treats_closed_pinyin_as_english(self):
        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_get_mode_with_fallback", return_value=config.IME_CMODE_NATIVE), \
             patch.object(toggle, "_get_open_status_with_fallback", return_value=False):
            self.assertFalse(toggle.get_ime_status())

    def test_ime_mode_opens_before_conversion_when_both_are_stale(self):
        state = {"open": False, "conversion": 0, "sentence": 0x8}

        def get_conversion(_himc, conversion, sentence):
            conversion._obj.value = state["conversion"]
            sentence._obj.value = state["sentence"]
            return True

        def set_conversion(_himc, mode, _sentence):
            if state["open"]:
                state["conversion"] = mode
            return True

        def set_open(_himc, value):
            state["open"] = bool(value)
            return True

        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(toggle.imm32, "ImmGetContext", return_value=30), \
             patch.object(toggle.imm32, "ImmReleaseContext", return_value=True), \
             patch.object(toggle.imm32, "ImmGetOpenStatus", side_effect=lambda _himc: state["open"]), \
             patch.object(toggle.imm32, "ImmSetOpenStatus", side_effect=set_open), \
             patch.object(toggle.imm32, "ImmGetConversionStatus", side_effect=get_conversion), \
             patch.object(toggle.imm32, "ImmSetConversionStatus", side_effect=set_conversion):
            self.assertTrue(toggle.switch_ime(MODE_IME))

        self.assertEqual(state["open"], True)
        self.assertEqual(state["conversion"], config.IME_CMODE_NATIVE)

    def test_ime_mode_returns_false_when_mode_is_unreadable(self):
        with self.assertLogs(
            "ime_switcher.toggle",
            level="WARNING",
        ) as logs, patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(toggle, "_get_mode_with_fallback", return_value=None):
            self.assertFalse(toggle.switch_ime(MODE_IME))

        flattened_logs = " ".join("\n".join(logs.output).split())
        self.assertIn("转换模式不可读，已取消本次切换", flattened_logs)
        self.assertNotIn("Shift 回退", flattened_logs)

    def test_ime_mode_returns_false_when_mode_set_fails(self):
        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(
                 toggle, "_get_mode_with_fallback",
                 side_effect=[config.IME_CMODE_NATIVE, 0],
             ), \
             patch.object(toggle, "_set_mode_with_fallback", return_value=False):
            self.assertFalse(toggle.switch_ime(MODE_IME))

    def test_capslock_path_consumes_press_and_invokes_ime_switch(self):
        class ProductionPathIME:
            def can_toggle(self):
                return toggle.can_switch(MODE_IME)

            def toggle(self):
                self.result = toggle.switch_ime(MODE_IME)
                return self.result

            def current_state(self):
                raise AssertionError("state is not needed for this path")

        class RecordingNative:
            def __init__(self):
                self.tap_calls = 0
                self.tapped = threading.Event()

            def tap(self):
                self.tap_calls += 1
                self.tapped.set()

        class RecordingLED:
            def __init__(self):
                self.turned_off = threading.Event()

            def off(self):
                self.turned_off.set()

            def on(self):
                raise AssertionError("short press enabled CapsLock LED")

        adapter = ProductionPathIME()
        native = RecordingNative()
        led = RecordingLED()
        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=config.LANGID_ZH_CN), \
             patch.object(toggle, "_is_microsoft_pinyin_context", return_value=True), \
             patch.object(toggle, "_get_mode_with_fallback", return_value=None):
            engine = CapsLockIME(
                ime_adapter=adapter,
                led_adapter=led,
                native_adapter=native,
            )
            self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
            self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
            self.assertTrue(native.tapped.wait(0.5))
            self.assertTrue(led.turned_off.wait(0.5))

        self.assertFalse(adapter.result)
        self.assertEqual(native.tap_calls, 1)

    def test_capslock_worker_survives_system_boundary_exceptions(self):
        class BoundaryIME:
            def __init__(self, action):
                self.action = action
                self.calls = 0
                self.called = threading.Event()

            def can_toggle(self):
                return True

            def toggle(self):
                self.calls += 1
                try:
                    return self.action()
                finally:
                    self.called.set()

            def current_state(self):
                raise AssertionError("state is not needed for this path")

        class RecordingNative:
            def __init__(self):
                self.tap_calls = 0
                self.tapped = threading.Event()

            def tap(self):
                self.tap_calls += 1
                self.tapped.set()

        class RecordingLED:
            def __init__(self):
                self.turned_off = threading.Event()

            def off(self):
                self.turned_off.set()

            def on(self):
                raise AssertionError("short press enabled CapsLock LED")

        for boundary_name in ("load", "post", "imm"):
            with self.subTest(boundary=boundary_name), ExitStack() as stack:
                stack.enter_context(patch.object(
                    toggle,
                    "_input_targets",
                    return_value=(100, 100),
                ))
                stack.enter_context(patch.object(
                    toggle,
                    "_current_language",
                    return_value=config.LANGID_ZH_CN,
                ))
                if boundary_name == "load":
                    mode = MODE_LAYOUT
                    stack.enter_context(patch.object(
                        toggle.user32,
                        "LoadKeyboardLayoutW",
                        side_effect=RuntimeError("private-worker-load-marker"),
                    ))
                elif boundary_name == "post":
                    mode = MODE_LAYOUT
                    stack.enter_context(patch.object(
                        toggle.user32,
                        "LoadKeyboardLayoutW",
                        return_value=config.LANGID_EN_US,
                    ))
                    stack.enter_context(patch.object(
                        toggle.user32,
                        "PostMessageW",
                        side_effect=ValueError("private-worker-post-marker"),
                    ))
                else:
                    mode = MODE_IME
                    stack.enter_context(patch.object(
                        toggle,
                        "_is_microsoft_pinyin_context",
                        return_value=True,
                    ))
                    stack.enter_context(patch.object(
                        toggle.imm32,
                        "ImmGetContext",
                        side_effect=RuntimeError("private-worker-imm-marker"),
                    ))

                adapter = BoundaryIME(lambda: toggle.switch_ime(mode))
                native = RecordingNative()
                led = RecordingLED()
                engine = CapsLockIME(
                    ime_adapter=adapter,
                    led_adapter=led,
                    native_adapter=native,
                )

                self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
                self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
                self.assertTrue(adapter.called.wait(0.5))
                self.assertTrue(native.tapped.wait(0.5))
                self.assertTrue(led.turned_off.wait(0.5))
                self.assertEqual(native.tap_calls, 1)

                adapter.action = lambda: True
                adapter.called.clear()
                led.turned_off.clear()
                self.assertTrue(engine.on_key_event(VK_CAPITAL, True))
                self.assertTrue(engine.on_key_event(VK_CAPITAL, False))
                self.assertTrue(adapter.called.wait(0.5))
                self.assertTrue(led.turned_off.wait(0.5))
                self.assertEqual(adapter.calls, 2)
                self.assertEqual(native.tap_calls, 1)

    def test_layout_request_prefers_focus_then_falls_back_to_foreground(self):
        with patch.object(
            toggle.user32, "PostMessageW", side_effect=[0, 1],
        ) as post:
            self.assertTrue(toggle._post_layout_change(20, 10, 123))

        self.assertEqual(post.call_args_list[0].args[0], 20)
        self.assertEqual(post.call_args_list[1].args[0], 10)

    def test_ime_mode_returns_false_outside_zh_cn(self):
        with patch.object(toggle, "_input_targets", return_value=(20, 10)), \
             patch.object(toggle, "_current_language", return_value=0x0409):
            self.assertFalse(toggle.switch_ime(MODE_IME))
