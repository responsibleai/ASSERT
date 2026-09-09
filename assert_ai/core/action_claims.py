# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Private canonical identities for reconciling target action evidence."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ActionClaim:
    """Non-persisted digests for one target action event."""

    kind: Literal["call", "result"]
    call_id_digest: str
    tool_digest: str
    arguments_digest: str | None
    arguments_supplied: bool


def _canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("action claim contains a non-canonical JSON value") from exc
    return hashlib.sha256(encoded).hexdigest()


def make_action_claim(
    *,
    kind: Literal["call", "result"],
    call_id: Any,
    tool: Any,
    arguments: Any,
    arguments_supplied: bool,
) -> ActionClaim:
    """Validate and digest raw action identity without retaining its values."""
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("action claim is missing a non-empty tool_call_id")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("action claim is missing a tool name")
    if kind == "call" and not arguments_supplied:
        arguments = {}
        arguments_supplied = True
    if arguments_supplied and not isinstance(arguments, dict):
        raise ValueError("action claim has non-object arguments")
    return ActionClaim(
        kind=kind,
        call_id_digest=_canonical_digest(call_id.strip()),
        tool_digest=_canonical_digest(tool.strip()),
        arguments_digest=(
            _canonical_digest(arguments) if arguments_supplied else None
        ),
        arguments_supplied=arguments_supplied,
    )


def action_claims_from_endpoint_events(events: Any) -> list[ActionClaim]:
    """Capture private claims from raw endpoint events before redaction."""
    if not isinstance(events, list):
        return []
    claims: list[ActionClaim] = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue
        role = event.get("role")
        if role not in {"tool_call", "tool_result"}:
            continue
        kind: Literal["call", "result"] = (
            "call" if role == "tool_call" else "result"
        )
        arguments_supplied = "tool_args" in event if kind == "result" else True
        try:
            claims.append(
                make_action_claim(
                    kind=kind,
                    call_id=event.get("tool_call_id"),
                    tool=event.get("tool_name"),
                    arguments=(
                        event.get("tool_args")
                        if "tool_args" in event
                        else {}
                    ),
                    arguments_supplied=arguments_supplied,
                )
            )
        except ValueError as exc:
            raise ValueError(
                f"target action event {index} is invalid: {exc}"
            ) from exc
    return claims
