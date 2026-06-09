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

    # -- bundled examples (`--example`) -----------------------------------

    def test_example_resolves_to_bundled_config(self) -> None:
        import unittest.mock

        mock_runner = unittest.mock.MagicMock()
        mock_runner.run_pipeline.return_value = 0
        with patch("assert_ai.cli._load_runner_module", return_value=mock_runner):
            result = self.runner.invoke(cli, ["run", "--example", "health-assistant"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        mock_runner.run_pipeline.assert_called_once()
        forwarded = mock_runner.run_pipeline.call_args.kwargs["config"]
        self.assertTrue(forwarded.endswith("eval_config.yaml"), msg=forwarded)
        self.assertIn("health_assistant", forwarded)
        self.assertTrue(Path(forwarded).exists(), msg=forwarded)

    def test_example_without_value_lists_catalog(self) -> None:
        result = self.runner.invoke(cli, ["run", "--example"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("health-assistant", result.output)
        self.assertIn("travel-planner-langgraph", result.output)

    def test_unknown_example_lists_catalog_and_errors(self) -> None:
        result = self.runner.invoke(cli, ["run", "--example", "does-not-exist"])
        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertIn("Unknown example", result.output)
        self.assertIn("health-assistant", result.output)

    def test_config_and_example_are_mutually_exclusive(self) -> None:
        with self.runner.isolated_filesystem():
            config = Path("eval.yaml")
            config.write_text("suite: test\n", encoding="utf-8")
            result = self.runner.invoke(
                cli, ["run", "--config", str(config), "--example", "health-assistant"]
            )
        self.assertEqual(result.exit_code, 1, msg=result.output)
        self.assertIn("not both", result.output)

    def test_run_without_config_or_example_lists_catalog(self) -> None:
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["run"])
        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertIn("--example", result.output)


class BundledExamplesTest(unittest.TestCase):
    def test_every_registered_example_config_exists(self) -> None:
        from assert_ai._examples import EXAMPLES, resolve_example

        self.assertTrue(EXAMPLES)
        for name in EXAMPLES:
            path = resolve_example(name)
            self.assertTrue(path.exists(), msg=f"{name} -> {path}")

    def test_install_hint_quotes_extras(self) -> None:
        from assert_ai._examples import EXAMPLES

        base = EXAMPLES["health-assistant"]
        self.assertEqual(base.install_hint, "pip install assert-ai")
        extra = EXAMPLES["travel-planner-langgraph"]
        self.assertEqual(extra.install_hint, 'pip install "assert-ai[langgraph,otel]"')


if __name__ == "__main__":
    unittest.main()
