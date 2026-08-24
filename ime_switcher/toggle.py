"""IME / keyboard-layout switching logic."""

import ctypes
import logging
import unicodedata
from ctypes import wintypes

from . import config
from .winapi import GUITHREADINFO, imm32, kernel32, user32

log = logging.getLogger(__name__)


def _input_targets() -> tuple[int | None, int | None]:
    """Return (focused input HWND, foreground top-level HWND)."""
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return None, None

    target = foreground
    thread_id = user32.GetWindowThreadProcessId(foreground, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if (
        thread_id
        and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info))
        and info.hwndFocus
    ):
        target = info.hwndFocus
    return target, foreground


def _default_ime_window(hwnd: int) -> int | None:
    if not hwnd:
        return None
    ime_window = imm32.ImmGetDefaultIMEWnd(hwnd)
    return int(ime_window) if ime_window else None


def _send_ime_control(hwnd: int, control: int, value: int = 0) -> int | None:
    """Use the IME's compatibility window when the target has no local HIMC."""
    ime_window = _default_ime_window(hwnd)
    if not ime_window:
        return None
    try:
        result = ctypes.c_size_t()
        sent = user32.SendMessageTimeoutW(
            ime_window,
            config.WM_IME_CONTROL,
            control,
            value,
            config.SMTO_ABORTIFHUNG,
            config.IME_CONTROL_TIMEOUT_MS,
            ctypes.byref(result),
        )
    except Exception as exc:
        log.warning(
            "WM_IME_CONTROL 失败 exception=%s",
            type(exc).__name__,
        )
        return None
    if not sent:
        log.warning(
            "WM_IME_CONTROL 超时或失败 HWND=%s GetLastError=%s",
            ime_window,
            kernel32.GetLastError(),
        )
        return None
    return int(result.value)


def _get_ime_conversion_mode(hwnd: int) -> int | None:
    """Read conversion mode from the target HIMC or its default IME window."""
    himc = imm32.ImmGetContext(hwnd)
    if himc:
        try:
            conversion = wintypes.DWORD()
            sentence = wintypes.DWORD()
            ok = imm32.ImmGetConversionStatus(
                himc, ctypes.byref(conversion), ctypes.byref(sentence),
            )
            if ok:
                return conversion.value
        except (OSError, ctypes.ArgumentError):
            log.warning("无法读取 IME conversion 状态")
            return None
        finally:
            imm32.ImmReleaseContext(hwnd, himc)
    result = _send_ime_control(hwnd, config.IMC_GETCONVERSIONMODE)
    return result


def _get_mode_with_fallback(target: int | None, foreground: int | None) -> int | None:
    if target:
        mode = _get_ime_conversion_mode(target)
        if mode is not None:
            return mode
    if foreground and foreground != target:
        return _get_ime_conversion_mode(foreground)
    return None


def _get_ime_open_status(hwnd: int) -> bool | None:
    """Read whether the target IME is open; ``False`` is a valid state."""
    himc = imm32.ImmGetContext(hwnd)
    if himc:
        try:
            return bool(imm32.ImmGetOpenStatus(himc))
        except (OSError, ctypes.ArgumentError):
            log.warning("无法读取 IME open 状态")
            return None
        finally:
            imm32.ImmReleaseContext(hwnd, himc)
    result = _send_ime_control(hwnd, config.IMC_GETOPENSTATUS)
    return None if result is None else bool(result)


def _get_open_status_with_fallback(
    target: int | None, foreground: int | None,
) -> bool | None:
    if target:
        status = _get_ime_open_status(target)
        if status is not None:
            return status
    if foreground and foreground != target:
        return _get_ime_open_status(foreground)
    return None


def _set_ime_conversion_mode(hwnd: int, mode: int) -> bool:
    """Write the target HIMC or its default IME window."""
    himc = imm32.ImmGetContext(hwnd)
    if himc:
        try:
            current = wintypes.DWORD()
            sentence = wintypes.DWORD()
            if imm32.ImmGetConversionStatus(
                himc, ctypes.byref(current), ctypes.byref(sentence),
            ) and imm32.ImmSetConversionStatus(
                himc, mode, sentence.value,
            ):
                return True
        except (OSError, ctypes.ArgumentError):
            log.warning("无法写入 IME conversion 状态")
            return False
        finally:
            imm32.ImmReleaseContext(hwnd, himc)
    return _send_ime_control(
        hwnd, config.IMC_SETCONVERSIONMODE, mode,
    ) is not None


