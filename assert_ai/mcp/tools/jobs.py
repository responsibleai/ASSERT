# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Persisted job tools for the ASSERT MCP adapter."""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.errors import adapt_tool_errors, invoke_tool
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.services.evaluations import EvaluationService
from assert_ai.services.job_models import (
    JobDetail,
    JobPage,
    JobStartResult,
    JobState,
)
from assert_ai.services.run_planning import EvaluationOverrides

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
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
_CANCEL_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass(frozen=True, slots=True)
class JobServices:
    """Application services and limits shared by persisted job tools."""

    workspace: WorkspaceService
    evaluations: EvaluationService
    max_response_bytes: int


def register_job_inspect_tools(
    server: MCPServer,
    services: JobServices,
) -> None:
    """Register job discovery and polling for every inspect-capable mode."""

    @server.tool(
        title="List ASSERT jobs",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def list_jobs(
        states: tuple[JobState, ...] = (),
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> JobPage:
        """List persisted jobs with bounded pagination."""
        page = invoke_tool(
            lambda: services.evaluations.list(
                states=states,
                cursor=cursor,
                limit=page_size,
            ),
            workspace=services.workspace,
        )
        return JobPage.model_validate(
            sanitize_for_mcp(page, workspace=services.workspace)
        )

    @server.tool(
        title="Get an ASSERT job",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def get_job(job_id: str) -> JobDetail:
        """Get current state, progress, result, and resources for one job."""
        job = invoke_tool(
            lambda: services.evaluations.get(job_id),
            workspace=services.workspace,
        )
        return JobDetail.model_validate(
            sanitize_for_mcp(job, workspace=services.workspace)
        )


def register_job_execute_tools(
    server: MCPServer,
    services: JobServices,
) -> None:
    """Register idempotent, non-blocking evaluation execution."""

    @server.tool(
        title="Start an ASSERT evaluation",
        annotations=_START_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def start_evaluation(
        config_ref: str,
        request_id: str,
        overrides: EvaluationOverrides | None = None,
    ) -> JobStartResult:
        """Validate, snapshot, and enqueue one evaluation without blocking."""
        started = invoke_tool(
            lambda: services.evaluations.start(
                config_ref,
                request_id=request_id,
                overrides=overrides,
            ),
            workspace=services.workspace,
        )
        return JobStartResult.model_validate(
            sanitize_for_mcp(started, workspace=services.workspace)
        )


def register_job_control_tools(
    server: MCPServer,
    services: JobServices,
) -> None:
    """Register cancellation and retry for enabled persisted-job kinds."""

    @server.tool(
        title="Cancel an ASSERT job",
        annotations=_CANCEL_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def cancel_job(job_id: str) -> JobDetail:
        """Request cooperative cancellation with process-tree escalation."""
        job = invoke_tool(
            lambda: services.evaluations.cancel(job_id),
            workspace=services.workspace,
        )
        return JobDetail.model_validate(
            sanitize_for_mcp(job, workspace=services.workspace)
        )

    @server.tool(
        title="Retry an ASSERT job",
        annotations=_START_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def retry_job(
        job_id: str,
        request_id: str,
    ) -> JobStartResult:
        """Retry a terminal job from its earliest unsafe stage."""
        started = invoke_tool(
            lambda: services.evaluations.retry(
                job_id,
                request_id=request_id,
            ),
            workspace=services.workspace,
        )
        return JobStartResult.model_validate(
            sanitize_for_mcp(started, workspace=services.workspace)
        )
