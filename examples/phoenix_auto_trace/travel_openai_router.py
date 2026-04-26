"""Travel planner — model-router (SWC endpoint, multi-model ensemble)."""
from phoenix.otel import register
register(auto_instrument=True)

import json
import os
from dotenv import load_dotenv
load_dotenv()

from openai import AzureOpenAI  # noqa: E402
from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT, OPENAI_TOOLS  # noqa: E402

client = AzureOpenAI(
    api_key=os.environ["AZURE_API_KEY_SWC"],
    azure_endpoint=os.environ["AZURE_API_BASE_SWC"],
    api_version="2024-12-01-preview",
)


def chat(message: str) -> str:
    """Multi-round travel planner using model-router."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]
    response = client.chat.completions.create(
        model="model-router",
        messages=messages,
        tools=OPENAI_TOOLS,
        tool_choice="auto",
    )
    msg = response.choices[0].message

    # Tool call loop (max 10 rounds)
    for _ in range(10):
        if not msg.tool_calls:
            break
        messages.append(msg)
        for tc in msg.tool_calls:
            result = simulate_tool(tc.function.name, json.loads(tc.function.arguments))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })
        response = client.chat.completions.create(
            model="model-router",
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

    return msg.content or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
