# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the mock setup layer.

Organized around the product commitments the layer exists to satisfy:

  - declare mocks in YAML, no code/Dockerfile edits
  - per-use-case mocks ("these inputs -> this response"), not just a mock DB
  - state across mocked calls; simulated failures; modular backends
  - separate modular mock file, distinct from the enforcement policy

Plus the invariant that makes the split safe: the mock file supplies content for
calls the policy already decided to mock, and can never change that decision.
"""
from __future__ import annotations

import json

import pytest

from assert_ai.integrations.sandbox.agent_hooks_context import AgentHooksContextBuilder
from assert_ai.integrations.sandbox.mediator import ActionMediator
from assert_ai.integrations.sandbox.mocks import (
    MockBackendError,
    MockCall,
    MockConfigError,
    MockLibrary,
    ScenarioBackend,
)
from assert_ai.integrations.sandbox.mocks.matching import MatcherError, match_value
from assert_ai.integrations.sandbox.policy import MediationPolicy


def _pre(name, args=None):
    return AgentHooksContextBuilder(agent_id="a", framework="f", session_id="s").pre_tool_call(
        call_id="tc-1", name=name, args=args or {}
    )


def _never_executes(_args):
    raise AssertionError("the real tool must not run for a mocked call")


# --- per-use-case mocks -------------------------------------------------------


def test_argument_matchers_select_the_use_case():
    """A rule can key on arguments, so one tool can have several use-case mocks."""
    library = MockLibrary.from_dict({
        "mocks": [
            {"tool": "apply_bill_credit", "when": {"amount": {"gt": 100}},
             "error": {"code": "CREDIT_LIMIT_EXCEEDED"}},
            {"tool": "apply_bill_credit", "when": {"bill_id": "B1234321"},
             "response": {"status": "credit_applied", "bill_id": "B1234321"}},
            {"tool": "apply_bill_credit", "response": {"status": "credit_applied"}},
        ]
    })

    over_limit = library.resolve(MockCall("apply_bill_credit", {"amount": 500, "bill_id": "B1234321"}))
    assert over_limit is not None
    assert over_limit.is_error is True
    assert over_limit.value["code"] == "CREDIT_LIMIT_EXCEEDED"

    seeded = library.resolve(MockCall("apply_bill_credit", {"amount": 25, "bill_id": "B1234321"}))
    assert seeded is not None
    assert seeded.is_error is False
    assert seeded.value["bill_id"] == "B1234321"

    other = library.resolve(MockCall("apply_bill_credit", {"amount": 25, "bill_id": "B9999999"}))
    assert other is not None
    assert other.value == {"status": "credit_applied"}


def test_most_specific_rule_wins_regardless_of_file_order():
    """Specificity, not authoring order, decides. A broad fallback declared first
    must not shadow a narrow use-case rule declared later."""
    library = MockLibrary.from_dict({
        "mocks": [
            {"tool": "send_message", "response": {"status": "sent", "id": "fallback"}},
            {"tool": "send_message", "when": {"recipient": {"not": "555-123-2002"}},
             "response": {"status": "sent", "id": "unverified"}},
        ]
    })
    unverified = library.resolve(MockCall("send_message", {"recipient": "555-000-9999"}))
    assert unverified is not None and unverified.value["id"] == "unverified"
    on_file = library.resolve(MockCall("send_message", {"recipient": "555-123-2002"}))
    assert on_file is not None and on_file.value["id"] == "fallback"


@pytest.mark.parametrize(
    "matcher,value,expected",
    [
        ({"gt": 100}, 500, True),
        ({"gt": 100}, 50, False),
        ({"lte": 100}, 100, True),
        ({"not": "x"}, "y", True),
        ({"not": "x"}, "x", False),
        ({"contains": "account"}, "your account details", True),
        ({"contains": "account"}, "nothing here", False),
        ({"regex": r".*@example\.com"}, "a@example.com", True),
        ({"in": ["a", "b"]}, "b", True),
        ({"in": ["a", "b"]}, "c", False),
        ({"any": None}, "anything", True),
        ("literal", "literal", True),
    ],
)
def test_matcher_operators(matcher, value, expected):
    assert match_value(value, matcher) is expected


def test_absent_argument_never_matches_a_value_assertion():
    """A rule about `recipient` must not fire on a call with no recipient."""
    library = MockLibrary.from_dict({
        "mocks": [{"tool": "send_message", "when": {"recipient": {"not": "555-123-2002"}},
                   "response": {"status": "sent"}}]
    })
    assert library.resolve(MockCall("send_message", {"body": "hi"})) is None


def test_malformed_matcher_fails_loudly():
    with pytest.raises(MatcherError):
        match_value("x", {"gt": 1, "lt": 5})  # two keys is ambiguous
    with pytest.raises(MatcherError):
        match_value("x", {"nonsense": 1})
    with pytest.raises(MatcherError):
        match_value("not-a-number", {"gt": 1})


# --- state across mocked calls ------------------------------------------------


def test_later_read_reflects_a_mocked_write():
    """If a state-changing call is mocked, a later read must agree with it."""
    library = MockLibrary.from_dict({
        "mocks": [
            {"tool": "get_line_status", "scenario": "line_restoration", "when_state": "resumed",
             "response": {"service_status": "connected", "suspended": False}},
            {"tool": "get_line_status", "response": {"service_status": "suspended", "suspended": True}},
            {"tool": "resume_line", "scenario": "line_restoration",
             "responses": [{"response": {"status": "resumed"}, "sets_state": "resumed"}]},
        ]
    })

    before = library.resolve(MockCall("get_line_status", {"line_id": "L1002"}))
    assert before is not None and before.value["suspended"] is True

    resumed = library.resolve(MockCall("resume_line", {"line_id": "L1002"}))
    assert resumed is not None and resumed.value["status"] == "resumed"

    after = library.resolve(MockCall("get_line_status", {"line_id": "L1002"}))
    assert after is not None
    assert after.value["suspended"] is False, "a read after a mocked write must see the write"


def test_scenario_sequence_advances_then_holds():
    library = MockLibrary.from_dict({
        "mocks": [{
            "tool": "resume_line", "scenario": "restore",
            "responses": [
                {"response": {"status": "resumed"}, "sets_state": "resumed"},
                {"response": {"status": "already_active"}},
            ],
        }]
    })
    statuses = []
    for _ in range(3):
        resolution = library.resolve(MockCall("resume_line", {}))
        assert resolution is not None
        statuses.append(resolution.value["status"])
    # The last step repeats rather than running off the end mid-eval.
    assert statuses == ["resumed", "already_active", "already_active"]


def test_reset_clears_scenario_state_between_cases():
    library = MockLibrary.from_dict({
        "mocks": [
            {"tool": "resume_line", "scenario": "restore",
             "responses": [{"response": {"status": "resumed"}, "sets_state": "resumed"}]},
            {"tool": "get_line_status", "scenario": "restore", "when_state": "resumed",
             "response": {"suspended": False}},
            {"tool": "get_line_status", "response": {"suspended": True}},
        ]
    })
    def suspended() -> bool:
        resolution = library.resolve(MockCall("get_line_status", {}))
        assert resolution is not None
        return resolution.value["suspended"]

    library.resolve(MockCall("resume_line", {}))
    assert suspended() is False
    library.reset()
    assert suspended() is True


def test_simulated_failure_is_still_a_mock_not_a_fourth_mode():
    """A simulated failure surfaces as an error to the agent, but the real tool
    still did not run and the mode is still `mock`."""
    library = MockLibrary.from_dict({
        "mocks": [{"tool": "apply_bill_credit", "error": {"code": "DOWNSTREAM_TIMEOUT"}}]
    })
    policy = MediationPolicy({"interactions": [{"match": "apply_bill_credit", "mode": "mock"}]})
    decision = ActionMediator(policy, mocks=library).mediate(
        _pre("apply_bill_credit", {"amount": 25}), _never_executes
    )
    assert decision.mode == "mock"
    assert decision.real_executed is False
    assert decision.is_error is True
    assert decision.flagged is True
    assert isinstance(decision.returned, dict)
    assert decision.returned["code"] == "DOWNSTREAM_TIMEOUT"


# --- modular backends ---------------------------------------------------------


def test_backend_is_inferred_from_rule_shape():
    library = MockLibrary.from_dict({
        "mocks": [
            {"tool": "a", "response": {}},
            {"tool": "b", "scenario": "s", "response": {}},
            {"tool": "c", "cassette": {}, "overrides": []},
        ]
    })
    assert {r.tool: r.backend for r in library.rules} == {"a": "inline", "b": "scenario", "c": "replay"}


def test_replay_with_overrides_reports_override_provenance(tmp_path):
    (tmp_path / "lookup.json").write_text(json.dumps({"bills": [{"charges": [{"description": "orig"}]}]}))
    library = MockLibrary.from_dict(
        {"mocks": [{"tool": "lookup", "backend": "replay", "cassette_file": "lookup",
                    "overrides": [{"path": "bills.0.charges.0.description", "value": "INJECTED"}]}]},
        base_dir=tmp_path,
    )
    library = MockLibrary(library.rules, cassette_dir=tmp_path)
    resolution = library.resolve(MockCall("lookup", {}))
    assert resolution is not None
    assert resolution.mock_source == "override"
    assert resolution.value["bills"][0]["charges"][0]["description"] == "INJECTED"


def test_contract_backend_fails_loudly_instead_of_degrading():
    """The contract-driven seam is declared but unimplemented. Asking for it must
    fail, not quietly return something a reader would mistake for contract-faithful."""
    library = MockLibrary.from_dict({"mocks": [{"tool": "x", "backend": "contract"}]})
    with pytest.raises(MockBackendError, match="not implemented"):
        library.resolve(MockCall("x", {}))


def test_custom_backend_can_be_injected():
    """The seam is real: an adopter can supply a backend for their own service
    contract without touching the mediator."""

    class StubBackend:
        name = "stub"

        def resolve(self, rule, call):
            from assert_ai.integrations.sandbox.mocks import Resolution

            return Resolution(value={"from": "custom", "tool": call.tool})

    library = MockLibrary.from_dict(
        {"mocks": [{"tool": "anything", "backend": "stub"}]},
        backends={"stub": StubBackend()},
    )
    resolution = library.resolve(MockCall("anything", {}))
    assert resolution is not None and resolution.value["from"] == "custom"


# --- the safety invariant -----------------------------------------------------


def test_mock_file_cannot_change_an_enforcement_decision():
    """The load-bearing safety property of splitting the files: mocks.yaml only
    supplies content for a call the policy ALREADY decided to mock. It can never
    turn a blocked call into a passed one, or cause a real execution."""
    policy = MediationPolicy({
        "interactions": [{"match": "safe_read", "mode": "pass"}],
        "default": {"mode": "block"},
    })
    # The mock file tries to speak for a blocked tool and for a passed tool.
    library = MockLibrary.from_dict({
        "mocks": [
            {"tool": "wire_money", "response": {"status": "ok", "executed": True}},
            {"tool": "safe_read", "response": {"status": "mocked-instead-of-real"}},
        ]
    })
    mediator = ActionMediator(policy, mocks=library)

    blocked = mediator.mediate(_pre("wire_money", {"amount": 1_000_000}), _never_executes)
    assert blocked.mode == "block"
    assert blocked.real_executed is False
    assert isinstance(blocked.returned, dict)
    assert blocked.returned["status"] == "blocked", "a mock rule must not unblock a call"

    executed = []
    passed = mediator.mediate(_pre("safe_read"), lambda args: executed.append(args) or {"real": True})
    assert passed.mode == "pass"
    assert passed.real_executed is True
    assert passed.returned == {"real": True}, "a mock rule must not shadow a passed call"
    assert executed == [{}]


def test_policy_inline_mocks_still_work_with_no_mock_file():
    """Backwards compatibility: every existing policy.yaml keeps working."""
    policy = MediationPolicy({
        "interactions": [{"match": "resume_line", "mode": "mock", "mock": {"status": "resumed"}}]
    })
    decision = ActionMediator(policy).mediate(_pre("resume_line"), _never_executes)
    assert decision.mode == "mock"
    assert decision.mock_source == "inline"
    assert decision.returned == {"status": "resumed"}


def test_unmatched_mock_rule_falls_back_to_policy_inline_payload():
    """A mock file that has nothing to say about a call is not an empty response;
    resolution falls through to the policy's inline payload."""
    policy = MediationPolicy({
        "interactions": [{"match": "resume_line", "mode": "mock", "mock": {"status": "from-policy"}}]
    })
    library = MockLibrary.from_dict({
        "mocks": [{"tool": "resume_line", "when": {"line_id": "L9999"}, "response": {"status": "from-mocks"}}]
    })
    mediator = ActionMediator(policy, mocks=library)
    matched = mediator.mediate(_pre("resume_line", {"line_id": "L9999"}), _never_executes)
    assert matched.returned == {"status": "from-mocks"}
    unmatched = mediator.mediate(_pre("resume_line", {"line_id": "L1002"}), _never_executes)
    assert unmatched.returned == {"status": "from-policy"}


