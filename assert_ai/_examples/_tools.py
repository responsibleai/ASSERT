# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared mock tool data for bundled example agents.

Deterministic, no API calls. Bundled copy of the repository
``examples/phoenix_auto_trace/_tools.py`` helper so the packaged travel-planner
example is importable from an installed wheel.
"""

import json

# ── Mock responses (deterministic, no API calls) ──────────────

MOCK_FLIGHTS = [
    {"airline": "ANA", "price": 1180, "route": "LAX -> NRT", "duration": "11h30m", "stops": 0},
    {"airline": "JAL", "price": 1350, "route": "LAX -> HND", "duration": "11h45m", "stops": 0},
    {"airline": "United", "price": 850, "route": "SFO -> NRT", "duration": "11h20m", "stops": 1},
]

MOCK_HOTELS = [
    {"name": "Hotel Granbell Shinjuku", "nightly_rate": 145, "rating": 4.2},
    {"name": "Mitsui Garden Ginza", "nightly_rate": 195, "rating": 4.5},
    {"name": "Dormy Inn Premium Shibuya", "nightly_rate": 110, "rating": 4.4},
]

MOCK_WEATHER = {
    "forecast": "Hot and humid, 28-32°C. Afternoon thunderstorms likely.",
    "advisory": "Typhoon season (Jun-Oct). Check forecasts before travel.",
    "recommendation": "Pack light clothing and rain gear.",
}

MOCK_ADVISORIES = {
    "visa_required": True,
    "visa_type": "Tourist visa or visa waiver (90 days)",
    "safety_level": "Level 1 - Exercise Normal Precautions",
    "health": ["No required vaccinations", "Japanese encephalitis risk in rural areas"],
    "warnings": ["Earthquake preparedness recommended", "Register with your embassy"],
}


def simulate_tool(name: str, args: dict) -> str:
    """Execute a mock tool call. Used by the bundled travel-planner example."""
    if name == "search_flights":
        dest = args.get("destination", "unknown")
        return json.dumps([{**f, "route": f["route"].split("->")[0].strip() + f" -> {dest}"} for f in MOCK_FLIGHTS])
    if name == "search_hotels":
        city = args.get("city", "unknown")
        return json.dumps([{**h, "city": city} for h in MOCK_HOTELS])
    if name == "check_weather":
        city = args.get("city", "unknown")
        return json.dumps({"city": city, **MOCK_WEATHER})
    if name == "check_travel_advisories":
        region = args.get("region", "unknown")
        return json.dumps({"region": region, **MOCK_ADVISORIES})
    if name == "validate_budget":
        flight = args.get("flight_cost", 0)
        hotel = args.get("hotel_cost", 0)
        other = args.get("other_costs", 0)
        budget = args.get("budget", 0)
        total = flight + hotel + other
        return json.dumps({"total": total, "budget": budget, "within_budget": total <= budget, "remaining": budget - total})
    return json.dumps({"error": f"Unknown tool: {name}"})
