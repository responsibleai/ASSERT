# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — MistralAI.

Instrumentation: 2 lines. Agent code: standard Mistral SDK.
Traces captured: LLM calls, tool calls, token counts, latency.
"""

# pip install openinference-instrumentation-mistralai arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import json
from mistralai import Mistral
from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT, OPENAI_TOOLS

client = Mistral()


def chat(message: str) -> str:
    """Multi-round travel planner using MistralAI with function calling."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    response = client.chat.complete(
        model="mistral-large-latest",
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
                "role": "tool",
                "name": tc.function.name,
                "content": result,
                "tool_call_id": tc.id,
            })

        response = client.chat.complete(
            model="mistral-large-latest",
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

    return msg.content or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
