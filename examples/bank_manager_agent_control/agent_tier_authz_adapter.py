"""ASSERT external-connector adapters for the three sensitivity-tier arms.

Why this exists
---------------
The first pass ran the three arms as ``target.callable``. A callable target
returns a plain string, so the judge saw only the final assistant message. The
behavior under test is an *ordering* property — was authorization obtained
BEFORE the protected record was read — and that ordering is invisible in the
final text. All three arms scored an identical 62.5% ``policy_violation``,
which is the signature of a saturated, non-discriminative dimension rather than
a real absence of effect.

An ASSERT connector may return ``{"text": ..., "events": [...]}`` with
``tool_call`` / ``tool_result`` events, which ASSERT serializes into the judge
transcript as real assistant tool calls and tool messages. That gives the judge
the evidence the rubric actually asks for.

Nothing about the agents changed. Same prompts, same tools, same Rego, same
policy decisions — only the transcript handed to the judge is richer.

Select the arm with ``TIER_AUTHZ_ARM_SELECT``: ``arm1`` | ``arm2`` | ``arm3``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.bank_manager_agent_control.agent_tier_authz import (  # noqa: E402
    DEFENSIVE_ADDENDUM,
    SYSTEM_PROMPT_BASE,
    _run_acs_arm_traced,
    _run_prompt_arm_traced,
)

ARMS = {
    "arm1": ("arm1_baseline", SYSTEM_PROMPT_BASE),
    "arm2": ("arm2_defensive_prompt", SYSTEM_PROMPT_BASE + DEFENSIVE_ADDENDUM),
    "arm3": ("arm3_acs_rego", None),
}


class Adapter:
    """ASSERT external connector. One instance per test case."""

    def __init__(self, scenario: dict[str, Any] | None = None) -> None:
        self.scenario = scenario or {}
        self.selector = os.environ.get("TIER_AUTHZ_ARM_SELECT", "arm1")
        if self.selector not in ARMS:
            raise ValueError(f"TIER_AUTHZ_ARM_SELECT must be one of {sorted(ARMS)}")

    async def send_message(self, message: str, history: Any = None) -> dict[str, Any]:
        arm, prompt = ARMS[self.selector]
        if prompt is None:
            return await _run_acs_arm_traced(message, arm)
        return await _run_prompt_arm_traced(message, prompt, arm)
