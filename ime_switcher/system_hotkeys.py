"""Reversible changes to the current user's language-switching shortcuts."""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import winreg
from pathlib import Path

from . import config
from .settings_store import save_json
from .winapi import user32

log = logging.getLogger(__name__)
TOGGLE_KEY = r"Keyboard Layout\Toggle"
VALUE_NAMES = ("Hotkey", "Language Hotkey", "Layout Hotkey")
SPI_SETLANGTOGGLE = 0x005B


class LanguageHotkeys:
    def __init__(self, registry=winreg, refresh=None, backup_path: Path | None = None):
        self._registry = registry
        self._refresh = refresh or self._reload_windows_settings
        self._backup_path = backup_path or config.USER_DATA_DIR / "language_hotkeys_backup.json"

    @staticmethod
    def _reload_windows_settings() -> bool:
        return bool(user32.SystemParametersInfoW(SPI_SETLANGTOGGLE, 0, None, 0))

    def _snapshot(self) -> dict:
        registry = self._registry
        values = {}
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, TOGGLE_KEY, 0, registry.KEY_READ) as key:
                for name in VALUE_NAMES:
                    try:
                        data, kind = registry.QueryValueEx(key, name)
                        binary = isinstance(data, bytes)
                        values[name] = {
                            "data": base64.b64encode(data).decode("ascii") if binary else data,
                            "type": kind,
                            "binary": binary,
                        }
                    except FileNotFoundError:
                        values[name] = None
        except FileNotFoundError:
            return dict.fromkeys(VALUE_NAMES)
        return values

    def apply(self, disabled: bool) -> bool:
        """Return whether Windows accepted reloading; retain backup on any failure."""
        try:
            backup = json.loads(self._backup_path.read_text(encoding="utf-8"))
            if not isinstance(backup, dict) or set(backup) != set(VALUE_NAMES):
                raise ValueError("Invalid language shortcut backup; refusing to overwrite it")
            # Validate the entire backup before performing any writes.
            restored = {}
            for name, value in backup.items():
                if value is None:
                    restored[name] = None
                else:
                    data = value["data"]
                    if value["binary"]:
                        data = base64.b64decode(data, validate=True)
                    restored[name] = (data, int(value["type"]))
        except FileNotFoundError:
            if not disabled:
                # No backup means Windows was never changed by this app (or the
                # backup was already consumed). Either way there is nothing to
                # restore, and the current values are not ours to guess at.
                log.info("No language shortcut backup; leaving current settings unchanged")
                return True
            backup = self._snapshot()
            save_json(self._backup_path, backup)
            restored = None

        registry = self._registry
        with registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER, TOGGLE_KEY, 0, registry.KEY_SET_VALUE,
        ) as key:
            for name in VALUE_NAMES:
                if disabled:
                    registry.SetValueEx(key, name, 0, registry.REG_SZ, "3")
                elif restored[name] is None:
                    try:
                        registry.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                else:
                    data, kind = restored[name]
                    registry.SetValueEx(key, name, 0, kind, data)

        accepted = self._refresh()
        if not accepted:
            log.warning("Windows did not reload language shortcuts (error %d); sign out to apply", ctypes.get_last_error())
        if not disabled and accepted:
            # Only remove the recovery state after all original values are restored.
            self._backup_path.unlink()
        return accepted
