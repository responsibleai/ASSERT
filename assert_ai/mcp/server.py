# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""MCP v2 server factory and stdio entry point for ASSERT."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from assert_ai.core.runtime_path_policy import RuntimePathPolicy
from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.models import (
    CapabilityGroup,
    ServerLimits,
    ServerInfo,
    ServerMode,
    WorkspaceInfo,
)
from assert_ai.mcp.resources import register_inspect_resources
from assert_ai.mcp.tools import (
    AuthorServices,
    CurationServices,
    InspectServices,
    JobServices,
    ProbeServices,
    register_author_tools,
    register_curation_tools,
    register_design_tools,
    register_inspect_tools,
    register_job_control_tools,
    register_job_execute_tools,
    register_job_inspect_tools,
    register_probe_tools,
    register_trace_tools,
)
from assert_ai.services.artifacts import ArtifactRepository
from assert_ai.services.configs import ConfigService
from assert_ai.services.curation import CurationService
from assert_ai.services.evaluations import (
    EvaluationJobManager,
    EvaluationService,
)
from assert_ai.services.job_store import JobStore
from assert_ai.services.library import LibraryService
from assert_ai.services.results import ResultRepository
from assert_ai.services.run_planning import (
    PreflightPolicy,
    RunPlanningService,
)
from assert_ai.services.target_probe import TargetProbeService

SERVER_NAME = "ASSERT"

_MODE_GROUPS: dict[ServerMode, tuple[CapabilityGroup, ...]] = {
    ServerMode.INSPECT: (CapabilityGroup.INSPECT,),
    ServerMode.AUTHOR: (
        CapabilityGroup.INSPECT,
        CapabilityGroup.AUTHOR,
    ),
    ServerMode.FULL: (
        CapabilityGroup.INSPECT,
        CapabilityGroup.AUTHOR,
        CapabilityGroup.DESIGN,
        CapabilityGroup.EXECUTE,
        CapabilityGroup.PROBE,
        CapabilityGroup.CURATE,
    ),
}
_GROUP_ORDER = {group: index for index, group in enumerate(CapabilityGroup)}
_AUTHOR_EXTENSION_GROUPS = {
    CapabilityGroup.DESIGN,
    CapabilityGroup.PROBE,
}


