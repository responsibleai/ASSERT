"""Tests for the context builder in ``p2m init``."""

from __future__ import annotations

import unittest

from p2m.init._context import (
    _context_window_for,
    _estimate_tokens,
    build_system_message,
)


class EstimateTokensTest(unittest.TestCase):
    def test_returns_roughly_quarter_length(self) -> None:
        text = "a" * 400
        tokens = _estimate_tokens(text)
        self.assertEqual(tokens, 100)

    def test_empty_string(self) -> None:
        result = _estimate_tokens("")
        self.assertLessEqual(result, 1)


class ContextWindowForTest(unittest.TestCase):
    def test_gpt4_1_mini_default(self) -> None:
        window = _context_window_for("gpt-4.1-mini")
        self.assertGreaterEqual(window, 100_000)

    def test_azure_prefix_stripped(self) -> None:
        window = _context_window_for("azure/gpt-5.4-mini")
        self.assertGreaterEqual(window, 100_000)

    def test_unknown_model_gets_default(self) -> None:
        window = _context_window_for("some-unknown-model")
        self.assertGreater(window, 0)


class BuildSystemMessageTest(unittest.TestCase):
    def test_basic_output_is_string(self) -> None:
        msg = build_system_message(model="azure/gpt-5.4-mini")
        self.assertIsInstance(msg, str)
        self.assertTrue(len(msg) > 100)

    def test_includes_schema_reference(self) -> None:
        msg = build_system_message(model="azure/gpt-5.4-mini")
        # Should mention config structure somewhere
        self.assertTrue(
            "suite" in msg.lower() or "config" in msg.lower() or "yaml" in msg.lower()
        )

    def test_describe_injected(self) -> None:
        msg = build_system_message(model="azure/gpt-5.4-mini", describe="A chatbot for pizza orders")
        self.assertIn("pizza", msg.lower())

    def test_dimension_hints_injected(self) -> None:
        msg = build_system_message(
            model="azure/gpt-5.4-mini",
            dimensions=["tone", "language"],
        )
        self.assertIn("tone", msg)
        self.assertIn("language", msg)


if __name__ == "__main__":
    unittest.main()