def test_mock_evidence_records_which_rule_fired():
    """Provenance has to survive into the evidence, or a reviewer cannot tell
    which mock produced the response the judge scored."""
    policy = MediationPolicy({"interactions": [{"match": "send_message", "mode": "mock"}]})
    library = MockLibrary.from_dict({
        "mocks": [{"tool": "send_message", "when": {"recipient": {"not": "555-123-2002"}},
                   "response": {"status": "sent"}, "note": "unverified destination"}]
    })
    decision = ActionMediator(policy, mocks=library).mediate(
        _pre("send_message", {"recipient": "555-000-9999", "body": "acct details"}), _never_executes
    )
    evidence = decision.evidence()
    assert evidence["mode"] == "mock"
    assert evidence["flagged"] is True
    assert evidence["real_executed"] is False
    assert evidence["replay"]["mock_rule"] == "send_message"
    assert evidence["replay"]["backend"] == "inline"
    assert evidence["replay"]["matched_args"] == ["recipient"]
    assert evidence["replay"]["note"] == "unverified destination"


# --- config validation --------------------------------------------------------


def test_malformed_mock_files_fail_loudly():
    with pytest.raises(MockConfigError, match="version"):
        MockLibrary.from_dict({"version": 99, "mocks": []})
    with pytest.raises(MockConfigError, match="tool"):
        MockLibrary.from_dict({"mocks": [{"response": {}}]})
    with pytest.raises(MockConfigError, match="must be a list"):
        MockLibrary.from_dict({"mocks": {"tool": "x"}})
    with pytest.raises(MockConfigError, match="unknown backend"):
        MockLibrary.from_dict({"mocks": [{"tool": "x", "backend": "nope"}]})


def test_incomplete_rules_fail_at_resolution():
    library = MockLibrary.from_dict({"mocks": [{"tool": "x", "backend": "inline"}]})
    with pytest.raises(MockBackendError, match="neither"):
        library.resolve(MockCall("x", {}))

    scenario_less = MockLibrary.from_dict({"mocks": [{"tool": "y", "backend": "scenario"}]})
    with pytest.raises(MockBackendError, match="scenario"):
        scenario_less.resolve(MockCall("y", {}))


def test_scenario_backend_state_is_observable():
    backend = ScenarioBackend()
    assert backend.current_state("s") == "start"
    backend.resolve({"scenario": "s", "response": {}, "sets_state": "done"}, MockCall("t", {}))
    assert backend.current_state("s") == "done"
    backend.reset()
    assert backend.current_state("s") == "start"
