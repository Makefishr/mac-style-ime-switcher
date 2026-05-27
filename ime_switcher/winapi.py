"""Win32 API declarations (ctypes)."""

import ctypes
from ctypes import wintypes

from . import config

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


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM,
    ctypes.POINTER(KBDLLHOOKSTRUCT),
)

# ═══════════════════════════════════════════════════════════
#  DLL handles
# ═══════════════════════════════════════════════════════════
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

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

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD),
]
user32.SendMessageTimeoutW.restype = ctypes.c_longlong

# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════

def is_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)
