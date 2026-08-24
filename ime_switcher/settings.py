"""Small persisted settings module and its tkinter editor."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import winreg
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import config, winapi

log = logging.getLogger(__name__)

MODE_IME = "ime"
MODE_LAYOUT = "layout"


class SaveResult(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL_FAILURE = "partial_failure"


def parse_setting_bool(value, *, default: bool) -> bool:
    """Parse a persisted boolean without misreading explicit strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        log.warning("设置中的布尔字段无效，已使用默认值")
        return default
    log.warning("设置中的布尔字段无效，已使用默认值")
    return default


@dataclass(frozen=True)
class Settings:
    mode: str = MODE_LAYOUT
    autostart: bool = False
    admin: bool = False

    @classmethod
    def from_dict(cls, data: dict, *, default_autostart: bool = False) -> "Settings":
        mode = data.get("mode", MODE_LAYOUT)
        if mode not in (MODE_IME, MODE_LAYOUT):
            mode = MODE_LAYOUT
        return cls(
            mode=mode,
            autostart=parse_setting_bool(
                data.get("autostart", default_autostart),
                default=default_autostart,
            ),
            admin=parse_setting_bool(data.get("admin", False), default=False),
        )

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "autostart": parse_setting_bool(
                self.autostart,
                default=False,
            ),
            "admin": parse_setting_bool(self.admin, default=False),
        }


@dataclass(frozen=True)
class RunValueSnapshot:
    exists: bool
    value: object | None = None
    value_type: int = winreg.REG_SZ


_settings_lock = threading.RLock()
_current: Settings | None = None
_current_path: Path | None = None
_settings_window_lock = threading.Lock()
_settings_thread: threading.Thread | None = None


def _normalize_settings(settings: Settings) -> Settings:
    return Settings.from_dict(settings.to_dict())


def _stage_settings_file(target: Path, settings: Settings) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(name)
    staged = False
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                json.dumps(
                    settings.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
            )
            handle.flush()
            if hasattr(os, "fsync"):
                os.fsync(handle.fileno())
        staged = True
        return temporary
    finally:
        if not staged:
            temporary.unlink(missing_ok=True)


def _stage_file_bytes(target: Path, content: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.restore.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(name)
    staged = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            if hasattr(os, "fsync"):
                os.fsync(handle.fileno())
        staged = True
        return temporary
    finally:
        if not staged:
            temporary.unlink(missing_ok=True)


def _file_matches_snapshot(
    target: Path,
    snapshot: tuple[bool, bytes | None],
) -> bool:
    existed, content = snapshot
    try:
        return existed and target.read_bytes() == content
    except FileNotFoundError:
        return not existed


def _restore_file_snapshot(
    target: Path,
    snapshot: tuple[bool, bytes | None],
) -> None:
    existed, content = snapshot
    if _file_matches_snapshot(target, snapshot):
        return
    if not existed:
        target.unlink(missing_ok=True)
        return

    temporary = _stage_file_bytes(target, content or b"")
    try:
        try:
            os.replace(temporary, target)
        except Exception:
            if not _file_matches_snapshot(target, snapshot):
                raise
    finally:
        temporary.unlink(missing_ok=True)


def load_settings(
    path: str | os.PathLike[str] | None = None,
    *,
    autostart_detector=None,
) -> Settings:
    """Load settings, using the current registry value for a first-run default."""
    target = Path(path or config.SETTINGS_FILE)
    default_autostart = (
        bool(autostart_detector())
        if autostart_detector is not None
        else is_autostart_enabled()
    )
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings root is not an object")
        return Settings.from_dict(data, default_autostart=default_autostart)
    except FileNotFoundError:
        return Settings(autostart=default_autostart)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("无法读取设置文件 %s，将使用默认设置: %s", target, exc)
        return Settings(autostart=default_autostart)


def save_settings(
    settings: Settings,
    path: str | os.PathLike[str] | None = None,
) -> None:
    with _settings_lock:
        _save_settings_unlocked(settings, path)


def _save_settings_unlocked(
    settings: Settings,
    path: str | os.PathLike[str] | None,
) -> None:
    target = Path(path or config.SETTINGS_FILE)
    normalized = _normalize_settings(settings)
    temporary = _stage_settings_file(target, normalized)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    global _current, _current_path
    if path is None or target == Path(config.SETTINGS_FILE):
        with _settings_lock:
            _current = normalized
            _current_path = target


def get_settings(
    path: str | os.PathLike[str] | None = None,
    *,
    autostart_detector=None,
) -> Settings:
    global _current, _current_path
    target = Path(path or config.SETTINGS_FILE)
    with _settings_lock:
        if _current is None or _current_path != target:
            _current = load_settings(
                target,
                autostart_detector=autostart_detector,
            )
            _current_path = target
        return _current


# ── HKCU Run ────────────────────────────────────────────────

def _exe_path() -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable])
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "__main__.py"))
    return f'"{sys.executable}" "{main}"'


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_READ,
        ) as key:
            winreg.QueryValueEx(key, config.APP_NAME)
        return True
    except FileNotFoundError:
        return False


