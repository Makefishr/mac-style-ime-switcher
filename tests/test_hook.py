import ctypes
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ime_switcher import config, hook
from ime_switcher.winapi import KBDLLHOOKSTRUCT


class HookAPI:
    def __init__(self, messages=()):
        self.messages = iter(messages)
        self.events = []
        self.active = set()
        self.next_handle = 1
        self.install_failure = set()
        self.unhook_error = None

    def PeekMessageW(self, *args):
        self.events.append("queue")
        return 0

    def SetWindowsHookExW(self, *args):
        self.events.append("install")
        number = self.next_handle
        self.next_handle += 1
        if number in self.install_failure:
            return 0
        if self.active:
            raise AssertionError("two hooks active simultaneously")
        self.active.add(number)
        return number

    def UnhookWindowsHookEx(self, handle):
        self.events.append("uninstall")
        if self.unhook_error is not None:
            ctypes.set_last_error(self.unhook_error)
            if self.unhook_error == hook.ERROR_INVALID_HOOK_HANDLE:
                self.active.discard(handle)
            return 0
        self.active.remove(handle)
        return 1

    def SetTimer(self, *args):
        self.events.append("timer")
        return 25

    def KillTimer(self, *args):
        self.events.append("kill_timer")
        return 1

    def GetMessageW(self, message, *args):
        item = next(self.messages, 0)
        if item in (0, -1):
            return item
        message._obj.message = item[0]
        message._obj.wParam = item[1]
        return 1

    def PostThreadMessageW(self, thread_id, message, *args):
        self.events.append(("post", thread_id, message))
        return 1

    def TranslateMessage(self, *args):
        self.events.append("translate")

    def DispatchMessageW(self, *args):
        self.events.append("dispatch")


class HookLoopTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(hook, "_modifiers_down", set()))
        self.key_state = self.enterContext(patch.object(hook, "is_key_down", return_value=False))

    def make_loop(self, api):
        def thread_id():
            api.events.append("thread_id")
            return 123
        return hook.HookLoop(api, SimpleNamespace(GetModuleHandleW=lambda _: 1,
                                                 GetCurrentThreadId=thread_id))

    def test_blocking_loop_reinstalls_in_order_and_cleans_up_on_quit(self):
        api = HookAPI([(hook.WM_TIMER, 25)])
        loop = self.make_loop(api)
        loop.run()
        self.assertEqual(api.events, ["queue", "thread_id", "install", "timer",
                                     "uninstall", "install", "kill_timer", "uninstall"])
        self.assertTrue(loop.ready.is_set())
        self.assertFalse(loop.installed)
        self.assertEqual(api.active, set())
        self.assertIsNone(config.hook_handle)

    def test_install_resynchronizes_modifiers_after_events_were_missed(self):
        hook._modifiers_down.update({config.VK_CONTROL, config.VK_LCONTROL, 0xA0})
        self.key_state.side_effect = lambda vk: vk == config.VK_RCONTROL
        self.make_loop(HookAPI()).run()
        self.assertEqual(hook._modifiers_down, {config.VK_RCONTROL})

    def test_stop_before_queue_is_created_never_installs(self):
        api = HookAPI()
        loop = self.make_loop(api)
        self.assertTrue(loop.stop())
        loop.run()
        self.assertNotIn("install", api.events)
        self.assertTrue(loop.ready.is_set())

    def test_stop_posts_quit_only_after_queue_creation(self):
        api = HookAPI()
        loop = self.make_loop(api)

        def get_message(*args):
            self.assertTrue(loop.ready.is_set())
            self.assertTrue(loop.stop())
            return 0

        api.GetMessageW = get_message
        loop.run()
        self.assertIn(("post", 123, hook.WM_QUIT), api.events)
        posts = api.events.count(("post", 123, hook.WM_QUIT))
        loop.stop()
        self.assertEqual(api.events.count(("post", 123, hook.WM_QUIT)), posts)

    def test_real_queue_timer_and_cross_thread_quit_with_fake_hook(self):
        # Only queue/timer APIs are real: no keyboard hook or input is installed.
        api = HookAPI()
        reinstalled = threading.Event()
        install = api.SetWindowsHookExW

        def fake_install(*args):
            handle = install(*args)
            if api.next_handle > 2:
                reinstalled.set()
            return handle

        api.SetWindowsHookExW = fake_install
        for name in ("PeekMessageW", "GetMessageW", "PostThreadMessageW", "SetTimer", "KillTimer"):
            setattr(api, name, getattr(hook.user32, name))
        loop = hook.HookLoop(api, hook.kernel32)
        thread = threading.Thread(target=loop.run, daemon=True)
        with patch.object(hook, "HOOK_REFRESH_MS", 50):
            thread.start()
            try:
                self.assertTrue(loop.ready.wait(2))
                self.assertTrue(reinstalled.wait(2))
            finally:
                loop.stop()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(api.active, set())
        self.assertGreaterEqual(api.events.count("install"), 2)

    def test_reinstall_failure_is_retried_on_next_timer(self):
        api = HookAPI([(hook.WM_TIMER, 25), (hook.WM_TIMER, 25)])
        api.install_failure.add(2)
        with self.assertLogs("ime_switcher.hook", level="ERROR"):
            self.make_loop(api).run()
        self.assertEqual(api.events.count("install"), 3)
        self.assertEqual(api.active, set())

    def test_invalid_old_handle_does_not_prevent_recovery(self):
        api = HookAPI([(hook.WM_TIMER, 25)])
        api.unhook_error = hook.ERROR_INVALID_HOOK_HANDLE
        self.make_loop(api).run()
        self.assertEqual(api.events.count("install"), 2)
        self.assertEqual(api.active, set())

    def test_unknown_unhook_error_never_installs_second_hook(self):
        api = HookAPI([(hook.WM_TIMER, 25)])
        api.unhook_error = 5
        with self.assertLogs("ime_switcher.hook", level="ERROR"):
            self.make_loop(api).run()
        self.assertEqual(api.events.count("install"), 1)

    def test_message_error_cleans_up_instead_of_spinning(self):
        api = HookAPI([-1])
        with self.assertLogs("ime_switcher.hook", level="ERROR"):
            self.make_loop(api).run()
        self.assertEqual(api.active, set())

    def test_startup_failure_sets_ready_and_does_not_enter_message_loop(self):
        api = HookAPI()
        api.install_failure.add(1)
        loop = self.make_loop(api)
        with self.assertLogs("ime_switcher.hook", level="ERROR"):
            loop.run()
        self.assertTrue(loop.ready.is_set())
        self.assertFalse(loop.installed)
        self.assertNotIn("timer", api.events)

    def test_unrelated_messages_and_timers_are_dispatched(self):
        api = HookAPI([(hook.WM_TIMER, 99), (1234, 0)])
        self.make_loop(api).run()
        self.assertEqual(api.events.count("dispatch"), 2)
        self.assertEqual(api.events.count("install"), 1)


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.CallNextHookEx.return_value = 42
        self.enterContext(patch.object(hook, "user32", self.api))
        self.enterContext(patch.object(hook, "engine", Mock(on_key_event=Mock(return_value=False))))
        self.key_state = self.enterContext(patch.object(hook, "is_key_down", return_value=False))
        self.enterContext(patch.object(hook, "_modifiers_down", set()))
        self.enterContext(patch.object(hook, "_block_ctrl_space", True))

    def key(self, vk, down=True, injected=False):
        event = KBDLLHOOKSTRUCT(vkCode=vk, flags=config.LLKHF_INJECTED if injected else 0)
        return hook._keyboard_hook(0, config.WM_KEYDOWN if down else 0x0101, ctypes.pointer(event))

    def test_ctrl_space_toggle_is_effective_immediately(self):
        self.key(config.VK_LCONTROL)
        self.assertEqual(self.key(config.VK_SPACE), 1)
        hook.set_block_ctrl_space(False)
        self.assertEqual(self.key(config.VK_SPACE), 42)

    def test_win_space_remains_blocked_with_ctrl_space_disabled(self):
        hook.set_block_ctrl_space(False)
        self.key_state.side_effect = lambda vk: vk == config.VK_LWIN
        self.assertEqual(self.key(config.VK_SPACE), 1)

    def test_ctrl_space_with_shift_or_alt_passes_through(self):
        self.key(config.VK_LCONTROL)
        for modifier in (config.VK_SHIFT, 0xA0, 0xA1, config.VK_MENU, 0xA4, 0xA5):
            with self.subTest(modifier=modifier):
                self.key(modifier)
                self.assertEqual(self.key(config.VK_SPACE), 42)
                self.key(modifier, False)
                self.assertEqual(self.key(config.VK_SPACE), 1)

    def test_releasing_one_ctrl_keeps_other_ctrl_pressed(self):
        self.key(config.VK_LCONTROL)
        self.key(config.VK_RCONTROL)
        self.key(config.VK_LCONTROL, False)
        self.assertEqual(self.key(config.VK_SPACE), 1)
        self.key(config.VK_RCONTROL, False)
        self.assertEqual(self.key(config.VK_SPACE), 42)

    def test_ctrl_shift_and_alt_shift_combinations_pass_through(self):
        self.key(config.VK_LCONTROL)
        for shift in (0xA0, 0xA1, config.VK_SHIFT):
            self.assertEqual(self.key(shift), 42)
        self.key(config.VK_LCONTROL, False)
        self.key_state.side_effect = lambda vk: vk == config.VK_MENU
        self.assertEqual(self.key(0xA0), 42)

    def test_injected_events_pass_through_without_changing_ctrl_state(self):
        self.assertEqual(self.key(config.VK_LCONTROL, injected=True), 42)
        self.assertEqual(self.key(config.VK_SPACE), 42)
