"""Tests for the design agent loop in ``assert-eval init``."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from rich.console import Console

from assert_eval.init._design_agent import run_design_loop


_MINIMAL_VALID_YAML = (
    "suite: test_suite\n"
    "behavior:\n"
    "  name: test_eval\n"
    "  description: A test evaluation\n"
    "context: Some context about the system\n"
    "pipeline:\n"
    "  systematize: {}\n"
    "  test_set: {}\n"
    "  inference: {}\n"
    "  judge: {}\n"
)


def _make_response(action: str, content: str, yaml_str: str | None = None) -> str:
    d: dict = {"action": action, "content": content}
    if yaml_str is not None:
        d["yaml"] = yaml_str
    return json.dumps(d)


def _quiet_console() -> Console:
    return Console(quiet=True)


class DesignAgentDoneTest(unittest.TestCase):
    """LLM immediately responds with done and valid YAML."""

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="system prompt")
    def test_non_interactive_done(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _make_response(
            "done", "Here is your config", yaml_str=_MINIMAL_VALID_YAML
        )
        result = run_design_loop(
            model="azure/gpt-5.4-mini",
            describe="A chatbot",
            seed_yaml=None,
            behavior_preset=None,
            judge_preset=None,
            dimension_hints=None,
            non_interactive=True,
            max_turns=5,
            console=_quiet_console(),
            no_color=True,
        )
        self.assertIsNotNone(result)
        self.assertIn("suite:", result)
        mock_llm.assert_called_once()


class DesignAgentSelfCorrectionTest(unittest.TestCase):
    """LLM produces invalid JSON first, then valid response."""

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="system prompt")
    def test_recovers_from_parse_error(self, _mock_sys, mock_llm) -> None:
        mock_llm.side_effect = [
            "This is not JSON",  # first call: parse error
            _make_response("done", "Fixed", yaml_str=_MINIMAL_VALID_YAML),
        ]
        result = run_design_loop(
            model="azure/gpt-5.4-mini",
            describe="A chatbot",
            seed_yaml=None,
            behavior_preset=None,
            judge_preset=None,
            dimension_hints=None,
            non_interactive=True,
            max_turns=5,
            console=_quiet_console(),
            no_color=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(mock_llm.call_count, 2)


class DesignAgentValidationRetryTest(unittest.TestCase):
    """LLM proposes invalid config, then fixes it."""

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="system prompt")
    def test_validation_failure_triggers_retry(self, _mock_sys, mock_llm) -> None:
        bad_yaml = "suite: test\nbehavior: not_a_dict\n"
        mock_llm.side_effect = [
            _make_response("done", "First try", yaml_str=bad_yaml),
            _make_response("done", "Second try", yaml_str=_MINIMAL_VALID_YAML),
        ]
        result = run_design_loop(
            model="azure/gpt-5.4-mini",
            describe="A chatbot",
            seed_yaml=None,
            behavior_preset=None,
            judge_preset=None,
            dimension_hints=None,
            non_interactive=True,
            max_turns=5,
            console=_quiet_console(),
            no_color=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(mock_llm.call_count, 2)


class DesignAgentBudgetExhaustionTest(unittest.TestCase):
    """LLM never produces valid output and exhausts the turn budget."""

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="system prompt")
    def test_returns_none_on_budget_exhaustion(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = "Not JSON"  # always fails parse
        result = run_design_loop(
            model="azure/gpt-5.4-mini",
            describe="A chatbot",
            seed_yaml=None,
            behavior_preset=None,
            judge_preset=None,
            dimension_hints=None,
            non_interactive=True,
            max_turns=3,
            console=_quiet_console(),
            no_color=True,
        )
        self.assertIsNone(result)
        self.assertEqual(mock_llm.call_count, 3)


class DesignAgentLLMErrorTest(unittest.TestCase):
    """LLM raises an auth error — loop should exit gracefully."""

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="system prompt")
    def test_auth_error_returns_none(self, _mock_sys, mock_llm) -> None:
        from assert_eval.core.model_client import LLMAuthError

        mock_llm.side_effect = LLMAuthError("bad key")
        result = run_design_loop(
            model="azure/gpt-5.4-mini",
            describe="A chatbot",
            seed_yaml=None,
            behavior_preset=None,
            judge_preset=None,
            dimension_hints=None,
            non_interactive=True,
            max_turns=5,
            console=_quiet_console(),
            no_color=True,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
