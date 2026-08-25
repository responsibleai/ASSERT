# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Typed public models for the ASSERT MCP adapter."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assert_ai.core.config_document import ConfigValidationReport
from assert_ai.mcp import ASSERT_MCP_API_VERSION
from assert_ai.services.library import PresetKind


class ServerMode(StrEnum):
    """Predefined MCP capability bundles."""

    INSPECT = "inspect"
    AUTHOR = "author"
    FULL = "full"


class CapabilityGroup(StrEnum):
    """Stable names for independently gated MCP capabilities."""

    INSPECT = "inspect"
    AUTHOR = "author"
    DESIGN = "design"
    EXECUTE = "execute"
    PROBE = "probe"
    CURATE = "curate"
    TRACE = "trace"
    ANALYSIS = "analysis"
    ACS = "acs"
    EXPORT = "export"


class WorkspaceInfo(BaseModel):
    """Workspace-relative roots managed by the MCP server."""

    model_config = ConfigDict(frozen=True)

    root: Literal["."] = "."
    configs_root: str = "evals"
    artifacts_root: str = "artifacts"
    results_root: str = "artifacts/results"


class ServerLimits(BaseModel):
    """Response limits fixed when the MCP server starts."""

    model_config = ConfigDict(frozen=True)

    default_page_size: int
    max_page_size: int
    max_response_bytes: int
    default_artifact_chunk_bytes: int
    max_artifact_chunk_bytes: int
    max_config_bytes: int
    max_concurrency: int
    max_prompt_sample_size: int
    max_scenario_sample_size: int
    model_allowlist_enabled: bool = False
    endpoint_host_allowlist_enabled: bool = False
    allowed_model_patterns: tuple[str, ...] = ()
    allowed_endpoint_hosts: tuple[str, ...] = ()
    target_probe_timeout_s: float


class ServerInfo(BaseModel):
    """Discovery metadata returned by ``get_server_info``."""

    model_config = ConfigDict(frozen=True)

    name: Literal["ASSERT"] = "ASSERT"
    server_version: str
    assert_mcp_api_version: Literal["1"] = ASSERT_MCP_API_VERSION
    mode: ServerMode
    enabled_capability_groups: list[CapabilityGroup]
    workspace: WorkspaceInfo = Field(default_factory=WorkspaceInfo)
    limits: ServerLimits
    target_kinds: list[
        Literal["callable", "model", "connector", "endpoint"]
    ] = Field(
        default_factory=lambda: [
            "callable",
            "model",
            "connector",
            "endpoint",
        ]
    )
    transports: list[Literal["stdio"]] = Field(default_factory=lambda: ["stdio"])
    protocol_notes: list[str] = Field(
        default_factory=lambda: [
            "Long evaluations use ASSERT job polling rather than one blocking MCP request.",
            "Resource and artifact identifiers are opaque and contain no host paths.",
        ]
    )


class _McpModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class PresetCatalogItem(_McpModel):
    """One built-in preset catalog entry."""

    kind: PresetKind
    name: str
    version: str | None = None
    tags: tuple[str, ...] = ()
    summary: str | None = None
    description: str | None = None
    resource_uri: str


class PresetCatalogPage(_McpModel):
    """Bounded page of built-in presets."""

    items: tuple[PresetCatalogItem, ...]
    next_cursor: str | None = None


class PresetResult(_McpModel):
    """Complete built-in preset definition."""

    kind: PresetKind
    name: str
    version: str | None = None
    tags: tuple[str, ...] = ()
    yaml: str
    document: dict[str, Any]
    resource_uri: str


class ConfigSchemaResult(_McpModel):
    """Canonical machine-readable evaluation config schema."""

    schema_version: int
    json_schema: dict[str, Any]
    resource_uri: Literal["assert://schema/eval-config"] = (
        "assert://schema/eval-config"
    )


class ConfigCatalogItem(_McpModel):
    """One managed config catalog entry."""

    config_ref: str
    etag: str
    size_bytes: int
    modified_at: str
    structurally_valid: bool
    resource_uri: str


