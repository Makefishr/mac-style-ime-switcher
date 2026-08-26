"""Per-user preferences for Mac-style IME Switcher."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


class SwitchingMethod(str, Enum):
    """CapsLock behavior selected by the user."""

    KEYBOARD_LAYOUTS = "keyboard_layouts"
    MICROSOFT_PINYIN_MODE = "microsoft_pinyin_mode"


@dataclass(frozen=True)
class Preferences:
    """Settings whose desired value is owned by the application."""

    run_as_administrator: bool = False
    switching_method: SwitchingMethod = SwitchingMethod.KEYBOARD_LAYOUTS


def settings_path() -> Path:
    """Return the writable per-user settings path."""
    return config.USER_DATA_DIR / "settings.json"


def load_preferences() -> Preferences:
    """Load known preferences, using defaults for missing or invalid data."""
    path = settings_path()
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("settings root must be a JSON object")
        run_as_administrator = data.get("run_as_administrator", False)
        if not isinstance(run_as_administrator, bool):
            raise ValueError("run_as_administrator must be true or false")
        switching_method = SwitchingMethod(
            data.get("switching_method", SwitchingMethod.KEYBOARD_LAYOUTS.value)
        )
        return Preferences(
            run_as_administrator=run_as_administrator,
            switching_method=switching_method,
        )
    except FileNotFoundError:
        return Preferences()
    except (OSError, ValueError, json.JSONDecodeError):
        log.exception("Unable to load settings from %s; using defaults", path)
        return Preferences()


def save_preferences(preferences: Preferences) -> None:
    """Atomically replace the per-user settings file."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=".settings-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(asdict(preferences), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
