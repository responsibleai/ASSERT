# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Canonical machine-readable model for ASSERT evaluation YAML."""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

EVAL_CONFIG_SCHEMA_VERSION = 1
EVAL_CONFIG_SCHEMA_ID = "https://github.com/responsibleai/ASSERT/schemas/eval-config-v1.json"

PIPELINE_STAGE_ORDER = (
    "systematize",
    "test_set",
    "inference",
    "judge",
)


class _DocumentModel(BaseModel):
    """Base class for strict YAML document nodes."""

    model_config = ConfigDict(extra="forbid")


class ModelDocument(_DocumentModel):
    """One LiteLLM model reference and generation options."""

    name: str = Field(description="Provider-defined model identifier.")
    temperature: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: str | None = Field(default=None)


class BehaviorDocument(_DocumentModel):
    """Behavior specification or reusable behavior preset reference."""

    name: str | None = Field(default=None)
    description: str | None = Field(default=None)
    preset: str | None = Field(default=None)


class StageDocument(_DocumentModel):
    """Fields shared by pipeline stage declarations."""

    enabled: bool | None = Field(default=None)
    file_path: str | None = Field(
        default=None,
        description="Compatibility field accepted by the shared pipeline loader.",
    )


class SystematizeDocument(StageDocument):
    """Configuration for behavior systematization and taxonomy conversion."""

    behavior_category_count: int | None = Field(default=None, gt=0)
    web_search: bool | None = Field(default=None)
    model: ModelDocument | None = Field(default=None)
    save_dir: str | None = Field(default=None)
    validators: Any = Field(default=None, deprecated=True)
    validator_models: Any = Field(default=None, deprecated=True)


class SamplingDocument(_DocumentModel):
    """Assignment sampling controls for one test-case kind."""

    method: Literal["pairwise", "stratified", "full_factorial", "random"] = "pairwise"
    stratify_by: list[str] | None = Field(default=None)
    replication: Literal["balanced", "none"] | None = Field(default=None)
    with_replacement: bool | None = Field(default=None)


class TestCaseGenerationDocument(_DocumentModel):
    """Prompt or scenario generation settings."""

    model: ModelDocument | None = Field(default=None)
    sample_size: int | None = Field(default=None, ge=1, le=100_000)
    timeout_s: float | None = Field(default=None, gt=0)
    sampling: SamplingDocument | None = Field(default=None)
    budget: Any = Field(
        default=None,
        deprecated=True,
        description="Removed alias. Use sample_size.",
    )


class ScenarioGenerationDocument(TestCaseGenerationDocument):
    """Scenario generation settings, including removed compatibility fields."""

    modality: Any = Field(
        default=None,
        deprecated=True,
        description="Removed field. Use test_set.tool_source.",
    )


class DimensionLevelDocument(_DocumentModel):
    """One explicit level of a test-set variation dimension."""

    name: str
    definition: str


class DimensionDocument(_DocumentModel):
    """One explicit or model-generated test-set variation dimension."""

    name: str
    description: str | None = Field(default=None)
    levels: list[DimensionLevelDocument] | None = Field(default=None)


class StratifyDocument(_DocumentModel):
    """Test-set dimension generation and level configuration."""

    dimensions: list[DimensionDocument] | None = Field(default=None)
    level_count: int | None = Field(default=None, gt=0)
    model: ModelDocument | None = Field(default=None)


class TestSetDocument(StageDocument):
    """Configuration for prompt and scenario test-set generation."""

    taxonomy_path: str | None = Field(default=None)
    save_path: str | None = Field(default=None)
    stratify: StratifyDocument | None = Field(default=None)
    tool_source: Literal["runtime", "per_test_case", "per_seed"] | None = Field(
        default=None,
        description="Tool source. per_seed is a deprecated alias for per_test_case.",
    )
    model: ModelDocument | None = Field(default=None)
    timeout_s: float | None = Field(default=None, gt=0)
    prompt: TestCaseGenerationDocument | None = Field(default=None)
    scenario: ScenarioGenerationDocument | None = Field(default=None)
    validators: Any = Field(default=None, deprecated=True)
    validator_model: Any = Field(default=None, deprecated=True)


class ToolsDocument(_DocumentModel):
    """Prompt Agent tool backend or simulator configuration."""

    module: str | None = Field(default=None)
    toolset: str | None = Field(default=None)
    simulator: str | None = Field(default=None)


