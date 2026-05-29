# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Phoenix OTel auto-instrumentation for the LangGraph travel planner.

2 lines to instrument, then run the agent unchanged. Phoenix auto-discovers
LangChain, LangGraph, OpenAI, and MCP tool calls.
"""

from __future__ import annotations

# pip install openinference-instrumentation-langchain arize-phoenix-otel
from phoenix.otel import register  # noqa: F401
register(auto_instrument=True)

from examples.travel_planner_langgraph.agent import chat_sync  # noqa: E402
