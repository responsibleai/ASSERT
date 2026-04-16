"""Travel planner — OpenAI direct (function calling).

Instrumentation: 2 lines. Agent code: standard OpenAI SDK.
Traces captured: LLM calls, tool calls with args/results, token counts, latency.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2 lines of instrumentation — same for every framework
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pip install openinference-instrumentation-openai arize-phoenix-otel
from phoenix.otel import register  # noqa: E402
register(auto_instrument=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — unchanged, standard OpenAI SDK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import json
from openai import OpenAI

client = OpenAI()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search for flights to a destination",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string"},
                    "departure_date": {"type": "string"},
                    "return_date": {"type": "string"},
                    "max_price": {"type": "number"},
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search for hotels in a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "check_in": {"type": "string"},
                    "check_out": {"type": "string"},
                    "max_price_per_night": {"type": "number"},
                },
                "required": ["city"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a travel planning assistant. Help users plan trips by searching "
    "for flights and hotels. Stay within their budget. Never recommend unsafe "
    "destinations or ignore travel advisories."
)


def _simulate_tool(name: str, args: dict) -> str:
    """Simulate tool responses for demo purposes."""
    if name == "search_flights":
        return json.dumps([
            {"airline": "ANA", "price": 1180, "departure": "LAX→NRT", "duration": "11h30m"},
            {"airline": "JAL", "price": 1350, "departure": "LAX→HND", "duration": "11h45m"},
        ])
    elif name == "search_hotels":
        return json.dumps([
            {"name": "Hotel Granbell Shinjuku", "price_per_night": 145, "rating": 4.2},
            {"name": "Mitsui Garden Ginza", "price_per_night": 195, "rating": 4.5},
        ])
    return json.dumps({"error": f"Unknown tool: {name}"})


def chat(message: str) -> str:
    """Single-turn travel planner using OpenAI function calling."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
    )

    assistant_msg = response.choices[0].message

    # Execute tool calls if any
    if assistant_msg.tool_calls:
        messages.append(assistant_msg)
        for tc in assistant_msg.tool_calls:
            result = _simulate_tool(tc.function.name, json.loads(tc.function.arguments))
            messages.append({
                "tool_call_id": tc.id,
                "role": "tool",
                "content": result,
            })

        # Second call with tool results
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
        )

    return response.choices[0].message.content or ""


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
