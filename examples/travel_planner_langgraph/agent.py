"""Multi-agent travel planner built with LangGraph.

Multi-node graph with conditional routing, tool calling, and shared state.
Architecture:
    coordinator → intent_classifier → flight_searcher → hotel_searcher
                                   → safety_advisor → itinerary_optimizer

Usage:
    uv run p2m run --config examples/travel_planner_langgraph/eval_config.yaml
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any, Sequence

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool as lc_tool
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from examples.phoenix_auto_trace._tools import simulate_tool

_DEPLOYMENT = os.environ.get("P2M_AZURE_DEPLOYMENT", "gpt-5.4-mini")


def _get_llm(temperature: float = 0) -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_deployment=_DEPLOYMENT,
        azure_endpoint=os.environ["AZURE_API_BASE"],
        api_key=os.environ["AZURE_API_KEY"],
        api_version="2024-12-01-preview",
        temperature=temperature,
        max_tokens=4000,
    )


# ── Tools (mock — same 5 tools as all demos) ─────────────────

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
def check_travel_advisories(country: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"country": country})

@lc_tool
def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 5000) -> str:
    """Validate that an itinerary fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })

_tools = [search_flights, search_hotels, check_weather, check_travel_advisories, validate_budget]


# ── Graph state ───────────────────────────────────────────────

class TravelState(dict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    intent: str
    destination: str
    budget: float


# ── Node implementations ─────────────────────────────────────

async def intent_classifier(state: TravelState) -> dict:
    """Classify user intent and extract travel parameters."""
    llm = _get_llm()
    response = await llm.ainvoke([
        {"role": "system", "content": (
            "You are a travel intent classifier. Extract: intent (book_trip, "
            "modify_trip, cancel_trip, ask_question), destination, budget. "
            'Respond as JSON: {"intent": ..., "destination": ..., "budget": ...}'
        )},
        *state.get("messages", []),
    ])
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
    """Search flights, hotels, weather, advisories using tools."""
    llm = _get_llm().bind_tools(_tools)
    dest = state.get("destination", "unknown")
    budget = state.get("budget", 3000)
    response = await llm.ainvoke([
        {"role": "system", "content": (
            "Search for flights, hotels, weather, and travel advisories for the "
            "destination. Then validate the budget. Use ALL available tools."
        )},
        {"role": "user", "content": f"Destination: {dest}, budget: ${budget}"},
    ])
    results = [response]
    if response.tool_calls:
        tool_node = ToolNode(_tools)
        tool_results = await tool_node.ainvoke({"messages": [response]})
        results.extend(tool_results.get("messages", []))
    return {"messages": results}


async def itinerary_optimizer(state: TravelState) -> dict:
    """Create final itinerary from research results."""
    llm = _get_llm(temperature=0.3)
    response = await llm.ainvoke([
        {"role": "system", "content": (
            "Create a complete travel itinerary based on the research above. "
            "Include flights, hotels, weather, advisories, and total cost. "
            "Never fabricate details — use only information from prior messages."
        )},
        *state.get("messages", []),
    ])
    return {"messages": [response]}


async def clarification(state: TravelState) -> dict:
    """Ask user for missing information."""
    llm = _get_llm(temperature=0.5)
    response = await llm.ainvoke([
        {"role": "system", "content": "Ask a clear follow-up question to get missing travel details."},
        *state.get("messages", []),
    ])
    return {"messages": [response]}


# ── Routing ───────────────────────────────────────────────────

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


# ── Build graph ───────────────────────────────────────────────

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
    """Single-turn entry point."""
    graph = get_graph()
    result = await graph.ainvoke({"messages": [HumanMessage(content=message)]})
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return ""


def chat_sync(message: str) -> str:
    """Synchronous wrapper for p2m callable integration."""
    return asyncio.run(chat(message))


if __name__ == "__main__":
    print(chat_sync("Plan a week in Tokyo for under $3000"))
