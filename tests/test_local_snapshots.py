# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from assert_ai.cli import cli
from assert_ai.local_snapshots import create_local_agent_snapshot, create_snapshot_from_config


def _write_discovery(path: Path, *, target: str = "hermes", agent: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    agent_payload = agent or {
        "id": target,
        "display_name": target.title(),
        "kind": target,
        "status": "found",
        "summary": "found local config or executable",
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "agents": [agent_payload],
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
    (source / "dist" / "credential-planner-runtime.js").write_text("export const name = 'credential-planner';\n", encoding="utf-8")
    (source / "dist" / "token-counter-runtime.js").write_text("export const name = 'token-counter';\n", encoding="utf-8")
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
    assert (snapshot_root / "runtime" / "dist" / "credential-planner-runtime.js").exists()
    assert (snapshot_root / "runtime" / "dist" / "token-counter-runtime.js").exists()
    assert not (snapshot_root / "runtime" / "client_secret_123.apps.googleusercontent.com.json").exists()
    assert not (snapshot_root / "runtime" / ".env").exists()


def test_create_local_agent_snapshot_uses_openclaw_default_roots_from_discovery(tmp_path: Path) -> None:
    workspace = tmp_path / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    runtime = tmp_path / ".npm-global" / "lib" / "node_modules" / "openclaw"
    runtime.mkdir(parents=True)
    (runtime / "package.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
    (runtime / "openclaw.mjs").write_text("console.log('openclaw');\n", encoding="utf-8")
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="openclaw",
        agent={
            "id": "openclaw",
            "display_name": "OpenClaw",
            "kind": "openclaw",
            "status": "ready",
            "summary": "ready for snapshot",
            "runtime": {"path": str(runtime), "valid": True},
            "workspace": {"path": str(workspace), "exists": True},
            "snapshot_defaults": [
                {"source": "workspace.path", "dest": ".openclaw/workspace", "home_fallback": ".openclaw/workspace"},
                {
                    "source": "runtime.path",
                    "dest": "runtime/openclaw-package",
                    "home_fallback": ".npm-global/lib/node_modules/openclaw",
                },
            ],
        },
    )

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="openclaw",
        copy_root_specs=[],
        include_root_specs=[],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    snapshot_root = result.snapshot_root
    assert (snapshot_root / ".openclaw" / "workspace" / "AGENTS.md").exists()
    assert (snapshot_root / "runtime" / "openclaw-package" / "openclaw.mjs").exists()
    assert [root["dest"] for root in result.manifest["copied_roots"]] == [
        ".openclaw/workspace",
        "runtime/openclaw-package",
    ]
    assert result.manifest["safety"]["default_runtime_roots"] is True


def test_create_local_agent_snapshot_uses_openclaw_home_defaults_when_discovery_paths_are_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    runtime = tmp_path / ".npm-global" / "lib" / "node_modules" / "openclaw"
    runtime.mkdir(parents=True)
    (runtime / "package.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
    (runtime / "openclaw.mjs").write_text("console.log('openclaw');\n", encoding="utf-8")
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="openclaw",
        agent={
            "id": "openclaw",
            "display_name": "OpenClaw",
            "kind": "openclaw",
            "status": "ready",
            "summary": "ready for snapshot",
            "runtime": {"path": "[LOCAL_PATH]", "valid": True},
            "workspace": {"path": "[LOCAL_PATH]", "exists": True},
            "snapshot_defaults": [
                {"source": "workspace.path", "dest": ".openclaw/workspace", "home_fallback": ".openclaw/workspace"},
                {
                    "source": "runtime.path",
                    "dest": "runtime/openclaw-package",
                    "home_fallback": ".npm-global/lib/node_modules/openclaw",
                },
            ],
        },
    )

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="openclaw",
        copy_root_specs=[],
        include_root_specs=[],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    assert (result.snapshot_root / ".openclaw" / "workspace" / "AGENTS.md").exists()
    assert (result.snapshot_root / "runtime" / "openclaw-package" / "openclaw.mjs").exists()


def test_create_local_agent_snapshot_uses_detected_config_root_by_default(tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    (memories / "MEMORY.md").write_text("Hermes memory pointer\n", encoding="utf-8")
    (hermes_home / ".env").write_text("API_SERVER_KEY=secret\n", encoding="utf-8")
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="hermes",
        agent={
            "id": "hermes",
            "display_name": "Hermes",
            "kind": "hermes",
            "status": "found",
            "summary": "found local config or executable",
            "config": {"path": str(hermes_home), "exists": True},
            "runtime": {"binary": "hermes", "valid": True},
        },
    )

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="hermes",
        copy_root_specs=[],
        include_root_specs=[],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    assert (result.snapshot_root / ".hermes" / "memories" / "MEMORY.md").exists()
    assert not (result.snapshot_root / ".hermes" / ".env").exists()
    assert [root["dest"] for root in result.manifest["copied_roots"]] == [".hermes"]
    assert result.manifest["safety"]["default_runtime_roots"] is True


def test_create_local_agent_snapshot_uses_known_config_root_when_discovery_path_is_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    (memories / "MEMORY.md").write_text("Hermes memory pointer\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="hermes",
        agent={
            "id": "hermes",
            "display_name": "Hermes",
            "kind": "hermes",
            "status": "ready",
            "summary": "ready for generic snapshot",
            "config": {"path": "[LOCAL_PATH]", "exists": True},
            "runtime": {"binary": "hermes", "valid": True},
        },
    )

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="hermes",
        copy_root_specs=[],
        include_root_specs=[],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    assert (result.snapshot_root / ".hermes" / "memories" / "MEMORY.md").exists()
    assert result.manifest["copied_roots"][0]["dest"] == ".hermes"


def test_cli_local_snapshot_create_defaults_output_dir_and_detected_config_root(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    (memories / "MEMORY.md").write_text("Hermes memory pointer\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="hermes",
        agent={
            "id": "hermes",
            "display_name": "Hermes",
            "kind": "hermes",
            "status": "ready",
            "summary": "ready for generic snapshot",
            "config": {"path": "[LOCAL_PATH]", "exists": True},
            "runtime": {"binary": "hermes", "valid": True},
        },
    )

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
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Created local-agent snapshot" in result.output
    assert "copied roots: 1" in result.output
    assert "elapsed:" in result.output
    assert "artifacts/local-agents/snapshots/hermes-" in result.output
    manifest_line = next(line for line in result.output.splitlines() if line.strip().startswith("manifest:"))
    manifest_path = Path(manifest_line.split("manifest:", 1)[1].strip())
    assert manifest_path.exists()
    assert (manifest_path.parent / "snapshot" / ".hermes" / "memories" / "MEMORY.md").exists()


def test_create_local_agent_snapshot_consumes_declared_snapshot_defaults_generically(tmp_path: Path) -> None:
    # A non-OpenClaw target with a two-root layout declared in the discovery
    # payload must be served by the GENERIC snapshot path, with no target-id
    # special-casing in the snapshot code.
    workspace = tmp_path / ".widget" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("widget instructions\n", encoding="utf-8")
    runtime = tmp_path / "opt" / "widget-runtime"
    runtime.mkdir(parents=True)
    (runtime / "main.js").write_text("console.log('widget');\n", encoding="utf-8")
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="widget",
        agent={
            "id": "widget",
            "display_name": "Widget",
            "kind": "widget",
            "status": "ready",
            "summary": "ready for snapshot",
            "workspace": {"path": str(workspace), "exists": True},
            "runtime": {"path": str(runtime), "valid": True},
            "snapshot_defaults": [
                {"source": "workspace.path", "dest": ".widget/workspace"},
                {"source": "runtime.path", "dest": "runtime/widget-package"},
            ],
        },
    )

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="widget",
        copy_root_specs=[],
        include_root_specs=[],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    assert (result.snapshot_root / ".widget" / "workspace" / "AGENTS.md").exists()
    assert (result.snapshot_root / "runtime" / "widget-package" / "main.js").exists()
    assert [root["dest"] for root in result.manifest["copied_roots"]] == [
        ".widget/workspace",
        "runtime/widget-package",
    ]
    assert result.manifest["safety"]["default_runtime_roots"] is True


def test_create_local_agent_snapshot_resolves_snapshot_default_home_fallback_when_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    # When discovery redacts source paths, declared home_fallback values resolve
    # the roots against the current home. Still generic: no target-id branch.
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / ".widget" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("widget instructions\n", encoding="utf-8")
    runtime = tmp_path / "opt" / "widget-runtime"
    runtime.mkdir(parents=True)
    (runtime / "main.js").write_text("console.log('widget');\n", encoding="utf-8")
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="widget",
        agent={
            "id": "widget",
            "display_name": "Widget",
            "kind": "widget",
            "status": "ready",
            "summary": "ready for snapshot",
            "workspace": {"path": "[LOCAL_PATH]", "exists": True},
            "runtime": {"path": "[LOCAL_PATH]", "valid": True},
            "snapshot_defaults": [
                {"source": "workspace.path", "dest": ".widget/workspace", "home_fallback": ".widget/workspace"},
                {"source": "runtime.path", "dest": "runtime/widget-package", "home_fallback": "opt/widget-runtime"},
            ],
        },
    )

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="widget",
        copy_root_specs=[],
        include_root_specs=[],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    assert (result.snapshot_root / ".widget" / "workspace" / "AGENTS.md").exists()
    assert (result.snapshot_root / "runtime" / "widget-package" / "main.js").exists()


def test_create_local_agent_snapshot_assigns_dest_for_include_roots(tmp_path: Path) -> None:
    discovery = tmp_path / "discovery.json"
    _write_discovery(discovery)
    chatworkspace = tmp_path / "chatworkspace"
    (chatworkspace / "notes").mkdir(parents=True)
    (chatworkspace / "notes" / "context.md").write_text("context\n", encoding="utf-8")

    result = create_local_agent_snapshot(
        discovery_path=discovery,
        target="hermes",
        copy_root_specs=[],
        include_root_specs=[str(chatworkspace)],
        output_dir=tmp_path / "snapshot-out",
        redact_paths=True,
    )

    assert (result.snapshot_root / "chatworkspace" / "notes" / "context.md").exists()
    assert result.manifest["copied_roots"][0]["dest"] == "chatworkspace"


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
    assert "elapsed:" in result.output
    assert (out / "snapshot" / ".openclaw" / "workspace" / "AGENTS.md").exists()
    manifest = json.loads((out / "snapshot_manifest.json").read_text(encoding="utf-8"))
    assert manifest["target"] == "openclaw"
    assert manifest["copied_roots"][0]["dest"] == ".openclaw/workspace"


def test_cli_local_snapshot_create_uses_openclaw_defaults_and_include_root(tmp_path: Path) -> None:
    workspace = tmp_path / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    runtime = tmp_path / ".npm-global" / "lib" / "node_modules" / "openclaw"
    runtime.mkdir(parents=True)
    (runtime / "package.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
    (runtime / "openclaw.mjs").write_text("console.log('openclaw');\n", encoding="utf-8")
    chatworkspace = tmp_path / "chatworkspace"
    chatworkspace.mkdir()
    (chatworkspace / "context.md").write_text("context\n", encoding="utf-8")
    discovery = tmp_path / "discovery.json"
    _write_discovery(
        discovery,
        target="openclaw",
        agent={
            "id": "openclaw",
            "display_name": "OpenClaw",
            "kind": "openclaw",
            "status": "ready",
            "summary": "ready for snapshot",
            "runtime": {"path": str(runtime), "valid": True},
            "workspace": {"path": str(workspace), "exists": True},
            "snapshot_defaults": [
                {"source": "workspace.path", "dest": ".openclaw/workspace", "home_fallback": ".openclaw/workspace"},
                {
                    "source": "runtime.path",
                    "dest": "runtime/openclaw-package",
                    "home_fallback": ".npm-global/lib/node_modules/openclaw",
                },
            ],
        },
    )
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
            "--include-root",
            str(chatworkspace),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "copied roots: 3" in result.output
    assert "elapsed:" in result.output
    assert (out / "snapshot" / ".openclaw" / "workspace" / "AGENTS.md").exists()
    assert (out / "snapshot" / "runtime" / "openclaw-package" / "openclaw.mjs").exists()
    assert (out / "snapshot" / "chatworkspace" / "context.md").exists()


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


def test_cli_local_snapshot_create_from_agent_config(tmp_path: Path) -> None:
    workspace = tmp_path / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    (tmp_path / ".openclaw" / ".env").write_text("SECRET=x\n", encoding="utf-8")
    config = tmp_path / "agent.yaml"
    config.write_text(
        f"""
id: openclaw
display_name: OpenClaw
roots:
  - source: {tmp_path / ".openclaw"}
    dest: .openclaw
    required: true
instruction_files:
  - {workspace / "AGENTS.md"}
""",
        encoding="utf-8",
    )
    out = tmp_path / "snapshot"

    result = CliRunner().invoke(
        cli,
        ["local", "snapshot", "create", "--config", str(config), "--output-dir", str(out)],
    )

    assert result.exit_code == 0, result.output
    assert "Created local-agent snapshot" in result.output
    assert "elapsed:" in result.output
    assert (out / "snapshot" / ".openclaw" / "workspace" / "AGENTS.md").exists()
    # built-in secret floor still applies even on the config path
    assert not (out / "snapshot" / ".openclaw" / ".env").exists()
    manifest = json.loads((out / "snapshot_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "agent_config"
    assert manifest["target"] == "openclaw"


def test_cli_local_snapshot_create_requires_config_or_discovery(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        ["local", "snapshot", "create", "--output-dir", str(tmp_path / "out")],
    )

    assert result.exit_code != 0
    assert "config" in result.output.lower() or "from" in result.output.lower()


def test_config_snapshot_honors_absolute_path_excludes(tmp_path: Path) -> None:
    # Decision #2: agents naturally emit REAL (absolute) paths in their exclude list.
    # An absolute exclude that points under a root must drop that file, even though the
    # copy loop matches against root-relative paths internally.
    from assert_ai.local_agent_config import load_agent_config

    home = tmp_path / ".openclaw"
    (home / "workspace").mkdir(parents=True)
    (home / "workspace" / "AGENTS.md").write_text("instructions\n", encoding="utf-8")
    (home / "workspace" / "private-notes.md").write_text("do not copy\n", encoding="utf-8")
    cfg = tmp_path / "agent.yaml"
    cfg.write_text(
        f"""
id: openclaw
roots:
  - source: {home}
    dest: .openclaw
exclude:
  - {home / "workspace" / "private-notes.md"}
""",
        encoding="utf-8",
    )

    config = load_agent_config(cfg)
    out = tmp_path / "out"
    create_snapshot_from_config(config=config, output_dir=out, redact_paths=True)

    snap = out / "snapshot"
    assert (snap / ".openclaw" / "workspace" / "AGENTS.md").exists()
    # The absolute-path exclude must have dropped this file.
    assert not (snap / ".openclaw" / "workspace" / "private-notes.md").exists()


def test_config_snapshot_copies_external_dependencies(tmp_path: Path) -> None:
    # Decision #3: external_dependencies is its own schema section and must be copied
    # into the snapshot (the agent needs it to run), not silently ignored.
    from assert_ai.local_agent_config import load_agent_config

    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    (home / "MEMORY.md").write_text("pointer map\n", encoding="utf-8")
    dep = tmp_path / "ChatWorkspace"
    (dep / "project").mkdir(parents=True)
    (dep / "project" / "context.md").write_text("external context\n", encoding="utf-8")
    cfg = tmp_path / "agent.yaml"
    cfg.write_text(
        f"""
id: hermes
roots:
  - source: {home}
    dest: .hermes
external_dependencies:
  - source: {dep}
""",
        encoding="utf-8",
    )

    config = load_agent_config(cfg)
    out = tmp_path / "out"
    result = create_snapshot_from_config(config=config, output_dir=out, redact_paths=True)

    snap = out / "snapshot"
    assert (snap / ".hermes" / "MEMORY.md").exists()
    # The external dependency must be present in the snapshot.
    assert (snap / "ChatWorkspace" / "project" / "context.md").read_text(encoding="utf-8") == "external context\n"
    # And it should be recorded in the manifest as a copied root.
    dests = {item["dest"] for item in result.manifest["copied_roots"]}
    assert "ChatWorkspace" in dests


def test_config_snapshot_copies_runtime_dependencies_blindly(tmp_path: Path) -> None:
    # Decision #1: copy blindly. A runtime's own dependency dirs (node_modules, venv)
    # are exactly what it needs to run, so the snapshot floor must NOT strip them.
    # Only secrets and pure churn (sessions/logs/.git) are floored.
    from assert_ai.local_agent_config import load_agent_config

    pkg = tmp_path / ".npm-global" / "lib" / "node_modules" / "openclaw"
    (pkg / "node_modules" / "left-pad").mkdir(parents=True)
    (pkg / "node_modules" / "left-pad" / "index.js").write_text("module.exports=1\n", encoding="utf-8")
    # A NESTED node_modules — this is what the old floor's "**/node_modules/**" would strip.
    (pkg / "lib" / "node_modules" / "dep").mkdir(parents=True)
    (pkg / "lib" / "node_modules" / "dep" / "core.js").write_text("nested dep\n", encoding="utf-8")
    (pkg / "venv" / "lib").mkdir(parents=True)
    (pkg / "venv" / "lib" / "runtime.py").write_text("py dep\n", encoding="utf-8")
    (pkg / "package.json").write_text("{}\n", encoding="utf-8")
    (pkg / ".env").write_text("TOKEN=x\n", encoding="utf-8")
    (pkg / "sessions").mkdir()
    (pkg / "sessions" / "old.log").write_text("churn\n", encoding="utf-8")
    cfg = tmp_path / "agent.yaml"
    cfg.write_text(
        f"""
id: openclaw
roots:
  - source: {pkg}
    dest: runtime/openclaw-package
""",
        encoding="utf-8",
    )

    config = load_agent_config(cfg)
    out = tmp_path / "out"
    create_snapshot_from_config(config=config, output_dir=out, redact_paths=True)

    snap = out / "snapshot" / "runtime" / "openclaw-package"
    # node_modules (top-level AND nested) and venv are runtime dependencies -> copied blindly.
    assert (snap / "node_modules" / "left-pad" / "index.js").exists()
    assert (snap / "lib" / "node_modules" / "dep" / "core.js").exists()
    assert (snap / "venv" / "lib" / "runtime.py").exists()
    assert (snap / "package.json").exists()
    # secrets and pure churn are still floored.
    assert not (snap / ".env").exists()
    assert not (snap / "sessions" / "old.log").exists()


def test_config_snapshot_dereferences_file_symlinks(tmp_path: Path) -> None:
    # Wall #4: a runtime's interpreter is often a SYMLINK (e.g. Hermes's
    # venv/bin/python -> uv-managed cpython). The copier must dereference file
    # symlinks so the cloned runtime has a working interpreter, not a dangling
    # link. The link target may live OUTSIDE the copied root.
    from assert_ai.local_agent_config import load_agent_config

    # Interpreter target lives outside the home root entirely.
    interp = tmp_path / "uv" / "cpython-3.11" / "bin"
    interp.mkdir(parents=True)
    (interp / "python3.11").write_text("#!/real/python\nprint('hi')\n", encoding="utf-8")

    home = tmp_path / ".hermes"
    venv_bin = home / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    # The three symlinks Hermes actually has.
    (venv_bin / "python").symlink_to(interp / "python3.11")
    (venv_bin / "python3").symlink_to(interp / "python3.11")
    (venv_bin / "activate").write_text("# activate\n", encoding="utf-8")
    cfg = tmp_path / "agent.yaml"
    cfg.write_text(
        f"""
id: hermes
roots:
  - source: {home}
    dest: .hermes
""",
        encoding="utf-8",
    )

    config = load_agent_config(cfg)
    out = tmp_path / "out"
    create_snapshot_from_config(config=config, output_dir=out, redact_paths=True)

    snap_bin = out / "snapshot" / ".hermes" / "venv" / "bin"
    py = snap_bin / "python"
    # The interpreter must exist as a real file with the target's content.
    assert py.exists(), "venv/bin/python must survive the copy"
    assert not py.is_symlink(), "symlink must be dereferenced to a real file"
    assert py.read_text(encoding="utf-8") == "#!/real/python\nprint('hi')\n"
    assert (snap_bin / "python3").exists()
    assert (snap_bin / "activate").exists()


def test_config_snapshot_skips_directory_symlinks(tmp_path: Path) -> None:
    # Directory symlinks are NOT dereferenced — that risks loops and silently
    # copying huge external trees. They are recorded as skipped, not copied.
    from assert_ai.local_agent_config import load_agent_config

    external = tmp_path / "external-tree"
    (external / "deep").mkdir(parents=True)
    (external / "deep" / "big.bin").write_text("huge\n", encoding="utf-8")

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "real.md").write_text("real\n", encoding="utf-8")
    (home / "linked-dir").symlink_to(external, target_is_directory=True)
    cfg = tmp_path / "agent.yaml"
    cfg.write_text(
        f"""
id: hermes
roots:
  - source: {home}
    dest: .hermes
""",
        encoding="utf-8",
    )

    config = load_agent_config(cfg)
    out = tmp_path / "out"
    create_snapshot_from_config(config=config, output_dir=out, redact_paths=True)

    snap = out / "snapshot" / ".hermes"
    assert (snap / "real.md").exists()
    # Directory symlink content must NOT be pulled in.
    assert not (snap / "linked-dir" / "deep" / "big.bin").exists()

