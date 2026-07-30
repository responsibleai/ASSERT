# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Action mediation core.

Consumes Agent Hooks-shaped `pre_tool_call` contexts and decides how the sandbox
responds to the attempted tool call.

Three enforcement modes only:

    pass   -> execute the real (sandbox-bound) tool, record the result
    mock   -> suppress the real tool, return synthetic content (see mock_source)
    block  -> suppress the real tool, return a hard denial/error

`mock_source` is the PROVENANCE of mock content and is NOT a separate mode and
NOT an intent marker:

    inline   -> return the hand-authored `mock:` payload from policy
    replay   -> return a recorded cassette response, verbatim
    override -> replay a recorded/base response with surgical field overrides

Whether a mock is adversarial (an injection) is a property of the eval scenario,
not of the mediator. It is visible in the returned content and joinable via the
case id; the mediator does not label intent. Recording is universal (handled by
the tool host), so "audit" is not a mode either.

`flagged` is derived from `mode` on the decision (mock/block flag, pass does not),
not stored, so it can never drift from the enforcement decision. Legacy `flag:`
and `audit:` policy keys are accepted but inert — they no longer set anything.
"""
from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .policy import MediationPolicy
from .records import MediationDecision

log = logging.getLogger(__name__)

Execute = Callable[[dict[str, Any]], Any]

# Legacy policy vocab -> canonical mode. Keeps older policy files, the ASSERT
# connector, and the runtime_contract passthrough working without a flag day.
# Note: legacy "poison"/"inject" were an intent label, not a provenance. They
# normalize to `mock`; the actual mock_source is then derived structurally
# (overrides -> override, recording -> replay, else inline), because intent is
# no longer a mediator concept.
_LEGACY_MODE_ALIASES: dict[str, str] = {
    "pass": "pass",
    "pass_through": "pass",
    "passthrough": "pass",
    "execute": "pass",
    "audit": "pass",        # audit was pass + a marker; recording is universal
    "mock": "mock",
    "inline": "mock",
    "replay": "mock",
    "poison": "mock",       # legacy intent label -> plain mock; source derived below
    "inject": "mock",
    "block": "block",
    "deny": "block",
}


def _normalize_mode(raw: str) -> str:
    return _LEGACY_MODE_ALIASES.get(str(raw or "").strip().lower(), "block")


class ActionMediator:
    def __init__(
        self,
        policy: MediationPolicy,
        *,
        cassette_dir: str | Path | None = None,
        mocks: Any | None = None,
    ) -> None:
        """
        `mocks` is an optional `MockLibrary` from this package's `.mocks`
        subpackage.

        When supplied, it is consulted FIRST for any call the policy decided to
        mock, so mock content lives in its own modular file instead of inline in
        the enforcement policy. When it has no rule for the call, resolution
        falls back to the policy's inline `mock:` payload, which keeps every
        existing policy file working unchanged.

        The mock library can never change the enforcement decision. It only
        supplies content for a call the policy already decided to mock, so
        adding mock fidelity can never weaken the safety posture.
        """
        self.policy = policy
        self.cassette_dir = Path(cassette_dir) if cassette_dir else None
        self.mocks = mocks

    def mediate(self, pre_context: dict[str, Any], execute_effective: Execute) -> MediationDecision:
        if pre_context.get("interception_point") != "pre_tool_call":
            raise ValueError("ActionMediator expects a pre_tool_call context")
        tool_call = pre_context.get("tool_call") or {}
        name = str(tool_call.get("name") or "")
        args = dict(tool_call.get("args") or {})
        rule = self.policy.decide(name)
        mode = _normalize_mode(str(rule.get("mode", "block")))
        note = str(rule.get("note", ""))
        matched = str(rule.get("match", ""))

        if mode == "pass":
            returned = execute_effective(args)
            return MediationDecision(
                mode="pass",
                returned=returned,
                real_executed=True,
                reason=note,
                matched=matched,
            )

        if mode == "mock":
            from_library = self._mock_from_library(name, args, note, matched, pre_context)
            if from_library is not None:
                return from_library
            return self._mock(rule, name, note, matched)

        return MediationDecision(
            mode="block",
            returned={"status": "blocked", "message": f"Tool {name} was blocked by sandbox mediation policy."},
            real_executed=False,
            reason=note or "blocked by default policy",
            matched=matched,
            is_error=True,
        )

    def _mock_from_library(
        self,
        name: str,
        args: dict[str, Any],
        note: str,
        matched: str,
        pre_context: Mapping[str, Any],
    ) -> MediationDecision | None:
        """Resolve mock content from the separate mock file, if one is loaded."""
        if self.mocks is None:
            return None
        from .mocks import MockCall  # local import keeps the core import-light

        case_id = pre_context.get("case_id") or (pre_context.get("session") or {}).get("case_id")
        resolution = self.mocks.resolve(MockCall(tool=name, args=args, case_id=case_id))
        if resolution is None:
            return None

        replay: dict[str, Any] = dict(resolution.detail)
        if resolution.state_note:
            replay["state"] = resolution.state_note
        return MediationDecision(
            mode="mock",
            returned=resolution.value,
            real_executed=False,
            reason=note,
            matched=matched,
            # A simulated failure is still a mock: the real tool did not run. It
            # surfaces as an error to the agent so the eval can test failure
            # handling, without inventing a fourth enforcement mode.
            is_error=resolution.is_error,
            mock_source=resolution.mock_source,
            replay=replay or None,
        )


    def _mock(
        self,
        rule: Mapping[str, Any],
        name: str,
        note: str,
        matched: str,
    ) -> MediationDecision:
        """Resolve mock content and derive its provenance structurally.

        Provenance is a fact about the rule's shape, not a declared intent:
          - has overrides                -> "override"
          - has a recording, no edits    -> "replay"
          - neither                      -> "inline"

        An explicit `mock_source` in the rule is honored only when it names a
        real provenance; a stale `mock_source: poison` (intent, not provenance)
        is ignored in favor of the structural derivation.
        """
        overrides = list(rule.get("overrides") or [])
        inline_cassette = rule.get("cassette")
        has_recording = inline_cassette is not None or (
            self.cassette_dir is not None and (self.cassette_dir / f"{name}.json").exists()
        )

        declared = str(rule.get("mock_source") or "").strip().lower()
        valid_declared = declared if declared in {"inline", "replay", "override"} else ""

        if overrides:
            source = "override"
        elif valid_declared:
            source = valid_declared
        elif has_recording:
            source = "replay"
        else:
            source = "inline"

        if source in {"replay", "override"}:
            base = inline_cassette if inline_cassette is not None else self._load_cassette(name)
            if base is None:
                # No recording available; fall back to any authored payload.
                base = rule.get("mock", rule.get("poison", rule.get("inject", {"status": "ok"})))
                source = "override" if overrides else "inline"
            returned = _apply_overrides(base, overrides) if overrides else base
            return MediationDecision(
                mode="mock",
                returned=returned,
                real_executed=False,
                reason=note,
                matched=matched,
                mock_source=source,
                replay={
                    "source": "inline" if inline_cassette is not None else "cassette_file",
                    "overrides": len(overrides),
                },
            )

        # Plain hand-authored inline fixture. `poison`/`inject` are accepted as
        # legacy payload keys but carry no special meaning now.
        # A rule with no payload anywhere is almost always a mistake, and the
        # old silent `{"status": "ok"}` default made it look like a working
        # mock. Containment still holds (the real tool never runs), so warn and
        # return an explicit self-describing placeholder instead of failing the
        # run.
        payload = rule.get("mock", rule.get("poison", rule.get("inject")))
        if payload is None:
            log.warning(
                "Tool %r is mocked but no mock payload was found in the policy rule or mock file; "
                "returning a placeholder response. Add a `mock:` payload or a mocks.yaml rule.",
                name,
            )
            payload = {
                "status": "ok",
                "note": f"No mock payload configured for {name}; sandbox returned a placeholder.",
            }
        return MediationDecision(
            mode="mock",
            returned=payload,
            real_executed=False,
            reason=note,
            matched=matched,
            mock_source="inline",
        )

    def _load_cassette(self, tool_name: str) -> Any | None:
        if not self.cassette_dir:
            return None
        path = self.cassette_dir / f"{tool_name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())


def _apply_overrides(obj: Any, overrides: list[Mapping[str, Any]]) -> Any:
    out = copy.deepcopy(obj)
    for override in overrides:
        segs = str(override["path"]).split(".")
        cur = out
        for seg in segs[:-1]:
            key: Any = int(seg) if isinstance(cur, list) else seg
            if isinstance(cur, list):
                cur = cur[key]
            else:
                cur = cur.setdefault(key, {})
        last: Any = int(segs[-1]) if isinstance(cur, list) else segs[-1]
        cur[last] = override["value"]
    return out
