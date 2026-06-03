# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — DSPy (declarative signatures).

Instrumentation: 2 lines. Agent code: standard DSPy.
Traces captured: module calls, LLM calls with signatures, optimization steps.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2 lines of instrumentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pip install openinference-instrumentation-dspy arize-phoenix-otel
from assert_ai import auto_trace  # noqa: E402
auto_trace()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — standard DSPy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import dspy
import os

from dotenv import load_dotenv
load_dotenv()

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT

_MODEL = os.environ.get("ASSERT_TARGET_MODEL", "azure/gpt-4o-mini")

lm = dspy.LM(_MODEL, temperature=0)
dspy.configure(lm=lm)


# ── Tool wrappers ─────────────────────────────────────────────

def _search_flights(destination: str) -> str:
    return simulate_tool("search_flights", {"destination": destination})


def _search_hotels(city: str) -> str:
    return simulate_tool("search_hotels", {"city": city})


def _check_weather(city: str) -> str:
    return simulate_tool("check_weather", {"city": city})


def _check_travel_advisories(country: str) -> str:
    return simulate_tool("check_travel_advisories", {"country": country})


def _validate_budget(flight_cost: float, hotel_cost: float, other_costs: float, budget: float) -> str:
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


# ── DSPy Signatures ──────────────────────────────────────────

class ExtractTravelIntent(dspy.Signature):
    """Extract travel parameters from a user request."""
    request: str = dspy.InputField(desc="User's travel planning request")
    destination: str = dspy.OutputField(desc="Travel destination city")
    country: str = dspy.OutputField(desc="Destination country")
    duration_days: int = dspy.OutputField(desc="Trip duration in days")
    budget: float = dspy.OutputField(desc="Total budget in USD")


class PlanItinerary(dspy.Signature):
    """Create a travel itinerary from search results."""
    destination: str = dspy.InputField()
    duration_days: int = dspy.InputField()
    budget: float = dspy.InputField()
    flights: str = dspy.InputField(desc="Available flight options as JSON")
    hotels: str = dspy.InputField(desc="Available hotel options as JSON")
    weather: str = dspy.InputField(desc="Weather forecast as JSON")
    advisories: str = dspy.InputField(desc="Travel advisories as JSON")
    budget_check: str = dspy.InputField(desc="Budget validation result as JSON")
    itinerary: str = dspy.OutputField(desc="Complete travel itinerary with costs and safety info")


class TravelPlanner(dspy.Module):
    def __init__(self):
        self.extract = dspy.Predict(ExtractTravelIntent)
        self.plan = dspy.Predict(PlanItinerary)

    def forward(self, request: str) -> str:
        intent = self.extract(request=request)

        flights = _search_flights(intent.destination)
        hotels = _search_hotels(intent.destination)
        weather = _check_weather(intent.destination)
        advisories = _check_travel_advisories(intent.country)
        budget_check = _validate_budget(
            flight_cost=1180, hotel_cost=145 * intent.duration_days,
            other_costs=0, budget=intent.budget,
        )

        result = self.plan(
            destination=intent.destination,
            duration_days=intent.duration_days,
            budget=intent.budget,
            flights=flights,
            hotels=hotels,
            weather=weather,
            advisories=advisories,
            budget_check=budget_check,
        )
        return result.itinerary


planner = TravelPlanner()


def chat(message: str) -> str:
    """Travel planner using DSPy declarative signatures."""
    return planner(request=message)


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
