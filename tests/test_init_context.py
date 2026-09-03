"""Tests for the context builder in ``assert-ai init``."""

from __future__ import annotations

import unittest
from pathlib import Path

import assert_ai.init._context as context_module
from assert_ai.init._context import (
    _context_window_for,
    _estimate_tokens,
    _load_harm_skill_text,
    build_system_message,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLAUDE_COPY = (
    _REPO_ROOT
    / ".claude"
    / "skills"
    / "run-assert-eval"
    / "workflows"
    / "research-eval-dimensions.md"
)
_PACKAGED_COPY = (
    _REPO_ROOT / "assert_ai" / "internal_pipeline_prompts" / "research_eval_dimensions.md"
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


class HarmSkillPackagingTest(unittest.TestCase):
    """The harm-template mode is only offered when its methodology is present.

    `pyproject.toml` ships `assert_ai*` only, so `.claude/` is absent from a
    wheel. Resolution that walks the filesystem from `__file__` lands in
    site-packages for every pip user and finds nothing, while the prompt still
    advertised the mode. These tests pin both halves: the methodology is
    packaged, and its absence withdraws the option instead of proceeding.
    """

    def test_packaged_copy_matches_the_claude_copy(self) -> None:
        """Two copies of one methodology can drift; only this test notices."""
        self.assertTrue(_PACKAGED_COPY.is_file(), f"missing: {_PACKAGED_COPY}")
        self.assertTrue(_CLAUDE_COPY.is_file(), f"missing: {_CLAUDE_COPY}")
        self.assertEqual(
            _PACKAGED_COPY.read_bytes(),
            _CLAUDE_COPY.read_bytes(),
            "the packaged methodology has drifted from the .claude copy; "
            "copy the .claude version over the packaged one",
        )

    def test_loads_without_a_source_checkout(self) -> None:
        """Simulates a wheel install: no `.claude/`, so only the package works."""
        original = context_module.__file__
        try:
            # Point `__file__` outside any checkout so `parents[2]` resolves to a
            # directory that holds neither candidate, exactly as site-packages does.
            context_module.__file__ = str(Path.home() / "not-a-checkout" / "_context.py")
            self.assertIsNotNone(_load_harm_skill_text())
        finally:
            context_module.__file__ = original

    def test_unavailable_methodology_withdraws_the_option(self) -> None:
        """Absence must not read as "proceed without the instructions"."""
        original = context_module._load_harm_skill_text
        try:
            context_module._load_harm_skill_text = lambda: None
            msg = build_system_message(model="azure/gpt-5.4-mini")
        finally:
            context_module._load_harm_skill_text = original

        self.assertIn("not available", msg.lower())
        lowered = msg.lower()
        offer = "automatic (harm template)"
        withdrawal = lowered.rfind("not available")
        self.assertGreater(
            withdrawal,
            lowered.find(offer),
            "the withdrawal notice must come after the menu that offers the mode",
        )


if __name__ == "__main__":
    unittest.main()
