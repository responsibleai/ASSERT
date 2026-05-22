"""Phoenix/OpenInference setup for the banking MCP LangGraph demo."""

from __future__ import annotations

import os
from typing import Any

_PROJECT_NAME = "banking-mcp-langgraph"
_TRACER_PROVIDER: Any | None = None
_MCP_INSTRUMENTOR = "manual spans"


def setup_tracing(project_name: str = _PROJECT_NAME) -> Any | None:
    """Register tracing once without blocking when Phoenix is not running.

    If PHOENIX_COLLECTOR_ENDPOINT is set, spans export to Phoenix. Otherwise we
    install a local SDK provider; P2M's live OTel collector attaches to that
    provider and still renders MCP tool calls in the viewer transcript.
    """
    global _TRACER_PROVIDER, _MCP_INSTRUMENTOR
    if _TRACER_PROVIDER is not None:
        return _TRACER_PROVIDER

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
        if endpoint:
            from phoenix.otel import register
            _TRACER_PROVIDER = register(
                endpoint=endpoint,
                project_name=project_name,
                set_global_tracer_provider=True,
                verbose=False,
            )
        else:
            from opentelemetry import trace as otel_trace
            from opentelemetry.sdk.trace import TracerProvider
            _TRACER_PROVIDER = TracerProvider()
            try:
                otel_trace.set_tracer_provider(_TRACER_PROVIDER)
            except Exception:
                _TRACER_PROVIDER = otel_trace.get_tracer_provider()
        LangChainInstrumentor().instrument(tracer_provider=_TRACER_PROVIDER)
    except Exception:
        _TRACER_PROVIDER = None

    try:
        from openinference.instrumentation.mcp import MCPInstrumentor  # type: ignore
    except Exception:
        _MCP_INSTRUMENTOR = "manual spans"
    else:  # pragma: no cover - depends on optional package availability
        MCPInstrumentor().instrument(tracer_provider=_TRACER_PROVIDER)
        _MCP_INSTRUMENTOR = "openinference-instrumentation-mcp"

    return _TRACER_PROVIDER


def mcp_instrumentor_name() -> str:
    return _MCP_INSTRUMENTOR
