"""Travel planner — smolagents (HuggingFace tool-calling agent).

Instrumentation: 2 lines. Agent code: standard smolagents.
Traces captured: agent steps, tool calls, LLM calls, reasoning traces, token counts.
"""

from __future__ import annotations

# pip install openinference-instrumentation-smolagents arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import os

from dotenv import load_dotenv
load_dotenv()

from smolagents import ToolCallingAgent, tool, LiteLLMModel  # noqa: E402

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT  # noqa: E402

_MODEL = os.environ.get("ASSERT_TARGET_MODEL", "azure/gpt-5.4-mini")

model = LiteLLMModel(model_id=_MODEL)


# ── Tools (simulated via shared module) ───────────────────────

@tool
def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget.

    Args:
        destination: Destination city.
        max_price: Maximum price in USD.
    """
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


@tool
def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city within nightly budget.

    Args:
        city: City name.
        max_nightly_rate: Maximum nightly rate in USD.
    """
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


@tool
def check_weather(city: str) -> str:
    """Check weather forecast for a destination city.

    Args:
        city: City name.
    """
    return simulate_tool("check_weather", {"city": city})


@tool
def check_travel_advisories(country: str) -> str:
    """Check visa requirements, safety advisories, and health precautions.

    Args:
        country: Destination country.
    """
    return simulate_tool("check_travel_advisories", {"country": country})


@tool
def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget.

    Args:
        flight_cost: Flight cost in USD.
        hotel_cost: Total hotel cost in USD.
        other_costs: Other costs in USD.
        budget: Total budget in USD.
    """
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


# ── Agent ─────────────────────────────────────────────────────

agent = ToolCallingAgent(
    tools=[search_flights, search_hotels, check_weather,
           check_travel_advisories, validate_budget],
    model=model,
    instructions=SYSTEM_PROMPT,
    verbosity_level=0,
)


def chat(message: str) -> str:
    """Travel planner using smolagents ToolCallingAgent."""
    return str(agent.run(message))


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
