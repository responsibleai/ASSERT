# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path
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
                "--default-page-size",
                "10",
                "--max-page-size",
                "25",
                "--max-response-bytes",
                "8192",
                "--default-artifact-chunk-bytes",
                "1024",
                "--max-artifact-chunk-bytes",
                "2048",
                "--max-config-bytes",
                "4096",
            ],
        )

    assert result.exit_code == 0, result.output
    create_kwargs = server_options.create.call_args.kwargs
    assert create_kwargs["workspace_root"].is_absolute()
    assert create_kwargs["mode"] == "author"
    assert create_kwargs["enabled_groups"] == ("design",)
    assert create_kwargs["default_page_size"] == 10
    assert create_kwargs["max_page_size"] == 25
    assert create_kwargs["max_response_bytes"] == 8192
    assert create_kwargs["default_artifact_chunk_bytes"] == 1024
    assert create_kwargs["max_artifact_chunk_bytes"] == 2048
    assert create_kwargs["max_config_bytes"] == 4096
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


def test_mcp_serve_loads_workspace_env_before_server() -> None:
    runner = CliRunner()
    calls: list[str] = []
    options = object()
    server_module = SimpleNamespace(
        ServerOptions=SimpleNamespace(
            create=Mock(side_effect=lambda **_: calls.append("options") or options)
        ),
        run_stdio_server=Mock(side_effect=lambda _: calls.append("serve")),
    )

    with runner.isolated_filesystem():
        env_file = Path(".env")
        env_file.write_text("AZURE_API_KEY=placeholder\n", encoding="utf-8")
        with (
            patch(
                "assert_ai.mcp._command.bootstrap_environment",
                side_effect=lambda **_: calls.append("environment"),
            ) as bootstrap,
            patch(
                "assert_ai.mcp._command._load_server_module",
                return_value=server_module,
            ),
        ):
            result = runner.invoke(
                cli,
                ["mcp", "serve", "--workspace", ".", "--env-file", ".env"],
            )

    assert result.exit_code == 0, result.output
    assert calls == ["environment", "options", "serve"]
    bootstrap.assert_called_once()
    assert bootstrap.call_args.kwargs["env_file"].name == ".env"
    assert bootstrap.call_args.kwargs["env_file"].is_absolute()


def test_mcp_serve_rejects_env_file_outside_workspace() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        workspace = Path("workspace")
        workspace.mkdir()
        Path("outside.env").write_text("AZURE_API_KEY=placeholder\n", encoding="utf-8")
        with (
            patch("assert_ai.mcp._command.bootstrap_environment") as bootstrap,
            patch("assert_ai.mcp._command._load_server_module") as load_server,
        ):
            result = runner.invoke(
                cli,
                [
                    "mcp",
                    "serve",
                    "--workspace",
                    str(workspace),
                    "--env-file",
                    str(Path("..") / "outside.env"),
                ],
            )

    assert result.exit_code == 1
    assert "escapes its expected root directory" in result.output
    bootstrap.assert_not_called()
    load_server.assert_not_called()


def test_mcp_serve_without_env_file_does_not_bootstrap() -> None:
    runner = CliRunner()
    server_module = SimpleNamespace(
        ServerOptions=SimpleNamespace(create=Mock(return_value=object())),
        run_stdio_server=Mock(),
    )
    with runner.isolated_filesystem(), patch(
        "assert_ai.mcp._command.bootstrap_environment",
    ) as bootstrap, patch(
        "assert_ai.mcp._command._load_server_module",
        return_value=server_module,
    ):
        result = runner.invoke(cli, ["mcp", "serve"])

    assert result.exit_code == 0, result.output
    bootstrap.assert_not_called()
