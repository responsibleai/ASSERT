# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from click.testing import CliRunner

from assert_ai.cli import cli


def test_root_help_lists_mcp_without_importing_sdk() -> None:
    runner = CliRunner()
    with patch("assert_ai.mcp._command.importlib.import_module") as import_module:
        result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "mcp" in result.output
    import_module.assert_not_called()


def test_mcp_serve_forwards_resolved_options() -> None:
    runner = CliRunner()
    run_stdio_server = Mock()
    options = object()
    server_options = SimpleNamespace(create=Mock(return_value=options))
    server_module = SimpleNamespace(
        ServerOptions=server_options,
        run_stdio_server=run_stdio_server,
    )

    with runner.isolated_filesystem(), patch(
        "assert_ai.mcp._command._load_server_module",
        return_value=server_module,
    ):
        result = runner.invoke(
            cli,
            [
                "mcp",
                "serve",
                "--workspace",
                ".",
                "--mode",
                "author",
                "--enable-group",
                "design",
            ],
        )

    assert result.exit_code == 0, result.output
    create_kwargs = server_options.create.call_args.kwargs
    assert create_kwargs["workspace_root"].is_absolute()
    assert create_kwargs["mode"] == "author"
    assert create_kwargs["enabled_groups"] == ("design",)
    run_stdio_server.assert_called_once_with(options)


def test_mcp_serve_reports_missing_optional_dependency() -> None:
    runner = CliRunner()
    missing = ModuleNotFoundError("No module named 'mcp'", name="mcp")
    with patch(
        "assert_ai.mcp._command.importlib.import_module",
        side_effect=missing,
    ):
        result = runner.invoke(cli, ["mcp", "serve"])

    assert result.exit_code == 1
    assert 'python -m pip install "assert-ai[mcp]"' in result.output
    assert "Traceback" not in result.output
