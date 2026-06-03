# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — OpenAI direct (function calling).

Instrumentation: 2 lines. Agent code: standard OpenAI SDK.
Traces captured: LLM calls, tool calls with args/results, token counts, latency.
"""

# pip install openinference-instrumentation-openai arize-phoenix-otel
from assert_ai import auto_trace
auto_trace()

import json
import os

from dotenv import load_dotenv
load_dotenv()

from openai import AzureOpenAI, OpenAI  # noqa: E402
from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT, OPENAI_TOOLS  # noqa: E402

_MODEL = os.environ.get("ASSERT_TARGET_MODEL", "gpt-4o-mini")


def _get_client():
    """Return AzureOpenAI client when Azure env vars are set, else OpenAI."""
    if os.environ.get("AZURE_API_KEY") and os.environ.get("AZURE_API_BASE"):
        return AzureOpenAI(
            api_key=os.environ["AZURE_API_KEY"],
            azure_endpoint=os.environ["AZURE_API_BASE"],
            api_version="2024-12-01-preview",
        )
    return OpenAI()


client = _get_client()


def chat(message: str) -> str:
    """Multi-round travel planner using OpenAI function calling."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    response = client.chat.completions.create(
        model=_MODEL,
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
            model=_MODEL,
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

    return msg.content or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
