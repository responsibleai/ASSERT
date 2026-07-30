# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""MCP-style tool-call adapter for the sandbox tool host.

The current preview does not run a full MCP stdio server. This adapter is the
thin boundary that such a server would call from its `CallTool` handler:
normalize the MCP request shape, delegate to `AgentHooksToolHost`, and return the
sandboxed tool result. Keeping this seam small makes the later real-stdio server
mechanical.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .tool_host import AgentHooksToolHost


class MCPRequestError(ValueError):
    pass


def normalize_mcp_call(request: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Normalize MCP object-shaped calls into `(tool_name, arguments)`.

    Accepts the common MCP keys used across SDKs/adapters:
    - `name`, `tool`, or `toolName`
    - `arguments`, `args`, or `input`
    """
    name = request.get("name") or request.get("tool") or request.get("toolName")
    if not isinstance(name, str) or not name:
        raise MCPRequestError("MCP request must include string name/tool/toolName")
    raw_args = request.get("arguments", request.get("args", request.get("input", {})))
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, Mapping):
        raise MCPRequestError("MCP request arguments/args/input must be an object")
    return name, dict(raw_args)


class MCPToolHostAdapter:
    """Adapter from MCP CallTool request objects to AgentHooksToolHost."""

    def __init__(self, host: AgentHooksToolHost) -> None:
        self.host = host

    def call_tool(self, request: Mapping[str, Any]) -> Any:
        name, args = normalize_mcp_call(request)
        return self.host.call_tool(name, args)
