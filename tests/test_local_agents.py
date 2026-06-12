# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from assert_ai.cli import cli
from assert_ai.local_agents import discover_local_agents


def _make_openclaw_fixture(root: Path) -> tuple[Path, Path, Path]:
    runtime = root / "npm-global" / "lib" / "node_modules" / "openclaw"
    runtime.mkdir(parents=True)
    (runtime / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.5.12"}), encoding="utf-8")
    (runtime / "openclaw.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")

    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("- Check files before answering.\n", encoding="utf-8")
    (workspace / "SOUL.md").write_text("Helpful and direct.\n", encoding="utf-8")
    memory = workspace / "memory"
    memory.mkdir()
    (memory / "context.md").write_text("Project context.\n", encoding="utf-8")
    (workspace / "secret.env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    manifest = workspace / "source-bundle.json"
    manifest.write_text(
        json.dumps(
            {
                "include": ["AGENTS.md", "SOUL.md", "memory/*.md", "*.env"],
                "exclude_patterns": ["*.env", "*secret*"],
                "redact": ["secrets", "host_paths"],
            }
        ),
        encoding="utf-8",
    )
    return runtime, workspace, manifest


def test_discover_local_agents_finds_openclaw_from_explicit_paths(tmp_path: Path) -> None:
    runtime, workspace, manifest = _make_openclaw_fixture(tmp_path)

    result = discover_local_agents(
        target="openclaw",
        runtime_path=runtime,
        workspace_path=workspace,
        source_bundle_path=manifest,
        redact_paths=True,
    )

    payload = result.to_json()
    assert payload["schema_version"] == 1
    assert len(payload["agents"]) == 1
    agent = payload["agents"][0]
    assert agent["id"] == "openclaw"
    assert agent["status"] == "ready"
    assert agent["runtime"]["version"] == "2026.5.12"
    assert agent["runtime"]["path"] == "[LOCAL_PATH]"
    assert agent["workspace"]["path"] == "[LOCAL_PATH]"
    assert {item["path"] for item in agent["candidate_files"]} == {
        "AGENTS.md",
        "SOUL.md",
        "memory/context.md",
    }
    assert "secret.env" in {item["path"] for item in agent["excluded_files"]}


def test_discover_local_agents_reports_common_agent_config_dirs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".gemini").mkdir()
    (home / ".gemini" / "settings.json").write_text("{}\n", encoding="utf-8")
    (home / ".config" / "opencode").mkdir(parents=True)

    payload = discover_local_agents(home=home, redact_paths=True).to_json()
    agents = {agent["id"]: agent for agent in payload["agents"]}

    assert agents["codex"]["status"] == "found"
    assert agents["gemini"]["status"] == "found"
    assert agents["opencode"]["status"] == "found"
    assert agents["codex"]["config"]["path"] == "[LOCAL_PATH]"
    assert agents["gemini"]["config"]["path"] == "[LOCAL_PATH]"


def test_cli_local_discover_lists_multiple_found_agents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".codex").mkdir()
    runner = CliRunner()

    result = runner.invoke(cli, ["local", "discover", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert "Found local agents" in result.output
    assert "hermes" in result.output
    assert "codex" in result.output
    assert "config: [LOCAL_PATH]" in result.output
    assert "found local config or executable" in result.output
    assert "codex (not found)" not in result.output
    assert "runtime: codex" not in result.output


def test_cli_local_discover_writes_reviewable_manifest(tmp_path: Path) -> None:
    runtime, workspace, manifest = _make_openclaw_fixture(tmp_path)
    out = tmp_path / "artifacts" / "local-agents" / "discovery.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "local",
            "discover",
            "--target",
            "openclaw",
            "--runtime-path",
            str(runtime),
            "--workspace",
            str(workspace),
            "--source-bundle",
            str(manifest),
            "--output",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Found local agents" in result.output
    assert "openclaw" in result.output
    assert "ready for snapshot" in result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    agent = payload["agents"][0]
    assert agent["id"] == "openclaw"
    assert agent["runtime"]["path"] == "[LOCAL_PATH]"
    assert {item["path"] for item in agent["candidate_files"]} == {
        "AGENTS.md",
        "SOUL.md",
        "memory/context.md",
    }
