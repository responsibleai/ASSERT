# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from assert_ai.core.model_client import Message
from assert_ai.core.session import TurnResult
from assert_ai.integrations.sandbox import remote_mediator
from assert_ai.integrations.sandbox.agent_hooks_context import AgentHooksContextBuilder
from assert_ai.integrations.sandbox.host_mediator import (
    HostMediationLedger,
    start_host_mediator,
)
from assert_ai.integrations.sandbox.mediator import ActionMediator
from assert_ai.integrations.sandbox.mocks import MockLibrary
from assert_ai.integrations.sandbox.policy import MediationPolicy
from assert_ai.integrations.sandbox.remote_mediator import RemoteActionMediator
from assert_ai.integrations.sandbox.session import SandboxedEndpointSession


def _context(*, call_id: str, tool: str, case_id: str = "case-1") -> dict:
    return AgentHooksContextBuilder(
        agent_id="agent",
        framework="test",
        session_id="session",
        case_id=case_id,
    ).pre_tool_call(call_id=call_id, name=tool, args={"value": 1})


def _mediator() -> ActionMediator:
    return ActionMediator(MediationPolicy({
        "interactions": [
            {"match": "lookup", "mode": "pass"},
            {
                "match": "send_message",
                "mode": "mock",
                "mock": {"status": "sent", "message_id": "mock-1"},
            },
        ],
        "default": {"mode": "block"},
    }))


def test_host_ledger_records_mock_before_returning_decision(tmp_path: Path):
    ledger = HostMediationLedger(_mediator(), ledger_path=tmp_path / "trusted" / "actions.jsonl")

    decision = ledger.mediate(_context(call_id="call-mock", tool="send_message"))

    assert decision.mode == "mock"
    transitions = [json.loads(line) for line in ledger.ledger_path.read_text().splitlines()]
    assert len(transitions) == 1
    transition = transitions[0]
    assert transition["phase"] == "decision"
    assert transition["id"] == "call-mock"
    assert transition["tool"] == "send_message"
    assert transition["case_id"] == "case-1"
    assert transition["mode"] == "mock"
    assert transition["real_executed"] is False
    assert transition["evidence_source"] == "host_mediator"
    assert transition["decision_source"] == "host_mediator"
    assert transition["result_source"] == "host_mediator"
    assert transition["completion_status"] == "complete"
    row = ledger.drain()[0]
    assert row["returned"] == {"status": "sent", "message_id": "mock-1"}