@dataclass(frozen=True)
class ServerOptions:
    """Launch-time settings fixed for the lifetime of one MCP server."""

    workspace_root: Path
    mode: ServerMode = ServerMode.INSPECT
    enabled_groups: tuple[CapabilityGroup, ...] = ()
    default_page_size: int = 50
    max_page_size: int = 200
    max_response_bytes: int = 1024 * 1024
    default_artifact_chunk_bytes: int = 64 * 1024
    max_artifact_chunk_bytes: int = 256 * 1024
    max_config_bytes: int = 256 * 1024
    max_concurrency: int = 32
    max_active_jobs: int = 1
    max_queued_jobs: int = 100
    max_job_log_bytes: int = 1024 * 1024
    max_trace_input_bytes: int = 64 * 1024 * 1024
    cancellation_grace_seconds: float = 10.0
    max_prompt_sample_size: int = 100_000
    max_scenario_sample_size: int = 100_000
    allowed_model_patterns: tuple[str, ...] = ()
    allowed_endpoint_hosts: tuple[str, ...] = ()
    target_probe_timeout_s: float = 15.0
    workspace: WorkspaceService = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.default_page_size < 1:
            raise ValueError("default_page_size must be positive")
        if self.max_page_size < self.default_page_size:
            raise ValueError("max_page_size must be >= default_page_size")
        if self.max_response_bytes < 4096:
            raise ValueError("max_response_bytes must be at least 4096")
        if self.max_config_bytes < 1:
            raise ValueError("max_config_bytes must be positive")
        if self.max_config_bytes > self.max_response_bytes:
            raise ValueError("max_config_bytes must not exceed max_response_bytes")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if self.max_active_jobs < 1:
            raise ValueError("max_active_jobs must be positive")
        if self.max_queued_jobs < 1:
            raise ValueError("max_queued_jobs must be positive")
        if not 4096 <= self.max_job_log_bytes <= 16 * 1024 * 1024:
            raise ValueError(
                "max_job_log_bytes must be between 4096 and 16777216"
            )
        if not 1024 <= self.max_trace_input_bytes <= 64 * 1024 * 1024:
            raise ValueError(
                "max_trace_input_bytes must be between 1024 and 67108864"
            )
        if self.cancellation_grace_seconds <= 0:
            raise ValueError(
                "cancellation_grace_seconds must be positive"
            )
        if self.max_prompt_sample_size < 1:
            raise ValueError("max_prompt_sample_size must be positive")
        if self.max_scenario_sample_size < 1:
            raise ValueError("max_scenario_sample_size must be positive")
        if self.target_probe_timeout_s <= 0:
            raise ValueError("target_probe_timeout_s must be positive")
        PreflightPolicy(
            max_concurrency=self.max_concurrency,
            max_prompt_sample_size=self.max_prompt_sample_size,
            max_scenario_sample_size=self.max_scenario_sample_size,
            allowed_model_patterns=self.allowed_model_patterns,
            allowed_endpoint_hosts=self.allowed_endpoint_hosts,
        )
        if self.default_artifact_chunk_bytes < 4:
            raise ValueError("default_artifact_chunk_bytes must be at least 4")
        if self.max_artifact_chunk_bytes < self.default_artifact_chunk_bytes:
            raise ValueError(
                "max_artifact_chunk_bytes must be >= default_artifact_chunk_bytes"
            )
        if self.max_artifact_chunk_bytes * 2 > self.max_response_bytes:
            raise ValueError(
                "max_artifact_chunk_bytes must not exceed half max_response_bytes"
            )
        workspace = WorkspaceService.create(self.workspace_root)
        object.__setattr__(self, "workspace_root", workspace.root)
        object.__setattr__(self, "workspace", workspace)

    @classmethod
    def create(
        cls,
        *,
        workspace_root: str | Path,
        mode: str | ServerMode = ServerMode.INSPECT,
        enabled_groups: Iterable[str | CapabilityGroup] = (),
        default_page_size: int = 50,
        max_page_size: int = 200,
        max_response_bytes: int = 1024 * 1024,
        default_artifact_chunk_bytes: int = 64 * 1024,
        max_artifact_chunk_bytes: int = 256 * 1024,
        max_config_bytes: int = 256 * 1024,
        max_concurrency: int = 32,
        max_active_jobs: int = 1,
        max_queued_jobs: int = 100,
        max_job_log_bytes: int = 1024 * 1024,
        max_trace_input_bytes: int = 64 * 1024 * 1024,
        cancellation_grace_seconds: float = 10.0,
        max_prompt_sample_size: int = 100_000,
        max_scenario_sample_size: int = 100_000,
        allowed_model_patterns: Iterable[str] = (),
        allowed_endpoint_hosts: Iterable[str] = (),
        target_probe_timeout_s: float = 15.0,
    ) -> "ServerOptions":
        parsed_mode = ServerMode(mode)
        parsed_groups = tuple(CapabilityGroup(group) for group in enabled_groups)
        invalid_groups = _AUTHOR_EXTENSION_GROUPS.intersection(parsed_groups)
        if parsed_mode is ServerMode.INSPECT and invalid_groups:
            names = ", ".join(sorted(group.value for group in invalid_groups))
            raise ValueError(
                f"Capability group(s) {names} require --mode author or --mode full."
            )
        return cls(
            workspace_root=Path(workspace_root),
            mode=parsed_mode,
            enabled_groups=parsed_groups,
            default_page_size=default_page_size,
            max_page_size=max_page_size,
            max_response_bytes=max_response_bytes,
            default_artifact_chunk_bytes=default_artifact_chunk_bytes,
            max_artifact_chunk_bytes=max_artifact_chunk_bytes,
            max_config_bytes=max_config_bytes,
            max_concurrency=max_concurrency,
            max_active_jobs=max_active_jobs,
            max_queued_jobs=max_queued_jobs,
            max_job_log_bytes=max_job_log_bytes,
            max_trace_input_bytes=max_trace_input_bytes,
            cancellation_grace_seconds=cancellation_grace_seconds,
            max_prompt_sample_size=max_prompt_sample_size,
            max_scenario_sample_size=max_scenario_sample_size,
            allowed_model_patterns=tuple(allowed_model_patterns),
            allowed_endpoint_hosts=tuple(allowed_endpoint_hosts),
            target_probe_timeout_s=target_probe_timeout_s,
        )

    @property
    def path_policy(self) -> RuntimePathPolicy:
        return self.workspace.path_policy

    @property
    def capability_groups(self) -> tuple[CapabilityGroup, ...]:
        groups = {*_MODE_GROUPS[self.mode], *self.enabled_groups}
        return tuple(sorted(groups, key=_GROUP_ORDER.__getitem__))


