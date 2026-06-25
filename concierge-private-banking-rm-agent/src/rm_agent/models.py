"""Minimal data shapes for the two-tool RM agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Uniform return from both `lookup` and `draft`.

    `data` carries tool-specific structured output. `blocked=True` means the
    tool refused to act and `message` explains why. The agent and the guarded
    runtime both consume this shape.
    """

    ok: bool = True
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    blocked: bool = False
    blocked_reason: str | None = None
