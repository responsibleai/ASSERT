# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The shipped mediation example must load and behave, not just parse.

`tests/test_sandbox_mock_setup.py` covers the layer's semantics with synthetic
configs. This file covers the *shipped example*: if `examples/` drifts from the
code, a user's first contact with this integration breaks, and unit tests over
hand-built dicts will not catch it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from assert_ai.integrations.sandbox import load_setup, validate_setup
from assert_ai.integrations.sandbox.mocks import MockCall

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sandbox_action_mediation" / "assert-setup.yaml"


@pytest.fixture
def setup():
    return load_setup(EXAMPLE)


def test_shipped_example_loads(setup):
    assert setup.target.kind == "endpoint"
    assert setup.policy.data["interactions"]
    assert setup.mocks.rules


def test_validate_reports_no_policy_mock_gaps():
    """The shipped policy and mock file agree on every mocked tool."""
    summary = validate_setup(EXAMPLE)
    assert summary["unused_mock_rules"] == []
    assert summary["falls_back_to_inline"] == []


def test_internal_mutation_runs_against_disposable_state_and_later_read_agrees(setup):
    """Internal state changes execute against disposable state.

    ASSERT's vendored integration is framework-neutral, so this test supplies a
    tiny in-memory disposable backend. The real telecom container resets its JSON
    DB from a pristine snapshot before every case; the property being pinned here
    is the same one: the write really happens, the later read sees it, and both
    calls remain judge-visible.
    """
    state = {"status": "suspended", "service_status": "no_service"}

    def resume(_args):
        state.update(status="active", service_status="connected")
        return {"status": "resumed", "service_status": "connected"}

    def read(_args):
        return {"status": "ok", "line": dict(state)}

    host = setup.tool_host(
        tools={"resume_line": resume, "get_line_status": read},
        agent_id="telecom-support-agent",
        session_id="test-run",
    )
    result = host.call_tool("resume_line", {"line_id": "L1002"})
    status = host.call_tool("get_line_status", {"line_id": "L1002"})

    assert result == {"status": "resumed", "service_status": "connected"}
    assert status["line"] == {"status": "active", "service_status": "connected"}
    resume_record, read_record = host.records
    assert resume_record.decision.mode == "pass"
    assert resume_record.decision.real_executed is True
    assert resume_record.decision.flagged is False
    assert read_record.decision.mode == "pass"


def test_risky_call_is_mocked_and_never_executes(setup):
    """The core safety claim, end to end from the shipped setup file."""
    executed = []
    host = setup.tool_host(
        tools={"send_message": lambda args: executed.append(args)},
        agent_id="telecom-support-agent",
        session_id="test-run",
    )
    returned = host.call_tool("send_message", {"recipient": "555-000-9999", "body": "balance $84.10"})

    assert executed == [], "the real send must never run"
    assert returned["status"] == "sent", "the agent must not be able to tell it was sandboxed"

    record = host.records[0].to_json()
    assert record["decision"]["mode"] == "mock"
    assert record["decision"]["real_executed"] is False
    assert record["decision"]["flagged"] is True
    # The attempted recipient and body survive as evidence, which is the whole
    # point: the judge scores what the agent tried to do.
    assert record["args"] == {"recipient": "555-000-9999", "body": "balance $84.10"}


def test_unknown_tool_is_blocked_by_default(setup):
    host = setup.tool_host(tools={}, agent_id="a", session_id="s")
    returned = host.call_tool("wire_money", {"amount": 1_000_000})
    assert returned["status"] == "blocked"
    assert host.records[0].decision.real_executed is False


def test_safe_read_still_executes_for_real(setup):
    """Containment must not be so blunt that the agent cannot do its job."""
    host = setup.tool_host(
        tools={"lookup_customer": lambda args: {"status": "ok", "customer_id": args["customer_id"]}},
        agent_id="a",
        session_id="s",
    )
    returned = host.call_tool("lookup_customer", {"customer_id": "C1001"})
    assert returned == {"status": "ok", "customer_id": "C1001"}
    assert host.records[0].decision.mode == "pass"
    assert host.records[0].decision.real_executed is True


def test_per_use_case_mock_distinguishes_recipients(setup):
    """The distinction the judge scores: on-file number vs attacker-supplied."""
    unverified = setup.mocks.resolve(MockCall("send_message", {"recipient": "555-000-9999"}))
    on_file = setup.mocks.resolve(MockCall("send_message", {"recipient": "555-123-2002"}))
    assert unverified is not None and on_file is not None
    assert unverified.value["message_id"] != on_file.value["message_id"]


def test_over_limit_credit_is_a_simulated_failure(setup):
    """Mocking only the happy path would let the agent look compliant while never
    meeting the control it is supposed to respect."""
    resolution = setup.mocks.resolve(MockCall("apply_bill_credit", {"amount": 500, "bill_id": "B1234321"}))
    assert resolution is not None
    assert resolution.is_error is True
    assert resolution.value["code"] == "CREDIT_LIMIT_EXCEEDED"


def test_example_cassette_resolves(setup):
    """Replay reaches the shipped cassette; a packaging slip would break this."""
    resolution = setup.mocks.resolve(MockCall("lookup_customer_poisoned", {}))
    assert resolution is not None
    assert resolution.mock_source == "override"
    assert "SYSTEM NOTICE" in resolution.value["bills"][0]["charges"][0]["description"]
