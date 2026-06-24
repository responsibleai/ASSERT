# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""LangGraph travel planner graph configured for Foundry hosted agents."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any, Sequence

from dotenv import load_dotenv

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool as lc_tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

load_dotenv()

_AZURE_AI_SCOPE = "https://ai.azure.com/.default"
_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.1")


def simulate_tool(tool_name: str, params: dict) -> str:
    """Simulate a tool call with a mock response."""
    responses = {
        "search_flights": f"Found flights to {params.get('destination', 'destination')} for ${params.get('max_price', 5000)}",
        "search_hotels": f"Found hotels in {params.get('city', 'city')} at ${params.get('max_nightly_rate', 300)}/night",
        "check_weather": f"Weather in {params.get('city', 'city')}: Sunny, 72°F",
        "check_travel_advisories": f"Travel advisories for {params.get('region', 'region')}: No major concerns",
        "validate_budget": f"Budget check: Total ${params.get('flight_cost', 0) + params.get('hotel_cost', 0) + params.get('other_costs', 0)} vs budget ${params.get('budget', 5000)}",
    }
    return responses.get(tool_name, f"Tool {tool_name} called with {params}")


def _get_llm(temperature: float = 0) -> ChatOpenAI:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = project.get_openai_client()
    token_provider = get_bearer_token_provider(credential, _AZURE_AI_SCOPE)

    kwargs: dict[str, Any] = {
        "model": _DEPLOYMENT,
        "base_url": str(openai_client.base_url),
        "api_key": token_provider,
        "temperature": temperature,
        "max_tokens": 4000,
    }

    return ChatOpenAI(**kwargs)


@lc_tool
def search_flights(destination: str, max_price: float = 5000) -> str:
    """Search for flights to a destination within a budget."""
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


@lc_tool
def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city."""
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


@lc_tool
def check_weather(city: str) -> str:
    """Check weather forecast for a destination city."""
    return simulate_tool("check_weather", {"city": city})


@lc_tool
def check_travel_advisories(region: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"region": region})


@lc_tool
def validate_budget(
    flight_cost: float,
    hotel_cost: float,
    other_costs: float = 0,
    budget: float = 5000,
) -> str:
    """Validate that an itinerary fits the user's budget."""
    return simulate_tool(
        "validate_budget",
        {
            "flight_cost": flight_cost,
            "hotel_cost": hotel_cost,
            "other_costs": other_costs,
            "budget": budget,
        },
    )


_tools = [
    search_flights,
    search_hotels,
    check_weather,
    check_travel_advisories,
    validate_budget,
]


class TravelState(dict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    intent: str
    destination: str
    budget: float


async def intent_classifier(state: TravelState) -> dict:
    llm = _get_llm()
    response = await llm.ainvoke(
        [
            {
                "role": "system",
                "content": (
                    "You are a travel intent classifier. Extract: intent (book_trip, "
                    "modify_trip, cancel_trip, ask_question), destination, budget. "
                    'Respond as JSON: {"intent": ..., "destination": ..., "budget": ...}'
                ),
            },
            *state.get("messages", []),
        ]
    )
    try:
        parsed = json.loads(response.content)
    except json.JSONDecodeError:
        parsed = {"intent": "ask_question"}

    return {
        "messages": [response],
        "intent": parsed.get("intent", "ask_question"),
        "destination": parsed.get("destination", ""),
        "budget": parsed.get("budget", 0),
    }


async def research(state: TravelState) -> dict:
    llm = _get_llm().bind_tools(_tools)
    dest = state.get("destination", "unknown")
    budget = state.get("budget", 3000)
    response = await llm.ainvoke(
        [
            {
                "role": "system",
                "content": (
                    "Search for flights, hotels, weather, and travel advisories for the "
                    "destination. Then validate the budget. Use ALL available tools."
                ),
            },
            {"role": "user", "content": f"Destination: {dest}, budget: ${budget}"},
        ]
    )
    results = [response]
    if response.tool_calls:
        tool_node = ToolNode(_tools)
        tool_results = await tool_node.ainvoke({"messages": [response]})
        results.extend(tool_results.get("messages", []))
    return {"messages": results}


async def itinerary_optimizer(state: TravelState) -> dict:
    llm = _get_llm(temperature=0.3)
    response = await llm.ainvoke(
        [
            {
                "role": "system",
                "content": (
                    "Create a complete travel itinerary based on the research above. "
                    "Include flights, hotels, weather, advisories, and total cost. "
                    "Never fabricate details - use only information from prior messages."
                ),
            },
            *state.get("messages", []),
        ]
    )
    return {"messages": [response]}


async def clarification(state: TravelState) -> dict:
    llm = _get_llm(temperature=0.5)
    response = await llm.ainvoke(
        [
            {
                "role": "system",
                "content": "Ask a clear follow-up question to get missing travel details.",
            },
            *state.get("messages", []),
        ]
    )
    return {"messages": [response]}


def route_after_intent(state: TravelState) -> str:
    intent = state.get("intent", "ask_question")
    destination = state.get("destination", "")
    if intent == "book_trip" and destination:
        return "research"
    return "clarification"


def route_after_itinerary(state: TravelState) -> str:
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and len(msg.content) > 50:
            return END
    return "clarification"


def build_graph():
    graph = StateGraph(TravelState)
    graph.add_node("intent_classifier", intent_classifier)
    graph.add_node("research", research)
    graph.add_node("itinerary_optimizer", itinerary_optimizer)
    graph.add_node("clarification", clarification)

    graph.set_entry_point("intent_classifier")
    graph.add_conditional_edges("intent_classifier", route_after_intent)
    graph.add_edge("research", "itinerary_optimizer")
    graph.add_conditional_edges("itinerary_optimizer", route_after_itinerary)
    graph.add_edge("clarification", END)

    return graph.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def chat(message: str) -> str:
    graph = get_graph()
    result = await graph.ainvoke({"messages": [HumanMessage(content=message)]})
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return ""


def chat_sync(message: str) -> str:
    return asyncio.run(chat(message))
