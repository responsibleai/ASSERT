# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — Haystack (pipeline with tool-calling loop).

Instrumentation: central helper call. Agent code: standard Haystack 2.x.
Traces captured: pipeline runs, LLM calls, tool invocations, token counts, latency.
"""

from __future__ import annotations

# Optional Phoenix export: pip install openinference-instrumentation-haystack arize-phoenix-otel
from assert_ai import auto_trace
auto_trace.enable()

import os

from dotenv import load_dotenv
load_dotenv()

from haystack.components.generators.chat import OpenAIChatGenerator  # noqa: E402
from haystack.dataclasses import ChatMessage  # noqa: E402
from haystack.tools import Tool  # noqa: E402
from haystack.utils import Secret  # noqa: E402

from examples.phoenix_auto_trace._tools import (  # noqa: E402
    simulate_tool, SYSTEM_PROMPT, OPENAI_TOOLS,
)

_MODEL = os.environ.get("ASSERT_TARGET_MODEL_SHORT", "gpt-4o-mini")


# ── Build Haystack Tool objects from shared OPENAI_TOOLS ──────

def _make_tool_fn(name: str):
    """Create a closure that dispatches to simulate_tool."""
    def fn(**kwargs):
        return simulate_tool(name, kwargs)
    fn.__name__ = name
    return fn


haystack_tools = [
    Tool(
        name=t["function"]["name"],
        description=t["function"]["description"],
        parameters=t["function"]["parameters"],
        function=_make_tool_fn(t["function"]["name"]),
    )
    for t in OPENAI_TOOLS
]

_tool_map = {t.name: t for t in haystack_tools}


# ── Generator ─────────────────────────────────────────────────

def _get_generator():
    """Return an Azure or OpenAI Haystack chat generator."""
    if os.environ.get("AZURE_API_KEY") and os.environ.get("AZURE_API_BASE"):
        from haystack.components.generators.chat import AzureOpenAIChatGenerator
        return AzureOpenAIChatGenerator(
            azure_deployment=_MODEL,
            azure_endpoint=os.environ["AZURE_API_BASE"],
            api_key=Secret.from_env_var("AZURE_API_KEY"),
            tools=haystack_tools,
        )
    return OpenAIChatGenerator(model=_MODEL, tools=haystack_tools)


generator = _get_generator()


# ── Chat implementation ───────────────────────────────────────

def chat(message: str) -> str:
    """Travel planner using Haystack pipeline with tool-calling loop."""
    messages = [
        ChatMessage.from_system(SYSTEM_PROMPT),
        ChatMessage.from_user(message),
    ]

    result = generator.run(messages=messages)
    reply = result["replies"][0]

    while reply.tool_calls:
        messages.append(reply)
        for tc in reply.tool_calls:
            tool_result = simulate_tool(tc.tool_name, tc.arguments)
            messages.append(ChatMessage.from_tool(
                tool_result=tool_result, origin=tc,
            ))
        result = generator.run(messages=messages)
        reply = result["replies"][0]

    return reply.text or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
