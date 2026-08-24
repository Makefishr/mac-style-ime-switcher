"""Win32 API declarations (ctypes)."""

import ctypes
import logging
from ctypes import wintypes

from . import config

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  Types
# ═══════════════════════════════════════════════════════════

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      wintypes.DWORD),
        ("scanCode",    wintypes.DWORD),
        ("flags",       wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


class GUITHREADINFO(ctypes.Structure):
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


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


FOLDERID_PROGRAM_FILES = GUID(
    0x905E63B6,
    0xC1BF,
    0x494E,
    (ctypes.c_ubyte * 8)(0xB2, 0x9C, 0x65, 0xB7, 0x32, 0xD3, 0xD2, 0x1A),
)
FOLDERID_PROGRAM_FILES_X86 = GUID(
    0x7C5A40EF,
    0xA0FB,
    0x4BFC,
    (ctypes.c_ubyte * 8)(0x87, 0x4A, 0xC0, 0xF2, 0xE0, 0xB9, 0xFA, 0x8E),
)


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM,
    ctypes.POINTER(KBDLLHOOKSTRUCT),
)

# ═══════════════════════════════════════════════════════════
#  DLL handles
# ═══════════════════════════════════════════════════════════
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32  = ctypes.windll.shell32
ole32    = ctypes.windll.ole32

shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(GUID),
    wintypes.DWORD,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.LPWSTR),
]
shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT

ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.restype = None

# SendInput uses the pointer-sized ULONG_PTR field in KEYBDINPUT and the
# ABI-sized INPUT record required by the Win32 declaration.
user32.SendInput.argtypes = [
    wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int,
]
user32.SendInput.restype = wintypes.UINT

# ═══════════════════════════════════════════════════════════
#  Keyboard hook
# ═══════════════════════════════════════════════════════════
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p

user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM,
    ctypes.POINTER(KBDLLHOOKSTRUCT),
]
user32.CallNextHookEx.restype = ctypes.c_longlong

# ═══════════════════════════════════════════════════════════
#  Key state
# ═══════════════════════════════════════════════════════════
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT

user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = wintypes.SHORT

user32.keybd_event.argtypes = [
    wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_ulonglong,
]
user32.keybd_event.restype = None

# ═══════════════════════════════════════════════════════════
#  Message loop (hook thread)
# ═══════════════════════════════════════════════════════════
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
    wintypes.UINT, wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_longlong

# ═══════════════════════════════════════════════════════════
#  Module / window / thread
# ═══════════════════════════════════════════════════════════
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetGUIThreadInfo.argtypes = [
    wintypes.DWORD, ctypes.POINTER(GUITHREADINFO),
]
user32.GetGUIThreadInfo.restype = wintypes.BOOL

# ═══════════════════════════════════════════════════════════
#  Keyboard layout switching
# ═══════════════════════════════════════════════════════════
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.LoadKeyboardLayoutW.restype = wintypes.HANDLE

user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HANDLE

user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.PostMessageW.restype = wintypes.BOOL

user32.SendMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.SendMessageW.restype = ctypes.c_longlong

# ═══════════════════════════════════════════════════════════
#  Single-instance mutex
# ═══════════════════════════════════════════════════════════
kernel32.CreateMutexW.argtypes = [
    ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
]
kernel32.CreateMutexW.restype = wintypes.HANDLE

kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


# ═══════════════════════════════════════════════════════════
#  IME window / conversion mode
# ═══════════════════════════════════════════════════════════
imm32 = ctypes.windll.imm32

imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND

imm32.ImmGetContext.argtypes = [wintypes.HWND]
imm32.ImmGetContext.restype = wintypes.HANDLE

imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
imm32.ImmReleaseContext.restype = wintypes.BOOL

imm32.ImmGetConversionStatus.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
imm32.ImmGetConversionStatus.restype = wintypes.BOOL

imm32.ImmSetConversionStatus.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
]
imm32.ImmSetConversionStatus.restype = wintypes.BOOL

imm32.ImmGetOpenStatus.argtypes = [wintypes.HANDLE]
imm32.ImmGetOpenStatus.restype = wintypes.BOOL

imm32.ImmSetOpenStatus.argtypes = [wintypes.HANDLE, wintypes.BOOL]
imm32.ImmSetOpenStatus.restype = wintypes.BOOL

imm32.ImmGetDescriptionW.argtypes = [
    wintypes.HANDLE, wintypes.LPWSTR, wintypes.UINT,
]
imm32.ImmGetDescriptionW.restype = wintypes.UINT

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t

# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════

def is_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def get_known_folder_path(
    folder_id: GUID,
    *,
    query=None,
    free=None,
) -> str | None:
    """Return a Windows Known Folder path and release its allocated buffer."""
    query = query or shell32.SHGetKnownFolderPath
    free = free or ole32.CoTaskMemFree
    path_pointer = wintypes.LPWSTR()
    status = None
    value = None
    failed = False

    try:
        status = query(
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(path_pointer),
        )
        if status == 0 and path_pointer:
            value = path_pointer.value
    except Exception as exc:
        failed = True
        log.warning(
            "Windows Known Folder 查询失败（%s）",
            type(exc).__name__,
        )
    finally:
        if path_pointer:
            try:
                free(ctypes.cast(path_pointer, ctypes.c_void_p))
            except Exception as exc:
                failed = True
                log.warning(
                    "Windows Known Folder 缓冲区释放失败（%s）",
                    type(exc).__name__,
                )

    if failed:
        return None
    if status != 0:
        log.warning("Windows Known Folder 查询失败（HRESULT）")
        return None
    if not value:
        log.warning("Windows Known Folder 查询返回空路径")
        return None
    return value
