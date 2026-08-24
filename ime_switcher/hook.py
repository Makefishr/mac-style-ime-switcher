"""Low-level keyboard hook and CapsLock handler."""

import ctypes
import logging
import time
from ctypes import wintypes

from . import config
from .caps_ime import engine, VK_CAPITAL
from .shift_guard import HookDecision, KeyEvent, ShiftTapGuard
from .winapi import INPUT, HOOKPROC, KBDLLHOOKSTRUCT, kernel32, user32

log = logging.getLogger(__name__)


def _input_batch(events: tuple[KeyEvent, ...]):
    inputs = (INPUT * len(events))()
    for index, event in enumerate(events):
        flags = config.KEYEVENTF_SCANCODE
        if event.flags & config.LLKHF_EXTENDED:
            flags |= config.KEYEVENTF_EXTENDEDKEY
        if not event.is_down:
            flags |= config.KEYEVENTF_KEYUP

        inputs[index].type = config.INPUT_KEYBOARD
        inputs[index].ki.wVk = 0
        inputs[index].ki.wScan = event.scan_code
        inputs[index].ki.dwFlags = flags
        inputs[index].ki.time = 0
        inputs[index].ki.dwExtraInfo = 0
    return inputs


def send_replay(events: tuple[KeyEvent, ...], send_input=None) -> int:
    """Submit one Win32 keyboard-input batch and return inserted count."""
    if not events:
        return 0

    inputs = _input_batch(events)
    sender = user32.SendInput if send_input is None else send_input
    inserted = sender(len(events), inputs, ctypes.sizeof(INPUT))
    return int(inserted)


def submit_replay(events: tuple[KeyEvent, ...], send_input=None) -> bool:
    """Submit a replay sequence and report whether the whole batch landed."""
    if not events:
        return True
    return send_replay(events, send_input=send_input) == len(events)


class KeyboardHookProcessor:
    """Production keyboard-hook event boundary."""

    def __init__(self, policy: ShiftTapGuard | None = None) -> None:
        self._policy = policy or ShiftTapGuard()

    def process(self, event: KeyEvent) -> HookDecision:
        return self._policy.process(event)

    def reset(self) -> HookDecision:
        return self._policy.reset()


_event_processor = KeyboardHookProcessor()


def reset_capslock_state(caps_engine=None) -> bool:
    """Reset CapsLock gesture state when the engine exposes that capability."""
    target = engine if caps_engine is None else caps_engine
    resetter = getattr(target, "reset", None)
    if not callable(resetter):
        return True
    try:
        resetter()
    except Exception:
        log.exception("CapsLock hook state reset failed")
        return False
    return True


def reset_hook_state(
    processor: KeyboardHookProcessor | None = None,
    send_input=None,
    release_input=None,
) -> bool:
    """Reset a hook processor and release any synthetic modifiers it owns."""
    target = _event_processor if processor is None else processor
    decision = target.reset()
    if not decision.replay:
        return True
    if submit_replay(decision.replay, send_input=send_input):
        return True

    fallback = user32.keybd_event if release_input is None else release_input
    try:
        for release in decision.replay:
            fallback(
                release.vk_code,
                release.scan_code & 0xFF,
                config.KEYEVENTF_KEYUP,
                0,
            )
    except Exception:
        log.exception("Lifecycle Shift release fallback failed")
        return False
    return True


def dispatch_keyboard_event(
    event: KeyEvent,
    processor: KeyboardHookProcessor | None = None,
    send_input=None,
    release_input=None,
) -> HookDecision:
    """Apply policy and make replay failure observable at the hook boundary."""
    target = _event_processor if processor is None else processor
    decision = target.process(event)
    if not decision.replay:
        return decision

    inserted = send_replay(decision.replay, send_input=send_input)
    if inserted == len(decision.replay):
        return decision

    if not event.is_down:
        return HookDecision(forward=True)

    cleanup = target.reset().replay
    if inserted and cleanup:
        cleanup_inserted = send_replay(cleanup, send_input=send_input)
        if cleanup_inserted != len(cleanup):
            fallback = (
                user32.keybd_event
                if release_input is None else release_input
            )
            try:
                for release in cleanup:
                    fallback(
                        release.vk_code,
                        release.scan_code & 0xFF,
                        config.KEYEVENTF_KEYUP,
                        0,
                    )
            except Exception:
                log.exception("Synthetic Shift release fallback failed")
                return HookDecision(forward=False)
    return HookDecision(forward=True)


def dispatch_hook_event(
    event: KeyEvent,
    *,
    caps_engine=None,
    processor: KeyboardHookProcessor | None = None,
    send_input=None,
    release_input=None,
) -> HookDecision:
    """Dispatch one low-level event through CapsLock and Shift policies."""
    if event.flags & config.LLKHF_INJECTED:
        return HookDecision(forward=True)

    target_caps = engine if caps_engine is None else caps_engine
    if (
        event.vk_code == VK_CAPITAL
        and target_caps.on_key_event(event.vk_code, event.is_down)
    ):
        return HookDecision(forward=False)

    return dispatch_keyboard_event(
        event,
        processor=processor,
        send_input=send_input,
        release_input=release_input,
    )


# ── Keyboard hook ────────────────────────────────────────────


@HOOKPROC
def _keyboard_hook(nCode: int, wParam: int, lParam: int) -> int:
    if nCode < 0:
        return user32.CallNextHookEx(
            config.hook_handle, nCode, wParam, lParam,
        )

    kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
    is_down = wParam in (config.WM_KEYDOWN, config.WM_SYSKEYDOWN)
    event = KeyEvent(
        vk_code=kb.vkCode,
        scan_code=kb.scanCode,
        flags=kb.flags,
        is_down=is_down,
    )

    decision = dispatch_hook_event(event)
    if decision.replay:
        return 1
    if not decision.forward:
        return 1

    return user32.CallNextHookEx(
        config.hook_handle, nCode, wParam, lParam,
    )


# ── Hook thread ──────────────────────────────────────────────


def hook_thread_main() -> None:
    """Install the low-level keyboard hook and run the message loop."""
    if not reset_capslock_state():
        log.error("CapsLock state reset failed before install")
    if not reset_hook_state():
        log.error("Keyboard hook state reset failed before install")

    module = kernel32.GetModuleHandleW(None)
    ptr = user32.SetWindowsHookExW(
        config.WH_KEYBOARD_LL, _keyboard_hook, module, 0,
    )
    if not ptr:
        err = ctypes.get_last_error()
        log.error("SetWindowsHookExW failed (error %d)", err)
        return
    config.hook_handle = ptr
    log.info("Keyboard hook installed (handle=%s)", config.hook_handle)

    msg = wintypes.MSG()
    while config.running:
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, config.PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.005)

    if not reset_capslock_state():
        log.error("CapsLock state reset failed before uninstall")
    if not reset_hook_state():
        log.error("Keyboard hook state reset failed before uninstall")
    user32.UnhookWindowsHookEx(config.hook_handle)
    config.hook_handle = None
    log.info("Keyboard hook uninstalled")