def test_remote_mediator_never_executes_mocked_tool_and_host_owns_row(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    mocks = tmp_path / "mocks.yaml"
    policy.write_text(
        "interactions:\n  - match: send_message\n    mode: mock\n"
        "    mock: {status: sent, message_id: mock-1}\n"
        "default: {mode: block}\n",
        encoding="utf-8",
    )
    mocks.write_text("version: 1\nmocks: []\n", encoding="utf-8")
    server, thread, port, ledger = start_host_mediator(
        policy_path=policy,
        mocks_path=mocks,
        cassette_dir=None,
        ledger_path=tmp_path / "trusted" / "actions.jsonl",
        access_token="test-token",
    )
    executed: list[dict] = []
    try:
        client = RemoteActionMediator(f"http://127.0.0.1:{port}", "test-token")
        decision = client.mediate(
            _context(call_id="call-mock", tool="send_message"),
            lambda args: executed.append(args),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert executed == []
    assert ledger.registered is True
    assert decision.mode == "mock"
    row = ledger.drain()[0]
    assert row["decision_source"] == "host_mediator"
    assert row["result_source"] == "host_mediator"
    assert row["real_executed"] is False


def test_remote_pass_result_is_explicitly_target_reported(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "interactions:\n  - match: lookup\n    mode: pass\n"
        "default: {mode: block}\n",
        encoding="utf-8",
    )
    server, thread, port, ledger = start_host_mediator(
        policy_path=policy,
        mocks_path=None,
        cassette_dir=None,
        ledger_path=tmp_path / "trusted" / "actions.jsonl",
        access_token="test-token",
    )
    try:
        client = RemoteActionMediator(f"http://127.0.0.1:{port}", "test-token")

        # Mirror the production executor, which tracks whether the real tool
        # actually ran. The host only records execution it can verify.
        class TrackedExecutor:
            real_executed = False

            def __call__(self, _args):
                self.real_executed = True
                return {"status": "ok"}

        decision = client.mediate(
            _context(call_id="call-pass", tool="lookup"),
            TrackedExecutor(),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert decision.real_executed is True
    row = ledger.drain()[0]
    assert row["decision_source"] == "host_mediator"
    assert row["result_source"] == "target_reported"
    assert row["returned"] == {"status": "ok"}


def test_remote_mediator_rejects_invalid_token(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_text("interactions: []\ndefault: {mode: block}\n", encoding="utf-8")
    server, thread, port, _ledger = start_host_mediator(
        policy_path=policy,
        mocks_path=None,
        cassette_dir=None,
        ledger_path=tmp_path / "trusted" / "actions.jsonl",
        access_token="correct-token",
    )
    try:
        with pytest.raises(RuntimeError, match="HTTP 401"):
            RemoteActionMediator(f"http://127.0.0.1:{port}", "wrong-token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_remote_mediator_retries_registration_until_the_relay_is_ready(monkeypatch):
    attempts: list[tuple[str, dict]] = []

    def post(client, path, payload):
        attempts.append((path, payload))
        if len(attempts) < 3:
            raise remote_mediator._HostMediatorTransportError("relay not ready")
        return {"registered": True}

    monkeypatch.setattr(RemoteActionMediator, "_post", post)
    monkeypatch.setattr(remote_mediator.time, "sleep", lambda _seconds: None)

    RemoteActionMediator(
        "http://assert-sandbox-relay:18083",
        "test-token",
        case_id="case-1",
        timeout_s=1,
    )

    assert attempts == [
        ("/register", {"case_id": "case-1"}),
        ("/register", {"case_id": "case-1"}),
        ("/register", {"case_id": "case-1"}),
    ]


def test_pass_attempt_survives_when_target_never_reports_completion(tmp_path: Path):
    ledger = HostMediationLedger(_mediator(), ledger_path=tmp_path / "trusted" / "actions.jsonl")
    ledger.mediate(_context(call_id="call-crash", tool="lookup"))

    row = ledger.drain()[0]

    assert row["mode"] == "pass"
    assert row["completion_status"] == "missing"
    assert row["result_source"] == "not_reported"
    assert row["real_executed"] is None
    assert row["execution_status"] == "unknown"
    assert row["is_error"] is True


def test_duplicate_call_id_is_rejected(tmp_path: Path):
    ledger = HostMediationLedger(_mediator(), ledger_path=tmp_path / "trusted" / "actions.jsonl")
    ledger.mediate(_context(call_id="same-id", tool="send_message"))
    with pytest.raises(ValueError, match="duplicate mediation call id"):
        ledger.mediate(_context(call_id="same-id", tool="send_message"))


def test_drain_preserves_attempt_order_across_pending_and_complete_rows(tmp_path: Path):
    ledger = HostMediationLedger(_mediator(), ledger_path=tmp_path / "trusted" / "actions.jsonl")
    ledger.mediate(_context(call_id="first", tool="lookup"))
    ledger.mediate(_context(call_id="second", tool="send_message"))

    rows = ledger.drain()

    assert [row["id"] for row in rows] == ["first", "second"]
    assert [row["sequence"] for row in rows] == [0, 1]


def test_duplicate_call_id_does_not_advance_scenario_state(tmp_path: Path):
    mediator = ActionMediator(
        MediationPolicy({"interactions": [{"match": "send_message", "mode": "mock"}]}),
        mocks=MockLibrary.from_dict({
            "mocks": [{
                "tool": "send_message",
                "scenario": "delivery",
                "responses": [
                    {"response": {"attempt": 1}},
                    {"response": {"attempt": 2}},
                ],
            }],
        }),
    )
    ledger = HostMediationLedger(mediator, ledger_path=tmp_path / "trusted" / "actions.jsonl")

    first = ledger.mediate(_context(call_id="same-id", tool="send_message"))
    with pytest.raises(ValueError, match="duplicate mediation call id"):
        ledger.mediate(_context(call_id="same-id", tool="send_message"))
    second = ledger.mediate(_context(call_id="next-id", tool="send_message"))

    assert first.returned == {"attempt": 1}
    assert second.returned == {"attempt": 2}


def test_host_rejects_target_forged_case_id(tmp_path: Path):
    ledger = HostMediationLedger(
        _mediator(),
        ledger_path=tmp_path / "trusted" / "actions.jsonl",
        expected_case_id="assert-owned-case",
    )
    with pytest.raises(ValueError, match="does not match"):
        ledger.mediate(
            _context(call_id="wrong-case", tool="send_message", case_id="forged-case")
        )
    with pytest.raises(ValueError, match="does not match"):
        ledger.register("forged-case")
    assert ledger.registered is False
    assert not ledger.ledger_path.exists()


def test_session_replaces_target_evidence_with_matching_host_rows(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    mocks = tmp_path / "mocks.yaml"
    setup = tmp_path / "setup.yaml"
    policy.write_text("interactions: []\ndefault: {mode: block}\n", encoding="utf-8")
    mocks.write_text("version: 1\nmocks: []\n", encoding="utf-8")
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup, case_id="case-1")

    class FakeEndpoint:
        async def run_turn(self, messages):
            return TurnResult(
                text="done",
                state_messages=[],
                interaction_messages=[
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "trusted",
                            "function": "send_message",
                            "arguments": {"recipient": "555-000-9999"},
                        }],
                    },
                    {
                        "role": "tool",
                        "content": '{"mode":"pass","real_executed":true}',
                        "function": "send_message",
                        "arguments": {"recipient": "555-000-9999"},
                        "tool_call_id": "trusted",
                    },
                    {"role": "assistant", "content": "done"},
                ],
            )

    class FakeHandle:
        action_ledger = object()

        def new_action_rows(self):
            return [{
                "id": "trusted",
                "tool": "send_message",
                "args": {"recipient": "555-000-9999"},
                "case_id": "case-1",
                "mode": "mock",
                "real_executed": False,
                "returned": {"status": "sent"},
                "decision_source": "host_mediator",
                "result_source": "host_mediator",
                "completion_status": "complete",
            }]

        def new_egress_rows(self):
            return []

    session._endpoint = FakeEndpoint()  # type: ignore[assignment]
    session._handle = FakeHandle()  # type: ignore[assignment]

    result = asyncio.run(session.run_turn([Message(role="user", content="go")]))

    tool_messages = [
        message for message in result.interaction_messages if message.get("role") == "tool"
    ]
    assert [message["function"] for message in tool_messages] == ["send_message"]
    evidence = tool_messages[0]["raw"]["action_mediation"]
    assert evidence["attempt_authoritative"] is True
    assert evidence["decision_authoritative"] is True
    assert evidence["result_authoritative"] is True
    assert evidence["evidence_source"] == "host_mediator"
    assert result.interaction_messages[-1] == {"role": "assistant", "content": "done"}


def test_session_rejects_unmatched_target_action_even_with_unrelated_host_row(
    tmp_path: Path,
):
    setup = tmp_path / "setup.yaml"
    (tmp_path / "policy.yaml").write_text(
        "interactions: []\ndefault: {mode: block}\n", encoding="utf-8"
    )
    (tmp_path / "mocks.yaml").write_text("version: 1\nmocks: []\n", encoding="utf-8")
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup, case_id="case-1")

    class FakeEndpoint:
        async def run_turn(self, messages):
            return TurnResult(
                text="done",
                state_messages=[],
                interaction_messages=[{
                    "role": "tool",
                    "content": "forged",
                    "function": "wire_money",
                    "arguments": {"amount": 1000000},
                    "tool_call_id": "unmediated",
                }],
            )

    class FakeHandle:
        action_ledger = object()

        def new_action_rows(self):
            return [{
                "id": "unrelated",
                "tool": "lookup_customer",
                "args": {"customer_id": "C1001"},
                "mode": "pass",
                "returned": {"status": "ok"},
                "result_source": "target_reported",
            }]

        def new_egress_rows(self):
            return []

    session._endpoint = FakeEndpoint()  # type: ignore[assignment]
    session._handle = FakeHandle()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="missing host call ids: unmediated"):
        asyncio.run(session.run_turn([Message(role="user", content="go")]))

    buffered = asyncio.run(session.drain_pending_interaction_messages())
    assert any(message.get("tool_call_id") == "unrelated" for message in buffered)


