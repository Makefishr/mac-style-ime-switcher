"""Tests for the public configuration and logging boundary."""

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_config_in_child(environment, *, fail_mkdir=False):
    child_code = """
import json
import logging
import os
from pathlib import Path

if os.environ.get("FAIL_MKDIR") == "1":
    from unittest.mock import patch
    with patch.object(Path, "mkdir", side_effect=OSError("blocked")):
        from ime_switcher import config
else:
    from ime_switcher import config

print(json.dumps({
    "app_dir": str(config.APP_DIR),
    "log_file": str(config.LOG_FILE),
    "settings_file": str(config.SETTINGS_FILE),
    "version": config.VERSION,
    "root_level": logging.getLogger().level,
}))
"""
    child_environment = dict(environment)
    child_environment["FAIL_MKDIR"] = "1" if fail_mkdir else "0"
    completed = subprocess.run(
        [sys.executable, "-c", child_code],
        cwd=REPO_ROOT,
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(
            f"config import failed: stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r}"
        )
    return json.loads(completed.stdout)


class TestLoggingConfiguration(unittest.TestCase):
    def test_release_version_is_1_4_0(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(Path(directory) / "local-app-data")

            result = load_config_in_child(environment)

        self.assertEqual(result["version"], "1.4.0")

    def test_logging_location_fallback_and_import_failure_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            local_appdata = Path(directory) / "local-app-data"
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(local_appdata)
            local_result = load_config_in_child(environment)

            self.assertEqual(
                Path(local_result["log_file"]),
                local_appdata / "MacStyleIME" / "ime_switcher.log",
            )
            self.assertTrue(Path(local_result["log_file"]).parent.is_dir())
            self.assertEqual(
                Path(local_result["settings_file"]),
                REPO_ROOT / "ime_switcher.json",
            )
            self.assertEqual(local_result["root_level"], logging.INFO)

            fallback_environment = os.environ.copy()
            fallback_environment.pop("LOCALAPPDATA", None)
            fallback_result = load_config_in_child(fallback_environment)

            fallback_log = Path(fallback_result["log_file"])
            self.assertEqual(
                fallback_log.parent.name,
                "MacStyleIME",
            )
            self.assertEqual(fallback_log.parent.parent, Path(tempfile.gettempdir()))
            self.assertNotEqual(
                fallback_log.parent,
                Path(fallback_result["app_dir"]),
            )
            self.assertTrue(fallback_log.parent.is_dir())

            failure_environment = os.environ.copy()
            failure_environment.pop("LOCALAPPDATA", None)
            failure_result = load_config_in_child(
                failure_environment,
                fail_mkdir=True,
            )

            self.assertNotEqual(
                Path(failure_result["log_file"]).parent,
                Path(failure_result["app_dir"]),
            )
            self.assertEqual(failure_result["root_level"], logging.INFO)