class TraceDocument(_DocumentModel):
    """OpenTelemetry trace capture configuration for a callable target."""

    backend: str = "phoenix"
    group_by: str = "session.id"


class TargetDocument(_DocumentModel):
    """Hosted model, callable, endpoint, or connector target declaration."""

    model: ModelDocument | None = Field(default=None)
    system_prompt: str | None = Field(default=None)
    tools: ToolsDocument | None = Field(default=None)
    connector: str | None = Field(default=None)
    callable: str | None = Field(default=None)
    endpoint: str | None = Field(default=None)
    trace: TraceDocument | None = Field(default=None)


class TesterDocument(_DocumentModel):
    """Optional model-driven scenario tester."""

    model: ModelDocument | None = Field(default=None)
    max_turns: Any = Field(
        default=None,
        deprecated=True,
        description="Removed field. Use pipeline.inference.max_turns.",
    )


class InferenceDocument(StageDocument):
    """Configuration for executing test cases against a target."""

    target: TargetDocument | None = Field(default=None)
    tester: TesterDocument | None = Field(default=None)
    max_turns: int | None = Field(default=None, gt=0)
    max_tool_calls: int | None = Field(default=None, gt=0)
    tool_timeout_s: float | None = Field(default=None, gt=0)
    startup_timeout_s: float | None = Field(default=None, gt=0)
    concurrency: int | None = Field(default=None, gt=0)
    test_set_path: str | None = Field(default=None)
    save_dir: str | None = Field(default=None)
    strict: bool | None = Field(default=None)


class OrdinalScaleDocument(_DocumentModel):
    """Ordered custom judge scale."""

    type: str
    values: dict[Any, str]


class JudgeDimensionDocument(_DocumentModel):
    """One custom judge dimension and rubric."""

    description: str
    rubric: str
    required_base: bool | None = Field(default=None)
    allow_not_applicable: bool | None = Field(default=None)
    scale: OrdinalScaleDocument | None = Field(default=None)