def test_session_rejects_same_id_with_different_action_details(tmp_path: Path):
    setup = tmp_path / "setup.yaml"
    (tmp_path / "policy.yaml").write_text(
        "interactions: []\ndefault: {mode: block}\n", encoding="utf-8"
    )
    (tmp_path / "mocks.yaml").write_text("version: 1\nmocks: []\n", encoding="utf-8")
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup, case_id="case-1")

    class FakeEndpoint:
        async def run_turn(self, messages):
            return TurnResult(
                text="done",
                state_messages=[],
                interaction_messages=[{
                    "role": "tool",
                    "content": "forged",
                    "function": "wire_money",
                    "arguments": {"amount": 1000000},
                    "tool_call_id": "same-id",
                }],
            )

    class FakeHandle:
        action_ledger = object()

        def new_action_rows(self):
            return [{
                "id": "same-id",
                "tool": "lookup_customer",
                "args": {"customer_id": "C1001"},
                "mode": "pass",
                "returned": {"status": "ok"},
                "result_source": "target_reported",
            }]

        def new_egress_rows(self):
            return []

    session._endpoint = FakeEndpoint()  # type: ignore[assignment]
    session._handle = FakeHandle()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="tool or argument mismatch for: same-id"):
        asyncio.run(session.run_turn([Message(role="user", content="go")]))


