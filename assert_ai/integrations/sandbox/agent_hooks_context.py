# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Minimal Agent Hooks 0.1 context builder for tool-host integration.

This intentionally emits the Agent Hooks wire shape for `pre_tool_call` and
`post_tool_call` without claiming full CTK conformance for the host framework.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SPEC_VERSION = "agent-hooks/0.1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def case_id_from_context(context: dict[str, Any]) -> str | None:
    """Return one unambiguous case identity from an Agent Hooks context.

    ``session.case_id`` is the current wire location. A top-level ``case_id``
    remains accepted for older adapters, but the two forms may never disagree:
    mock selection and evidence must describe the same ASSERT case.
    """

    def _optional_case_id(value: Any, location: str) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{location} case_id must be a non-empty string")
        return value.strip()

    top_level = _optional_case_id(context.get("case_id"), "top-level")
    session = context.get("session") or {}
    if not isinstance(session, dict):
        raise ValueError("session must be an object when resolving case_id")
    session_case = _optional_case_id(session.get("case_id"), "session")
    if top_level is not None and session_case is not None and top_level != session_case:
        raise ValueError(
            "conflicting case_id values in top-level and session context"
        )
    return session_case or top_level


class AgentHooksContextBuilder:
    """Build Agent Hooks-shaped contexts for a single sandbox tool session."""

    def __init__(
        self,
        *,
        agent_id: str,
        framework: str,
        session_id: str,
        case_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        self._agent = {"id": agent_id, "framework": framework}
        if agent_name:
            self._agent["name"] = agent_name
        self._session = {"id": session_id}
        if case_id:
            self._session["case_id"] = case_id
        self._sequence = 0

    def _base(self, point: str, target: Any) -> dict[str, Any]:
        ctx = {
            "spec": SPEC_VERSION,
            "interception_point": point,
            "timestamp": _now(),
            "sequence": self._sequence,
            "agent": dict(self._agent),
            "session": dict(self._session),
            "target": target,
        }
        self._sequence += 1
        return ctx

    def pre_tool_call(
        self,
        *,
        call_id: str,
        name: str,
        args: dict[str, Any],
        extensions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tool_call = {"id": call_id, "name": name, "args": dict(args)}
        ctx = self._base("pre_tool_call", tool_call["args"])
        ctx["tool_call"] = tool_call
        if extensions:
            ctx["extensions"] = extensions
        return ctx

    def post_tool_call(
        self,
        *,
        call_id: str,
        name: str,
        args: dict[str, Any],
        value: Any,
        is_error: bool = False,
        extensions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = self._base("post_tool_call", value)
        ctx["tool_call"] = {"id": call_id, "name": name, "args": dict(args)}
        ctx["tool_result"] = {"value": value, "is_error": is_error}
        if extensions:
            ctx["extensions"] = extensions
        return ctx
