# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Mock backends: where a mocked tool response comes from.

Do not reinvent service-integration mocking, and keep the backend modular so
different services can retain their own API/response semantics. This module
defines the seam that makes that
possible: the mock layer resolves *which* rule applies, then hands off to a
backend to produce the bytes.

Three backends ship here, matching the tiers in the design doc:

    inline    -> hand-authored response declared in the mock YAML
    scenario  -> stateful, sequenced responses (WireMock-style scenario/state)
    replay    -> a recorded cassette response, optionally with field overrides

A fourth is a stub seam rather than a claim: `contract` is where an
API-contract-driven generator (WireMock/Prism-style) would plug in. It is not
implemented, and it raises rather than silently degrading, so a config that asks
for it fails loudly instead of quietly returning something unfaithful.

The backend protocol is intentionally tiny — resolve(rule, call) -> Resolution —
so an adopter can write their own against their own service contract without
touching the mediator.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..cassettes import read_cassette_json
from ..mediator import _apply_overrides


class MockBackendError(RuntimeError):
    """Raised when a backend cannot honor the rule it was handed."""


@dataclass(frozen=True)
class MockCall:
    """The tool call a backend is being asked to answer."""

    tool: str
    args: Mapping[str, Any]
    case_id: str | None = None


@dataclass(frozen=True)
class Resolution:
    """A backend's answer.

    `mock_source` is provenance, matching the mediator's existing vocabulary
    (inline | replay | override). `is_error` lets a backend express a *simulated
    failure* so an eval can test how the agent handles a failing tool — without
    inventing a fourth enforcement
    mode. A simulated failure is still a mock: the real tool did not run.
    """

    value: Any
    mock_source: str = "inline"
    is_error: bool = False
    state_note: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class MockBackend(Protocol):
    """Produce a mocked response for one matched rule."""

    name: str

    def resolve(self, rule: Mapping[str, Any], call: MockCall) -> Resolution: ...


class InlineBackend:
    """Return the hand-authored `response:` payload from the mock file."""

    name = "inline"

    def resolve(self, rule: Mapping[str, Any], call: MockCall) -> Resolution:
        if "response" not in rule and "error" not in rule:
            raise MockBackendError(
                f"inline mock for '{call.tool}' declares neither `response:` nor `error:`"
            )
        if "error" in rule:
            return Resolution(value=copy.deepcopy(rule["error"]), mock_source="inline", is_error=True)
        return Resolution(value=copy.deepcopy(rule["response"]), mock_source="inline")