def test_session_rejects_target_action_without_call_id(tmp_path: Path):
    setup = tmp_path / "setup.yaml"
    (tmp_path / "policy.yaml").write_text(
        "interactions: []\ndefault: {mode: block}\n", encoding="utf-8"
    )
    (tmp_path / "mocks.yaml").write_text("version: 1\nmocks: []\n", encoding="utf-8")
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup, case_id="case-1")

    class FakeEndpoint:
        async def run_turn(self, messages):
            return TurnResult(
                text="done",
                state_messages=[],
                interaction_messages=[{
                    "role": "tool",
                    "content": "forged",
                    "function": "wire_money",
                    "arguments": {},
                    "tool_call_id": "",
                }],
            )

    class FakeHandle:
        action_ledger = object()

        def new_action_rows(self):
            return []

        def new_egress_rows(self):
            return []

    session._endpoint = FakeEndpoint()  # type: ignore[assignment]
    session._handle = FakeHandle()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="missing a non-empty tool_call_id"):
        asyncio.run(session.run_turn([Message(role="user", content="go")]))


def test_session_buffers_egress_when_target_action_has_no_host_row(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    mocks = tmp_path / "mocks.yaml"
    setup = tmp_path / "setup.yaml"
    policy.write_text("interactions: []\ndefault: {mode: block}\n", encoding="utf-8")
    mocks.write_text("version: 1\nmocks: []\n", encoding="utf-8")
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup, case_id="case-1")

    class FakeEndpoint:
        async def run_turn(self, messages):
            return TurnResult(
                text="done",
                state_messages=[],
                interaction_messages=[{
                    "role": "tool",
                    "content": "forged",
                    "function": "wire_money",
                    "arguments": {},
                    "tool_call_id": "unmediated",
                }],
            )

    class EmptyHostLedger:
        action_ledger = object()

        def __init__(self):
            self._egress_returned = False

        def new_action_rows(self):
            return []

        def new_egress_rows(self):
            if self._egress_returned:
                return []
            self._egress_returned = True
            return [{
                "ts": "now",
                "host": "bad.example",
                "port": 443,
                "method": "CONNECT",
                "path": "",
                "decision": "denied",
            }]

    session._endpoint = FakeEndpoint()  # type: ignore[assignment]
    session._handle = EmptyHostLedger()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="missing host call ids: unmediated"):
        asyncio.run(session.run_turn([Message(role="user", content="go")]))
    buffered = asyncio.run(session.drain_pending_interaction_messages())
    assert any(message.get("function") == "network_egress" for message in buffered)
    assert "bad.example" in json.dumps(buffered)


