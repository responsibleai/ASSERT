# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for parsing the agent runtime-config (what to copy + how it's described).

The config is a declarative YAML an agent emits about itself. Paths are
real-machine / source-relative; ASSERT owns translation into the sandbox.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assert_ai.local_agent_config import (
    AgentRuntimeConfig,
    ConfigRoot,
    load_agent_config,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_agent_config_parses_id_and_roots(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: openclaw
display_name: OpenClaw
roots:
  - source: ~/.openclaw/workspace
    dest: .openclaw/workspace
    kind: workspace
    required: true
  - source: ~/.npm-global/lib/node_modules/openclaw
    dest: runtime/openclaw-package
    kind: runtime
""",
    )

    config = load_agent_config(cfg)

    assert isinstance(config, AgentRuntimeConfig)
    assert config.id == "openclaw"
    assert config.display_name == "OpenClaw"
    assert len(config.roots) == 2
    first = config.roots[0]
    assert isinstance(first, ConfigRoot)
    # ~ is expanded to a real-machine absolute path
    assert first.source == Path("~/.openclaw/workspace").expanduser()
    assert first.dest == ".openclaw/workspace"
    assert first.kind == "workspace"
    assert first.required is True
    assert config.roots[1].required is False  # default


def test_load_agent_config_root_without_dest_is_none(tmp_path: Path) -> None:
    # dest omitted -> ASSERT derives it later. The agent only has to declare source.
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: codex
roots:
  - source: ~/.codex
""",
    )

    config = load_agent_config(cfg)

    assert config.roots[0].dest is None
    assert config.roots[0].source == Path("~/.codex").expanduser()


def test_load_agent_config_parses_global_exclude(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: hermes
roots:
  - source: ~/.hermes
    dest: .hermes
exclude:
  - "auth.json"
  - "auth.json.bak*"
""",
    )

    config = load_agent_config(cfg)

    assert "auth.json" in config.exclude
    assert "auth.json.bak*" in config.exclude


def test_load_agent_config_parses_per_root_excludes(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: hermes
roots:
  - source: ~/.hermes
    dest: .hermes
    exclude:
      - "hermes-agent/venv/**"
      - "sessions/**"
""",
    )

    config = load_agent_config(cfg)

    assert config.roots[0].exclude == ["hermes-agent/venv/**", "sessions/**"]


def test_load_agent_config_requires_id(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "agent.yaml",
        """
display_name: No Id
roots:
  - source: ~/.somewhere
""",
    )

    with pytest.raises(ValueError, match="id"):
        load_agent_config(cfg)


def test_load_agent_config_requires_roots_to_be_a_list(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: broken
roots: "not a list"
""",
    )

    with pytest.raises(ValueError, match="roots"):
        load_agent_config(cfg)


def test_load_agent_config_root_requires_source(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: broken
roots:
  - dest: .somewhere
""",
    )

    with pytest.raises(ValueError, match="source"):
        load_agent_config(cfg)


def test_load_agent_config_rejects_absolute_or_escaping_dest(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: broken
roots:
  - source: ~/.hermes
    dest: ../escape
""",
    )

    with pytest.raises(ValueError, match="dest"):
        load_agent_config(cfg)
