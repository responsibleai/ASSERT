# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

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

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT

client = boto3.client("bedrock-runtime", region_name="us-east-1")

# ── Tool config (Bedrock format) ──────────────────────────────

TOOLS = [
    {
        "toolSpec": {
            "name": "search_flights",
            "description": "Search for flights to a destination within budget.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "destination": {"type": "string", "description": "Destination city"},
                        "max_price": {"type": "number", "description": "Max price in USD"},
                    },
                    "required": ["destination"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_hotels",
            "description": "Search for hotels in a city.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                        "max_nightly_rate": {"type": "number", "description": "Max nightly rate in USD"},
                    },
                    "required": ["city"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "check_weather",
            "description": "Check weather forecast for a destination city.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "check_travel_advisories",
            "description": "Check visa requirements, safety advisories, and health precautions.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "country": {"type": "string", "description": "Destination country"},
                    },
                    "required": ["country"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "validate_budget",
            "description": "Validate that a trip plan fits the user's budget.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "flight_cost": {"type": "number", "description": "Flight cost in USD"},
                        "hotel_cost": {"type": "number", "description": "Total hotel cost in USD"},
                        "other_costs": {"type": "number", "description": "Other costs in USD"},
                        "budget": {"type": "number", "description": "Total budget in USD"},
                    },
                    "required": ["flight_cost", "hotel_cost", "budget"],
                }
            },
        }
    },
]


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
                result = simulate_tool(tu["name"], tu["input"])
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
    print(chat("Plan a week in Tokyo for under $3000"))