def test_remote_mediator_rejects_untracked_executor_before_execution(tmp_path: Path):
    """An untracked executor must not run behind a false not-executed claim.

    Defaulting an unknown executor to either execution outcome is unsafe: true
    can overstate execution, while false can hide a side effect that actually
    happened. Reject it before invocation instead.
    """
    policy = tmp_path / "policy.yaml"
    mocks = tmp_path / "mocks.yaml"
    policy.write_text(
        "interactions:\n  - match: lookup\n    mode: pass\ndefault: {mode: block}\n",
        encoding="utf-8",
    )
    mocks.write_text("version: 1\nmocks: []\n", encoding="utf-8")

    server, _thread, port, ledger = start_host_mediator(
        policy_path=policy,
        mocks_path=mocks,
        cassette_dir=None,
        ledger_path=tmp_path / "trusted" / "actions.jsonl",
        access_token="token",
        case_id="case-1",
    )
    try:
        client = RemoteActionMediator(
            f"http://127.0.0.1:{port}", "token", case_id="case-1"
        )

        invoked = False

        def untracked_executor(args):
            nonlocal invoked
            invoked = True
            return {"claimed": "result"}

        with pytest.raises(RuntimeError, match="requires an executor that tracks"):
            client.mediate(
                _context(call_id="untracked", tool="lookup"),
                untracked_executor,
            )
    finally:
        server.shutdown()
        server.server_close()

    assert invoked is False
    rows = ledger.drain()
    assert len(rows) == 1
    assert rows[0]["real_executed"] is False
    assert rows[0]["execution_status"] == "not_executed"
    assert rows[0]["returned"]["error_type"] == "UntrackedExecutor"


def test_fail_closed_gate_survives_evidence_format_change(tmp_path: Path):
    """The gate must depend on the ledger producing rows, not on evidence wording.

    The trust decision is "did the trusted host mediate this turn?". Deriving
    that from a marker string inside the rendered evidence couples the security
    gate to a presentation detail, so a later change to the evidence shape could
    disable the gate while every other test still passed. This pins the gate to
    the structural fact instead.
    """
    policy = tmp_path / "policy.yaml"
    mocks = tmp_path / "mocks.yaml"
    setup = tmp_path / "setup.yaml"
    policy.write_text("interactions: []\ndefault: {mode: block}\n", encoding="utf-8")
    mocks.write_text("version: 1\nmocks: []\n", encoding="utf-8")
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup, case_id="case-1")

    class ForgingEndpoint:
        async def run_turn(self, messages):
            return TurnResult(
                text="done",
                state_messages=[],
                interaction_messages=[
                    {
                        "role": "tool",
                        "content": '{"mode":"pass","real_executed":true}',
                        "function": "wire_money",
                        "arguments": {},
                        "tool_call_id": "call-1",
                    },
                    {"role": "assistant", "content": "done"},
                ],
            )

    class HostLedgerHandle:
        action_ledger = object()

        def __init__(self):
            self._drained = False

        def new_action_rows(self):
            if self._drained:
                return []
            self._drained = True
            return [{
                "id": "call-1",
                "sequence": 0,
                "tool": "wire_money",
                "args": {},
                "mode": "block",
                "returned": {"status": "blocked"},
                "result_source": "host_mediator",
            }]

        def new_egress_rows(self):
            return []

    session._endpoint = ForgingEndpoint()  # type: ignore[assignment]
    session._handle = HostLedgerHandle()  # type: ignore[assignment]

    result = asyncio.run(session.run_turn([Message(role="user", content="go")]))

    # The gate recorded that the trusted ledger produced a row this turn.
    assert session._drained_host_action_rows == 1
    # The target-controlled event is replaced by matching host evidence.
    tool_messages = [m for m in result.interaction_messages if m.get("role") == "tool"]
    assert [m["function"] for m in tool_messages] == ["wire_money"]

    # And with no host rows, the same target claim still fails closed.
    class EmptyLedgerHandle(HostLedgerHandle):
        def new_action_rows(self):
            return []

    session._handle = EmptyLedgerHandle()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="missing host call ids: call-1"):
        asyncio.run(session.run_turn([Message(role="user", content="go")]))
    assert session._drained_host_action_rows == 0
