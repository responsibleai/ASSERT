# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — Portkey AI Gateway.

Instrumentation: 2 lines. Agent code: Portkey SDK (OpenAI-compatible).
Traces captured: LLM calls, gateway routing, fallbacks, token counts, latency.
"""

# pip install openinference-instrumentation-portkey arize-phoenix-otel
from assert_ai import auto_trace
auto_trace()

import json
from portkey_ai import Portkey
from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT, OPENAI_TOOLS

client = Portkey(provider="openai")


def chat(message: str) -> str:
    """Multi-round travel planner via Portkey AI gateway."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=OPENAI_TOOLS,
        tool_choice="auto",
    )
    msg = response.choices[0].message

    while msg.tool_calls:
        messages.append(msg)
        for tc in msg.tool_calls:
            result = simulate_tool(tc.function.name, json.loads(tc.function.arguments))
            messages.append({
                "tool_call_id": tc.id,
                "role": "tool",
                "content": result,
            })

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

    return msg.content or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
