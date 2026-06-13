# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for creating a snapshot from an agent runtime-config.

The config declares real-machine roots; the snapshot copies them to sandbox
destinations, applying the built-in secret floor plus config-declared excludes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assert_ai.local_agent_config import AgentRuntimeConfig, ConfigRoot
from assert_ai.local_snapshots import create_snapshot_from_config


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    ws = home / ".agent" / "workspace"
    ws.mkdir(parents=True)
    (ws / "AGENTS.md").write_text("be helpful", encoding="utf-8")
    (ws / "MEMORY.md").write_text("remember", encoding="utf-8")
    (home / ".agent" / ".env").write_text("SECRET=x", encoding="utf-8")
    (home / ".agent" / "auth.json").write_text('{"token":"x"}', encoding="utf-8")
    sessions = home / ".agent" / "sessions"
    sessions.mkdir()
    (sessions / "s1.jsonl").write_text("noise", encoding="utf-8")
    return home


def test_create_snapshot_from_config_copies_declared_roots(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    config = AgentRuntimeConfig(
        id="agent",
        roots=[ConfigRoot(source=home / ".agent", dest=".agent", required=True)],
    )

    result = create_snapshot_from_config(config=config, output_dir=tmp_path / "out")

    copied = result.snapshot_root / ".agent" / "workspace" / "AGENTS.md"
    assert copied.exists()
    assert copied.read_text(encoding="utf-8") == "be helpful"


def test_create_snapshot_from_config_applies_builtin_secret_floor(tmp_path: Path) -> None:
    # Even with no config-declared exclude, .env and auth.json must not be copied.
    home = _make_home(tmp_path)
    config = AgentRuntimeConfig(
        id="agent",
        roots=[ConfigRoot(source=home / ".agent", dest=".agent")],
        exclude=["auth.json"],  # the Hermes gap, now declarable
    )

    result = create_snapshot_from_config(config=config, output_dir=tmp_path / "out")

    assert not (result.snapshot_root / ".agent" / ".env").exists()
    assert not (result.snapshot_root / ".agent" / "auth.json").exists()
    excluded_dests = {item["dest"] for item in result.manifest["excluded_files"]}
    assert ".agent/.env" in excluded_dests
    assert ".agent/auth.json" in excluded_dests


def test_create_snapshot_from_config_applies_per_root_exclude(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    config = AgentRuntimeConfig(
        id="agent",
        roots=[ConfigRoot(source=home / ".agent", dest=".agent", exclude=["sessions/**"])],
    )

    result = create_snapshot_from_config(config=config, output_dir=tmp_path / "out")

    assert not (result.snapshot_root / ".agent" / "sessions" / "s1.jsonl").exists()
    # but the real instruction files survived
    assert (result.snapshot_root / ".agent" / "workspace" / "AGENTS.md").exists()


def test_create_snapshot_from_config_derives_dest_when_omitted(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    config = AgentRuntimeConfig(
        id="agent",
        roots=[ConfigRoot(source=home / ".agent")],  # no dest -> ASSERT derives one
    )

    result = create_snapshot_from_config(config=config, output_dir=tmp_path / "out")

    # derived from the source dir name (hidden -> kept verbatim)
    assert (result.snapshot_root / ".agent" / "workspace" / "AGENTS.md").exists()


def test_create_snapshot_from_config_writes_consumable_manifest(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    config = AgentRuntimeConfig(
        id="agent",
        display_name="Agent",
        roots=[ConfigRoot(source=home / ".agent", dest=".agent")],
        instruction_files=["~/.agent/workspace/AGENTS.md"],
    )

    result = create_snapshot_from_config(config=config, output_dir=tmp_path / "out", redact_paths=True)

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["target"] == "agent"
    assert manifest["snapshot_root"] == "snapshot"
    assert manifest["source"] == "agent_config"
    assert manifest["copied_roots"][0]["dest"] == ".agent"
    # instruction files recorded for spec build to translate later
    assert manifest["instruction_files"] == ["~/.agent/workspace/AGENTS.md"]
    # redacted: no real home path leaks into the manifest
    assert "/home" not in result.manifest_path.read_text(encoding="utf-8") or not manifest["copied_roots"][0]["source"].startswith("/")


def test_create_snapshot_from_config_requires_at_least_one_root(tmp_path: Path) -> None:
    config = AgentRuntimeConfig(id="agent", roots=[])

    with pytest.raises(ValueError, match="root"):
        create_snapshot_from_config(config=config, output_dir=tmp_path / "out")


def test_create_snapshot_from_config_required_root_missing_raises(tmp_path: Path) -> None:
    config = AgentRuntimeConfig(
        id="agent",
        roots=[ConfigRoot(source=tmp_path / "does-not-exist", dest="x", required=True)],
    )

    with pytest.raises(ValueError, match="required"):
        create_snapshot_from_config(config=config, output_dir=tmp_path / "out")
