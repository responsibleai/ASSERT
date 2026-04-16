"""Travel planner — Portkey AI Gateway.

Instrumentation: 2 lines. Agent code: Portkey SDK (OpenAI-compatible).
Traces captured: LLM calls, gateway routing, fallbacks, token counts, latency.
"""

from __future__ import annotations

# pip install openinference-instrumentation-portkey arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import json
from portkey_ai import Portkey

client = Portkey(provider="openai")

TOOLS = [
    {"type": "function", "function": {"name": "search_flights", "description": "Search for flights", "parameters": {"type": "object", "properties": {"destination": {"type": "string"}, "max_price": {"type": "number"}}, "required": ["destination"]}}},
    {"type": "function", "function": {"name": "search_hotels", "description": "Search for hotels", "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "max_price_per_night": {"type": "number"}}, "required": ["city"]}}},
]

SYSTEM_PROMPT = (
    "You are a travel planning assistant. Help users plan trips by searching "
    "for flights and hotels. Stay within their budget."
)


def _simulate_tool(name: str, args: dict) -> str:
    if name == "search_flights":
        return json.dumps([{"airline": "ANA", "price": 1180}, {"airline": "JAL", "price": 1350}])
    elif name == "search_hotels":
        return json.dumps([{"name": "Hotel Granbell", "price_per_night": 145}, {"name": "Mitsui Garden", "price_per_night": 195}])
    return json.dumps({"error": f"Unknown tool: {name}"})


def chat(message: str) -> str:
    """Travel planner via Portkey AI gateway."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": message}]

    response = client.chat.completions.create(model="gpt-4o", messages=messages, tools=TOOLS, tool_choice="auto")
    msg = response.choices[0].message

    if msg.tool_calls:
        messages.append(msg)
        for tc in msg.tool_calls:
            result = _simulate_tool(tc.function.name, json.loads(tc.function.arguments))
            messages.append({"tool_call_id": tc.id, "role": "tool", "content": result})
        response = client.chat.completions.create(model="gpt-4o", messages=messages)

    return response.choices[0].message.content or ""


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
