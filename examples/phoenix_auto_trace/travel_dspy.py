"""Travel planner — DSPy (declarative signatures).

Instrumentation: 2 lines. Agent code: standard DSPy.
Traces captured: module calls, LLM calls with signatures, optimization steps.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2 lines of instrumentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pip install openinference-instrumentation-dspy arize-phoenix-otel
from phoenix.otel import register  # noqa: E402
register(auto_instrument=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — standard DSPy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import json
import dspy

lm = dspy.LM("openai/gpt-4o", temperature=0)
dspy.configure(lm=lm)


class ExtractTravelIntent(dspy.Signature):
    """Extract travel parameters from a user request."""
    request: str = dspy.InputField(desc="User's travel planning request")
    destination: str = dspy.OutputField(desc="Travel destination")
    duration_days: int = dspy.OutputField(desc="Trip duration in days")
    budget: float = dspy.OutputField(desc="Total budget in USD")


class PlanItinerary(dspy.Signature):
    """Create a travel itinerary from search results."""
    destination: str = dspy.InputField()
    duration_days: int = dspy.InputField()
    budget: float = dspy.InputField()
    flights: str = dspy.InputField(desc="Available flight options as JSON")
    hotels: str = dspy.InputField(desc="Available hotel options as JSON")
    itinerary: str = dspy.OutputField(desc="Complete travel itinerary with costs")


def _search_flights(destination: str) -> str:
    return json.dumps([
        {"airline": "ANA", "price": 1180, "departure": "LAX→NRT", "duration": "11h30m"},
        {"airline": "JAL", "price": 1350, "departure": "LAX→HND", "duration": "11h45m"},
    ])


def _search_hotels(city: str) -> str:
    return json.dumps([
        {"name": "Hotel Granbell Shinjuku", "price_per_night": 145, "rating": 4.2},
        {"name": "Mitsui Garden Ginza", "price_per_night": 195, "rating": 4.5},
    ])


class TravelPlanner(dspy.Module):
    def __init__(self):
        self.extract = dspy.Predict(ExtractTravelIntent)
        self.plan = dspy.Predict(PlanItinerary)

    def forward(self, request: str) -> str:
        intent = self.extract(request=request)

        flights = _search_flights(intent.destination)
        hotels = _search_hotels(intent.destination)

        result = self.plan(
            destination=intent.destination,
            duration_days=intent.duration_days,
            budget=intent.budget,
            flights=flights,
            hotels=hotels,
        )
        return result.itinerary


planner = TravelPlanner()


def chat(message: str) -> str:
    """Travel planner using DSPy declarative signatures."""
    return planner(request=message)


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
