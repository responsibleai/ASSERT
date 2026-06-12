# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from assert_ai.cli import cli
from assert_ai.local_snapshots import create_local_agent_snapshot


def _write_discovery(path: Path, *, target: str = "hermes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "agents": [
                    {
                        "id": target,
                        "display_name": target.title(),
                        "kind": target,
                        "status": "found",
                        "summary": "found local config or executable",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_create_local_agent_snapshot_copies_explicit_roots_and_excludes_secrets(tmp_path: Path) -> None:
    discovery = tmp_path / "artifacts" / "discovery.json"
    _write_discovery(discovery)
    context_root = tmp_path / "KnowledgeBase"
    (context_root / "work").mkdir(parents=True)
    (context_root / "work" / "context.md").write_text("project context\n", encoding="utf-8")
    (context_root / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    (context_root / "nested-secret.txt").write_text("do not copy\n", encoding="utf-8")

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="hermes",
        copy_root_specs=[f"{context_root}:KnowledgeBase"],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    snapshot_root = tmp_path / "snapshot-out" / "snapshot"
    manifest_path = tmp_path / "snapshot-out" / "snapshot_manifest.json"
    assert result.manifest_path == manifest_path
    assert (snapshot_root / "KnowledgeBase" / "work" / "context.md").read_text(encoding="utf-8") == "project context\n"
    assert not (snapshot_root / "KnowledgeBase" / ".env").exists()
    assert not (snapshot_root / "KnowledgeBase" / "nested-secret.txt").exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["target"] == "hermes"
    assert manifest["snapshot_root"] == "snapshot"
    assert manifest["copied_roots"] == [
        {
            "source": "[LOCAL_PATH]",
            "dest": "KnowledgeBase",
            "files_copied": 1,
            "bytes_copied": len("project context\n"),
        }
    ]
    assert {item["dest"] for item in manifest["excluded_files"]} == {
        "KnowledgeBase/.env",
        "KnowledgeBase/nested-secret.txt",
    }
    assert str(tmp_path) not in manifest_path.read_text(encoding="utf-8")


def test_snapshot_excludes_secret_data_without_dropping_runtime_code_names(tmp_path: Path) -> None:
    discovery = tmp_path / "discovery.json"
    _write_discovery(discovery)
    source = tmp_path / "runtime"
    (source / "dist").mkdir(parents=True)
    (source / "dist" / "channel-secret-runtime.js").write_text("export const name = 'secret-tool';\n", encoding="utf-8")
    (source / "client_secret_123.apps.googleusercontent.com.json").write_text("{}\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    create_local_agent_snapshot(
        discovery_path=discovery,
        target="hermes",
        copy_root_specs=[f"{source}:runtime"],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    snapshot_root = tmp_path / "snapshot-out" / "snapshot"
    assert (snapshot_root / "runtime" / "dist" / "channel-secret-runtime.js").exists()
    assert not (snapshot_root / "runtime" / "client_secret_123.apps.googleusercontent.com.json").exists()
    assert not (snapshot_root / "runtime" / ".env").exists()


def test_cli_local_snapshot_create_writes_manifest(tmp_path: Path) -> None:
    discovery = tmp_path / "discovery.json"
    _write_discovery(discovery, target="openclaw")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli,
        [
            "local",
            "snapshot",
            "create",
            "--from",
            str(discovery),
            "--target",
            "openclaw",
            "--copy-root",
            f"{workspace}:.openclaw/workspace",
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Created local-agent snapshot" in result.output
    assert "copied roots: 1" in result.output
    assert (out / "snapshot" / ".openclaw" / "workspace" / "AGENTS.md").exists()
    manifest = json.loads((out / "snapshot_manifest.json").read_text(encoding="utf-8"))
    assert manifest["target"] == "openclaw"
    assert manifest["copied_roots"][0]["dest"] == ".openclaw/workspace"


def test_local_snapshot_create_rejects_unsafe_copy_root_dest(tmp_path: Path) -> None:
    discovery = tmp_path / "discovery.json"
    _write_discovery(discovery)
    source = tmp_path / "source"
    source.mkdir()

    result = CliRunner().invoke(
        cli,
        [
            "local",
            "snapshot",
            "create",
            "--from",
            str(discovery),
            "--target",
            "hermes",
            "--copy-root",
            f"{source}:../escape",
            "--output-dir",
            str(tmp_path / "snapshot"),
        ],
    )

    assert result.exit_code != 0
    assert "copy-root destination must be relative and stay inside the snapshot" in result.output