def install_autostart() -> None:
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, config.APP_NAME, 0, winreg.REG_SZ, _exe_path())
    log.info("开机自启已开启")


def remove_autostart() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, config.APP_NAME)
        log.info("开机自启已关闭")
    except FileNotFoundError:
        pass


def set_autostart(enabled: bool) -> None:
    if enabled:
        install_autostart()
    else:
        remove_autostart()


class _RunRegistryAdapter:
    def snapshot(self) -> RunValueSnapshot:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                config.REG_RUN_KEY,
                0,
                winreg.KEY_READ,
            ) as key:
                value, value_type = winreg.QueryValueEx(key, config.APP_NAME)
            return RunValueSnapshot(True, value, value_type)
        except FileNotFoundError:
            return RunValueSnapshot(False)

    def apply(self, enabled: bool) -> None:
        set_autostart(enabled)

    def restore(self, snapshot: RunValueSnapshot) -> None:
        if not snapshot.exists:
            remove_autostart()
            return
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            config.REG_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                config.APP_NAME,
                0,
                snapshot.value_type,
                snapshot.value,
            )


def _try_restore_run(adapter, snapshot) -> bool:
    try:
        adapter.restore(snapshot)
        return True
    except Exception:
        log.error("设置回滚失败（无法恢复开机自启）")
        return False


def _try_cleanup_temporary(temporary: Path) -> bool:
    try:
        temporary.unlink(missing_ok=True)
        return True
    except Exception:
        log.error("设置回滚失败（无法清理暂存配置）")
        return False


def _try_restore_file(
    target: Path,
    snapshot: tuple[bool, bytes | None],
) -> bool:
    try:
        _restore_file_snapshot(target, snapshot)
        return True
    except Exception:
        log.error("设置回滚失败（无法恢复配置文件）")
        return False


def save_user_settings(
    new_settings: Settings,
    *,
    path: str | os.PathLike[str] | None = None,
    registry_adapter=None,
) -> SaveResult:
    """Commit settings-file and autostart changes as one operation."""
    with _settings_lock:
        return _save_user_settings_unlocked(
            new_settings,
            path=path,
            registry_adapter=registry_adapter,
        )


def _save_user_settings_unlocked(
    new_settings: Settings,
    *,
    path: str | os.PathLike[str] | None,
    registry_adapter,
) -> SaveResult:
    global _current, _current_path
    target = Path(path or config.SETTINGS_FILE)
    try:
        old_file_exists = target.exists()
        old_file = (
            old_file_exists,
            target.read_bytes() if old_file_exists else None,
        )
    except Exception:
        log.warning("设置保存失败（无法读取原配置）")
        return SaveResult.FAILURE
    adapter = registry_adapter or _RunRegistryAdapter()
    try:
        old_run = adapter.snapshot()
    except Exception:
        log.warning("设置保存失败（无法读取开机自启状态）")
        return SaveResult.FAILURE
    with _settings_lock:
        old_cache = _current, _current_path

    normalized = _normalize_settings(new_settings)
    try:
        temporary = _stage_settings_file(target, normalized)
    except Exception:
        log.warning("设置保存失败（无法暂存配置）")
        return SaveResult.FAILURE
    try:
        adapter.apply(normalized.autostart)
    except Exception:
        run_restored = _try_restore_run(adapter, old_run)
        temp_cleaned = _try_cleanup_temporary(temporary)
        log.warning("设置保存失败（无法更新开机自启）")
        if run_restored and temp_cleaned:
            return SaveResult.FAILURE
        return SaveResult.PARTIAL_FAILURE
    try:
        os.replace(temporary, target)
    except Exception:
        run_restored = _try_restore_run(adapter, old_run)
        file_restored = _try_restore_file(target, old_file)
        temp_cleaned = _try_cleanup_temporary(temporary)
        log.warning("设置保存失败（无法提交配置）")
        if run_restored and file_restored and temp_cleaned:
            return SaveResult.FAILURE
        return SaveResult.PARTIAL_FAILURE

    with _settings_lock:
        _current = normalized
        _current_path = target
    return SaveResult.SUCCESS


# ── Optional administrator relaunch ────────────────────────

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _protected_frozen_executable() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None

    executable = Path(sys.executable)
    if not executable.is_absolute():
        return None
    try:
        resolved_executable = executable.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved_executable.is_file():
        return None

    for folder_id in (
        winapi.FOLDERID_PROGRAM_FILES,
        winapi.FOLDERID_PROGRAM_FILES_X86,
    ):
        value = winapi.get_known_folder_path(folder_id)
        if not value:
            continue
        root = Path(value)
        if not root.is_absolute():
            continue
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if not resolved_root.is_dir():
            continue
        try:
            resolved_executable.relative_to(resolved_root)
        except ValueError:
            continue
        return resolved_executable
    return None


