"""Tests for the ``assert-ai init`` CLI command."""

from __future__ import annotations

import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from assert_ai.cli import cli
from assert_ai.init._context import _load_harm_skill_text, build_system_message


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
    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
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

    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
    def test_describe_file_carries_shell_hostile_text(self, _mock_sys, mock_llm) -> None:
        """Generated prose reaches the design agent without shell quoting."""
        mock_llm.return_value = _done_response()
        description = 'The "agent" runs `whoami`; it $(exits) with \'quotes\'.\nSecond line.'
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("describe.txt").write_text(description, encoding="utf-8")
            result = runner.invoke(cli, [
                "init",
                "--describe-file", "describe.txt",
                "--non-interactive",
            ])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(Path("eval_config.yaml").exists())
        user_message = next(
            m for m in mock_llm.call_args.kwargs["messages"] if m["role"] == "user"
        )
        self.assertIn(description, user_message["content"])

    def test_describe_and_describe_file_are_mutually_exclusive(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("describe.txt").write_text("A chatbot", encoding="utf-8")
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--describe-file", "describe.txt",
                "--non-interactive",
            ])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("mutually exclusive", result.output)

    def test_empty_describe_file_fails(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("describe.txt").write_text("   \n", encoding="utf-8")
            result = runner.invoke(cli, [
                "init",
                "--describe-file", "describe.txt",
                "--non-interactive",
            ])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("empty", result.output)

    def test_missing_describe_file_fails(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                "init",
                "--describe-file", "nope.txt",
                "--non-interactive",
            ])
            self.assertNotEqual(result.exit_code, 0)

    def test_non_utf8_describe_file_fails_cleanly(self) -> None:
        """A non-UTF-8 file exits via _error, not an UnicodeDecodeError traceback."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            # UTF-16 bytes are not decodable as UTF-8.
            Path("describe.txt").write_bytes("a measurable behavior".encode("utf-16"))
            result = runner.invoke(cli, [
                "init",
                "--describe-file", "describe.txt",
                "--non-interactive",
            ])
            self.assertNotEqual(result.exit_code, 0)
            self.assertNotIsInstance(result.exception, UnicodeDecodeError)
            self.assertIn("not valid UTF-8", result.output)

    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
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

    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
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
            "### 0. Mode Selection",
            "### 1. Application Context",
            "### 3. Pipeline Default Model",
            "Automatic harm-template flow",
            "policy_violation",
            "overrefusal",
        ):
            self.assertIn(anchor, prompt, f"missing anchor: {anchor!r}")

    def test_prompt_injects_harm_template_skill(self) -> None:
        """The automatic flow relies on the harm skill being injected.

        Guards both the wrapper preamble (which adapts the skill to the
        no-web-tools init runtime) and a distinctive line from the skill
        body itself, so a broken loader can't silently drop the skill.

        The body check reads the heading from whichever methodology doc
        this repo actually ships, so the test follows the loader's
        resolution order instead of pinning one repo layout.
        """
        prompt = build_system_message()
        self.assertIn("Harm Eval Template Skill", prompt)
        # Adaptation preamble reconciling the skill with the init runtime.
        self.assertIn("do **not** have live web-browsing tools", prompt)
        skill_text = _load_harm_skill_text()
        self.assertIsNotNone(skill_text, "no harm methodology doc was found to inject")
        assert skill_text is not None
        heading = skill_text.splitlines()[0].strip()
        self.assertIn(heading, prompt)

    def test_prompt_web_capability_toggles_with_web_search(self) -> None:
        """Web-capability wording flips with the ``web_search`` flag.

        With web search on the prompt advertises the tool and still directs the
        harm flow to do the literature review, which is the methodology's main
        value: it is what produces researched dimensions instead of recalled
        ones. With it off, the knowledge-only, no-fabricated-URLs guidance
        stands in. Neither branch may promise a retrieval it cannot perform.
        """
        with_web = build_system_message(web_search=True)
        self.assertIn("Live Web Research", with_web)
        # The research imperative survives: naming the frameworks to search is
        # what makes this a literature review rather than a recall exercise.
        self.assertIn("Attempt the skill's research", with_web)
        self.assertIn("MLCommons AILuminate", with_web)
        self.assertIn("Never emit a URL you did not retrieve", with_web)

        without_web = build_system_message(web_search=False)
        self.assertNotIn("Live Web Research", without_web)
        self.assertIn("do **not** have live web-browsing tools", without_web)

    def test_web_search_is_dropped_when_the_fallback_is_already_active(self) -> None:
        """A prompt must never promise research the runtime cannot perform.

        When the process has already fallen back to Chat Completions there is no
        Responses-API `web_search` tool to hand the model, but the flag the user
        passed is still True. Composing the prompt from the requested flag then
        advertises live research that cannot happen, and the model answers by
        inventing citations. The default stays True; only this genuinely
        toolless state drops it.
        """
        with patch(
            "assert_ai.core.model_client.chat_completions_fallback_active",
            return_value=True,
        ), patch(
            "assert_ai.init._design_agent.run_design_loop", return_value=None
        ) as loop:
            runner = CliRunner()
            with runner.isolated_filesystem():
                runner.invoke(cli, [
                    "init",
                    "--describe", "A chatbot",
                    "--non-interactive",
                    "--web-search",
                ])

        self.assertTrue(loop.called, "design loop was never reached")
        self.assertFalse(
            loop.call_args.kwargs["web_search"],
            "web search must be dropped when the Responses API is unavailable",
        )

    def test_web_search_survives_when_the_fallback_is_inactive(self) -> None:
        """Guards the fix against over-reach: normal runs keep live research."""
        with patch(
            "assert_ai.core.model_client.chat_completions_fallback_active",
            return_value=False,
        ), patch(
            "assert_ai.init._llm.web_search_available", return_value=True
        ), patch(
            "assert_ai.init._design_agent.run_design_loop", return_value=None
        ) as loop:
            runner = CliRunner()
            with runner.isolated_filesystem():
                runner.invoke(cli, [
                    "init",
                    "--describe", "A chatbot",
                    "--non-interactive",
                    "--web-search",
                ])

        self.assertTrue(loop.called, "design loop was never reached")
        self.assertTrue(
            loop.call_args.kwargs["web_search"],
            "web search must survive when the Responses API is available",
        )

    def test_prompt_includes_default_model_hint_when_provided(self) -> None:
        prompt = build_system_message(default_model_hint="azure/gpt-5.4")
        self.assertIn("Pipeline default_model Hint (from --default-model)", prompt)
        self.assertIn("azure/gpt-5.4", prompt)

    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
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


class InitWebSearchTest(unittest.TestCase):
    """Live web research wiring for the design agent."""

    def test_web_search_available_gates_by_family(self) -> None:
        from assert_ai.init._llm import web_search_available

        self.assertTrue(web_search_available("azure/gpt-5.4-mini"))
        self.assertTrue(web_search_available("openai/gpt-4o"))
        self.assertFalse(web_search_available("gemini/gemini-1.5-pro"))

    @patch("assert_ai.init._llm._chat_completion_web_search", return_value="{}")
    def test_chat_completion_routes_to_web_search_path(self, mock_web) -> None:
        from assert_ai.init._llm import chat_completion

        chat_completion(model="azure/gpt-5.4-mini", messages=[], web_search=True)
        self.assertTrue(mock_web.called)

    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
    def test_web_search_flag_passed_to_loop(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _done_response()
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--non-interactive",
                "--model", "azure/gpt-5.4-mini",
                "--web-search",
            ])
            self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(mock_llm.call_args.kwargs["web_search"])

    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
    def test_no_web_search_flag_disables_web(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _done_response()
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--non-interactive",
                "--no-web-search",
            ])
            self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(mock_llm.call_args.kwargs["web_search"])

    @patch("assert_ai.init._design_agent.chat_completion")
    @patch("assert_ai.init._design_agent.build_system_message", return_value="sys")
    def test_web_search_degrades_for_unsupported_model(self, _mock_sys, mock_llm) -> None:
        mock_llm.return_value = _done_response()
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, [
                "init",
                "--describe", "A chatbot",
                "--non-interactive",
                "--model", "gemini/gemini-1.5-pro",
                "--web-search",
            ])
            self.assertEqual(result.exit_code, 0, result.output)
        # Unsupported family → web search disabled before the loop runs.
        self.assertFalse(mock_llm.call_args.kwargs["web_search"])


if __name__ == "__main__":
    unittest.main()
