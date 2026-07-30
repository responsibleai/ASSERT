# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""A sandbox tool host that emits Agent Hooks-shaped contexts around tool calls."""
from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

from .agent_hooks_context import AgentHooksContextBuilder
from .mediator import ActionMediator
from .records import MediationRecord

ToolImpl = Callable[[dict[str, Any]], Any]


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
        mediator: ActionMediator,
        agent_id: str,
        session_id: str,
        framework: str = "openclaw-mcp-sandbox",
    ) -> None:
        self._tools = dict(tools)
        self._mediator = mediator
        self._builder = AgentHooksContextBuilder(
            agent_id=agent_id,
            framework=framework,
            session_id=session_id,
        )
        self.records: list[MediationRecord] = []

    def call_tool(self, name: str, args: dict[str, Any] | None = None) -> Any:
        args = dict(args or {})
        call_id = f"tc-{int(time.time() * 1000):x}-{secrets.token_hex(3)}"
        pre = self._builder.pre_tool_call(call_id=call_id, name=name, args=args)

        def execute_effective(effective_args: dict[str, Any]) -> Any:
            impl = self._tools.get(name)
            if impl is None:
                return {"status": "not_found", "message": f"No tool named {name}"}
            return impl(dict(effective_args))

        decision = self._mediator.mediate(pre, execute_effective)
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
