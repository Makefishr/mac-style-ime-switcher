import logging
import os
import unittest
from pathlib import Path

from ime_switcher import config


class LoggingLevelTests(unittest.TestCase):
    def test_pillow_logs_are_not_emitted_at_debug_level(self):
        self.assertGreaterEqual(
            logging.getLogger("PIL").getEffectiveLevel(), logging.INFO,
        )

    def test_project_logs_keep_the_debug_level(self):
        self.assertLessEqual(
            logging.getLogger("ime_switcher").getEffectiveLevel(), logging.DEBUG,
        )


class TestIsolationTests(unittest.TestCase):
    def test_user_data_is_redirected_away_from_real_profile(self):
        self.assertTrue(config.USER_DATA_DIR.name.startswith(f"{config.APP_NAME}-tests-"))
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            self.assertNotEqual(
                config.USER_DATA_DIR, Path(local_app_data) / config.APP_NAME,
            )

    def test_root_logger_never_holds_a_file_handler_during_tests(self):
        self.assertFalse(
            any(isinstance(handler, logging.FileHandler)
                for handler in logging.getLogger().handlers)
        )
