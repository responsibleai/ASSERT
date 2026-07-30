# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Hostile-input regression tests for the endpoint and mediation paths.

Each test here corresponds to a defect found by probing the happy path with
malformed or adversarial input. They exist so the same class of bug cannot
return silently.
"""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import pytest

from assert_ai.core.model_client import Message
from assert_ai.core.session import HTTPEndpointSession
from assert_ai.integrations.sandbox import validate_setup
from assert_ai.integrations.sandbox.agent_hooks_context import AgentHooksContextBuilder
from assert_ai.integrations.sandbox.mediator import ActionMediator
from assert_ai.integrations.sandbox.mocks import MockLibrary
from assert_ai.integrations.sandbox.policy import MediationPolicy

SECRET = "api_key=SUPERSECRETVALUE0123456789abcdef"


class _Response:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self._payload = payload

    def post(self, *args, **kwargs):
        return _Response(self._payload)


def _turn(payload):
    session = HTTPEndpointSession(endpoint="http://localhost:8080/chat")
    setattr(session, "_aiohttp", aiohttp)
    setattr(session, "_session", _Client(payload))
    return asyncio.run(session.run_turn([Message(role="user", content="hi")]))


# --- endpoint hostile input ---------------------------------------------------


def test_credentials_in_endpoint_events_are_redacted():
    """Endpoint events are agent-influenced and land in run artifacts.

    Sanitizing only the top-level response text left tool arguments and tool
    results as an unredacted path for the same credential patterns.
    """
    result = _turn({
        "response": "done",
        "events": [
            {
                "role": "tool_result",
                "tool_name": "fetch",
                "tool_args": {"auth": SECRET},
                "tool_call_id": "tc1",
                "content": f"upstream said {SECRET}",
            },
            {"role": "assistant", "content": f"leaked: {SECRET}"},
        ],
    })
    persisted = json.dumps(result.interaction_messages)
    assert "SUPERSECRETVALUE" not in persisted
    assert "[REDACTED]" in persisted


@pytest.mark.parametrize(
    "payload",
    [
        {"events": []},
        {"response": None},
        {"response": "ok", "events": "not-a-list"},
        {"response": "ok", "events": ["x", 5, None]},
        {"response": "ok", "events": [{"role": "unknown", "content": "z"}]},
        {"response": "ok", "events": None},
    ],
)
def test_malformed_endpoint_payloads_do_not_crash(payload):
    result = _turn(payload)
    assert isinstance(result.text, str)
    assert result.interaction_messages[0]["role"] == "user"


def test_non_string_response_is_coerced_and_logged(caplog):
    """A numeric response previously became an empty string, which reads as
    'the agent said nothing' rather than 'the endpoint is misconfigured'."""
    with caplog.at_level(logging.WARNING):
        result = _turn({"response": 42})
    assert result.text == "42"
    assert any("non-string response" in record.message for record in caplog.records)


def test_endpoint_raw_never_carries_the_full_payload():
    result = _turn({"response": "ok", "internal": {"token": SECRET}})
    assert result.raw == {"endpoint": "http://localhost:8080/chat"}
    assert "SUPERSECRETVALUE" not in json.dumps(result.interaction_messages)


# --- mediation config hazards -------------------------------------------------


def _pre(name, args=None):
    return AgentHooksContextBuilder(agent_id="a", framework="f", session_id="s").pre_tool_call(
        call_id="tc-1", name=name, args=args or {}
    )


def test_mock_without_any_payload_warns_instead_of_silently_returning_ok(caplog):
    """`mode: mock` with no payload anywhere used to return a bare
    `{"status": "ok"}`, which looks like a configured mock."""
    policy = MediationPolicy({"interactions": [{"match": "t", "mode": "mock"}]})
    mediator = ActionMediator(policy, mocks=MockLibrary.empty())
    with caplog.at_level(logging.WARNING):
        decision = mediator.mediate(_pre("t"), lambda args: {"real": True})

    assert decision.mode == "mock"
    assert decision.real_executed is False, "containment must still hold"
    assert "No mock payload configured" in json.dumps(decision.returned)
    assert any("no mock payload" in record.message.lower() for record in caplog.records)


def test_validate_does_not_report_working_glob_rules_as_unused(tmp_path):
    """A `send_*` mock rule covering a `send_message` policy rule was reported
    as dead content AND as falling back to inline, which is contradictory."""
    (tmp_path / "policy.yaml").write_text(
        "interactions:\n  - match: send_message\n    mode: mock\ndefault:\n  mode: block\n"
    )
    (tmp_path / "mocks.yaml").write_text(
        "version: 1\nmocks:\n  - tool: send_*\n    response: {ok: 1}\n"
    )
    (tmp_path / "setup.yaml").write_text(
        "version: 1\ntarget:\n  kind: endpoint\n  url: http://x/y\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n"
    )
    summary = validate_setup(tmp_path / "setup.yaml")
    assert summary["unused_mock_rules"] == []
    assert summary["falls_back_to_inline"] == []


def test_validate_still_reports_genuinely_dead_mock_rules(tmp_path):
    """The glob fix must not blind the validator to real typos."""
    (tmp_path / "policy.yaml").write_text(
        "interactions:\n  - match: send_message\n    mode: mock\ndefault:\n  mode: block\n"
    )
    (tmp_path / "mocks.yaml").write_text(
        "version: 1\nmocks:\n  - tool: send_mesage\n    response: {ok: 1}\n"
    )
    (tmp_path / "setup.yaml").write_text(
        "version: 1\ntarget:\n  kind: endpoint\n  url: http://x/y\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n"
    )
    summary = validate_setup(tmp_path / "setup.yaml")
    assert summary["unused_mock_rules"] == ["send_mesage"]
    assert summary["falls_back_to_inline"] == ["send_message"]
