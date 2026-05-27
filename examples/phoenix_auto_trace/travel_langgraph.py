"""LangGraph travel planner — OTel auto-instrumented agent.

Multi-node graph with mock tools: agent calls tools, OTel captures every
LLM call, tool invocation, and routing decision via Phoenix auto-instrumentation.

Usage:
    uv run assert-eval run --config examples/travel_planner_langgraph/eval_config.yaml
"""
# NOTE: do NOT use `from __future__ import annotations` — LangGraph's StateGraph
# requires runtime-resolvable type hints for state schema introspection.

import os
from typing import Annotated, Optional

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool as lc_tool
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from examples.phoenix_auto_trace._tools import simulate_tool

_MODEL_DEPLOYMENT = os.environ.get("ASSERT_AZURE_DEPLOYMENT", "gpt-5.4-mini")

SYSTEM_PROMPT = """\
You are a travel planning assistant with access to tools for searching flights,
hotels, checking weather, travel advisories, and validating budgets.

Always use your tools before making recommendations. Every itinerary must include
transport, accommodation, weather, advisory information, and total cost breakdown.
When a request is ambiguous, ask 1-2 clarifying questions before calling tools.
Never fabricate details — use tool results only.
"""


# ── Tools (simulated via shared mock data) ────────────────────

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


# ── Graph ─────────────────────────────────────────────────────

_tools = [search_flights, search_hotels, check_weather, check_travel_advisories, validate_budget]
_tool_node = ToolNode(_tools)

_llm = AzureChatOpenAI(
    azure_deployment=_MODEL_DEPLOYMENT,
    azure_endpoint=os.environ["AZURE_API_BASE"],
    api_key=os.environ["AZURE_API_KEY"],
    api_version="2024-12-01-preview",
    temperature=0.2,
    max_tokens=4000,
).bind_tools(_tools)


class _TravelState(dict):
    messages: Annotated[list, add_messages]


def _agent_node(state: _TravelState) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(state.get("messages", []))
    return {"messages": [_llm.invoke(messages)]}


def _should_continue(state: _TravelState) -> str:
    last = state.get("messages", [])[-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


def _build_graph():
    graph = StateGraph(_TravelState)
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", _tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


_graph = _build_graph()


def _build_graph():
    graph = StateGraph(_TravelState)
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", _tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


_graph = _build_graph()


# ── Entry point ───────────────────────────────────────────────

def chat(message: str, history: Optional[list] = None) -> str:
    """Invoke the LangGraph travel planner. Returns final assistant text."""
    messages = []
    if history:
        for h in history:
            role = h.get("role", "user")
            content = h.get("content", "")
            messages.append(HumanMessage(content=content) if role == "user" else AIMessage(content=content))
    messages.append(HumanMessage(content=message))

    result = _graph.invoke({"messages": messages})

    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return ""


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
