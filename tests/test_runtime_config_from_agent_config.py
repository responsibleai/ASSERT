# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the bridge: derive a runtime launch-config from an agent-config.

The self-introspection step emits a single agent-config (launch/endpoint/
model_routing/...). This bridge turns that one file into the RuntimeLaunchConfig
the generic Docker backend already consumes -- so a generic run needs nothing
hand-authored beyond what the agent reported about itself.
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
)
from assert_ai.local_sandbox import (
    RuntimeLaunchConfig,
    build_runtime_config_from_agent_config,
)


def _hermes_like() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        id="hermes-default-wsl",
        display_name="Hermes",
        roots=[ConfigRoot(source=Path("/home/u/.hermes"), dest=None)],
        launch=LaunchSpec(
            command=(
                "/home/u/.hermes/hermes-agent/venv/bin/python",
                "-m",
                "hermes_cli.main",
                "gateway",
                "run",
                "--replace",
            )
        ),
        endpoint=EndpointSpec(
            url="http://127.0.0.1:8642/v1/chat/completions",
            protocol="openai_chat",
            model="hermes-agent",
        ),
        model_routing=ModelRoutingSpec(resolved_provider="copilot"),
    )


def test_bridge_maps_core_fields() -> None:
    runtime_config = build_runtime_config_from_agent_config(_hermes_like())

    assert isinstance(runtime_config, RuntimeLaunchConfig)
    assert runtime_config.id == "hermes-default-wsl"
    assert runtime_config.harness == "rampart-docker"
    # launch command carried straight through from the agent's self-report
    assert runtime_config.launch_command == (
        "/home/u/.hermes/hermes-agent/venv/bin/python",
        "-m",
        "hermes_cli.main",
        "gateway",
        "run",
        "--replace",
    )
    # endpoint port parsed from the agent's endpoint url
    assert runtime_config.endpoint_port == 8642
    # provider route comes from model_routing.resolved_provider
    assert runtime_config.provider_route == "copilot"


def test_bridge_defaults_rampart_root_to_known_location() -> None:
    runtime_config = build_runtime_config_from_agent_config(_hermes_like())
    # rampart_root must resolve so the auth-proxy step can find its scripts.
    assert runtime_config.rampart_root is not None


def test_bridge_requires_launch_command() -> None:
    cfg = AgentRuntimeConfig(id="bare", roots=[ConfigRoot(source=Path("/x"))])
    with pytest.raises(ValueError, match="launch"):
        build_runtime_config_from_agent_config(cfg)
