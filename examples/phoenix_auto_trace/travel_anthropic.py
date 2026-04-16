"""Travel planner — Anthropic Claude (native tool use).

Instrumentation: 2 lines. Agent code: standard Anthropic SDK.
Traces captured: LLM calls, tool use blocks, token counts, latency.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2 lines of instrumentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pip install openinference-instrumentation-anthropic arize-phoenix-otel
from phoenix.otel import register  # noqa: E402
register(auto_instrument=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — standard Anthropic SDK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import json
import anthropic

client = anthropic.Anthropic()

TOOLS = [
    {
        "name": "search_flights",
        "description": "Search for flights to a destination",
        "input_schema": {
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
    {
        "name": "search_hotels",
        "description": "Search for hotels in a city",
        "input_schema": {
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
]

SYSTEM_PROMPT = (
    "You are a travel planning assistant. Help users plan trips by searching "
    "for flights and hotels. Stay within their budget. Never recommend unsafe "
    "destinations or ignore travel advisories."
)


def _simulate_tool(name: str, args: dict) -> str:
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
    """Travel planner using Anthropic Claude with tool use."""
    messages = [{"role": "user", "content": message}]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
    )

    # Process tool use blocks
    while response.stop_reason == "tool_use":
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in tool_blocks:
            result = _simulate_tool(block.name, block.input)
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
            tools=TOOLS,
            messages=messages,
        )

    # Extract final text
    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(text_blocks)


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