def _set_ime_open_status(hwnd: int, open_status: bool) -> bool:
    himc = imm32.ImmGetContext(hwnd)
    if himc:
        try:
            if imm32.ImmSetOpenStatus(himc, bool(open_status)):
                return True
        except (OSError, ctypes.ArgumentError):
            log.warning("无法写入 IME open 状态")
            return False
        finally:
            imm32.ImmReleaseContext(hwnd, himc)
    return _send_ime_control(
        hwnd, config.IMC_SETOPENSTATUS, int(bool(open_status)),
    ) is not None


def _set_open_status_with_fallback(
    target: int | None, foreground: int | None, open_status: bool,
) -> bool:
    if target and _set_ime_open_status(target, open_status):
        return True
    if foreground and foreground != target:
        return _set_ime_open_status(foreground, open_status)
    return False


def _set_mode_with_fallback(
    target: int | None, foreground: int | None, mode: int,
) -> bool:
    if target and _set_ime_conversion_mode(target, mode):
        return True
    if foreground and foreground != target:
        return _set_ime_conversion_mode(foreground, mode)
    return False


def _native_mode_matches(
    target: int | None, foreground: int | None, expected_mode: int,
) -> bool:
    """Verify the native/alphanumeric bit after asking the IME to change it."""
    observed = _get_mode_with_fallback(target, foreground)
    return (
        observed is not None
        and (observed & config.IME_CMODE_NATIVE)
        == (expected_mode & config.IME_CMODE_NATIVE)
    )


def _ime_state_matches(
    target: int | None,
    foreground: int | None,
    expected_mode: int,
    expected_open_status: bool | None,
) -> bool:
    if not _native_mode_matches(target, foreground, expected_mode):
        return False
    if expected_open_status is None:
        return True
    observed_open_status = _get_open_status_with_fallback(target, foreground)
    if observed_open_status is None:
        return False
    if expected_open_status is False:
        # English is observable either as a closed IME or as an open IME with
        # the native conversion bit cleared.
        return True
    return observed_open_status is True


def _post_layout_change(
    target: int | None, foreground: int | None, new_hkl: int,
) -> bool:
    """Post to the focused input target first, then the top-level window."""
    try:
        if target and user32.PostMessageW(
            target, config.WM_INPUTLANGCHANGEREQUEST, 0, new_hkl,
        ):
            return True
        if foreground and foreground != target and user32.PostMessageW(
            foreground, config.WM_INPUTLANGCHANGEREQUEST, 0, new_hkl,
        ):
            log.info("布局请求已回退到前台窗口 HWND=%s", foreground)
            return True
    except Exception as exc:
        log.warning(
            "布局切换请求失败 exception=%s",
            type(exc).__name__,
        )
    return False


def _current_language(target: int | None) -> int | None:
    if not target:
        return None
    thread_id = user32.GetWindowThreadProcessId(target, None)
    if not thread_id:
        return None
    hkl = user32.GetKeyboardLayout(thread_id)
    if not hkl:
        return None
    return hkl & 0xFFFF


def _is_microsoft_pinyin_context(
    foreground: int | None, target: int | None,
) -> bool:
    input_target = target or foreground
    if not input_target:
        return False
    thread_id = user32.GetWindowThreadProcessId(input_target, None)
    if not thread_id:
        return False
    hkl = user32.GetKeyboardLayout(thread_id)
    if not hkl:
        return False
    description = ctypes.create_unicode_buffer(128)
    if not imm32.ImmGetDescriptionW(hkl, description, len(description)):
        log.info("微软拼音描述不可用，拒绝 IME 模式切换")
        return False
    value = " ".join(
        unicodedata.normalize("NFKC", description.value).split(),
    ).casefold()
    if not value:
        log.info("微软拼音描述为空，拒绝 IME 模式切换")
        return False
    if value in config.MICROSOFT_PINYIN_DESCRIPTION_ALLOWLIST:
        return True
    log.info("非微软拼音输入上下文，拒绝 IME 模式切换: %s", description.value)
    return False