def _server_version() -> str:
    try:
        return version("assert-ai")
    except PackageNotFoundError:
        return "0.1.0"


def build_server(options: ServerOptions) -> MCPServer:
    """Build an in-process MCP server for the configured workspace."""
    server = MCPServer(
        SERVER_NAME,
        description="Local, spec-driven evaluation workflows for AI agents.",
        version=_server_version(),
    )

    configs = ConfigService(
        options.workspace,
        max_config_bytes=options.max_config_bytes,
        default_page_size=options.default_page_size,
        max_page_size=options.max_page_size,
    )
    planning = RunPlanningService(
        options.workspace,
        configs,
        policy=PreflightPolicy(
            max_concurrency=options.max_concurrency,
            max_prompt_sample_size=options.max_prompt_sample_size,
            max_scenario_sample_size=options.max_scenario_sample_size,
            allowed_model_patterns=options.allowed_model_patterns,
            allowed_endpoint_hosts=options.allowed_endpoint_hosts,
        ),
    )
    jobs_db = options.path_policy.resolve_managed_output(
        options.workspace.artifacts_root / "mcp" / "jobs.sqlite3",
        field_name="evaluation job store",
        expected_root=options.workspace.artifacts_root,
        reject_links=True,
    )
    job_store = JobStore(
        jobs_db,
        path_policy=options.path_policy,
        expected_root=options.workspace.artifacts_root,
    )
    execution_enabled = CapabilityGroup.EXECUTE in options.capability_groups
    trace_enabled = CapabilityGroup.TRACE in options.capability_groups
    jobs_enabled = execution_enabled or trace_enabled
    job_manager = EvaluationJobManager(
        options.workspace,
        job_store,
        max_active_jobs=options.max_active_jobs,
        max_log_bytes=options.max_job_log_bytes,
        launch_enabled=jobs_enabled,
        job_kinds=tuple(
            kind
            for kind, enabled in (
                ("evaluation", execution_enabled),
                ("trace_judging", trace_enabled),
            )
            if enabled
        ),
        cancellation_grace_seconds=options.cancellation_grace_seconds,
    )
    evaluations = EvaluationService(
        options.workspace,
        configs,
        planning,
        job_store,
        job_manager,
        default_page_size=options.default_page_size,
        max_page_size=options.max_page_size,
        max_queued_jobs=options.max_queued_jobs,
        max_trace_input_bytes=options.max_trace_input_bytes,
    )
    job_services = JobServices(
        workspace=options.workspace,
        evaluations=evaluations,
        max_response_bytes=options.max_response_bytes,
    )

    @server.tool(
        title="Get ASSERT server information",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    def get_server_info() -> ServerInfo:
        """Describe this ASSERT server's API, workspace, and capabilities."""
        return ServerInfo(
            server_version=_server_version(),
            mode=options.mode,
            enabled_capability_groups=list(options.capability_groups),
            workspace=WorkspaceInfo(
                root=options.workspace.reference(options.workspace.root),
                configs_root=options.workspace.reference(options.workspace.configs_root),
                artifacts_root=options.workspace.reference(options.workspace.artifacts_root),
                results_root=options.workspace.reference(options.workspace.results_root),
            ),
            limits=ServerLimits(
                default_page_size=options.default_page_size,
                max_page_size=options.max_page_size,
                max_response_bytes=options.max_response_bytes,
                default_artifact_chunk_bytes=options.default_artifact_chunk_bytes,
                max_artifact_chunk_bytes=options.max_artifact_chunk_bytes,
                max_config_bytes=options.max_config_bytes,
                max_concurrency=options.max_concurrency,
                max_active_jobs=options.max_active_jobs,
                max_queued_jobs=options.max_queued_jobs,
                max_job_log_bytes=options.max_job_log_bytes,
                max_trace_input_bytes=options.max_trace_input_bytes,
                cancellation_grace_seconds=(
                    options.cancellation_grace_seconds
                ),
                max_prompt_sample_size=options.max_prompt_sample_size,
                max_scenario_sample_size=options.max_scenario_sample_size,
                model_allowlist_enabled=bool(options.allowed_model_patterns),
                endpoint_host_allowlist_enabled=bool(
                    options.allowed_endpoint_hosts
                ),
                allowed_model_patterns=options.allowed_model_patterns,
                allowed_endpoint_hosts=options.allowed_endpoint_hosts,
                target_probe_timeout_s=options.target_probe_timeout_s,
            ),
        )

    if CapabilityGroup.INSPECT in options.capability_groups:
        results = ResultRepository(
            options.workspace.results_root,
            path_policy=options.path_policy,
            default_page_size=options.default_page_size,
            max_page_size=options.max_page_size,
            max_page_bytes=options.max_response_bytes,
            max_item_bytes=options.max_response_bytes,
        )
        services = InspectServices(
            workspace=options.workspace,
            library=LibraryService(
                default_page_size=options.default_page_size,
                max_page_size=options.max_page_size,
            ),
            configs=configs,
            results=results,
            artifacts=ArtifactRepository(
                options.workspace,
                results,
                default_page_size=options.default_page_size,
                max_page_size=options.max_page_size,
                default_chunk_bytes=options.default_artifact_chunk_bytes,
                max_chunk_bytes=options.max_artifact_chunk_bytes,
                max_text_artifact_bytes=options.max_response_bytes,
            ),
            max_response_bytes=options.max_response_bytes,
        )
        register_inspect_tools(server, services)
        register_job_inspect_tools(server, job_services)
        register_inspect_resources(
            server,
            services,
            job_services=job_services,
            inline_artifact_bytes=options.max_artifact_chunk_bytes,
        )

    author_services = AuthorServices(
        workspace=options.workspace,
        configs=configs,
        planning=planning,
        max_response_bytes=options.max_response_bytes,
        allowed_model_patterns=options.allowed_model_patterns,
    )
    if CapabilityGroup.AUTHOR in options.capability_groups:
        register_author_tools(server, author_services)
    if CapabilityGroup.DESIGN in options.capability_groups:
        register_design_tools(server, author_services)
    if CapabilityGroup.PROBE in options.capability_groups:
        register_probe_tools(
            server,
            ProbeServices(
                workspace=options.workspace,
                probe=TargetProbeService(
                    options.workspace,
                    configs,
                    timeout_s=options.target_probe_timeout_s,
                ),
                max_response_bytes=options.max_response_bytes,
            ),
        )
    if CapabilityGroup.CURATE in options.capability_groups:
        register_curation_tools(
            server,
            CurationServices(
                workspace=options.workspace,
                curation=CurationService(
                    options.workspace,
                    job_store=job_store,
                ),
                max_response_bytes=options.max_response_bytes,
            ),
        )
    if execution_enabled:
        register_job_execute_tools(server, job_services)
    if jobs_enabled:
        register_job_control_tools(server, job_services)
    if trace_enabled:
        register_trace_tools(server, job_services)
    if jobs_enabled:
        job_manager.start()

    return server


def run_stdio_server(options: ServerOptions) -> None:
    """Run the configured server over stdio."""
    build_server(options).run("stdio")
