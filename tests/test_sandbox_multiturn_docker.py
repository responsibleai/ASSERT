# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Multi-turn host-mediated containment against a real Docker sandbox.

Every other host-mediation proof exercises a single turn. That leaves the
cross-turn behavior of public action identifiers, ledger sequence numbers, and
reconciliation unproven against a real container. This module drives two turns
through one live sandbox session and asserts that host evidence stays
one-to-one, ordered, and individually addressable across the whole session.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from assert_ai.core.model_client import Message
from assert_ai.integrations.sandbox.runtime import docker_available
from assert_ai.integrations.sandbox.session import SandboxedEndpointSession

ROOT = Path(__file__).resolve().parents[1]
RUN = os.environ.get("ASSERT_RUN_DOCKER_TESTS", "").lower() in {"1", "true", "yes"}
if RUN and not docker_available():
    raise RuntimeError(
        "ASSERT_RUN_DOCKER_TESTS is enabled, but the Docker daemon is unavailable; "
        "required containment tests cannot be skipped"
    )
pytestmark = pytest.mark.skipif(
    not RUN,
    reason="set ASSERT_RUN_DOCKER_TESTS=1 to run real Docker containment tests",
)


@pytest.fixture(scope="module", autouse=True)
def _stock_image():
    """Reuse the prebuilt local image.

    The stock Dockerfile installs from PyPI, which the local Docker builder
    cannot reach (TLS handshake failure). The image under test is built
    separately from host-vendored wheels; CI builds it the normal way.
    """
    present = subprocess.run(
        [
            "docker", "image", "ls", "-q",
            "--filter", "reference=assert-sandbox-stock-agent:local",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not present.stdout.strip():
        subprocess.run(
            [
                "docker", "build",
                "-f", "examples/sandbox_action_mediation/stock_agent/Dockerfile",
                "-t", "assert-sandbox-stock-agent:local",
                ".",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


def _actions(response):
    """Return (public_id, tool) for each mediated action in a turn."""
    pairs = []
    for message in response.interaction_messages:
        for call in message.get("tool_calls") or []:
            pairs.append((call["id"], call["function"]))
    return pairs


def _mediated_tools(response):
    """Tool names for host-mediated actions, excluding egress audit events."""
    return [
        tool
        for _id, tool in _actions(response)
        if tool != "network_egress"
    ]


def test_multi_turn_host_mediation_keeps_actions_addressable_and_ordered():
    async def exercise():
        session = SandboxedEndpointSession(
            setup_path=ROOT / "examples/sandbox_action_mediation/assert-setup-container.yaml"
        )
        await session.open()
        assert session._handle is not None
        handle = session._handle
        container = handle.container
        try:
            first = await session.run_turn([
                Message(role="user", content="First request: look up C1001 and notify")
            ])
            second = await session.run_turn([
                Message(role="user", content="Second request: look up C1001 and notify again")
            ])
            ledger = [
                json.loads(line)
                for line in handle.action_ledger.ledger_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            return first, second, ledger, container
        finally:
            await session.close()

    first, second, ledger, container = asyncio.run(exercise())

    first_actions = _actions(first)
    second_actions = _actions(second)

    # Each turn performs the same two tools, so a per-turn identifier scheme
    # would hand both turns the same public IDs.
    assert _mediated_tools(first) == ["lookup_customer", "send_message"]
    assert _mediated_tools(second) == ["lookup_customer", "send_message"]

    all_ids = [action_id for action_id, _tool in first_actions + second_actions]
    assert len(set(all_ids)) == len(all_ids), (
        f"public action IDs collided across turns: {all_ids}"
    )

    # Host-mediated IDs must come from the host ledger sequence and keep
    # advancing across turns rather than restarting per turn.
    host_ids = [
        action_id
        for action_id, tool in first_actions + second_actions
        if tool != "network_egress"
    ]
    assert host_ids == [
        "host-action-0",
        "host-action-1",
        "host-action-2",
        "host-action-3",
    ]

    # Host sequence numbers are the authoritative chronology.
    sequences = [row["sequence"] for row in ledger if row.get("phase") == "attempt"]
    assert sequences == [0, 1, 2, 3]

    # The irreversible action must stay mocked on every turn, not just the first.
    results = [
        json.loads(message.get("content") or "{}")
        for response in (first, second)
        for message in response.interaction_messages
        if message.get("role") == "tool" and message.get("function") == "send_message"
    ]
    assert len(results) == 2
    for result in results:
        assert result["mode"] == "mock"
        assert result["real_executed"] is False
        assert result["decision_source"] == "host_mediator"
        assert result["result_authoritative"] is True

    # Containment must survive the whole session, not just its first turn.
    remaining = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name={container}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert remaining.stdout.strip() == "", "sandbox container outlived the session"
