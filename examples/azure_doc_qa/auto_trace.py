# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Phoenix OTel auto-instrumentation for the Azure Doc QA agent.

2 lines to instrument, then run the agent unchanged. Phoenix auto-discovers
LangChain, LangGraph, OpenAI, and MCP tool calls.
"""

from __future__ import annotations

# 1 line to instrument — lazy via assert_ai.auto_trace so importing this
# module is fast when no Phoenix collector is running. See assert_ai/tracing.py.
from assert_ai import auto_trace
auto_trace()

from examples.azure_doc_qa.agent import chat_sync  # noqa: E402
