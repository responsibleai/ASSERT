# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — LlamaIndex (ReAct agent).

Instrumentation: central helper call. Agent code: standard LlamaIndex.
Traces captured: LLM calls, tool invocations,
response synthesis, token counts.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Central helper instrumentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Optional Phoenix export: pip install openinference-instrumentation-llama-index arize-phoenix-otel
from assert_ai import auto_trace  # noqa: E402
auto_trace.enable()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — standard LlamaIndex
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os

from dotenv import load_dotenv
load_dotenv()

from llama_index.core import Settings
from llama_index.core.tools import FunctionTool
from llama_index.core.agent import ReActAgent
from llama_index.llms.openai import OpenAI

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT

_MODEL = os.environ.get("ASSERT_TARGET_MODEL_SHORT", "gpt-4o-mini")

Settings.llm = OpenAI(
    model=_MODEL,
    temperature=0,
    api_key=os.environ.get("AZURE_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
    api_base=os.environ.get("AZURE_API_BASE", None),
)


# ── Tools (simulated via shared module) ───────────────────────

def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


def check_weather(city: str) -> str:
    """Check weather forecast for a destination city."""
    return simulate_tool("check_weather", {"city": city})


def check_travel_advisories(region: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"region": region})


def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


flight_tool = FunctionTool.from_defaults(fn=search_flights)
hotel_tool = FunctionTool.from_defaults(fn=search_hotels)
weather_tool = FunctionTool.from_defaults(fn=check_weather)
advisory_tool = FunctionTool.from_defaults(fn=check_travel_advisories)
budget_tool = FunctionTool.from_defaults(fn=validate_budget)

agent = ReActAgent.from_tools(
    tools=[flight_tool, hotel_tool, weather_tool, advisory_tool, budget_tool],
    llm=OpenAI(
        model=_MODEL,
        temperature=0,
        api_key=os.environ.get("AZURE_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
        api_base=os.environ.get("AZURE_API_BASE", None),
    ),
    system_prompt=SYSTEM_PROMPT,
    verbose=False,
)


def chat(message: str) -> str:
    """Travel planner using LlamaIndex ReAct agent."""
    response = agent.chat(message)
    return str(response)


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
