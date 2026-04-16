"""Travel planner — LangChain/LangGraph (multi-node graph).

Instrumentation: 2 lines. Agent code: standard LangGraph.
Traces captured: graph node executions, LLM calls per node, tool invocations,
routing decisions, token counts, latency per node.

This is a simplified version of examples/travel_planner/agent.py — same
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
import json
from typing import Annotated, Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode


# ── Tools (simulated) ─────────────────────────────────────────

@tool
def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return json.dumps([
        {"airline": "ANA", "price": 1180, "departure": "LAX→NRT", "duration": "11h30m"},
        {"airline": "JAL", "price": 1350, "departure": "LAX→HND", "duration": "11h45m"},
    ])


@tool
def search_hotels(city: str, max_price_per_night: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return json.dumps([
        {"name": "Hotel Granbell Shinjuku", "price_per_night": 145, "rating": 4.2},
        {"name": "Mitsui Garden Ginza", "price_per_night": 195, "rating": 4.5},
    ])


# ── Graph state ───────────────────────────────────────────────

class TravelState(dict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ── Nodes ─────────────────────────────────────────────────────

tools = [search_flights, search_hotels]
llm = ChatOpenAI(model="gpt-4o", temperature=0)
llm_with_tools = llm.bind_tools(tools)


async def planner(state: TravelState) -> dict:
    """Main planner node — decides what to do and calls tools."""
    messages = state.get("messages", [])
    system = {
        "role": "system",
        "content": (
            "You are a travel planning assistant. Search for flights and hotels "
            "to plan trips within the user's budget. Never recommend unsafe "
            "destinations or ignore travel advisories."
        ),
    }
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
    """Synchronous entry point for P2M callable integration."""
    return asyncio.run(_chat_async(message))


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
