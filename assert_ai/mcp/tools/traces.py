# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Imported OpenTelemetry trace-judging MCP tools."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from assert_ai.mcp.errors import adapt_tool_errors, invoke_tool
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.mcp.tools.jobs import JobServices
from assert_ai.services.job_models import JobStartResult, TraceJudgingPreflight

_PREFLIGHT_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_START_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)


def register_trace_tools(
    server: MCPServer,
    services: JobServices,
) -> None:
    """Register pure trace preflight and persisted trace-job launch."""

    @server.tool(
        title="Preflight imported trace judging",
        annotations=_PREFLIGHT_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def preflight_trace_judging(
        config_ref: str,
        trace_ref: str,
        group_by: str = "session.id",
        suite_id: str | None = None,
        run_id: str | None = None,
    ) -> TraceJudgingPreflight:
        """Validate OTLP JSON and estimate the judge calls without writing."""
        plan = invoke_tool(
            lambda: services.evaluations.preflight_trace_judging(
                config_ref,
                trace_ref,
                group_by=group_by,
                suite_id=suite_id,
                run_id=run_id,
            ),
            workspace=services.workspace,
        )
        return TraceJudgingPreflight.model_validate(
            sanitize_for_mcp(plan, workspace=services.workspace)
        )

    @server.tool(
        title="Start imported trace judging",
        annotations=_START_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def start_trace_judging(
        config_ref: str,
        trace_ref: str,
        request_id: str,
        group_by: str = "session.id",
        suite_id: str | None = None,
        run_id: str | None = None,
    ) -> JobStartResult:
        """Snapshot OTLP JSON and enqueue judging without blocking."""
        started = invoke_tool(
            lambda: services.evaluations.start_trace_judging(
                config_ref,
                trace_ref,
                request_id=request_id,
                group_by=group_by,
                suite_id=suite_id,
                run_id=run_id,
            ),
            workspace=services.workspace,
        )
        return JobStartResult.model_validate(
            sanitize_for_mcp(started, workspace=services.workspace)
        )