class ConfigCatalogPage(_McpModel):
    """Bounded page of managed configs."""

    items: tuple[ConfigCatalogItem, ...]
    next_cursor: str | None = None


class ConfigResult(_McpModel):
    """One sanitized managed evaluation config."""

    config_ref: str
    yaml: str
    document: dict[str, Any]
    etag: str
    validation: ConfigValidationReport
    resource_uri: str


class ConfigValidationResult(_McpModel):
    """Layered validation report for a managed config or draft."""

    source: Literal["config", "yaml", "document"]
    config_ref: str
    validation: ConfigValidationReport


class ConfigSaveToolResult(_McpModel):
    """Identity and ETag after an atomic managed-config save."""

    config_ref: str
    etag: str
    created: bool
    validation: ConfigValidationReport
    resource_uri: str


class ConfigDesignResult(_McpModel):
    """Unpersisted model-generated config draft."""

    yaml: str
    document: dict[str, Any]
    validation: ConfigValidationReport
    model_cost_incurred: Literal[True] = True
    persisted: Literal[False] = False


class SuiteCatalogItem(_McpModel):
    """Lightweight suite metadata."""

    suite_id: str
    status: str | None = None
    behavior_name: str | None = None
    behavior_category_count: int = 0
    prompt_test_case_count: int = 0
    scenario_test_case_count: int = 0
    run_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    latest_run: dict[str, Any] | None = None
    resources: dict[str, str] = Field(default_factory=dict)


class SuiteCatalogPage(_McpModel):
    """Bounded page of suite metadata."""

    items: tuple[SuiteCatalogItem, ...]
    next_cursor: str | None = None


class SuiteResult(_McpModel):
    """One suite's metadata-only detail."""

    schema_version: int
    suite_id: str
    status: str
    behavior: dict[str, Any]
    behavior_category_count: int
    test_case_counts: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None
    run_count: int
    latest_run: dict[str, Any] | None = None
    resources: dict[str, str] = Field(default_factory=dict)


class RunCatalogItem(_McpModel):
    """Lightweight run metadata."""

    suite_id: str
    run_id: str
    status: str | None = None
    current_stage: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str | None = None
    prompt_metrics: dict[str, Any] | None = None
    scenario_metrics: dict[str, Any] | None = None
    models: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] | None = None
    resources: dict[str, str] = Field(default_factory=dict)


class RunCatalogPage(_McpModel):
    """Bounded page of run metadata."""

    items: tuple[RunCatalogItem, ...]
    next_cursor: str | None = None


class RunResult(_McpModel):
    """One run's metadata-only detail."""

    schema_version: int
    suite_id: str
    run_id: str
    state: str
    current_stage: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str | None = None
    stages: dict[str, Any] = Field(default_factory=dict)
    stage_timings: dict[str, Any] = Field(default_factory=dict)
    stage_summaries: dict[str, Any] = Field(default_factory=dict)
    models: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    resources: dict[str, str] = Field(default_factory=dict)


class RunReferenceInput(_McpModel):
    """Suite/run pair accepted by comparison tools."""

    suite_id: str
    run_id: str


class RunComparisonResult(_McpModel):
    """Structured comparison across two or more runs."""

    metric: str
    baseline: str
    runs: list[dict[str, Any]]
    dimension_deltas: dict[str, dict[str, Any]]
    behavior_category_deltas: list[dict[str, Any]]
    warnings: list[str]


class TestCasePage(_McpModel):
    """Bounded page of test-case rows."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None


class TestCaseResult(_McpModel):
    """One complete test case."""

    row: dict[str, Any]
    resource_uri: str


class ScorePage(_McpModel):
    """Bounded page of score rows."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None


class FailurePage(_McpModel):
    """Bounded page of failure rows."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None


class TranscriptResult(_McpModel):
    """One inference transcript joined with its test case and score."""

    suite_id: str
    run_id: str
    type: str
    test_case_id: str
    test_case: dict[str, Any] | None = None
    inference: dict[str, Any]
    score: dict[str, Any] | None = None
    resource_uri: str
