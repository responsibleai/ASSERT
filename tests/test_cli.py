# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from assert_ai.cli import cli


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_removed_commands_are_unavailable(self) -> None:
        for command in ["taxonomy", "test_set"]:
            with self.subTest(command=command):
                result = self.runner.invoke(cli, [command])
                self.assertNotEqual(result.exit_code, 0)

    def test_help_shows_run_subcommand(self) -> None:
        result = self.runner.invoke(cli, ["--help"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Commands:", result.output)
        self.assertIn("run", result.output)

    @unittest.skip("--config is now required; default eval.yaml lookup removed in merge")
    def test_missing_default_config_errors(self) -> None:
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["run"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("No eval.yaml found in current directory", result.output)

    @unittest.skip("--stage and --from options removed in merge")
    def test_stage_and_from_are_mutually_exclusive(self) -> None:
        with self.runner.isolated_filesystem():
            Path("eval.yaml").write_text("suite: test\nstages: []\n", encoding="utf-8")
            result = self.runner.invoke(cli, ["run", "--stage", "judge", "--from", "inference"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("--stage and --from cannot be used together.", result.output)

    @unittest.skip("--stage, --resume, --from options removed in merge")
    def test_run_forwards_new_options(self) -> None:
        with self.runner.isolated_filesystem():
            config = Path("eval.yaml")
            config.write_text("suite: test\nstages: []\n", encoding="utf-8")
            resolved_config = str(config.resolve())

            with patch("assert_ai.runner.run_pipeline", return_value=0) as run_pipeline:
                result = self.runner.invoke(
                    cli,
                    [
                        "run",
                        "--stage",
                        "judge",
                        "--stage",
                        "inference",
                        "--force-stage",
                        "judge",
                        "--resume",
                    ],
                )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            run_pipeline.call_args.kwargs,
            {
                "config": resolved_config,
                "force_stages": ["judge"],
                "stage_filter": ["judge", "inference"],
                "from_stage": None,
                "resume": True,
            },
        )


    def test_run_concurrency_forwarded(self) -> None:
        with self.runner.isolated_filesystem():
            config = Path("eval.yaml")
            config.write_text("suite: test\nstages: []\n", encoding="utf-8")

            import unittest.mock

            mock_runner = unittest.mock.MagicMock()
            mock_runner.run_pipeline.return_value = 0
            with patch("assert_ai.cli._load_runner_module", return_value=mock_runner):
                result = self.runner.invoke(
                    cli,
                    ["run", "--config", str(config), "--concurrency", "5"],
                )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        mock_runner.run_pipeline.assert_called_once()
        self.assertEqual(mock_runner.run_pipeline.call_args.kwargs["concurrency"], 5)

    def test_verbose_flag_accepted(self) -> None:
        result = self.runner.invoke(cli, ["-v", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_quiet_flag_accepted(self) -> None:
        result = self.runner.invoke(cli, ["-q", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_log_file_flag_accepted(self) -> None:
        result = self.runner.invoke(cli, ["--log-file", "/tmp/test.log", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_output_json_flag_accepted(self) -> None:
        result = self.runner.invoke(cli, ["--output", "json", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_output_text_flag_accepted(self) -> None:
        result = self.runner.invoke(cli, ["--output", "text", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)


class CliAuthModeLoggingTest(unittest.TestCase):
    """Verifies the Azure auth-mode log is emitted by LLM-invoking subcommands,
    AFTER ``.env`` and subcommand logging flags have been applied. Regression
    coverage for PR #237 Issue 3 (the line previously fired in the ``cli``
    group callback, bypassing ``--quiet``/``--output json`` and emitting a
    stale pre-dotenv value)."""

    def setUp(self) -> None:
        self.runner = CliRunner()

    def _make_runner_mock(self):
        import unittest.mock as mock
        runner_module = mock.MagicMock()
        runner_module.run_pipeline.return_value = 0
        return runner_module

    def test_help_does_not_emit_auth_mode_log(self) -> None:
        """``--help`` (and other non-LLM invocations of the group callback)
        must not call ``log_resolved_azure_auth_mode``. The line previously
        fired here, leaking into help text and any non-LLM subcommand."""
        with patch("assert_ai.core.model_client.log_resolved_azure_auth_mode") as log_fn:
            result = self.runner.invoke(cli, ["--help"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        log_fn.assert_not_called()

    def test_run_emits_auth_mode_log_after_runner_loads(self) -> None:
        """``run`` must call ``log_resolved_azure_auth_mode`` AFTER
        ``_load_runner_module`` (so the line reflects the dotenv-resolved
        mode, not whatever was frozen at module import)."""
        with self.runner.isolated_filesystem():
            config = Path("eval.yaml")
            config.write_text("suite: test\nstages: []\n", encoding="utf-8")

            call_order: list[str] = []
            runner_mock = self._make_runner_mock()

            def _load_runner_module():
                call_order.append("load_runner")
                return runner_mock

            def _log_auth_mode():
                call_order.append("log_auth")

            with patch("assert_ai.cli._load_runner_module", side_effect=_load_runner_module), \
                 patch(
                     "assert_ai.core.model_client.log_resolved_azure_auth_mode",
                     side_effect=_log_auth_mode,
                 ):
                result = self.runner.invoke(cli, ["run", "--config", str(config)])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            call_order,
            ["load_runner", "log_auth"],
            msg="auth-mode log must fire AFTER runner.py loads (runner.py "
                "triggers load_dotenv + refresh_azure_auth_mode).",
        )

    def test_run_respects_subcommand_quiet_flag(self) -> None:
        """``assert-ai run --quiet`` must apply ``--quiet`` to the root
        logger BEFORE emitting the auth-mode log. Previously the flag was
        ignored because the log fired from the group callback before the
        subcommand body re-configured logging."""
        with self.runner.isolated_filesystem():
            config = Path("eval.yaml")
            config.write_text("suite: test\nstages: []\n", encoding="utf-8")

            captured_level: dict[str, int] = {}

            def _capture_level():
                import logging as _logging
                captured_level["level"] = _logging.getLogger().level

            with patch("assert_ai.cli._load_runner_module", return_value=self._make_runner_mock()), \
                 patch(
                     "assert_ai.core.model_client.log_resolved_azure_auth_mode",
                     side_effect=_capture_level,
                 ):
                result = self.runner.invoke(
                    cli, ["run", "--quiet", "--config", str(config)]
                )

        import logging as _logging
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            captured_level.get("level"),
            _logging.WARNING,
            msg="auth-mode log fired before subcommand --quiet was applied "
                "(root logger should be at WARNING when the log emits).",
        )


if __name__ == "__main__":
    unittest.main()
