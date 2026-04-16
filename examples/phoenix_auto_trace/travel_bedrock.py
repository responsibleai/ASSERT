"""Travel planner — AWS Bedrock (Anthropic Claude on AWS).

Instrumentation: 2 lines. Agent code: standard Bedrock via boto3.
Traces captured: LLM calls, tool use, token counts, latency.
"""

from __future__ import annotations

# pip install openinference-instrumentation-bedrock arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import json
import boto3

client = boto3.client("bedrock-runtime", region_name="us-east-1")

TOOLS = [
    {
        "toolSpec": {
            "name": "search_flights",
            "description": "Search for flights to a destination",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "destination": {"type": "string"},
                        "max_price": {"type": "number"},
                    },
                    "required": ["destination"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_hotels",
            "description": "Search for hotels in a city",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "max_price_per_night": {"type": "number"},
                    },
                    "required": ["city"],
                }
            },
        }
    },
]

SYSTEM_PROMPT = (
    "You are a travel planning assistant. Help users plan trips by searching "
    "for flights and hotels. Stay within their budget."
)


def _simulate_tool(name: str, args: dict) -> str:
    if name == "search_flights":
        return json.dumps([
            {"airline": "ANA", "price": 1180, "departure": "LAX→NRT"},
            {"airline": "JAL", "price": 1350, "departure": "LAX→HND"},
        ])
    elif name == "search_hotels":
        return json.dumps([
            {"name": "Hotel Granbell Shinjuku", "price_per_night": 145},
            {"name": "Mitsui Garden Ginza", "price_per_night": 195},
        ])
    return json.dumps({"error": f"Unknown tool: {name}"})


def chat(message: str) -> str:
    """Travel planner using AWS Bedrock Converse API."""
    messages = [{"role": "user", "content": [{"text": message}]}]

    response = client.converse(
        modelId="anthropic.claude-sonnet-4-20250514-v1:0",
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        toolConfig={"tools": TOOLS},
    )

    while response["stopReason"] == "tool_use":
        assistant_content = response["output"]["message"]["content"]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for block in assistant_content:
            if "toolUse" in block:
                tu = block["toolUse"]
                result = _simulate_tool(tu["name"], tu["input"])
                tool_results.append({
                    "toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [{"json": json.loads(result)}],
                    }
                })

        messages.append({"role": "user", "content": tool_results})
        response = client.converse(
            modelId="anthropic.claude-sonnet-4-20250514-v1:0",
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={"tools": TOOLS},
        )

    for block in response["output"]["message"]["content"]:
        if "text" in block:
            return block["text"]
    return ""


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
