"""Bank-manager agent with Agent Shield tool gates."""
from __future__ import annotations

import asyncio
from typing import Any

import agent_shield.adapters.langchain  # noqa: F401 - registers Shield.with_langchain().
from agent_shield import Shield
from langchain_openai import AzureChatOpenAI
from langgraph.errors import GraphRecursionError

from .agent import (
    AGENT_MODEL,
    DEFAULT_AGENT_MODEL,
    EXAMPLE_DIR,
    _run_mcp_agent,
    build_langgraph_agent,
)

GUARDRAILS_YAML = str(EXAMPLE_DIR / "guardrails.yaml")
GUARDED_RECURSION_LIMIT = 12
GUARDED_TIMEOUT_S = 120
GUARDED_BLOCKED_RESPONSE = (
    "I can't complete that banking action because the requested tool action "
    "was blocked by the banking policy guardrail."
)

__all__ = ["AGENT_MODEL", "DEFAULT_AGENT_MODEL", "chat_guarded"]


async def _invoke_guarded_agent(
    message: str,
    llm: AzureChatOpenAI,
    raw_tools: list[Any],
) -> object:
    shield = (
        Shield.from_yaml(GUARDRAILS_YAML)
        .with_langchain()
        .with_client(llm)
        .build()
    )
    guarded_tools = shield.protect_tools(raw_tools)
    native_agent = build_langgraph_agent(llm, guarded_tools)
    guarded_runner = shield.guard(native_agent)
    try:
        return await asyncio.wait_for(
            guarded_runner.run(
                message,
                config={"recursion_limit": GUARDED_RECURSION_LIMIT},
            ),
            timeout=GUARDED_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, GraphRecursionError):
        return GUARDED_BLOCKED_RESPONSE


def chat_guarded(message: str) -> str:
    """ASSERT callable: bank-manager agent with Agent Shield."""
    return asyncio.run(_run_mcp_agent(message, _invoke_guarded_agent))
