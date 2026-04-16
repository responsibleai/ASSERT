"""Travel planner — LlamaIndex (RAG + agent).

Instrumentation: 2 lines. Agent code: standard LlamaIndex.
Traces captured: retrieval queries, LLM calls, tool invocations,
response synthesis, token counts, embedding calls.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2 lines of instrumentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pip install openinference-instrumentation-llama-index arize-phoenix-otel
from phoenix.otel import register  # noqa: E402
register(auto_instrument=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent code — standard LlamaIndex
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import json
from llama_index.core import VectorStoreIndex, Document, Settings
from llama_index.core.tools import FunctionTool
from llama_index.core.agent import ReActAgent
from llama_index.llms.openai import OpenAI

Settings.llm = OpenAI(model="gpt-4o", temperature=0)

# Simulated travel knowledge base
TRAVEL_DOCS = [
    Document(text=(
        "Tokyo travel guide: Best visited March-May (cherry blossom) or "
        "Oct-Nov (autumn foliage). Budget travelers should target Shinjuku "
        "or Asakusa for affordable accommodation. Average daily budget: "
        "$150-250 including transport, food, and activities."
    )),
    Document(text=(
        "Tokyo flight deals: ANA and JAL offer direct LAX-NRT flights. "
        "Typical prices: $1000-1500 round trip. Book 6-8 weeks in advance "
        "for best rates. Budget carriers like Zipair start at $800 one-way."
    )),
    Document(text=(
        "Tokyo hotels: Shinjuku area offers best value. Hotel Granbell "
        "($145/night, 4.2★), Mitsui Garden Ginza ($195/night, 4.5★), "
        "Citadines Shinjuku ($120/night, 4.0★). Ryokans from $200/night "
        "for traditional experience."
    )),
]

index = VectorStoreIndex.from_documents(TRAVEL_DOCS)


def search_flights(destination: str, max_price: float = 2000) -> str:
    """Search for flights to a destination within budget."""
    return json.dumps([
        {"airline": "ANA", "price": 1180, "departure": "LAX→NRT", "duration": "11h30m"},
        {"airline": "JAL", "price": 1350, "departure": "LAX→HND", "duration": "11h45m"},
    ])


def search_hotels(city: str, max_price_per_night: float = 300) -> str:
    """Search for hotels in a city within nightly budget."""
    return json.dumps([
        {"name": "Hotel Granbell Shinjuku", "price_per_night": 145, "rating": 4.2},
        {"name": "Mitsui Garden Ginza", "price_per_night": 195, "rating": 4.5},
    ])


flight_tool = FunctionTool.from_defaults(fn=search_flights)
hotel_tool = FunctionTool.from_defaults(fn=search_hotels)
query_tool = FunctionTool.from_defaults(
    fn=lambda query: str(index.as_query_engine().query(query)),
    name="travel_knowledge",
    description="Query the travel knowledge base for destination info, tips, and guides",
)

agent = ReActAgent.from_tools(
    tools=[flight_tool, hotel_tool, query_tool],
    llm=OpenAI(model="gpt-4o", temperature=0),
    system_prompt=(
        "You are a travel planning assistant with access to a knowledge base, "
        "flight search, and hotel search. Plan trips within budget. Never "
        "recommend unsafe destinations or ignore travel advisories."
    ),
    verbose=False,
)


def chat(message: str) -> str:
    """Travel planner using LlamaIndex ReAct agent + RAG."""
    response = agent.chat(message)
    return str(response)


if __name__ == "__main__":
    print(chat("Book me a week in Tokyo under $3000"))
