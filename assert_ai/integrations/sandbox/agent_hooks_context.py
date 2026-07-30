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


class AgentHooksContextBuilder:
    """Build Agent Hooks-shaped contexts for a single sandbox tool session."""

    def __init__(
        self,
        *,
        agent_id: str,
        framework: str,
        session_id: str,
        agent_name: str | None = None,
    ) -> None:
        self._agent = {"id": agent_id, "framework": framework}
        if agent_name:
            self._agent["name"] = agent_name
        self._session = {"id": session_id}
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
