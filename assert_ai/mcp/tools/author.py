# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Config authoring, pure preflight, and isolated probe MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.errors import adapt_tool_errors, invoke_tool
from assert_ai.mcp.models import (
    ConfigDesignResult,
    ConfigSaveToolResult,
    ConfigValidationResult,
)
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.mcp.uris import config_uri
from assert_ai.services.configs import (
    ConfigDesignRequest,
    ConfigService,
)
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.run_planning import (
    EvaluationOverrides,
    EvaluationPreflight,
    RunPlanningService,
)
from assert_ai.services.target_probe import (
    TargetProbeResult,
    TargetProbeService,
)

_PURE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_WRITE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)
_DESIGN_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
_PROBE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


@dataclass(frozen=True, slots=True)
class AuthorServices:
    workspace: WorkspaceService
    configs: ConfigService
    planning: RunPlanningService
    max_response_bytes: int
    allowed_model_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeServices:
    workspace: WorkspaceService
    probe: TargetProbeService
    max_response_bytes: int


def register_author_tools(
    server: MCPServer,
    services: AuthorServices,
) -> None:
    """Register deterministic authoring and preflight tools."""

    @server.tool(
        title="Validate an ASSERT config",
        annotations=_PURE_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def validate_config(
        config_ref: str | None = None,
        yaml_text: str | None = None,
        document: dict[str, Any] | None = None,
        validation_ref: str = "draft.yaml",
    ) -> ConfigValidationResult:
        """Validate exactly one managed config, YAML draft, or config document."""
        _require_exactly_one_config_source(
            config_ref=config_ref,
            yaml_text=yaml_text,
            document=document,
        )
        if config_ref is not None:
            record = invoke_tool(
                lambda: services.configs.get_config(config_ref),
                workspace=services.workspace,
            )
            return ConfigValidationResult.model_validate(
                sanitize_for_mcp(
                    ConfigValidationResult(
                        source="config",
                        config_ref=record.config_ref,
                        validation=record.validation,
                    ),
                    workspace=services.workspace,
                )
            )
        if yaml_text is not None:
            report = invoke_tool(
                lambda: services.configs.validate_yaml(
                    yaml_text,
                    config_ref=validation_ref,
                ),
                workspace=services.workspace,
            )
            return ConfigValidationResult.model_validate(
                sanitize_for_mcp(
                    ConfigValidationResult(
                        source="yaml",
                        config_ref=validation_ref,
                        validation=report,
                    ),
                    workspace=services.workspace,
                )
            )
        assert document is not None
        report = invoke_tool(
            lambda: services.configs.validate_document(
                document,
                config_ref=validation_ref,
            ),
            workspace=services.workspace,
        )
        return ConfigValidationResult.model_validate(
            sanitize_for_mcp(
                ConfigValidationResult(
                    source="document",
                    config_ref=validation_ref,
                    validation=report,
                ),
                workspace=services.workspace,
            )
        )

    @server.tool(
        title="Save an ASSERT config",
        annotations=_WRITE_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def save_config(
        config_ref: str,
        yaml_text: str | None = None,
        document: dict[str, Any] | None = None,
        expected_etag: str | None = None,
    ) -> ConfigSaveToolResult:
        """Validate and atomically create or replace one managed config."""
        if (yaml_text is None) == (document is None):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Provide exactly one of yaml_text or document",
            )
        saved = invoke_tool(
            lambda: services.configs.save_config(
                config_ref,
                yaml_text=yaml_text,
                document=document,
                expected_etag=expected_etag,
            ),
            workspace=services.workspace,
        )
        return ConfigSaveToolResult.model_validate(
            sanitize_for_mcp(
                ConfigSaveToolResult(
                    config_ref=saved.config_ref,
                    etag=saved.etag,
                    created=saved.created,
                    validation=saved.validation,
                    resource_uri=config_uri(saved.config_ref),
                ),
                workspace=services.workspace,
            )
        )

    @server.tool(
        title="Preflight an ASSERT evaluation",
        annotations=_PURE_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def preflight_evaluation(
        config_ref: str,
        overrides: EvaluationOverrides | None = None,
    ) -> EvaluationPreflight:
        """Plan an exact effective run without importing targets or writing files."""
        plan = invoke_tool(
            lambda: services.planning.preflight(
                config_ref,
                overrides=overrides,
            ),
            workspace=services.workspace,
        )
        payload = plan.model_dump(mode="json")
        credentials = payload.pop("credentials")
        sanitized = sanitize_for_mcp(
            payload,
            workspace=services.workspace,
        )
        sanitized["credentials"] = credentials
        return EvaluationPreflight.model_validate(sanitized)


def register_design_tools(
    server: MCPServer,
    services: AuthorServices,
) -> None:
    """Register the model-backed, non-persisting config designer."""

    @server.tool(
        title="Design an ASSERT config",
        annotations=_DESIGN_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def design_config(
        description: Annotated[str, Field(min_length=1)],
        model: Annotated[str, Field(min_length=1)] = "azure/gpt-5.4-mini",
        seed_config_ref: str | None = None,
        seed_yaml: str | None = None,
        behavior_preset: str | None = None,
        judge_preset: str | None = None,
        dimension_hints: str | None = None,
        default_model_hint: str | None = None,
        max_turns: Annotated[int, Field(ge=1, le=100)] = 5,
    ) -> ConfigDesignResult:
        """Call ASSERT's design model and return an unpersisted config draft."""
        description = description.strip()
        model = model.strip()
        if not description:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "description must not be blank",
            )
        if not model:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "model must not be blank",
            )
        if services.allowed_model_patterns and not any(
            fnmatchcase(model, pattern)
            for pattern in services.allowed_model_patterns
        ):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"Design model {model!r} is not allowed by server policy",
            )
        draft = invoke_tool(
            lambda: services.configs.design_config(
                ConfigDesignRequest(
                    description=description,
                    model=model,
                    seed_config_ref=seed_config_ref,
                    seed_yaml=seed_yaml,
                    behavior_preset=behavior_preset,
                    judge_preset=judge_preset,
                    dimension_hints=dimension_hints,
                    default_model_hint=default_model_hint,
                    max_turns=max_turns,
                )
            ),
            workspace=services.workspace,
        )
        return ConfigDesignResult.model_validate(
            sanitize_for_mcp(
                ConfigDesignResult(
                    yaml=draft.yaml,
                    document=draft.document,
                    validation=draft.validation,
                ),
                workspace=services.workspace,
            )
        )


def register_probe_tools(
    server: MCPServer,
    services: ProbeServices,
) -> None:
    """Register disposable-subprocess target probing."""

    @server.tool(
        title="Probe an ASSERT target",
        annotations=_PROBE_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def probe_target(config_ref: str) -> TargetProbeResult:
        """Import and inspect a managed config's target in an isolated process."""
        result = invoke_tool(
            lambda: services.probe.probe(config_ref),
            workspace=services.workspace,
        )
        return TargetProbeResult.model_validate(
            sanitize_for_mcp(result, workspace=services.workspace)
        )


def _require_exactly_one_config_source(
    *,
    config_ref: str | None,
    yaml_text: str | None,
    document: dict[str, Any] | None,
) -> None:
    if sum(
        value is not None
        for value in (config_ref, yaml_text, document)
    ) != 1:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Provide exactly one of config_ref, yaml_text, or document",
        )
