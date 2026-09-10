# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Config authoring, pure preflight, and isolated probe MCP tools."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from assert_ai.mcp.dependencies import AuthorServices, ProbeServices
from assert_ai.mcp.errors import adapt_tool_errors
from assert_ai.mcp.models import (
    ConfigDesignResult,
    ConfigSaveToolResult,
    ConfigValidationResult,
)
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.mcp.uris import config_uri
from assert_ai.services.configs import ConfigDesignRequest
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.run_planning import (
    EvaluationOverrides,
    EvaluationPreflight,
)
from assert_ai.services.target_probe import TargetProbeResult

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
_OPEN_WORLD_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


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
        source: Literal["config", "yaml", "document"]
        resolved_ref = validation_ref
        if config_ref is not None:
            source = "config"
            record = services.configs.get_config(config_ref)
            resolved_ref = record.config_ref
            report = record.validation
        elif yaml_text is not None:
            source = "yaml"
            report = services.configs.validate_yaml(
                yaml_text,
                config_ref=validation_ref,
            )
        else:
            source = "document"
            assert document is not None
            report = services.configs.validate_document(
                document,
                config_ref=validation_ref,
            )
        return ConfigValidationResult.model_validate(
            sanitize_for_mcp(
                ConfigValidationResult(
                    source=source,
                    config_ref=resolved_ref,
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
        saved = services.configs.save_config(
            config_ref,
            yaml_text=yaml_text,
            document=document,
            expected_etag=expected_etag,
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
        plan = services.planning.preflight(
            config_ref,
            overrides=overrides,
        )
        sanitized = sanitize_for_mcp(
            plan,
            workspace=services.workspace,
        )
        sanitized["credentials"] = [
            credential.model_dump(mode="json") for credential in plan.credentials
        ]
        return EvaluationPreflight.model_validate(sanitized)


def register_design_tools(
    server: MCPServer,
    services: AuthorServices,
) -> None:
    """Register the model-backed, non-persisting config designer."""

    @server.tool(
        title="Design an ASSERT config",
        annotations=_OPEN_WORLD_ANNOTATIONS,
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
        draft = services.configs.design_config(
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
        annotations=_OPEN_WORLD_ANNOTATIONS,
        structured_output=True,
    )
    @adapt_tool_errors(
        services.workspace,
        max_response_bytes=services.max_response_bytes,
    )
    def probe_target(config_ref: str) -> TargetProbeResult:
        """Import and inspect a managed config's target in an isolated process."""
        result = services.probe.probe(config_ref)
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
