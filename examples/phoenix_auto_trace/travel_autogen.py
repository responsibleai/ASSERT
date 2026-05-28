# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Travel planner — AutoGen AgentChat (multi-agent conversation).

Instrumentation: 2 lines. Agent code: standard AutoGen AgentChat.
Traces captured: agent messages, tool calls, LLM calls, team orchestration, token counts.
"""

from __future__ import annotations

# pip install openinference-instrumentation-autogen-agentchat arize-phoenix-otel
from phoenix.otel import register
register(auto_instrument=True)

import asyncio
import os

from dotenv import load_dotenv
load_dotenv()

from autogen_agentchat.agents import AssistantAgent  # noqa: E402
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination  # noqa: E402
from autogen_agentchat.teams import RoundRobinGroupChat  # noqa: E402
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient, OpenAIChatCompletionClient  # noqa: E402

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT  # noqa: E402

_MODEL = os.environ.get("ASSERT_TARGET_MODEL_SHORT", "gpt-5.4-mini")


_MODEL_INFO = {
    "vision": False,
    "function_calling": True,
    "json_output": True,
    "family": "unknown",
}


def _get_model_client():
    """Return AzureOpenAI model client when Azure env vars are set, else OpenAI."""
    if os.environ.get("AZURE_API_KEY") and os.environ.get("AZURE_API_BASE"):
        return AzureOpenAIChatCompletionClient(
            model=_MODEL,
            azure_endpoint=os.environ["AZURE_API_BASE"],
            api_key=os.environ["AZURE_API_KEY"],
            api_version="2024-12-01-preview",
            model_info=_MODEL_INFO,
        )
    return OpenAIChatCompletionClient(model=_MODEL, model_info=_MODEL_INFO)


# ── Tools (simulated via shared module) ───────────────────────

def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


def check_weather(city: str) -> str:
    """Check weather forecast for a destination city."""
    return simulate_tool("check_weather", {"city": city})


def check_travel_advisories(country: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"country": country})


def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


# ── Agents ────────────────────────────────────────────────────

_model_client = _get_model_client()

researcher = AssistantAgent(
    name="researcher",
    model_client=_model_client,
    system_message=(
        "You are a travel researcher. Use the provided tools to gather "
        "flight, hotel, weather, and advisory information for the user's trip request."
    ),
    tools=[search_flights, search_hotels, check_weather,
           check_travel_advisories, validate_budget],
)

planner = AssistantAgent(
    name="planner",
    model_client=_model_client,
    system_message=(
        SYSTEM_PROMPT
        + "\n\nSynthesize the research into a complete itinerary with costs, "
        "safety info, and daily plan. Say TERMINATE when done."
    ),
)


# ── Chat implementation ───────────────────────────────────────

async def _chat_async(message: str) -> str:
    termination = TextMentionTermination("TERMINATE") | MaxMessageTermination(20)
    team = RoundRobinGroupChat(
        [researcher, planner], termination_condition=termination,
    )
    result = await team.run(task=message)
    for msg in reversed(result.messages):
        if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip():
            return msg.content.replace("TERMINATE", "").strip()
    return ""


def chat(message: str) -> str:
    """Travel planner using AutoGen AgentChat multi-agent team."""
    return asyncio.run(_chat_async(message))


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
