"""Keyboard-hook event policy for suppressing isolated Shift taps."""

from __future__ import annotations

from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class KeyEvent:
    """One observed low-level keyboard event."""

    vk_code: int
    scan_code: int
    flags: int
    is_down: bool


@dataclass(frozen=True)
class HookDecision:
    """The observable action at the keyboard-hook boundary."""

    forward: bool
    replay: tuple[KeyEvent, ...] = ()


class ShiftTapGuard:
    """Suppress physical generic Shift taps until a modifier use is known."""

    def __init__(self) -> None:
        self._held_shifts: dict[int, KeyEvent] = {}
        self._replayed_shifts: set[int] = set()
        self._held_modifiers: set[int] = set()

    def reset(self) -> HookDecision:
        """Clear physical state and release every Shift already replayed."""
        releases = tuple(
            KeyEvent(
                vk_code=shift.vk_code,
                scan_code=shift.scan_code,
                flags=shift.flags & config.LLKHF_EXTENDED,
                is_down=False,
            )
            for vk_code, shift in self._held_shifts.items()
            if vk_code in self._replayed_shifts
        )
        self._held_shifts.clear()
        self._replayed_shifts.clear()
        self._held_modifiers.clear()
        return HookDecision(forward=False, replay=releases)

    def process(self, event: KeyEvent) -> HookDecision:
        if event.flags & config.LLKHF_INJECTED:
            return HookDecision(forward=True)

        if event.vk_code == config.VK_CAPITAL:
            return HookDecision(forward=True)

        if event.vk_code not in (
            config.VK_SHIFT,
            config.VK_LSHIFT,
            config.VK_RSHIFT,
        ):
            if event.vk_code in (
                config.VK_CONTROL,
                config.VK_LCONTROL,
                config.VK_RCONTROL,
                config.VK_MENU,
                config.VK_LMENU,
                config.VK_RMENU,
                config.VK_LWIN,
                config.VK_RWIN,
            ):
                if event.is_down:
                    self._held_modifiers.add(event.vk_code)
                else:
                    self._held_modifiers.discard(event.vk_code)
                return HookDecision(forward=True)

            if event.is_down:
                ctrl_down = bool(
                    self._held_modifiers
                    & {
                        config.VK_CONTROL,
                        config.VK_LCONTROL,
                        config.VK_RCONTROL,
                    }
                )
                win_down = bool(
                    self._held_modifiers
                    & {config.VK_LWIN, config.VK_RWIN}
                )
                if event.vk_code == config.VK_SPACE and (ctrl_down or win_down):
                    return HookDecision(forward=False)

                pending = tuple(
                    shift
                    for vk_code, shift in self._held_shifts.items()
                    if vk_code not in self._replayed_shifts
                )
                if pending:
                    self._replayed_shifts.update(
                        shift.vk_code for shift in pending
                    )
                    return HookDecision(
                        forward=False,
                        replay=pending + (event,),
                    )
            return HookDecision(forward=True)

        if event.is_down:
            self._held_shifts.setdefault(event.vk_code, event)
            return HookDecision(forward=False)

        if event.vk_code in self._replayed_shifts:
            self._replayed_shifts.remove(event.vk_code)
            self._held_shifts.pop(event.vk_code, None)
            return HookDecision(forward=False, replay=(event,))

        self._held_shifts.pop(event.vk_code, None)
        return HookDecision(forward=False)
