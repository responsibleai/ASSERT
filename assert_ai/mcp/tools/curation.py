# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Versioned generated-artifact curation MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.errors import adapt_tool_errors, invoke_tool
from assert_ai.mcp.models import CurationToolResult, TestCaseRevisionInput
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.services.curation import (
    CurationResult,
    CurationService,
    TestCaseRevision,
)

_CURATION_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)


@dataclass(frozen=True, slots=True)
class CurationServices:
    """Dependencies shared by curation tool handlers."""

    workspace: WorkspaceService
    curation: CurationService
    max_response_bytes: int


def register_curation_tools(
    server: MCPServer,
    services: CurationServices,
) -> None:
    """Register immutable taxonomy and test-case revision tools."""

    @server.tool(
        title="Revise an ASSERT taxonomy",
        annotations=_CURATION_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def revise_taxonomy(
        suite_id: str,
        taxonomy: dict[str, Any],
        expected_etag: Annotated[str, Field(min_length=1)],
        change_summary: Annotated[str, Field(min_length=1, max_length=500)],
    ) -> CurationToolResult:
        """Create and activate an immutable taxonomy revision."""
        result = invoke_tool(
            lambda: services.curation.revise_taxonomy(
                suite_id,
                taxonomy,
                expected_etag=expected_etag,
                change_summary=change_summary,
            ),
            workspace=services.workspace,
        )
        return _result(result, services=services)

    @server.tool(
        title="Revise one ASSERT test case",
        annotations=_CURATION_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def revise_test_case(
        suite_id: str,
        test_case_id: Annotated[str, Field(min_length=1, max_length=255)],
        updates: dict[str, Any],
        expected_etag: Annotated[str, Field(min_length=1)],
        change_summary: Annotated[str, Field(min_length=1, max_length=500)],
    ) -> CurationToolResult:
        """Create and activate one immutable test-case revision."""
        result = invoke_tool(
            lambda: services.curation.revise_test_case(
                suite_id,
                test_case_id,
                updates,
                expected_etag=expected_etag,
                change_summary=change_summary,
            ),
            workspace=services.workspace,
        )
        return _result(result, services=services)

    @server.tool(
        title="Revise multiple ASSERT test cases",
        annotations=_CURATION_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def bulk_revise_test_cases(
        suite_id: str,
        revisions: Annotated[
            tuple[TestCaseRevisionInput, ...],
            Field(min_length=1),
        ],
        expected_etag: Annotated[str, Field(min_length=1)],
        change_summary: Annotated[str, Field(min_length=1, max_length=500)],
    ) -> CurationToolResult:
        """Create and activate one immutable multi-test-case revision."""
        result = invoke_tool(
            lambda: services.curation.bulk_revise_test_cases(
                suite_id,
                tuple(
                    TestCaseRevision(
                        test_case_id=revision.test_case_id,
                        updates=revision.updates,
                    )
                    for revision in revisions
                ),
                expected_etag=expected_etag,
                change_summary=change_summary,
            ),
            workspace=services.workspace,
        )
        return _result(result, services=services)


def _result(
    result: CurationResult,
    *,
    services: CurationServices,
) -> CurationToolResult:
    payload = result.model_dump(mode="json")
    return CurationToolResult.model_validate(
        sanitize_for_mcp(payload, workspace=services.workspace)
    )
