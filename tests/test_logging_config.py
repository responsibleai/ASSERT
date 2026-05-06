"""Tests for p2m.logging_config."""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from p2m.logging_config import configure_logging


class ConfigureLoggingTest(unittest.TestCase):
    """Verify that configure_logging sets the expected root logger state."""

    def tearDown(self) -> None:
        # Reset root logger to avoid cross-test pollution.
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_default_level_is_info(self) -> None:
        configure_logging()
        self.assertEqual(logging.getLogger().level, logging.INFO)

    def test_verbose_sets_debug(self) -> None:
        configure_logging(verbose=True)
        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_quiet_sets_warning(self) -> None:
        configure_logging(quiet=True)
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    def test_console_handler_uses_original_stderr(self) -> None:
        configure_logging()
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)
        handler = root.handlers[0]
        # RichHandler wraps a Console; verify it targets __stderr__.
        console = handler.console  # type: ignore[attr-defined]
        self.assertIs(console.file, sys.__stderr__)

    def test_file_handler_added_when_log_file_set(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "test.log"
            configure_logging(log_file=log_path)
            root = logging.getLogger()
            self.assertEqual(len(root.handlers), 2)
            file_handler = root.handlers[1]
            self.assertIsInstance(file_handler, logging.FileHandler)

    def test_file_handler_creates_parent_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "nested" / "dir" / "test.log"
            configure_logging(log_file=log_path)
            self.assertTrue(log_path.parent.exists())

    def test_litellm_loggers_pinned_to_warning(self) -> None:
        configure_logging(verbose=True)
        for name in ("LiteLLM", "litellm", "httpx"):
            with self.subTest(logger=name):
                self.assertEqual(logging.getLogger(name).level, logging.WARNING)

    def test_repeated_calls_do_not_duplicate_handlers(self) -> None:
        configure_logging()
        configure_logging()
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)
