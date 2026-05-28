# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — OpenAI Agents SDK (multi-agent orchestration).

Instrumentation: 2 lines. Agent code: standard openai-agents SDK.
Traces captured: agent runs, handoffs, tool calls, LLM completions, token counts.
"""

from __future__ import annotations

# pip install openinference-instrumentation-openai-agents arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import asyncio
import os

from dotenv import load_dotenv
load_dotenv()

from agents import Agent, Runner, function_tool, set_default_openai_client  # noqa: E402
from openai import AsyncAzureOpenAI  # noqa: E402

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT  # noqa: E402

_MODEL = os.environ.get("P2M_TARGET_MODEL_SHORT", "gpt-5.4-mini")

# Configure Azure when env vars are set; otherwise uses OPENAI_API_KEY
if os.environ.get("AZURE_API_KEY") and os.environ.get("AZURE_API_BASE"):
    _azure_client = AsyncAzureOpenAI(
        api_key=os.environ["AZURE_API_KEY"],
        azure_endpoint=os.environ["AZURE_API_BASE"],
        api_version="2024-12-01-preview",
    )
    set_default_openai_client(_azure_client)


# ── Tools (simulated via shared module) ───────────────────────

@function_tool
def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


@function_tool
def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


@function_tool
def check_weather(city: str) -> str:
    """Check weather forecast for a destination city."""
    return simulate_tool("check_weather", {"city": city})


@function_tool
def check_travel_advisories(country: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"country": country})


@function_tool
def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


# ── Agent ─────────────────────────────────────────────────────

travel_agent = Agent(
    name="travel_planner",
    instructions=SYSTEM_PROMPT,
    model=_MODEL,
    tools=[search_flights, search_hotels, check_weather,
           check_travel_advisories, validate_budget],
)


async def _chat_async(message: str) -> str:
    result = await Runner.run(travel_agent, message)
    return result.final_output


def chat(message: str) -> str:
    """Travel planner using OpenAI Agents SDK."""
    return asyncio.run(_chat_async(message))


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
