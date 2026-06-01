# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared mock tool data for phoenix_auto_trace demos.

All 14 demos use the same 5 tools with the same mock responses.
Each demo imports this module for mock execution, then registers
tools in its framework's native format.
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
    """Execute a mock tool call. Used by all demos."""
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


# ── System prompt (shared across all demos) ───────────────────

SYSTEM_PROMPT = (
    "You are a travel planning assistant with access to real-time tools. "
    "For every trip request: 1) search flights, 2) search hotels, "
    "3) check weather, 4) check travel advisories, 5) validate the total "
    "fits the budget. Never fabricate details — use tool results only. "
    "Surface visa requirements, safety advisories, and health precautions."
)

# ── Tool schemas (OpenAI function-calling format) ─────────────
# Frameworks that use OpenAI-compatible schemas can import directly.
# Others (Anthropic, Google, Bedrock) adapt from these.

OPENAI_TOOLS = [
    {"type": "function", "function": {
        "name": "search_flights",
        "description": "Search for flights to a destination within budget.",
        "parameters": {"type": "object", "properties": {
            "destination": {"type": "string", "description": "Destination city"},
            "max_price": {"type": "number", "description": "Max price in USD"},
        }, "required": ["destination"]},
    }},
    {"type": "function", "function": {
        "name": "search_hotels",
        "description": "Search for hotels in a city.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "City name"},
            "max_nightly_rate": {"type": "number", "description": "Max nightly rate in USD"},
        }, "required": ["city"]},
    }},
    {"type": "function", "function": {
        "name": "check_weather",
        "description": "Check weather forecast for a destination city.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "City name"},
        }, "required": ["city"]},
    }},
    {"type": "function", "function": {
        "name": "check_travel_advisories",
        "description": "Check visa requirements, safety advisories, and health precautions.",
        "parameters": {"type": "object", "properties": {
            "region": {"type": "string", "description": "Destination region"},
        }, "required": ["region"]},
    }},
    {"type": "function", "function": {
        "name": "validate_budget",
        "description": "Validate that a trip plan fits the user's budget.",
        "parameters": {"type": "object", "properties": {
            "flight_cost": {"type": "number"},
            "hotel_cost": {"type": "number"},
            "other_costs": {"type": "number"},
            "budget": {"type": "number"},
        }, "required": ["flight_cost", "hotel_cost", "budget"]},
    }},
]

# ── Anthropic tool format ─────────────────────────────────────

ANTHROPIC_TOOLS = [
    {"name": t["function"]["name"], "description": t["function"]["description"],
     "input_schema": t["function"]["parameters"]}
    for t in OPENAI_TOOLS
]
