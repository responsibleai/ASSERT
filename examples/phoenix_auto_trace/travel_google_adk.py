# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — Google ADK (Agent Development Kit).

Instrumentation: 2 lines. Agent code: standard Google ADK.
Traces captured: agent execution, LLM calls, tool invocations, sub-agent delegations.
"""

from __future__ import annotations

# pip install openinference-instrumentation-google-adk arize-phoenix-otel
from assert_ai import auto_trace
auto_trace()

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT


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


def check_travel_advisories(country: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"country": country})


def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


agent = Agent(
    name="travel_planner",
    model="gemini-2.0-flash",
    instruction=SYSTEM_PROMPT,
    tools=[
        FunctionTool(search_flights),
        FunctionTool(search_hotels),
        FunctionTool(check_weather),
        FunctionTool(check_travel_advisories),
        FunctionTool(validate_budget),
    ],
)


def chat(message: str) -> str:
    """Travel planner using Google ADK agent."""
    from google.adk.runners import InMemoryRunner
    runner = InMemoryRunner(agent=agent)
    result = runner.run(user_id="demo", session_id="demo", new_message=message)
    return result.text or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
