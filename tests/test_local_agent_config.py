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
    EndpointSpec,
    LaunchSpec,
    ModelRoutingSpec,
    SmokeProbeSpec,
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


def test_load_agent_config_parses_external_dependencies_as_roots(tmp_path: Path) -> None:
    # External deps live in their own schema section, but they are copy roots too:
    # the agent depends on them to run, so they must end up in the snapshot.
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: hermes
roots:
  - source: ~/.hermes
    dest: .hermes
external_dependencies:
  - source: ~/ChatWorkspace
  - source: ~/LocalOps/hermes
    dest: LocalOps/hermes
""",
    )

    config = load_agent_config(cfg)

    assert len(config.external_dependencies) == 2
    first = config.external_dependencies[0]
    assert isinstance(first, ConfigRoot)
    assert first.source == Path("~/ChatWorkspace").expanduser()
    assert first.dest is None  # derived later
    assert first.kind == "external_dependency"
    second = config.external_dependencies[1]
    assert second.source == Path("~/LocalOps/hermes").expanduser()
    assert second.dest == "LocalOps/hermes"


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


def test_load_agent_config_parses_launch_endpoint_routing_and_smoke(tmp_path: Path) -> None:
    # The self-introspection step emits launch/endpoint/model_routing/smoke_probe.
    # These must be parsed so a generic run can be driven entirely from this one file.
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: hermes-default-wsl
roots:
  - source: ~/.hermes
launch:
  command:
    - /home/jakepresent/.hermes/hermes-agent/venv/bin/python
    - -m
    - hermes_cli.main
    - gateway
    - run
    - --replace
endpoint:
  url: http://127.0.0.1:8642/v1/chat/completions
  protocol: openai_chat
  model: hermes-agent
model_routing:
  config_file: /home/jakepresent/.hermes/config.yaml
  provider_key: model.provider
  model_key: model.default
  api_mode_key: model.api_mode
  credential_file: /home/jakepresent/.hermes/auth.json
  resolved_provider: copilot
  resolved_base_url: https://api.githubcopilot.com
  resolved_api_mode: codex_responses
smoke_probe:
  prompt: "What does your configured memory say Jake prefers?"
""",
    )

    config = load_agent_config(cfg)

    assert isinstance(config.launch, LaunchSpec)
    assert config.launch.command == (
        "/home/jakepresent/.hermes/hermes-agent/venv/bin/python",
        "-m",
        "hermes_cli.main",
        "gateway",
        "run",
        "--replace",
    )

    assert isinstance(config.endpoint, EndpointSpec)
    assert config.endpoint.url == "http://127.0.0.1:8642/v1/chat/completions"
    assert config.endpoint.protocol == "openai_chat"
    assert config.endpoint.model == "hermes-agent"
    assert config.endpoint.port == 8642  # parsed from the url

    assert isinstance(config.model_routing, ModelRoutingSpec)
    assert config.model_routing.config_file == Path("/home/jakepresent/.hermes/config.yaml")
    assert config.model_routing.provider_key == "model.provider"
    assert config.model_routing.resolved_provider == "copilot"
    assert config.model_routing.resolved_base_url == "https://api.githubcopilot.com"

    assert isinstance(config.smoke_probe, SmokeProbeSpec)
    assert config.smoke_probe.prompt.startswith("What does your configured memory")


def test_load_agent_config_optional_sections_default_to_none(tmp_path: Path) -> None:
    # A minimal config (no launch/endpoint/routing/smoke) is still valid; those
    # sections simply default to None so snapshot-only flows keep working.
    cfg = _write(
        tmp_path / "agent.yaml",
        """
id: codex
roots:
  - source: ~/.codex
""",
    )

    config = load_agent_config(cfg)

    assert config.launch is None
    assert config.endpoint is None
    assert config.model_routing is None
    assert config.smoke_probe is None

