"""Tests for assert_eval.logging_config."""

from __future__ import annotations

import json
import logging
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assert_eval.logging_config import configure_logging


class ConfigureLoggingTest(unittest.TestCase):
    """Verify that configure_logging sets the expected root logger state."""

    def tearDown(self) -> None:
        # Reset root logger to avoid cross-test pollution.
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
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
            self.tearDown()

    def test_file_handler_creates_parent_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "nested" / "dir" / "test.log"
            configure_logging(log_file=log_path)
            self.assertTrue(log_path.parent.exists())
            self.tearDown()

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

    def test_json_output_uses_stream_handler(self) -> None:
        configure_logging(json_output=True)
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)
        handler = root.handlers[0]
        self.assertIsInstance(handler, logging.StreamHandler)
        self.assertNotIsInstance(handler, logging.FileHandler)

    def test_json_output_emits_valid_json(self) -> None:
        configure_logging(json_output=True)
        root = logging.getLogger()
        handler = root.handlers[0]
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = handler.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["message"], "hello world")
        self.assertEqual(parsed["logger"], "test")
        self.assertIn("timestamp", parsed)

    def test_json_output_includes_exception(self) -> None:
        configure_logging(json_output=True)
        root = logging.getLogger()
        handler = root.handlers[0]
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="failed", args=(), exc_info=exc_info,
        )
        output = handler.format(record)
        parsed = json.loads(output)
        self.assertIn("exception", parsed)
        self.assertIn("boom", parsed["exception"])
