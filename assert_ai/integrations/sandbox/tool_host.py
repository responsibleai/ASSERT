# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""A sandbox tool host that emits Agent Hooks-shaped contexts around tool calls."""
from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from .agent_hooks_context import AgentHooksContextBuilder
from .records import MediationDecision, MediationRecord

ToolImpl = Callable[[dict[str, Any]], Any]


def _canonicalize_tool_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Create the one JSON-native argument snapshot used by every boundary."""
    raw_args = {} if args is None else args
    if not isinstance(raw_args, dict):
        raise ValueError("tool arguments must be a JSON object")
    try:
        encoded = json.dumps(raw_args, ensure_ascii=False, allow_nan=False)
        canonical = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("tool arguments must contain only JSON-native values") from exc
    if not isinstance(canonical, dict):
        raise ValueError("tool arguments must be a JSON object")
    return canonical


class Mediator(Protocol):
    def mediate(
        self,
        pre_context: dict[str, Any],
        execute_effective: Callable[[dict[str, Any]], Any],
    ) -> MediationDecision: ...


class AgentHooksToolHost:
    """Framework-neutral tool host for sandboxed action mediation.

    This is the boundary that an MCP server, HTTP tool router, or framework adapter
    can call. It emits Agent Hooks-shaped pre/post tool contexts, delegates the
    decision to the action mediator, optionally executes the real sandbox tool,
    and records mediation evidence.
    """

    def __init__(
        self,
        *,
        tools: Mapping[str, ToolImpl],
        mediator: Mediator,
        agent_id: str,
        session_id: str,
        case_id: str | None = None,
        framework: str = "openclaw-mcp-sandbox",
    ) -> None:
        self._tools = dict(tools)
        self._mediator = mediator
        self._builder = AgentHooksContextBuilder(
            agent_id=agent_id,
            framework=framework,
            session_id=session_id,
            case_id=case_id,
        )
        self.records: list[MediationRecord] = []

    def call_tool(self, name: str, args: dict[str, Any] | None = None) -> Any:
        args = _canonicalize_tool_args(args)
        call_id = f"tc-{int(time.time() * 1000):x}-{secrets.token_hex(3)}"
        pre = self._builder.pre_tool_call(call_id=call_id, name=name, args=args)
        impl = self._tools.get(name)

        class TrackedExecutor:
            real_executed = False

            def __call__(self, effective_args: dict[str, Any]) -> Any:
                if impl is None:
                    return {"status": "not_found", "message": f"No tool named {name}"}
                self.real_executed = True
                return impl(dict(effective_args))

        execute_effective = TrackedExecutor()

        decision = self._mediator.mediate(pre, execute_effective)
        if decision.mode == "pass" and not execute_effective.real_executed:
            # The policy allowed the request, but no implementation existed to
            # execute. Do not claim a real side effect occurred merely because
            # the mediation mode was pass-through.
            decision = replace(
                decision,
                real_executed=False,
                is_error=True,
            )
        extension = {"action_mediation": decision.evidence()}
        post = self._builder.post_tool_call(
            call_id=call_id,
            name=name,
            args=args,
            value=decision.returned,
            is_error=decision.is_error,
            extensions=extension,
        )
        record = MediationRecord(
            tool=name,
            args=args,
            decision=decision,
            pre_context=pre,
            post_context=post,
            id=call_id,
            extensions=extension,
        )
        self.records.append(record)
        return decision.returned
