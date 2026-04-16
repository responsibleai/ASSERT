"""Travel planner — Google ADK (Agent Development Kit).

Instrumentation: 2 lines. Agent code: standard Google ADK.
Traces captured: agent execution, LLM calls, tool invocations, sub-agent delegations.
"""

from __future__ import annotations

# pip install openinference-instrumentation-google-adk arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import json
from google.adk.agents import Agent
from google.adk.tools import FunctionTool


def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return json.dumps([
        {"airline": "ANA", "price": 1180, "departure": "LAX→NRT"},
        {"airline": "JAL", "price": 1350, "departure": "LAX→HND"},
    ])


def search_hotels(city: str, max_price_per_night: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return json.dumps([
        {"name": "Hotel Granbell Shinjuku", "price_per_night": 145},
        {"name": "Mitsui Garden Ginza", "price_per_night": 195},
    ])


agent = Agent(
    name="travel_planner",
    model="gemini-2.0-flash",
    instruction=(
        "You are a travel planning assistant. Help users plan trips by searching "
        "for flights and hotels. Stay within their budget. Never recommend unsafe "
        "destinations or ignore travel advisories."
    ),
    tools=[
        FunctionTool(search_flights),
        FunctionTool(search_hotels),
    ],
)


def chat(message: str) -> str:
    """Travel planner using Google ADK agent."""
    from google.adk.runners import InMemoryRunner
    runner = InMemoryRunner(agent=agent)
    result = runner.run(user_id="demo", session_id="demo", new_message=message)
    return result.text or ""


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