class ScenarioBackend:
    """Stateful, sequenced responses — the WireMock scenario pattern.

    If you mock a state-changing call, a later read has to reflect it. A scenario
    rule declares
    an ordered list of responses and, optionally, the state transition each one
    performs. The backend keeps per-scenario state for the life of a run and
    advances it as calls arrive.

        - tool: transfer_funds
          backend: scenario
          scenario: account_transfer
          responses:
            - response: {status: ok, balance: 500}
              sets_state: transferred
            - response: {status: ok, balance: 500}

        - tool: get_balance
          backend: scenario
          scenario: account_transfer
          when_state: transferred
          response: {balance: 500}

    State is keyed by case ID plus scenario name and scoped to this backend
    instance. A fresh runner still starts clean, while a shared host also keeps
    interleaved cases independent.
    """

    name = "scenario"

    def __init__(self) -> None:
        self._state: dict[tuple[str, str], str] = {}
        self._cursor: dict[tuple[str, str, str], int] = {}

    def current_state(self, scenario: str) -> str:
        """Return legacy/default-case state; kept stable for custom backends."""
        return self._state.get(("", scenario), "start")

    def _current_state_for_call(self, scenario: str, case_id: str | None) -> str:
        """Read built-in case state without changing the extension signature."""
        if (
            type(self).current_state is not ScenarioBackend.current_state
            or type(self).matches_state is not ScenarioBackend.matches_state
        ):
            return self.current_state(scenario)
        return self._state.get((case_id or "", scenario), "start")

    def _case_key(self, case_id: str | None) -> str:
        """Use legacy shared state when an override cannot accept case identity."""
        if (
            type(self).current_state is not ScenarioBackend.current_state
            or type(self).matches_state is not ScenarioBackend.matches_state
        ):
            return ""
        return case_id or ""

    def reset(self) -> None:
        self._state.clear()
        self._cursor.clear()

    def matches_state(self, rule: Mapping[str, Any]) -> bool:
        """Whether a rule's `when_state` guard holds in the legacy default case."""
        want = rule.get("when_state")
        if want is None:
            return True
        scenario = str(rule.get("scenario") or "")
        return self.current_state(scenario) == str(want)

    def matches_state_for_call(self, rule: Mapping[str, Any], call: MockCall) -> bool:
        """Evaluate state for a case while preserving older subclass overrides."""
        if (
            type(self).matches_state is not ScenarioBackend.matches_state
            or type(self).current_state is not ScenarioBackend.current_state
        ):
            return self.matches_state(rule)
        want = rule.get("when_state")
        if want is None:
            return True
        scenario = str(rule.get("scenario") or "")
        return self._current_state_for_call(scenario, call.case_id) == str(want)

    def resolve(self, rule: Mapping[str, Any], call: MockCall) -> Resolution:
        scenario = str(rule.get("scenario") or "")
        if not scenario:
            raise MockBackendError(f"scenario mock for '{call.tool}' is missing `scenario:`")

        responses = rule.get("responses")
        if responses is None:
            # Single-response scenario rule: still allowed to move state.
            step = {k: rule[k] for k in ("response", "error", "sets_state") if k in rule}
            if not step:
                raise MockBackendError(
                    f"scenario mock for '{call.tool}' declares neither `responses:` nor `response:`"
                )
            steps = [step]
        else:
            if not isinstance(responses, list) or not responses:
                raise MockBackendError(f"`responses:` for '{call.tool}' must be a non-empty list")
            steps = list(responses)

        case_key = self._case_key(call.case_id)
        key = (case_key, scenario, call.tool)
        index = self._cursor.get(key, 0)
        # The last step repeats once exhausted, so a scenario cannot run off the
        # end mid-eval and start returning nothing.
        step = steps[min(index, len(steps) - 1)]
        self._cursor[key] = index + 1

        before = self._current_state_for_call(scenario, call.case_id)
        if "sets_state" in step:
            self._state[(case_key, scenario)] = str(step["sets_state"])
        after = self._current_state_for_call(scenario, call.case_id)

        is_error = "error" in step
        payload = step.get("error") if is_error else step.get("response")
        if payload is None:
            raise MockBackendError(
                f"scenario step {index} for '{call.tool}' declares neither `response:` nor `error:`"
            )

        return Resolution(
            value=copy.deepcopy(payload),
            mock_source="inline",
            is_error=is_error,
            state_note=f"{scenario}: {before} -> {after}" if before != after else f"{scenario}: {after}",
            detail={"scenario": scenario, "step": index, "state": after},
        )


class ReplayBackend:
    """Return a recorded response, optionally with surgical field overrides.

    Reuses the mediator's existing override application so replay/override
    provenance stays identical to what the mediator already emits.
    """

    name = "replay"

    def __init__(self, cassette_dir: str | Path | None = None) -> None:
        self.cassette_dir = Path(cassette_dir) if cassette_dir else None

    def resolve(self, rule: Mapping[str, Any], call: MockCall) -> Resolution:
        base = rule.get("cassette")
        origin = "inline"
        if base is None:
            name = str(rule.get("cassette_file") or call.tool)
            if not self.cassette_dir:
                raise MockBackendError(
                    f"replay mock for '{call.tool}' needs a cassette dir or an inline `cassette:`"
                )
            try:
                base = read_cassette_json(self.cassette_dir, name)
            except FileNotFoundError as exc:
                raise MockBackendError(
                    f"cassette not found for '{call.tool}': {name!r}"
                ) from exc
            except (OSError, ValueError) as exc:
                raise MockBackendError(
                    f"cassette for '{call.tool}' could not be read safely: {exc}"
                ) from exc
            origin = "cassette_file"

        overrides = list(rule.get("overrides") or [])
        value = _apply_overrides(base, overrides) if overrides else copy.deepcopy(base)
        return Resolution(
            value=value,
            mock_source="override" if overrides else "replay",
            detail={"replay_origin": origin, "overrides": len(overrides)},
        )


class ContractBackend:
    """Seam for API-contract-driven mock generation. Deliberately unimplemented.

    Reuse existing tooling (WireMock/Prism-style: point at the API contract,
    generate a mock with the same API surface, write custom
    code behind it). That belongs behind this seam. Until it is real, asking for
    it fails loudly rather than returning a lower-fidelity stand-in that a
    reader might mistake for contract-faithful.
    """

    name = "contract"

    def resolve(self, rule: Mapping[str, Any], call: MockCall) -> Resolution:
        raise MockBackendError(
            f"backend 'contract' is not implemented yet (tool '{call.tool}'). "
            "Use inline/scenario/replay, or supply a custom backend. "
            "Contract-driven generation is the intended reuse point for existing "
            "service-integration mock tooling."
        )


def default_backends(cassette_dir: str | Path | None = None) -> dict[str, MockBackend]:
    return {
        "inline": InlineBackend(),
        "scenario": ScenarioBackend(),
        "replay": ReplayBackend(cassette_dir),
        "contract": ContractBackend(),
    }
