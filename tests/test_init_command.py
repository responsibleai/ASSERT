"""Tests for the ``p2m init`` CLI command."""

from __future__ import annotations

import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from p2m.cli import cli


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
    @patch("p2m.init._design_agent.chat_completion")
    @patch("p2m.init._design_agent.build_system_message", return_value="sys")
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

    @patch("p2m.init._design_agent.chat_completion")
    @patch("p2m.init._design_agent.build_system_message", return_value="sys")
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

    @patch("p2m.init._design_agent.chat_completion")
    @patch("p2m.init._design_agent.build_system_message", return_value="sys")
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


if __name__ == "__main__":
    unittest.main()
