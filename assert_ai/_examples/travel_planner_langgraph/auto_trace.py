# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""OpenInference auto-instrumentation for the bundled LangGraph travel planner.

Central helper installs available OpenInference instrumentors without starting
Phoenix unless a collector is configured or reachable.
"""

from __future__ import annotations

# pip install openinference-instrumentation-langchain arize-phoenix-otel
from assert_ai import auto_trace  # noqa: F401
auto_trace.enable()

from assert_ai._examples.travel_planner_langgraph.agent import chat_sync  # noqa: E402
