# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — Groq (fast inference).

Instrumentation: 2 lines. Agent code: standard Groq SDK (OpenAI-compatible).
Traces captured: LLM calls, tool calls, token counts, latency.
"""

# pip install openinference-instrumentation-groq arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import json
from groq import Groq
from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT, OPENAI_TOOLS

client = Groq()


def chat(message: str) -> str:
    """Multi-round travel planner using Groq's fast inference."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
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
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

    return msg.content or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