def can_switch(mode: str) -> bool:
    """Decide whether CapsLock should be consumed in the current context."""
    try:
        target, foreground = _input_targets()
        language = _current_language(target or foreground)
        if mode == "ime":
            return (
                language == config.LANGID_ZH_CN
                and _is_microsoft_pinyin_context(foreground, target)
            )
        if mode == "layout":
            return language in (config.LANGID_ZH_CN, config.LANGID_EN_US)
        return False
    except Exception as exc:
        log.warning(
            "无法确认当前输入上下文，拒绝切换 exception=%s",
            type(exc).__name__,
        )
        return False


def switch_ime(mode: str = "layout") -> bool:
    try:
        return _switch_ime(mode)
    except Exception as exc:
        log.warning(
            "switch_ime failed mode=%s exception=%s",
            mode,
            type(exc).__name__,
        )
        return False


def _switch_ime(mode: str) -> bool:
    """Switch using the selected strategy; return whether a change was sent."""
    target, foreground = _input_targets()
    language = _current_language(target or foreground)
    if foreground is None or language is None:
        log.warning("switch_ime: no usable foreground window")
        return False

    if mode == "ime":
        if (
            language != config.LANGID_ZH_CN
            or not _is_microsoft_pinyin_context(foreground, target)
        ):
            return False
        current = _get_mode_with_fallback(target, foreground)
        if current is not None:
            open_status = _get_open_status_with_fallback(target, foreground)
            if open_status is None:
                log.warning("微软拼音打开状态不可读，拒绝切换")
                return False
            currently_chinese = bool(
                open_status and (current & config.IME_CMODE_NATIVE),
            )
            desired_chinese = not currently_chinese
            new_mode = (
                current | config.IME_CMODE_NATIVE
                if desired_chinese
                else current & ~config.IME_CMODE_NATIVE
            )
            expected_open_status = desired_chinese

            if expected_open_status is True:
                open_written = _set_open_status_with_fallback(
                    target, foreground, True,
                )
                mode_written = _set_mode_with_fallback(
                    target, foreground, new_mode,
                )
            else:
                mode_written = _set_mode_with_fallback(
                    target, foreground, new_mode,
                )
                open_written = (
                    expected_open_status is False
                    and _set_open_status_with_fallback(
                        target, foreground, False,
                    )
                )
            if (
                mode_written
                and (
                    expected_open_status is None
                    or open_written
                )
                and _ime_state_matches(
                    target,
                    foreground,
                    new_mode,
                    expected_open_status,
                )
            ):
                log.info("微软拼音内部模式已切换")
                return True
            log.warning("微软拼音切换结果未确认，已取消本次切换")
        else:
            log.warning("微软拼音转换模式不可读，已取消本次切换")
        return False

    if mode != "layout" or language not in (
        config.LANGID_ZH_CN,
        config.LANGID_EN_US,
    ):
        return False

    if language == config.LANGID_ZH_CN:
        new_hkl = user32.LoadKeyboardLayoutW(
            config.LAYOUT_EN_US, config.KLF_NOTELLSHELL,
        )
        if not new_hkl or not _post_layout_change(target, foreground, new_hkl):
            log.warning("无法切换到英文键盘布局")
            return False
        log.info("键盘布局已切换: 中 → En")
        return True

    new_hkl = user32.LoadKeyboardLayoutW(
        config.LAYOUT_ZH_CN, config.KLF_NOTELLSHELL,
    )
    if not new_hkl:
        log.warning("LoadKeyboardLayout(%s) 失败", config.LAYOUT_ZH_CN)
        return False
    if not _post_layout_change(target, foreground, new_hkl):
        log.warning("无法切换到中文键盘布局")
        return False
    log.info("键盘布局已切换: En → 中")
    return True


def get_ime_status() -> bool:
    """Return whether the foreground context can currently type Chinese."""
    target, foreground = _input_targets()
    if _current_language(target or foreground) != config.LANGID_ZH_CN:
        return False
    mode = _get_mode_with_fallback(target, foreground)
    if mode is None:
        return True
    open_status = _get_open_status_with_fallback(target, foreground)
    if open_status is False:
        return False
    return bool(mode & config.IME_CMODE_NATIVE)
