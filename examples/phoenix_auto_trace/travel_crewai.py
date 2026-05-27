# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — CrewAI (multi-agent crew).

Instrumentation: 2 lines. Agent code: standard CrewAI.
Traces captured: agent delegations, LLM calls per agent, tool invocations,
crew execution flow, token counts.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2 lines of instrumentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pip install openinference-instrumentation-crewai arize-phoenix-otel
from phoenix.otel import register  # noqa: E402
register(auto_instrument=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — standard CrewAI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from crewai import Agent, Crew, Task, Process
from crewai.tools import tool

import os
from dotenv import load_dotenv
load_dotenv()

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT

_MODEL = os.environ.get("P2M_TARGET_MODEL", "azure/gpt-4.1-nano")


def _get_crewai_llm():
    """Build a CrewAI-compatible LLM via LiteLLM."""
    from crewai import LLM
    return LLM(model=_MODEL, temperature=0)


# ── Tools (simulated via shared module) ───────────────────────

@tool("search_flights")
def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


@tool("search_hotels")
def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


@tool("check_weather")
def check_weather(city: str) -> str:
    """Check weather forecast for a destination city."""
    return simulate_tool("check_weather", {"city": city})


@tool("check_travel_advisories")
def check_travel_advisories(country: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"country": country})


@tool("validate_budget")
def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


# ── Agents ────────────────────────────────────────────────────

flight_researcher = Agent(
    role="Flight Researcher",
    goal="Find the best flight options within the traveler's budget",
    backstory="Expert at finding flight deals across airlines and routes.",
    tools=[search_flights],
    llm=_get_crewai_llm(),
    verbose=False,
)

hotel_researcher = Agent(
    role="Hotel Researcher",
    goal="Find the best hotel options matching the traveler's preferences",
    backstory="Hospitality expert who knows the best neighborhoods and properties.",
    tools=[search_hotels],
    llm=_get_crewai_llm(),
    verbose=False,
)

safety_advisor = Agent(
    role="Safety Advisor",
    goal="Assess weather conditions and travel advisories for the destination",
    backstory="Risk assessment specialist who ensures travelers are well-prepared for their destination.",
    tools=[check_weather, check_travel_advisories],
    llm=_get_crewai_llm(),
    verbose=False,
)

travel_planner = Agent(
    role="Travel Planner",
    goal="Create a complete, budget-optimized travel itinerary",
    backstory=(
        "Senior travel planner who combines flight, hotel, safety research and "
        "budget validation into actionable itineraries."
    ),
    tools=[validate_budget],
    llm=_get_crewai_llm(),
    verbose=False,
)


def chat(message: str) -> str:
    """Travel planner using a CrewAI crew of 4 agents."""
    find_flights = Task(
        description=f"Find flight options for: {message}",
        expected_output="A list of flight options with prices and details",
        agent=flight_researcher,
    )

    find_hotels = Task(
        description=f"Find hotel options for: {message}",
        expected_output="A list of hotel options with prices and ratings",
        agent=hotel_researcher,
    )

    assess_safety = Task(
        description=f"Check weather and travel advisories for: {message}",
        expected_output="Weather forecast, visa requirements, safety level, and health precautions",
        agent=safety_advisor,
    )

    plan_trip = Task(
        description=(
            f"Based on the flight, hotel, and safety research, create a complete "
            f"itinerary for: {message}. Validate the budget and include total cost breakdown."
        ),
        expected_output="A detailed travel itinerary with costs and safety information",
        agent=travel_planner,
        context=[find_flights, find_hotels, assess_safety],
    )

    crew = Crew(
        agents=[flight_researcher, hotel_researcher, safety_advisor, travel_planner],
        tasks=[find_flights, find_hotels, assess_safety, plan_trip],
        process=Process.sequential,
        verbose=False,
    )

    result = crew.kickoff()
    return str(result)


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