def maybe_relaunch_as_admin() -> bool:
    """Return true if this process should continue running."""
    if not get_settings().admin or is_admin():
        return True

    executable = _protected_frozen_executable()
    if executable is None:
        log.error(
            "管理员权限仅支持受保护的 Program Files 安装，拒绝自动提权",
        )
        return False
    params = subprocess.list2cmdline(sys.argv[1:])

    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(executable), params, str(config.APP_DIR), 1,
    )
    if result <= 32:
        log.error("管理员权限请求失败或被拒绝 (ShellExecuteW=%s)，程序退出", result)
    else:
        log.info("已请求管理员权限，原进程退出")
    return False


# ── Minimal tkinter window ─────────────────────────────────

def save_settings_and_close(
    new_settings: Settings,
    *,
    root,
    show_error_fn,
    path: str | os.PathLike[str] | None = None,
    registry_adapter=None,
) -> SaveResult:
    result = save_user_settings(
        new_settings,
        path=path,
        registry_adapter=registry_adapter,
    )
    if result is SaveResult.SUCCESS:
        root.destroy()
    elif result is SaveResult.FAILURE:
        show_error_fn(
            "保存失败",
            "设置保存失败，请重试。",
            parent=root,
        )
    elif result is SaveResult.PARTIAL_FAILURE:
        show_error_fn(
            "保存失败",
            "设置未完全保存，请重新检查开机自启。",
            parent=root,
        )
    return result


def _show_settings_window() -> None:
    import tkinter as tk
    from tkinter import messagebox

    current = get_settings()
    root = tk.Tk()
    root.title("MacStyleIME 设置")
    root.resizable(False, False)

    mode_var = tk.StringVar(value=current.mode)
    autostart_var = tk.BooleanVar(value=current.autostart)
    admin_var = tk.BooleanVar(value=current.admin)

    frame = tk.Frame(root, padx=16, pady=12)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="CapsLock 切换模式").pack(anchor="w")
    tk.Radiobutton(
        frame, text="微软拼音内部中文/英文模式", variable=mode_var,
        value=MODE_IME,
    ).pack(anchor="w")
    tk.Radiobutton(
        frame, text="中文键盘布局 / 英文键盘布局（默认）", variable=mode_var,
        value=MODE_LAYOUT,
    ).pack(anchor="w")
    tk.Checkbutton(
        frame, text="开机自启", variable=autostart_var,
    ).pack(anchor="w", pady=(8, 0))
    tk.Checkbutton(
        frame, text="以管理员权限启动", variable=admin_var,
    ).pack(anchor="w")
    tk.Label(
        frame,
        text="管理员选项下次启动生效；仅支持 Windows Program Files 安装，便携使用请关闭。",
        fg="gray",
    ).pack(anchor="w", pady=(2, 8))

    def save_and_close() -> None:
        new_settings = Settings(
            mode=mode_var.get(),
            autostart=bool(autostart_var.get()),
            admin=bool(admin_var.get()),
        )
        save_settings_and_close(
            new_settings,
            root=root,
            show_error_fn=messagebox.showerror,
        )

    buttons = tk.Frame(frame)
    buttons.pack(fill="x", pady=(4, 0))
    tk.Button(buttons, text="保存", width=10, command=save_and_close).pack(side="right")
    tk.Button(buttons, text="取消", width=10, command=root.destroy).pack(side="right", padx=(0, 8))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


def _settings_window_thread_main() -> None:
    global _settings_thread
    current_thread = threading.current_thread()
    try:
        _show_settings_window()
    except Exception:
        log.exception("设置窗口运行失败")
    finally:
        with _settings_window_lock:
            if _settings_thread is current_thread:
                _settings_thread = None


def _open_settings_window(*, thread_factory=threading.Thread) -> bool:
    """Start one Tk-owned UI thread without blocking the tray callback."""
    global _settings_thread
    with _settings_window_lock:
        if _settings_thread is not None and _settings_thread.is_alive():
            return False
        thread = thread_factory(
            target=_settings_window_thread_main,
            name="SettingsUI",
            daemon=True,
        )
        _settings_thread = thread

    try:
        thread.start()
    except Exception:
        with _settings_window_lock:
            if _settings_thread is thread:
                _settings_thread = None
        log.exception("无法启动设置窗口线程")
        return False
    return True


def show_settings() -> bool:
    """Open the settings window asynchronously, at most once at a time."""
    return _open_settings_window()