class JudgeDocument(StageDocument):
    """Configuration for transcript judging."""

    model: ModelDocument | None = Field(default=None)
    n: int | None = Field(default=None, gt=0)
    dimensions: dict[str, JudgeDimensionDocument] | None = Field(default=None)
    disabled_dimensions: list[str] | None = Field(default=None)
    inference_set_path: str | None = Field(default=None)
    taxonomy_path: str | None = Field(default=None)
    save_dir: str | None = Field(default=None)
    preset: Any = Field(default=None)

    @field_validator(
        "preset",
        mode="before",
        json_schema_input_type=str | list[str] | None,
    )
    @classmethod
    def _validate_preset(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    raise ValueError(
                        f"pipeline.judge.preset[{index}] must be a string"
                    )
            return value
        raise ValueError(
            "pipeline.judge.preset must be a string or a list of strings"
        )


class PipelineDocument(_DocumentModel):
    """Canonical ordered ASSERT pipeline declaration."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"minProperties": 1},
    )

    # Non-optional annotations keep JSON Schema stage values object-only while
    # defaults allow each stage key to be omitted.
    systematize: SystematizeDocument = Field(default=None)  # type: ignore[assignment]
    test_set: TestSetDocument = Field(default=None)  # type: ignore[assignment]
    inference: InferenceDocument = Field(default=None)  # type: ignore[assignment]
    judge: JudgeDocument = Field(default=None)  # type: ignore[assignment]

    @model_validator(mode="after")
    def _require_stage(self) -> PipelineDocument:
        if not any(getattr(self, stage_name) is not None for stage_name in PIPELINE_STAGE_ORDER):
            raise ValueError("pipeline must define at least one stage")
        return self


class EvalConfigDocument(_DocumentModel):
    """Complete structural model for ``eval_config.yaml``."""

    suite: str | None = Field(default=None)
    run: str | None = Field(default=None)
    behavior: BehaviorDocument | None = Field(default=None)
    context: str | None = Field(default=None)
    default_model: ModelDocument | None = Field(default=None)
    artifacts_root: str | None = Field(default=None)
    results_dir: str | None = Field(default=None)
    pipeline: PipelineDocument


class ConfigValidationCode(StrEnum):
    """Stable categories for machine-correctable config issues."""

    INVALID_YAML = "INVALID_YAML"
    REQUIRED_FIELD = "REQUIRED_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    SEMANTIC_ERROR = "SEMANTIC_ERROR"
    WORKSPACE_VIOLATION = "WORKSPACE_VIOLATION"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    DEPRECATED_FIELD = "DEPRECATED_FIELD"


class ConfigValidationIssue(BaseModel):
    """One config issue located by an RFC 6901 JSON Pointer."""

    model_config = ConfigDict(frozen=True)

    code: ConfigValidationCode
    path: str
    message: str


class ConfigValidationReport(BaseModel):
    """Versioned structural validation result."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = EVAL_CONFIG_SCHEMA_VERSION
    valid: bool
    issues: tuple[ConfigValidationIssue, ...] = ()
    warnings: tuple[ConfigValidationIssue, ...] = ()


class EvalConfigDocumentError(ValueError):
    """Raised when a mapping does not conform to ``EvalConfigDocument``."""

    def __init__(self, issues: tuple[ConfigValidationIssue, ...]) -> None:
        self.issues = issues
        super().__init__(format_config_validation_issues(issues))


def _json_pointer(location: tuple[str | int, ...]) -> str:
    if not location:
        return ""
    parts = []
    for part in location:
        escaped = str(part).replace("~", "~0").replace("/", "~1")
        parts.append(escaped)
    return "/" + "/".join(parts)


def _issue_code(error_type: str) -> ConfigValidationCode:
    if error_type == "missing":
        return ConfigValidationCode.REQUIRED_FIELD
    if error_type == "extra_forbidden":
        return ConfigValidationCode.UNKNOWN_FIELD
    if error_type.endswith(("_type", "_parsing")) or error_type in {
        "bool_type",
        "dict_type",
        "float_type",
        "int_type",
        "list_type",
        "model_type",
        "string_type",
    }:
        return ConfigValidationCode.INVALID_TYPE
    return ConfigValidationCode.INVALID_VALUE


def _validation_issues(exc: ValidationError) -> tuple[ConfigValidationIssue, ...]:
    issues: list[ConfigValidationIssue] = []
    for error in exc.errors(
        include_context=False,
        include_input=False,
        include_url=False,
    ):
        location = tuple(error.get("loc") or ())
        issues.append(
            ConfigValidationIssue(
                code=_issue_code(str(error.get("type") or "")),
                path=_json_pointer(location),
                message=str(error.get("msg") or "Invalid value"),
            )
        )
    return tuple(issues)


def validate_eval_config_document(raw: Any) -> ConfigValidationReport:
    """Validate a decoded YAML value without resolving paths or loading presets."""
    try:
        EvalConfigDocument.model_validate(raw)
    except ValidationError as exc:
        issues = _validation_issues(exc)
        return ConfigValidationReport(valid=False, issues=issues)
    return ConfigValidationReport(valid=True)


def require_valid_eval_config_document(raw: Any) -> EvalConfigDocument:
    """Return the parsed document or raise a stable issue-bearing error."""
    try:
        return EvalConfigDocument.model_validate(raw)
    except ValidationError as exc:
        raise EvalConfigDocumentError(_validation_issues(exc)) from exc


def validate_eval_config_yaml(yaml_text: str) -> ConfigValidationReport:
    """Validate YAML syntax and the decoded config document."""
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        issue = ConfigValidationIssue(
            code=ConfigValidationCode.INVALID_YAML,
            path="",
            message=str(exc),
        )
        return ConfigValidationReport(valid=False, issues=(issue,))
    return validate_eval_config_document(raw)


def format_config_validation_issues(
    issues: tuple[ConfigValidationIssue, ...] | list[ConfigValidationIssue],
) -> str:
    """Render stable compact issue lines for CLI and init feedback."""
    return "; ".join(
        f"{issue.code.value} {issue.path or '/'}: {issue.message}"
        for issue in issues
    )


def get_eval_config_json_schema() -> dict[str, Any]:
    """Return the versioned JSON Schema used by MCP and other adapters."""
    schema = deepcopy(EvalConfigDocument.model_json_schema(mode="validation"))
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = EVAL_CONFIG_SCHEMA_ID
    schema["x-assert-schema-version"] = EVAL_CONFIG_SCHEMA_VERSION
    return schema
