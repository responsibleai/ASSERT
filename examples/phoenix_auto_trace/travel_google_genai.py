# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — Google GenAI (Gemini).

Instrumentation: 2 lines. Agent code: standard google-genai SDK.
Traces captured: LLM calls, function calls, token counts, latency.
"""

from __future__ import annotations

# pip install openinference-instrumentation-google-genai arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import json
from google import genai
from google.genai import types

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT

client = genai.Client()

# ── Tool declarations ─────────────────────────────────────────

search_flights_decl = types.FunctionDeclaration(
    name="search_flights",
    description="Search for flights to a destination within budget.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "destination": types.Schema(type=types.Type.STRING, description="Destination city"),
            "max_price": types.Schema(type=types.Type.NUMBER, description="Max price in USD"),
        },
        required=["destination"],
    ),
)

search_hotels_decl = types.FunctionDeclaration(
    name="search_hotels",
    description="Search for hotels in a city.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "city": types.Schema(type=types.Type.STRING, description="City name"),
            "max_nightly_rate": types.Schema(type=types.Type.NUMBER, description="Max nightly rate in USD"),
        },
        required=["city"],
    ),
)

check_weather_decl = types.FunctionDeclaration(
    name="check_weather",
    description="Check weather forecast for a destination city.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "city": types.Schema(type=types.Type.STRING, description="City name"),
        },
        required=["city"],
    ),
)

check_travel_advisories_decl = types.FunctionDeclaration(
    name="check_travel_advisories",
    description="Check visa requirements, safety advisories, and health precautions.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "region": types.Schema(type=types.Type.STRING, description="Destination region"),
        },
        required=["region"],
    ),
)

validate_budget_decl = types.FunctionDeclaration(
    name="validate_budget",
    description="Validate that a trip plan fits the user's budget.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "flight_cost": types.Schema(type=types.Type.NUMBER, description="Flight cost in USD"),
            "hotel_cost": types.Schema(type=types.Type.NUMBER, description="Total hotel cost in USD"),
            "other_costs": types.Schema(type=types.Type.NUMBER, description="Other costs in USD"),
            "budget": types.Schema(type=types.Type.NUMBER, description="Total budget in USD"),
        },
        required=["flight_cost", "hotel_cost", "budget"],
    ),
)

TOOLS = types.Tool(function_declarations=[
    search_flights_decl, search_hotels_decl, check_weather_decl,
    check_travel_advisories_decl, validate_budget_decl,
])


def chat(message: str) -> str:
    """Travel planner using Google Gemini with function calling."""
    contents = [types.Content(role="user", parts=[types.Part.from_text(message)])]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[TOOLS],
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash", contents=contents, config=config,
    )

    # Multi-round tool-calling loop
    while response.candidates and response.candidates[0].content.parts:
        function_calls = [p for p in response.candidates[0].content.parts if p.function_call]
        if not function_calls:
            break

        contents.append(response.candidates[0].content)
        for part in function_calls:
            fc = part.function_call
            result = json.loads(simulate_tool(fc.name, dict(fc.args)))
            contents.append(types.Content(
                parts=[types.Part.from_function_response(name=fc.name, response=result)],
            ))

        response = client.models.generate_content(
            model="gemini-2.0-flash", contents=contents, config=config,
        )

    return response.text or ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
