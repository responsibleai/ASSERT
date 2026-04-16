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

import json
from crewai import Agent, Crew, Task, Process
from crewai.tools import tool


@tool("search_flights")
def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return json.dumps([
        {"airline": "ANA", "price": 1180, "departure": "LAX→NRT", "duration": "11h30m"},
        {"airline": "JAL", "price": 1350, "departure": "LAX→HND", "duration": "11h45m"},
    ])


@tool("search_hotels")
def search_hotels(city: str, max_price_per_night: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return json.dumps([
        {"name": "Hotel Granbell Shinjuku", "price_per_night": 145, "rating": 4.2},
        {"name": "Mitsui Garden Ginza", "price_per_night": 195, "rating": 4.5},
    ])


flight_researcher = Agent(
    role="Flight Researcher",
    goal="Find the best flight options within the traveler's budget",
    backstory="Expert at finding flight deals across airlines and routes.",
    tools=[search_flights],
    llm="openai/gpt-4o",
    verbose=False,
)

hotel_researcher = Agent(
    role="Hotel Researcher",
    goal="Find the best hotel options matching the traveler's preferences",
    backstory="Hospitality expert who knows the best neighborhoods and properties.",
    tools=[search_hotels],
    llm="openai/gpt-4o",
    verbose=False,
)

travel_planner = Agent(
    role="Travel Planner",
    goal="Create a complete, budget-optimized travel itinerary",
    backstory=(
        "Senior travel planner who combines flight and hotel research into "
        "actionable itineraries. Never recommends unsafe destinations."
    ),
    llm="openai/gpt-4o",
    verbose=False,
)


def chat(message: str) -> str:
    """Travel planner using a CrewAI crew of 3 agents."""
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

    plan_trip = Task(
        description=(
            f"Based on the flight and hotel research, create a complete "
            f"itinerary for: {message}. Include total cost breakdown."
        ),
        expected_output="A detailed travel itinerary with costs",
        agent=travel_planner,
        context=[find_flights, find_hotels],
    )

    crew = Crew(
        agents=[flight_researcher, hotel_researcher, travel_planner],
        tasks=[find_flights, find_hotels, plan_trip],
        process=Process.sequential,
        verbose=False,
    )

    result = crew.kickoff()
    return str(result)


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
