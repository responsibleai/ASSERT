# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Convert mediation records into ASSERT-style tool evidence events."""
from __future__ import annotations

import json
from typing import Any

from .records import MediationRecord


def compact_evidence(record: MediationRecord) -> dict[str, Any]:
    """Evidence shape intended for judge input.

    Keep the full Agent Hooks contexts in the side ledger, but pass a compact
    action-evidence row to ASSERT so multi-turn scenario prompts don't balloon.
    """
    return {
        "tool": record.tool,
        "args": record.args,
        "mode": record.decision.mode,
        "real_executed": record.decision.real_executed,
        "flagged": record.decision.flagged,
        "matched": record.decision.matched,
        "reason": record.decision.reason,
        "returned": record.decision.returned,
        "agent_hooks": {
            "spec": record.pre_context.get("spec"),
            "pre": {
                "interception_point": record.pre_context.get("interception_point"),
                "sequence": record.pre_context.get("sequence"),
                "tool_call": record.pre_context.get("tool_call"),
            },
            "post": {
                "interception_point": record.post_context.get("interception_point"),
                "sequence": record.post_context.get("sequence"),
                "tool_call": record.post_context.get("tool_call"),
                "tool_result": record.post_context.get("tool_result"),
            },
        },
    }


def assert_tool_event(record: MediationRecord) -> dict[str, Any]:
    evidence = compact_evidence(record)
    return {
        "role": "tool_result",
        "tool_name": record.tool,
        "tool_args": record.args,
        "tool_call_id": record.id,
        "content": json.dumps(evidence, sort_keys=True),
        "raw": {"action_mediation": evidence},
    }
