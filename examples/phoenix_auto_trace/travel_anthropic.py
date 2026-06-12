# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — Anthropic Claude (native tool use).

Instrumentation: central helper call. Agent code: standard Anthropic SDK.
Traces captured: LLM calls, tool use blocks, token counts, latency.
"""

# Optional Phoenix export: pip install openinference-instrumentation-anthropic arize-phoenix-otel
from assert_ai import auto_trace
auto_trace.enable()

import json
import anthropic
from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT, ANTHROPIC_TOOLS

client = anthropic.Anthropic()


def chat(message: str) -> str:
    """Multi-round travel planner using Anthropic Claude with tool use."""
    messages = [{"role": "user", "content": message}]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=ANTHROPIC_TOOLS,
        messages=messages,
    )

    while response.stop_reason == "tool_use":
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in tool_blocks:
            result = simulate_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=ANTHROPIC_TOOLS,
            messages=messages,
        )

    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(text_blocks)


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
