# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — PydanticAI (agent framework with typed tools).

Instrumentation: central helper call. Agent code: standard PydanticAI.
Traces captured: agent runs, tool calls, LLM calls, structured output, token counts.
"""

from __future__ import annotations

# Optional Phoenix export: pip install openinference-instrumentation-pydantic-ai arize-phoenix-otel
from assert_ai import auto_trace
auto_trace.enable()

import os

from dotenv import load_dotenv
load_dotenv()

from pydantic_ai import Agent  # noqa: E402

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT  # noqa: E402

_MODEL = os.environ.get("ASSERT_TARGET_MODEL_SHORT", "gpt-4o-mini")


def _get_model():
    """Return PydanticAI model — Azure when env vars set, else OpenAI."""
    from pydantic_ai.models.openai import OpenAIModel
    if os.environ.get("AZURE_API_KEY") and os.environ.get("AZURE_API_BASE"):
        from pydantic_ai.providers.azure import AzureProvider
        return OpenAIModel(
            _MODEL,
            provider=AzureProvider(
                azure_endpoint=os.environ["AZURE_API_BASE"],
                api_key=os.environ["AZURE_API_KEY"],
                api_version="2024-12-01-preview",
            ),
        )
    return OpenAIModel(_MODEL, provider="openai")


# ── Agent definition ──────────────────────────────────────────

agent = Agent(_get_model(), system_prompt=SYSTEM_PROMPT)


# ── Tools (simulated via shared module) ───────────────────────

@agent.tool_plain
def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


@agent.tool_plain
def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


@agent.tool_plain
def check_weather(city: str) -> str:
    """Check weather forecast for a destination city."""
    return simulate_tool("check_weather", {"city": city})


@agent.tool_plain
def check_travel_advisories(region: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"region": region})


@agent.tool_plain
def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


# ── Chat implementation ───────────────────────────────────────

def chat(message: str) -> str:
    """Travel planner using PydanticAI agent with tool-calling."""
    result = agent.run_sync(message)
    return result.output


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
