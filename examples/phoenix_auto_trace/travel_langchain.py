"""Travel planner — LangChain/LangGraph (multi-node graph).

Instrumentation: 2 lines. Agent code: standard LangGraph.
Traces captured: graph node executions, LLM calls per node, tool invocations,
routing decisions, token counts, latency per node.

This is a simplified version of examples/travel_planner_langgraph/agent.py — same
architecture, no MCP dependency, self-contained with simulated tools.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2 lines of instrumentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pip install openinference-instrumentation-langchain arize-phoenix-otel
from phoenix.otel import register  # noqa: E402
register(auto_instrument=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — standard LangGraph
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import asyncio
import os
from typing import Annotated, Sequence

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from examples.phoenix_auto_trace._tools import simulate_tool, SYSTEM_PROMPT


def _get_llm():
    """Return AzureChatOpenAI when Azure env vars are set, else ChatOpenAI."""
    if os.environ.get("AZURE_API_KEY") and os.environ.get("AZURE_API_BASE"):
        return AzureChatOpenAI(
            azure_deployment=os.environ.get("ASSERT_AZURE_DEPLOYMENT", "gpt-5.4-mini"),
            azure_endpoint=os.environ["AZURE_API_BASE"],
            api_key=os.environ["AZURE_API_KEY"],
            api_version="2024-12-01-preview",
            temperature=0,
        )
    return ChatOpenAI(model=os.environ.get("ASSERT_TARGET_MODEL", "gpt-4o"), temperature=0)


# ── Tools (simulated via shared module) ───────────────────────

@tool
def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return simulate_tool("search_flights", {"destination": destination, "max_price": max_price})


@tool
def search_hotels(city: str, max_nightly_rate: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return simulate_tool("search_hotels", {"city": city, "max_nightly_rate": max_nightly_rate})


@tool
def check_weather(city: str) -> str:
    """Check weather forecast for a destination city."""
    return simulate_tool("check_weather", {"city": city})


@tool
def check_travel_advisories(country: str) -> str:
    """Check visa requirements, safety advisories, and health precautions."""
    return simulate_tool("check_travel_advisories", {"country": country})


@tool
def validate_budget(flight_cost: float, hotel_cost: float, other_costs: float = 0, budget: float = 0) -> str:
    """Validate that a trip plan fits the user's budget."""
    return simulate_tool("validate_budget", {
        "flight_cost": flight_cost, "hotel_cost": hotel_cost,
        "other_costs": other_costs, "budget": budget,
    })


# ── Graph state ───────────────────────────────────────────────

class TravelState(dict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ── Nodes ─────────────────────────────────────────────────────

tools = [search_flights, search_hotels, check_weather, check_travel_advisories, validate_budget]
llm = _get_llm()
llm_with_tools = llm.bind_tools(tools)


async def planner(state: TravelState) -> dict:
    """Main planner node — decides what to do and calls tools."""
    messages = state.get("messages", [])
    system = {"role": "system", "content": SYSTEM_PROMPT}
    response = await llm_with_tools.ainvoke([system, *messages])
    return {"messages": [response]}


def should_use_tools(state: TravelState) -> str:
    """Route to tools if the last message has tool calls."""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


# ── Build graph ───────────────────────────────────────────────

def build_graph():
    graph = StateGraph(TravelState)
    graph.add_node("planner", planner)
    graph.add_node("tools", ToolNode(tools))

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", should_use_tools)
    graph.add_edge("tools", "planner")

    return graph.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def _chat_async(message: str) -> str:
    graph = get_graph()
    result = await graph.ainvoke({"messages": [HumanMessage(content=message)]})
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return ""


def chat(message: str) -> str:
    """Synchronous entry point for ASSERT callable integration."""
    return asyncio.run(_chat_async(message))


if __name__ == "__main__":
    print(chat("Plan a week in Tokyo for under $3000"))
