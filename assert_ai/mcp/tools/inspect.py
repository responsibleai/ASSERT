# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Read-only ASSERT MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from assert_ai.core.config_document import (
    ConfigValidationReport,
    EVAL_CONFIG_SCHEMA_VERSION,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.errors import adapt_tool_errors, invoke_tool
from assert_ai.mcp.models import (
    ConfigCatalogItem,
    ConfigCatalogPage,
    ConfigResult,
    ConfigSchemaResult,
    FailurePage,
    PresetCatalogItem,
    PresetCatalogPage,
    PresetResult,
    RunCatalogItem,
    RunCatalogPage,
    RunComparisonResult,
    RunReferenceInput,
    RunResult,
    ScorePage,
    SuiteCatalogItem,
    SuiteCatalogPage,
    SuiteResult,
    TestCasePage,
    TestCaseResult,
    TranscriptResult,
)
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.mcp.uris import (
    config_uri,
    preset_uri,
    run_config_uri,
    run_manifest_uri,
    run_summary_uri,
    run_transcript_uri,
    suite_taxonomy_uri,
    suite_test_case_uri,
)
from assert_ai.services.artifacts import (
    ArtifactChunk,
    ArtifactPage,
    ArtifactRepository,
)
from assert_ai.services.configs import ConfigService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.library import LibraryService, PresetKind
from assert_ai.services.results import ResultRepository, RunReference

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass(frozen=True, slots=True)
class InspectServices:
    """Application services shared by all inspect tools and resources."""

    workspace: WorkspaceService
    library: LibraryService
    configs: ConfigService
    results: ResultRepository
    artifacts: ArtifactRepository
    max_response_bytes: int


