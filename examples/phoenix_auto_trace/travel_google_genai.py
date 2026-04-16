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

client = genai.Client()

search_flights_decl = types.FunctionDeclaration(
    name="search_flights",
    description="Search for flights to a destination",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "destination": types.Schema(type=types.Type.STRING),
            "max_price": types.Schema(type=types.Type.NUMBER),
        },
        required=["destination"],
    ),
)

search_hotels_decl = types.FunctionDeclaration(
    name="search_hotels",
    description="Search for hotels in a city",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "city": types.Schema(type=types.Type.STRING),
            "max_price_per_night": types.Schema(type=types.Type.NUMBER),
        },
        required=["city"],
    ),
)

TOOLS = types.Tool(function_declarations=[search_flights_decl, search_hotels_decl])

SYSTEM_PROMPT = (
    "You are a travel planning assistant. Help users plan trips by searching "
    "for flights and hotels. Stay within their budget."
)


def _simulate_tool(name: str, args: dict) -> dict:
    if name == "search_flights":
        return {"flights": [{"airline": "ANA", "price": 1180}, {"airline": "JAL", "price": 1350}]}
    elif name == "search_hotels":
        return {"hotels": [{"name": "Hotel Granbell", "price_per_night": 145}, {"name": "Mitsui Garden", "price_per_night": 195}]}
    return {"error": f"Unknown tool: {name}"}


def chat(message: str) -> str:
    """Travel planner using Google Gemini with function calling."""
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[TOOLS],
        ),
    )

    # Handle function calls
    if response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.function_call:
                fc = part.function_call
                result = _simulate_tool(fc.name, dict(fc.args))
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        types.Content(role="user", parts=[types.Part.from_text(message)]),
                        response.candidates[0].content,
                        types.Content(parts=[types.Part.from_function_response(name=fc.name, response=result)]),
                    ],
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=[TOOLS]),
                )

    return response.text or ""


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
