# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — Instructor (structured LLM output via Pydantic).

Instrumentation: 2 lines. Agent code: Instructor-patched OpenAI client.
Traces captured: LLM calls with structured output schemas, token counts, latency.
"""

from __future__ import annotations

# pip install openinference-instrumentation-instructor arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import os

from dotenv import load_dotenv
load_dotenv()

import instructor  # noqa: E402
from openai import AzureOpenAI, OpenAI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT  # noqa: E402

_MODEL = os.environ.get("P2M_TARGET_MODEL_SHORT", "gpt-5.4-mini")


def _get_client():
    """Return instructor-patched AzureOpenAI or OpenAI client."""
    if os.environ.get("AZURE_API_KEY") and os.environ.get("AZURE_API_BASE"):
        base = AzureOpenAI(
            api_key=os.environ["AZURE_API_KEY"],
            azure_endpoint=os.environ["AZURE_API_BASE"],
            api_version="2024-12-01-preview",
        )
    else:
        base = OpenAI()
    return instructor.from_openai(base)


client = _get_client()


# ── Pydantic models for structured output ─────────────────────

class TravelIntent(BaseModel):
    destination: str
    country: str
    duration_days: int
    budget: float


class FlightOption(BaseModel):
    airline: str
    price: float
    route: str
    duration: str
    stops: int


class HotelOption(BaseModel):
    name: str
    nightly_rate: float
    rating: float


class TravelItinerary(BaseModel):
    destination: str
    country: str
    duration_days: int
    recommended_flight: FlightOption
    recommended_hotel: HotelOption
    total_cost: float
    within_budget: bool
    weather_summary: str
    visa_info: str
    safety_level: str
    health_precautions: list[str]
    daily_plan: str


# ── Chat implementation ───────────────────────────────────────

def chat(message: str) -> str:
    """Travel planner using Instructor for structured LLM output."""
    # Step 1: Extract travel intent via structured output
    intent = client.chat.completions.create(
        model=_MODEL,
        response_model=TravelIntent,
        messages=[
            {"role": "system", "content": "Extract travel parameters from the user request."},
            {"role": "user", "content": message},
        ],
    )

    # Step 2: Gather tool data using extracted intent
    flights = simulate_tool("search_flights", {"destination": intent.destination})
    hotels = simulate_tool("search_hotels", {"city": intent.destination})
    weather = simulate_tool("check_weather", {"city": intent.destination})
    advisories = simulate_tool("check_travel_advisories", {"country": intent.country})
    budget_check = simulate_tool("validate_budget", {
        "flight_cost": 1180,
        "hotel_cost": 145 * intent.duration_days,
        "other_costs": 0,
        "budget": intent.budget,
    })

    # Step 3: Generate structured itinerary
    itinerary = client.chat.completions.create(
        model=_MODEL,
        response_model=TravelItinerary,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
            {"role": "assistant", "content": (
                f"Here is the research data:\n"
                f"Flights: {flights}\nHotels: {hotels}\n"
                f"Weather: {weather}\nAdvisories: {advisories}\n"
                f"Budget check: {budget_check}"
            )},
            {"role": "user", "content": "Now create the complete structured itinerary."},
        ],
    )

    return itinerary.model_dump_json(indent=2)


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