def register_inspect_tools(
    server: MCPServer,
    services: InspectServices,
) -> None:
    """Register the complete read-only inspect capability group."""

    workspace = services.workspace

    @server.tool(
        title="List ASSERT presets",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_presets(
        kind: PresetKind | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> PresetCatalogPage:
        """List built-in behavior and judge presets using bounded pagination."""
        page = invoke_tool(
            lambda: services.library.list_presets(
                kind=kind,
                cursor=cursor,
                page_size=page_size,
            ),
            workspace=workspace,
        )
        return PresetCatalogPage(
            items=tuple(
                PresetCatalogItem(
                    **item.model_dump(mode="python"),
                    resource_uri=preset_uri(item.kind.value, item.name),
                )
                for item in page.items
            ),
            next_cursor=page.next_cursor,
        )

    @server.tool(
        title="Get an ASSERT preset",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def get_preset(kind: PresetKind, name: str) -> PresetResult:
        """Get one complete built-in preset by kind and name."""
        record = invoke_tool(
            lambda: services.library.get_preset(kind, name),
            workspace=workspace,
        )
        document = _safe_mapping(record.document, workspace=workspace)
        return PresetResult(
            kind=record.kind,
            name=record.name,
            version=record.version,
            tags=record.tags,
            yaml=_dump_yaml(document),
            document=document,
            resource_uri=preset_uri(record.kind.value, record.name),
        )

    @server.tool(
        title="Get the ASSERT eval config schema",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def get_config_schema() -> ConfigSchemaResult:
        """Get the canonical Draft 2020-12 schema for eval_config.yaml."""
        schema = invoke_tool(
            services.configs.get_schema,
            workspace=workspace,
        )
        return ConfigSchemaResult(
            schema_version=EVAL_CONFIG_SCHEMA_VERSION,
            json_schema=_safe_mapping(schema, workspace=workspace),
        )

    @server.tool(
        title="List managed ASSERT configs",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_configs(
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> ConfigCatalogPage:
        """List workspace-managed eval configs using bounded pagination."""
        page = invoke_tool(
            lambda: services.configs.list_configs(
                cursor=cursor,
                limit=page_size,
            ),
            workspace=workspace,
        )
        return ConfigCatalogPage(
            items=tuple(
                ConfigCatalogItem(
                    **item.model_dump(mode="python"),
                    resource_uri=config_uri(item.config_ref),
                )
                for item in page.items
            ),
            next_cursor=page.next_cursor,
        )

    @server.tool(
        title="Get a managed ASSERT config",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def get_config(config_ref: str) -> ConfigResult:
        """Get one normalized config, its ETag, and validation report."""
        record = invoke_tool(
            lambda: services.configs.get_config(config_ref),
            workspace=workspace,
        )
        document = _safe_mapping(record.document, workspace=workspace)
        validation = ConfigValidationReport.model_validate(
            sanitize_for_mcp(record.validation, workspace=workspace)
        )
        return ConfigResult(
            config_ref=record.config_ref,
            yaml=_dump_yaml(document),
            document=document,
            etag=record.etag,
            validation=validation,
            resource_uri=config_uri(record.config_ref),
        )

    @server.tool(
        title="List ASSERT result suites",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_suites(
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> SuiteCatalogPage:
        """List result suites without loading score or transcript rows."""
        page = invoke_tool(
            lambda: services.results.list_suite_catalog_entries(
                cursor=cursor,
                page_size=page_size,
            ),
            workspace=workspace,
        )
        items = []
        for raw_item in page.items:
            item = _safe_mapping(raw_item, workspace=workspace)
            suite_id = str(item["suite_id"])
            item["resources"] = {
                "taxonomy": suite_taxonomy_uri(suite_id),
            }
            items.append(SuiteCatalogItem.model_validate(item))
        return SuiteCatalogPage(
            items=tuple(items),
            next_cursor=page.next_cursor,
        )

    @server.tool(
        title="Get an ASSERT result suite",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def get_suite(suite_id: str) -> SuiteResult:
        """Get metadata, behavior, counts, and resource links for one suite."""
        summary = invoke_tool(
            lambda: services.results.get_suite(suite_id),
            workspace=workspace,
        )
        payload = _public_suite(summary, workspace=workspace)
        payload["resources"] = {
            "taxonomy": suite_taxonomy_uri(suite_id),
        }
        return SuiteResult.model_validate(payload)

    @server.tool(
        title="List ASSERT runs",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_runs(
        suite_id: str,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> RunCatalogPage:
        """List runs in one suite without loading score or transcript rows."""
        page = invoke_tool(
            lambda: services.results.list_run_catalog_entries(
                suite_id,
                cursor=cursor,
                page_size=page_size,
            ),
            workspace=workspace,
        )
        items = []
        for raw_item in page.items:
            item = _safe_mapping(raw_item, workspace=workspace)
            run_id = str(item["run_id"])
            item["resources"] = _run_resources(suite_id, run_id)
            items.append(RunCatalogItem.model_validate(item))
        return RunCatalogPage(
            items=tuple(items),
            next_cursor=page.next_cursor,
        )

    @server.tool(
        title="Get an ASSERT run",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def get_run(suite_id: str, run_id: str) -> RunResult:
        """Get metadata-only quality, timing, usage, and model details."""
        summary = invoke_tool(
            lambda: services.results.load_run_detail(suite_id, run_id),
            workspace=workspace,
        )
        payload = _public_run(summary, workspace=workspace)
        payload["resources"] = _run_resources(suite_id, run_id)
        return RunResult.model_validate(payload)

    @server.tool(
        title="Compare ASSERT runs",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def compare_runs(
        run_refs: list[RunReferenceInput],
        metric: str = "policy_violation",
        behavior_limit: int = 8,
    ) -> RunComparisonResult:
        """Compare two or more within-suite or cross-suite runs."""
        if len(run_refs) > 20:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "compare_runs accepts at most 20 run references",
            )
        if behavior_limit < 0 or behavior_limit > services.results.max_page_size:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                (
                    "behavior_limit must be between 0 and "
                    f"{services.results.max_page_size}"
                ),
            )
        result = invoke_tool(
            lambda: services.results.compare_runs(
                [
                    RunReference(
                        suite_id=reference.suite_id,
                        run_id=reference.run_id,
                    )
                    for reference in run_refs
                ],
                metric=metric,
                behavior_limit=behavior_limit,
            ),
            workspace=workspace,
        )
        return RunComparisonResult.model_validate(
            _safe_mapping(result, workspace=workspace)
        )

    @server.tool(
        title="List ASSERT test cases",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_test_cases(
        suite_id: str,
        run_id: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
        kind: str | None = None,
        behavior: str | None = None,
        test_case_id: str | None = None,
        factors: dict[str, Any] | None = None,
    ) -> TestCasePage:
        """Query a suite or run test set with stable, source-bound cursors."""
        page = invoke_tool(
            lambda: services.results.list_test_cases(
                suite_id,
                run_id=run_id,
                cursor=cursor,
                page_size=page_size,
                kind=kind,
                behavior=behavior,
                test_case_id=test_case_id,
                factors=factors,
            ),
            workspace=workspace,
        )
        return TestCasePage(
            items=_safe_list(page.items, workspace=workspace),
            next_cursor=page.next_cursor,
        )

    @server.tool(
        title="Get an ASSERT test case",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def get_test_case(
        suite_id: str,
        test_case_id: str,
        kind: str | None = None,
        run_id: str | None = None,
    ) -> TestCaseResult:
        """Get one complete test case through its JSONL index."""
        row = invoke_tool(
            lambda: services.results.get_test_case(
                suite_id,
                test_case_id,
                kind=kind,
                run_id=run_id,
            ),
            workspace=workspace,
        )
        return TestCaseResult(
            row=_safe_mapping(row, workspace=workspace),
            resource_uri=suite_test_case_uri(
                suite_id,
                test_case_id,
                kind=kind,
                run_id=run_id,
            ),
        )

    @server.tool(
        title="List ASSERT scores",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_scores(
        suite_id: str,
        run_id: str,
        cursor: str | None = None,
        page_size: int | None = None,
        kind: str | None = None,
        behavior: str | None = None,
        test_case_id: str | None = None,
        dimension: str | None = None,
        dimension_value: bool | int | str | None = None,
        match_not_applicable: bool = False,
        judge_status: str | None = None,
        target: str | None = None,
        stop_reason: str | None = None,
        has_tool_use: bool | None = None,
        factors: dict[str, Any] | None = None,
    ) -> ScorePage:
        """Query score rows by behavior, dimension, status, target, or tool use."""
        page = invoke_tool(
            lambda: services.results.list_scores(
                suite_id,
                run_id,
                cursor=cursor,
                page_size=page_size,
                kind=kind,
                behavior=behavior,
                test_case_id=test_case_id,
                dimension=dimension,
                dimension_value=dimension_value,
                match_not_applicable=match_not_applicable,
                judge_status=judge_status,
                target=target,
                stop_reason=stop_reason,
                has_tool_use=has_tool_use,
                factors=factors,
            ),
            workspace=workspace,
        )
        return ScorePage(
            items=_safe_list(page.items, workspace=workspace),
            next_cursor=page.next_cursor,
        )

    @server.tool(
        title="List ASSERT failures",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_failures(
        suite_id: str,
        run_id: str,
        dimension: str = "policy_violation",
        include_judge_failures: bool = True,
        cursor: str | None = None,
        page_size: int | None = None,
        kind: str | None = None,
        behavior: str | None = None,
    ) -> FailurePage:
        """List flagged score rows and optional judge failures."""
        page = invoke_tool(
            lambda: services.results.list_failures(
                suite_id,
                run_id,
                dimension=dimension,
                include_judge_failures=include_judge_failures,
                cursor=cursor,
                page_size=page_size,
                kind=kind,
                behavior=behavior,
            ),
            workspace=workspace,
        )
        return FailurePage(
            items=_safe_list(page.items, workspace=workspace),
            next_cursor=page.next_cursor,
        )

    @server.tool(
        title="Get an ASSERT transcript",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def get_transcript(
        suite_id: str,
        run_id: str,
        test_case_id: str,
        kind: str | None = None,
    ) -> TranscriptResult:
        """Join one test case, inference transcript, and score verdict."""
        transcript = invoke_tool(
            lambda: services.results.get_transcript(
                suite_id,
                run_id,
                test_case_id,
                kind=kind,
            ),
            workspace=workspace,
        )
        payload = _safe_mapping(transcript, workspace=workspace)
        payload["resource_uri"] = run_transcript_uri(
            suite_id,
            run_id,
            test_case_id,
            kind=kind,
        )
        return TranscriptResult.model_validate(payload)

    @server.tool(
        title="List ASSERT artifacts",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def list_artifacts(
        suite_id: str,
        run_id: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> ArtifactPage:
        """List manifest-backed artifacts without exposing filesystem paths."""
        return invoke_tool(
            lambda: services.artifacts.list_artifacts(
                suite_id,
                run_id=run_id,
                cursor=cursor,
                page_size=page_size,
            ),
            workspace=workspace,
        )

    @server.tool(
        title="Read an ASSERT artifact chunk",
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(workspace, max_response_bytes=services.max_response_bytes)
    def read_artifact_chunk(
        artifact_id: str,
        offset: int = 0,
        chunk_size: int | None = None,
    ) -> ArtifactChunk:
        """Read one bounded, redacted text or base64 binary artifact chunk."""
        return invoke_tool(
            lambda: services.artifacts.read_artifact_chunk(
                artifact_id,
                offset=offset,
                chunk_size=chunk_size,
            ),
            workspace=workspace,
        )


def _safe_mapping(
    value: Any,
    *,
    workspace: WorkspaceService,
) -> dict[str, Any]:
    sanitized = sanitize_for_mcp(value, workspace=workspace)
    if not isinstance(sanitized, dict):
        raise TypeError("Expected a mapping from the application service")
    return sanitized


def _safe_list(
    value: Any,
    *,
    workspace: WorkspaceService,
) -> list[dict[str, Any]]:
    sanitized = sanitize_for_mcp(value, workspace=workspace)
    if not isinstance(sanitized, list) or not all(
        isinstance(item, dict) for item in sanitized
    ):
        raise TypeError("Expected a list of mappings from the application service")
    return sanitized


def _dump_yaml(document: dict[str, Any]) -> str:
    text = yaml.safe_dump(
        document,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return text if text.endswith("\n") else text + "\n"


def _public_suite(
    summary: dict[str, Any],
    *,
    workspace: WorkspaceService,
) -> dict[str, Any]:
    payload = _safe_mapping(summary, workspace=workspace)
    for key in (
        "artifact_versions",
        "sources",
        "run_set_identity",
        "run_catalog_identity",
    ):
        payload.pop(key, None)
    return payload


def _public_run(
    summary: dict[str, Any],
    *,
    workspace: WorkspaceService,
) -> dict[str, Any]:
    payload = _safe_mapping(summary, workspace=workspace)
    for key in ("artifact_versions", "sources", "indexes"):
        payload.pop(key, None)
    return payload


def _run_resources(suite_id: str, run_id: str) -> dict[str, str]:
    return {
        "summary": run_summary_uri(suite_id, run_id),
        "manifest": run_manifest_uri(suite_id, run_id),
        "config": run_config_uri(suite_id, run_id),
    }
