"""Tests for the ``assert-eval init`` CLI command."""

from __future__ import annotations

import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from assert_eval.cli import cli
from assert_eval.init._context import build_system_message


_MINIMAL_VALID_YAML = (
    "suite: test_suite\n"
    "behavior:\n"
    "  name: test_eval\n"
    "  description: A test evaluation\n"
    "context: Some context\n"
    "pipeline:\n"
    "  systematize: {}\n"
    "  test_set: {}\n"
    "  inference: {}\n"
    "  judge: {}\n"
)


def _done_response(yaml_str: str = _MINIMAL_VALID_YAML) -> str:
    return json.dumps({
        "action": "done",
        "content": "Here is your config",
        "yaml": yaml_str,
    })


class InitCommandTest(unittest.TestCase):
    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="sys")
    def test_non_interactive_generates_file(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _done_response()
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--non-interactive",
                "--model", "azure/gpt-5.4-mini",
            ])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(Path("eval_config.yaml").exists())

    def test_non_interactive_without_describe_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--non-interactive"])
        self.assertNotEqual(result.exit_code, 0)

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="sys")
    def test_dry_run_does_not_write(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _done_response()
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--non-interactive",
                "--dry-run",
            ])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertFalse(Path("eval_config.yaml").exists())

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="sys")
    def test_force_overwrites(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _done_response()
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("eval_config.yaml").write_text("old", encoding="utf-8")
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--non-interactive",
                "--force",
            ])
            self.assertEqual(result.exit_code, 0, result.output)
            content = Path("eval_config.yaml").read_text(encoding="utf-8")
            self.assertIn("suite:", content)


class InitPromptContentTest(unittest.TestCase):
    """Regression tests over the assembled init system prompt.

    The first test is a low-resolution canary: it asserts that the section
    headers and key directives added in this change are still present in
    ``prompts/init_system.md``. It does not test LLM behavior — it only
    guards against accidental deletion during future prompt refactors.
    Reword freely; just keep the named anchors below intact (or update both
    the prompt and this test in the same commit).
    """

    def test_prompt_contains_required_section_anchors(self) -> None:
        prompt = build_system_message()
        for anchor in (
            "### 1. Application Context",
            "### 3. Pipeline Default Model",
            "policy_violation",
            "overrefusal",
        ):
            self.assertIn(anchor, prompt, f"missing anchor: {anchor!r}")

    def test_prompt_includes_default_model_hint_when_provided(self) -> None:
        prompt = build_system_message(default_model_hint="azure/gpt-5.4")
        self.assertIn("Pipeline default_model Hint (from --default-model)", prompt)
        self.assertIn("azure/gpt-5.4", prompt)

    @patch("assert_eval.init._design_agent.chat_completion")
    @patch("assert_eval.init._design_agent.build_system_message", return_value="sys")
    def test_design_agent_surfaces_model_hint_to_llm(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _done_response()
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--non-interactive",
                "--model", "azure/gpt-5.4-mini",
                "--default-model", "azure/gpt-5.4",
            ])
            self.assertEqual(result.exit_code, 0, result.output)

        # Inspect the user message handed to the LLM on the first call.
        messages = mock_llm.call_args.kwargs["messages"]
        user_msgs = [m for m in messages if m.get("role") == "user"]
        self.assertTrue(user_msgs, "expected at least one user message")
        first_user = user_msgs[0]["content"]
        # Design-agent-model hint should always be present.
        self.assertIn("Design-agent model", first_user)
        self.assertIn("azure/gpt-5.4-mini", first_user)
        # --default-model hint should be surfaced when provided.
        self.assertIn("Pipeline default_model hint", first_user)
        self.assertIn("azure/gpt-5.4", first_user)


if __name__ == "__main__":
    unittest.main()
