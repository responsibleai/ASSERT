# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Records emitted by the sandbox action mediator.

The mediator collapses to three enforcement modes:

- ``pass``  — execute the real (sandbox-bound) tool and record the result.
- ``mock``  — suppress the real tool and return synthetic content. The provenance
              of that content is carried in ``mock_source`` (hand-authored inline
              payload, verbatim recorded replay, or replay with surgical overrides).
- ``block`` — suppress the real tool and return a hard denial/error.

Recording is universal: every mediated call emits a record regardless of mode,
so "audit" is a property of the mediator, not a fourth mode.

``mock_source`` describes *provenance only* — where the bytes came from and
whether they were mutated. It deliberately does NOT encode intent. Whether a
mock carries an adversarial injection is a property of the eval scenario
(expressed in the ASSERT case, visible in the returned content, joinable via
``case_id``), never a mediator concept.

``flagged`` is likewise *derived, not stored* — see ``MediationDecision.flagged``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MediationMode = Literal["pass", "mock", "block"]
# Provenance of mock content. Not a mode, and not an intent marker.
#   inline   -> hand-authored payload from the policy
#   replay   -> a recorded cassette response, verbatim
#   override -> a recorded/base response with surgical field edits applied
MockSource = Literal["inline", "replay", "override"]


@dataclass(frozen=True)
class MediationDecision:
    mode: MediationMode
    returned: Any | None = None
    real_executed: bool = False
    reason: str = ""
    matched: str = ""
    is_error: bool = False
    mock_source: str | None = None
    replay: dict[str, Any] | None = None

    @property
    def flagged(self) -> bool:
        """Derived, not stored: a call is flagged iff the mediator intervened.

        - ``pass``  -> the agent saw reality; there is nothing for the mediator to
          flag, so a passed call is never flagged.
        - ``mock``  -> the agent was handed synthetic bytes instead of the real
          result; its view diverged from reality.
        - ``block`` -> the action was refused.

        Whether a *passed* benign call is "interesting" (e.g. worth surfacing a
        particular read) is a per-eval salience judgment. That belongs to the
        scenario/judge and is filterable by tool name + ``case_id`` — it is not a
        bit the harm-agnostic mediator stamps into the base policy. Deriving
        ``flagged`` from ``mode`` makes it impossible for the flag to drift out of
        sync with the enforcement decision.
        """
        return self.mode != "pass"

    def evidence(self) -> dict[str, Any]:
        out = {
            "mode": self.mode,
            "real_executed": self.real_executed,
            "flagged": self.flagged,
            "reason": self.reason,
            "matched": self.matched,
            "returned": self.returned,
        }
        if self.mock_source:
            out["mock_source"] = self.mock_source
        if self.replay:
            out["replay"] = self.replay
        return out


@dataclass(frozen=True)
class MediationRecord:
    tool: str
    args: dict[str, Any]
    decision: MediationDecision
    pre_context: dict[str, Any]
    post_context: dict[str, Any]
    id: str
    extensions: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "decision": self.decision.evidence(),
            "agent_hooks": {
                "pre_tool_call": self.pre_context,
                "post_tool_call": self.post_context,
            },
            "extensions": self.extensions or {},
        }
