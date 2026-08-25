# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure evaluation preflight and spend-policy checks."""

from __future__ import annotations

import importlib.util
import os
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from assert_ai.config import ConfigError, load_runtime_context
from assert_ai.core.artifact_cache import (
    activate_artifact_plan,
    activate_latest_artifacts,
    find_reusable_artifact_plan,
    is_cacheable_stage,
    supports_artifact_cache,
)
from assert_ai.core.config_document import (
    ConfigValidationIssue,
    ConfigValidationReport,
    PIPELINE_STAGE_ORDER,
)
from assert_ai.core.run_plan import resolve_forced_stages
from assert_ai.core.security import (
    validate_callable_ref,
    validate_module_ref,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.configs import ConfigService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.stages import STAGES

StageName = Literal["systematize", "test_set", "inference", "judge"]


class _ServiceModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ModelOverrides(_ServiceModel):
    """Explicit model substitutions applied only to existing config roles."""

    default_model: str | None = Field(default=None, min_length=1)
    systematize_model: str | None = Field(default=None, min_length=1)
    test_set_model: str | None = Field(default=None, min_length=1)
    prompt_generator_model: str | None = Field(default=None, min_length=1)
    scenario_generator_model: str | None = Field(default=None, min_length=1)
    stratify_model: str | None = Field(default=None, min_length=1)
    target_model: str | None = Field(default=None, min_length=1)
    tester_model: str | None = Field(default=None, min_length=1)
    judge_model: str | None = Field(default=None, min_length=1)


class EvaluationOverrides(_ServiceModel):
    """Typed operational overrides accepted by pure preflight."""

    suite: str | None = Field(default=None, min_length=1)
    run: str | None = Field(default=None, min_length=1)
    force_stages: tuple[StageName, ...] = ()
    strict: bool = False
    concurrency: int | None = Field(default=None, ge=1)
    prompt_sample_size: int | None = Field(default=None, ge=1)
    scenario_sample_size: int | None = Field(default=None, ge=1)
    models: ModelOverrides = Field(default_factory=ModelOverrides)


@dataclass(frozen=True, slots=True)
class PreflightPolicy:
    """Operator-owned execution limits evaluated before a job can start."""

    max_concurrency: int = 32
    max_prompt_sample_size: int = 100_000
    max_scenario_sample_size: int = 100_000
    allowed_model_patterns: tuple[str, ...] = ()
    allowed_endpoint_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if self.max_prompt_sample_size < 1:
            raise ValueError("max_prompt_sample_size must be positive")
        if self.max_scenario_sample_size < 1:
            raise ValueError("max_scenario_sample_size must be positive")
        if any(not pattern.strip() for pattern in self.allowed_model_patterns):
            raise ValueError("allowed_model_patterns cannot contain empty values")
        if any(not host.strip() for host in self.allowed_endpoint_hosts):
            raise ValueError("allowed_endpoint_hosts cannot contain empty values")


class StageAction(StrEnum):
    DISABLED = "disabled"
    REUSE = "reuse"
    RUN = "run"


class PreflightIssue(_ServiceModel):
    code: str
    path: str = ""
    message: str


class StagePreflight(_ServiceModel):
    name: StageName
    scope: Literal["suite", "run"]
    action: StageAction
    forced: bool = False
    cacheable: bool = False
    artifact_version: str | None = None
    will_call_model: bool = False
    reason: str


class ModelUse(_ServiceModel):
    role: str
    stage: StageName
    model: str
    provider: str


class CredentialRequirement(_ServiceModel):
    provider: str
    variables: dict[str, bool]
    satisfied: bool | None
    note: str


class TargetPreflight(_ServiceModel):
    kind: Literal["model", "callable", "connector", "endpoint"]
    identifier: str
    trace_enabled: bool = False
    static_validation: Literal["valid", "invalid"]
    probe_required: bool = False


class ModelCallEstimate(_ServiceModel):
    minimum: int | None = None
    maximum: int | None = None
    basis: str


class EvaluationPreflight(_ServiceModel):
    config_ref: str
    source_etag: str
    effective_document: dict[str, Any]
    validation: ConfigValidationReport
    ready: bool
    suite_id: str | None = None
    run_id: str | None = None
    strict: bool = False
    concurrency: int | None = None
    sample_sizes: dict[str, int | None] = Field(default_factory=dict)
    target: TargetPreflight | None = None
    stages: tuple[StagePreflight, ...] = ()
    models: tuple[ModelUse, ...] = ()
    credentials: tuple[CredentialRequirement, ...] = ()
    managed_outputs: dict[str, str] = Field(default_factory=dict)
    estimated_model_calls: ModelCallEstimate = Field(
        default_factory=lambda: ModelCallEstimate(
            basis="No enabled stage uses a model.",
            minimum=0,
            maximum=0,
        )
    )
    blocking_issues: tuple[PreflightIssue, ...] = ()
    warnings: tuple[PreflightIssue, ...] = ()


@dataclass(slots=True)
class RunPlanningService:
    """Build an exact, non-mutating execution plan for one managed config."""

    workspace: WorkspaceService
    configs: ConfigService
    policy: PreflightPolicy = PreflightPolicy()

    def preflight(
        self,
        config_ref: str,
        *,
        overrides: EvaluationOverrides | None = None,
    ) -> EvaluationPreflight:
        record = self.configs.get_config(config_ref)
        effective = deepcopy(record.document)
        applied = overrides or EvaluationOverrides()
        _apply_overrides(effective, applied)
        validation = self.configs.validate_document(
            effective,
            config_ref=record.config_ref,
        )
        blocking = [
            _validation_issue(issue)
            for issue in validation.issues
        ]
        warnings = [
            _validation_issue(issue)
            for issue in validation.warnings
        ]
        if not validation.valid:
            return EvaluationPreflight(
                config_ref=record.config_ref,
                source_etag=record.etag,
                effective_document=effective,
                validation=validation,
                ready=False,
                strict=applied.strict,
                blocking_issues=tuple(blocking),
                warnings=tuple(warnings),
            )

        config_path = self.workspace.path_policy.resolve_config_path(
            record.config_ref,
            must_exist=True,
            reject_links=True,
        )
        try:
            ctx = load_runtime_context(
                deepcopy(effective),
                config_path,
                stage_modules=STAGES,
                path_policy=self.workspace.path_policy,
            )
        except (ConfigError, OSError, ValueError) as exc:
            issue = PreflightIssue(
                code=ServiceErrorCode.PREFLIGHT_FAILED.value,
                message=str(exc),
            )
            return EvaluationPreflight(
                config_ref=record.config_ref,
                source_etag=record.etag,
                effective_document=effective,
                validation=validation,
                ready=False,
                strict=applied.strict,
                blocking_issues=(issue,),
                warnings=tuple(warnings),
            )

        configured = [name for name, _ in ctx["stages"]]
        try:
            forced = set(
                resolve_forced_stages(
                    configured,
                    applied.force_stages,
                )
            )
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                str(exc),
            ) from exc

        ctx["strict"] = applied.strict
        models = _collect_model_uses(effective)
        target, target_issues = _target_preflight(ctx, self.policy)
        blocking.extend(target_issues)
        blocking.extend(_policy_issues(effective, models, ctx, self.policy))
        credentials, credential_issues, credential_warnings = (
            _credential_requirements(models)
        )
        blocking.extend(credential_issues)
        warnings.extend(credential_warnings)
        stages = _stage_plan(ctx, forced, models)
        managed_outputs = {
            "artifacts_root": self.workspace.reference(ctx["artifacts_root"]),
            "results_root": self.workspace.reference(ctx["results_dir"]),
            "suite_root": self.workspace.reference(ctx["suite_root"]),
        }
        if ctx.get("run_root") is not None:
            managed_outputs["run_root"] = self.workspace.reference(
                ctx["run_root"]
            )

        concurrency = _effective_concurrency(ctx)
        sample_sizes = _sample_sizes(effective)
        estimate = _estimate_model_calls(stages)
        return EvaluationPreflight(
            config_ref=record.config_ref,
            source_etag=record.etag,
            effective_document=effective,
            validation=validation,
            ready=not blocking,
            suite_id=str(ctx["suite_id"]),
            run_id=(
                str(ctx["run_id"])
                if ctx.get("run_id") is not None
                else None
            ),
            strict=applied.strict,
            concurrency=concurrency,
            sample_sizes=sample_sizes,
            target=target,
            stages=tuple(stages),
            models=tuple(models),
            credentials=tuple(credentials),
            managed_outputs=managed_outputs,
            estimated_model_calls=estimate,
            blocking_issues=tuple(_deduplicate_issues(blocking)),
            warnings=tuple(_deduplicate_issues(warnings)),
        )


def _apply_overrides(
    document: dict[str, Any],
    overrides: EvaluationOverrides,
) -> None:
    if overrides.suite is not None:
        document["suite"] = overrides.suite.strip()
    if overrides.run is not None:
        document["run"] = overrides.run.strip()
    pipeline = document.get("pipeline")
    if not isinstance(pipeline, dict):
        return

    if overrides.concurrency is not None:
        inference = _require_mapping(
            pipeline,
            "inference",
            "concurrency override requires pipeline.inference",
        )
        inference["concurrency"] = overrides.concurrency
    if overrides.prompt_sample_size is not None:
        test_set = _require_mapping(
            pipeline,
            "test_set",
            "prompt sample override requires pipeline.test_set",
        )
        prompt = _require_mapping(
            test_set,
            "prompt",
            "prompt sample override requires pipeline.test_set.prompt",
        )
        prompt["sample_size"] = overrides.prompt_sample_size
    if overrides.scenario_sample_size is not None:
        test_set = _require_mapping(
            pipeline,
            "test_set",
            "scenario sample override requires pipeline.test_set",
        )
        scenario = _require_mapping(
            test_set,
            "scenario",
            "scenario sample override requires pipeline.test_set.scenario",
        )
        scenario["sample_size"] = overrides.scenario_sample_size
    _apply_model_overrides(document, overrides.models)


def _apply_model_overrides(
    document: dict[str, Any],
    overrides: ModelOverrides,
) -> None:
    values = overrides.model_dump(exclude_none=True)
    if not values:
        return
    pipeline = document.get("pipeline")
    if not isinstance(pipeline, dict):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Model overrides require a pipeline mapping",
        )
    if overrides.default_model is not None:
        _replace_model(document, "default_model", overrides.default_model)
    if overrides.systematize_model is not None:
        stage = _require_mapping(
            pipeline,
            "systematize",
            "systematize_model requires pipeline.systematize",
        )
        _replace_model(stage, "model", overrides.systematize_model)
    if overrides.test_set_model is not None:
        stage = _require_mapping(
            pipeline,
            "test_set",
            "test_set_model requires pipeline.test_set",
        )
        _replace_model(stage, "model", overrides.test_set_model)
    for field_name, child_name in (
        ("prompt_generator_model", "prompt"),
        ("scenario_generator_model", "scenario"),
        ("stratify_model", "stratify"),
    ):
        value = getattr(overrides, field_name)
        if value is None:
            continue
        stage = _require_mapping(
            pipeline,
            "test_set",
            f"{field_name} requires pipeline.test_set",
        )
        child = _require_mapping(
            stage,
            child_name,
            f"{field_name} requires pipeline.test_set.{child_name}",
        )
        _replace_model(child, "model", value)
    if overrides.target_model is not None:
        inference = _require_mapping(
            pipeline,
            "inference",
            "target_model requires pipeline.inference",
        )
        target = _require_mapping(
            inference,
            "target",
            "target_model requires pipeline.inference.target",
        )
        if any(target.get(kind) for kind in ("callable", "connector", "endpoint")):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "target_model cannot replace a callable, connector, or endpoint target",
            )
        _replace_model(target, "model", overrides.target_model)
    if overrides.tester_model is not None:
        inference = _require_mapping(
            pipeline,
            "inference",
            "tester_model requires pipeline.inference",
        )
        tester = _require_mapping(
            inference,
            "tester",
            "tester_model requires pipeline.inference.tester",
        )
        _replace_model(tester, "model", overrides.tester_model)
    if overrides.judge_model is not None:
        judge = _require_mapping(
            pipeline,
            "judge",
            "judge_model requires pipeline.judge",
        )
        _replace_model(judge, "model", overrides.judge_model)


def _require_mapping(
    owner: dict[str, Any],
    key: str,
    message: str,
) -> dict[str, Any]:
    value = owner.get(key)
    if not isinstance(value, dict):
        raise ServiceError(ServiceErrorCode.INVALID_ARGUMENT, message)
    return value


def _replace_model(
    owner: dict[str, Any],
    key: str,
    model_name: str,
) -> None:
    name = model_name.strip()
    if not name:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"{key} model name must not be blank",
        )
    current = owner.get(key)
    model = dict(current) if isinstance(current, dict) else {}
    model["name"] = name
    owner[key] = model


def _stage_plan(
    ctx: dict[str, Any],
    forced: set[str],
    models: list[ModelUse],
) -> list[StagePreflight]:
    plans: list[StagePreflight] = []
    if supports_artifact_cache(ctx):
        ctx.setdefault("artifact_versions", {})
        activate_latest_artifacts(ctx, repair=False)
    upstream_cache_miss = False
    model_stages = {model.stage for model in models}

    for stage_name, raw_cfg in ctx["stages"]:
        module = STAGES[stage_name]
        enabled = raw_cfg.get("enabled", True)
        cacheable = is_cacheable_stage(stage_name)
        is_forced = stage_name in forced
        action = StageAction.RUN
        version = None
        reason = "Enabled stage will execute."
        if not enabled:
            action = StageAction.DISABLED
            reason = "Stage is disabled in the effective config."
        elif cacheable and supports_artifact_cache(ctx):
            if is_forced:
                reason = "Forced regeneration invalidates cached output."
                upstream_cache_miss = True
            elif upstream_cache_miss:
                reason = "An upstream cache miss invalidates this stage."
                upstream_cache_miss = True
            else:
                reusable = find_reusable_artifact_plan(
                    ctx=ctx,
                    stage_name=stage_name,
                    raw_cfg=raw_cfg,
                )
                if reusable is not None:
                    action = StageAction.REUSE
                    version = reusable.version
                    reason = "Input hashes match a complete artifact version."
                    activate_artifact_plan(ctx, reusable)
                else:
                    reason = "No complete artifact version matches the inputs."
                    upstream_cache_miss = True
        plans.append(
            StagePreflight(
                name=stage_name,
                scope=module.SCOPE,
                action=action,
                forced=is_forced,
                cacheable=cacheable,
                artifact_version=version,
                will_call_model=(
                    enabled
                    and action is not StageAction.REUSE
                    and stage_name in model_stages
                ),
                reason=reason,
            )
        )
    return plans


def _collect_model_uses(document: dict[str, Any]) -> list[ModelUse]:
    pipeline = document.get("pipeline")
    if not isinstance(pipeline, dict):
        return []
    default = _model_name(document.get("default_model"))
    models: list[ModelUse] = []

    systematize = pipeline.get("systematize")
    if _enabled_stage(systematize):
        _append_model(
            models,
            role="systematize",
            stage="systematize",
            model=_model_name(systematize.get("model")) or default,
        )

    test_set = pipeline.get("test_set")
    if _enabled_stage(test_set):
        stage_model = _model_name(test_set.get("model")) or default
        for role, key in (
            ("test_set_prompt", "prompt"),
            ("test_set_scenario", "scenario"),
            ("test_set_stratify", "stratify"),
        ):
            child = test_set.get(key)
            if isinstance(child, dict):
                child_model = _model_name(child.get("model"))
                if key == "stratify":
                    effective_model = child_model or default or stage_model
                else:
                    effective_model = child_model or stage_model
                _append_model(
                    models,
                    role=role,
                    stage="test_set",
                    model=effective_model,
                )

    inference = pipeline.get("inference")
    if _enabled_stage(inference):
        target = inference.get("target")
        if isinstance(target, dict):
            has_non_model_target = any(
                target.get(kind)
                for kind in ("callable", "connector", "endpoint")
            )
            if not has_non_model_target:
                _append_model(
                    models,
                    role="target",
                    stage="inference",
                    model=_model_name(target.get("model")) or default,
                )
        tester = inference.get("tester")
        if isinstance(tester, dict):
            _append_model(
                models,
                role="tester",
                stage="inference",
                model=_model_name(tester.get("model")) or default,
            )

    judge = pipeline.get("judge")
    if _enabled_stage(judge):
        _append_model(
            models,
            role="judge",
            stage="judge",
            model=_model_name(judge.get("model")) or default,
        )
    return models


def _append_model(
    models: list[ModelUse],
    *,
    role: str,
    stage: StageName,
    model: str | None,
) -> None:
    if model is None:
        return
    models.append(
        ModelUse(
            role=role,
            stage=stage,
            model=model,
            provider=_model_provider(model),
        )
    )


def _enabled_stage(value: Any) -> bool:
    return isinstance(value, dict) and value.get("enabled", True)


def _model_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    return str(name) if isinstance(name, str) and name else None


def _model_provider(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[0].lower()
    if model.lower().startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return "unknown"


def _target_preflight(
    ctx: dict[str, Any],
    policy: PreflightPolicy,
) -> tuple[TargetPreflight | None, list[PreflightIssue]]:
    target = ctx.get("target")
    if target is None:
        return None, []
    issues: list[PreflightIssue] = []
    kind: Literal["model", "callable", "connector", "endpoint"]
    identifier: str
    probe_required = False
    try:
        if target.model is not None:
            kind = "model"
            identifier = str(target.model.name)
        elif target.callable:
            kind = "callable"
            identifier = str(target.callable)
            validate_callable_ref(identifier)
            probe_required = True
        elif target.connector:
            kind = "connector"
            identifier = str(target.connector)
            validate_module_ref(identifier)
            probe_required = True
        else:
            kind = "endpoint"
            identifier = _endpoint_origin(str(target.endpoint or ""))
            host = urlsplit(str(target.endpoint or "")).hostname
            if host is None:
                raise ValueError("Endpoint target must include a hostname")
            if policy.allowed_endpoint_hosts and not any(
                fnmatchcase(host.lower(), pattern.lower())
                for pattern in policy.allowed_endpoint_hosts
            ):
                issues.append(
                    PreflightIssue(
                        code="ENDPOINT_NOT_ALLOWED",
                        path="/pipeline/inference/target/endpoint",
                        message=(
                            f"Endpoint host {host!r} is not allowed by server policy"
                        ),
                    )
                )
    except ValueError as exc:
        issues.append(
            PreflightIssue(
                code="TARGET_INVALID",
                path="/pipeline/inference/target",
                message=str(exc),
            )
        )
        return None, issues
    return (
        TargetPreflight(
            kind=kind,
            identifier=identifier,
            trace_enabled=target.trace is not None,
            static_validation="invalid" if issues else "valid",
            probe_required=probe_required,
        ),
        issues,
    )


def _endpoint_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Endpoint target must use http or https and include a hostname")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, "", "", ""))


def _policy_issues(
    document: dict[str, Any],
    models: list[ModelUse],
    ctx: dict[str, Any],
    policy: PreflightPolicy,
) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    concurrency = _effective_concurrency(ctx)
    if concurrency is not None and concurrency > policy.max_concurrency:
        issues.append(
            PreflightIssue(
                code="CONCURRENCY_LIMIT_EXCEEDED",
                path="/pipeline/inference/concurrency",
                message=(
                    f"Concurrency {concurrency} exceeds the server limit "
                    f"of {policy.max_concurrency}"
                ),
            )
        )
    sample_sizes = _sample_sizes(document)
    for kind, limit in (
        ("prompt", policy.max_prompt_sample_size),
        ("scenario", policy.max_scenario_sample_size),
    ):
        value = sample_sizes[kind]
        if value is not None and value > limit:
            issues.append(
                PreflightIssue(
                    code="SAMPLE_SIZE_LIMIT_EXCEEDED",
                    path=f"/pipeline/test_set/{kind}/sample_size",
                    message=(
                        f"{kind} sample size {value} exceeds the server limit "
                        f"of {limit}"
                    ),
                )
            )
    if policy.allowed_model_patterns:
        for model in models:
            if any(
                fnmatchcase(model.model, pattern)
                for pattern in policy.allowed_model_patterns
            ):
                continue
            issues.append(
                PreflightIssue(
                    code="MODEL_NOT_ALLOWED",
                    path=_model_role_path(model.role),
                    message=(
                        f"Model {model.model!r} is not allowed by server policy"
                    ),
                )
            )
    return issues


def _effective_concurrency(ctx: dict[str, Any]) -> int | None:
    evaluation = ctx.get("evaluation")
    inference = (
        getattr(evaluation, "inference", None)
        if evaluation is not None
        else None
    )
    value = getattr(inference, "concurrency", None)
    return int(value) if isinstance(value, int) else None


def _sample_sizes(document: dict[str, Any]) -> dict[str, int | None]:
    pipeline = document.get("pipeline")
    test_set = (
        pipeline.get("test_set")
        if isinstance(pipeline, dict)
        else None
    )
    result: dict[str, int | None] = {"prompt": None, "scenario": None}
    if not isinstance(test_set, dict):
        return result
    for kind in result:
        value = test_set.get(kind)
        sample_size = value.get("sample_size") if isinstance(value, dict) else None
        result[kind] = sample_size if isinstance(sample_size, int) else None
    return result


def _credential_requirements(
    models: list[ModelUse],
) -> tuple[
    list[CredentialRequirement],
    list[PreflightIssue],
    list[PreflightIssue],
]:
    requirements: list[CredentialRequirement] = []
    blockers: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []
    providers = sorted({model.provider for model in models})
    for provider in providers:
        variables: dict[str, bool]
        satisfied: bool | None
        note: str
        if provider == "azure":
            variables = _configured_variables(
                "AZURE_API_BASE",
                "AZURE_API_KEY",
                "ASSERT_AZURE_USE_AAD",
            )
            aad_available = _module_available("azure.identity")
            satisfied = variables["AZURE_API_BASE"] and (
                variables["AZURE_API_KEY"] or aad_available
            )
            note = (
                "AZURE_API_BASE is required; authentication may use "
                "AZURE_API_KEY or the Azure identity chain."
            )
        elif provider == "azure_ai":
            variables = _configured_variables(
                "AZURE_AI_API_BASE",
                "AZURE_AI_API_KEY",
                "ASSERT_AZURE_USE_AAD",
            )
            aad_available = _module_available("azure.identity")
            satisfied = variables["AZURE_AI_API_BASE"] and (
                variables["AZURE_AI_API_KEY"] or aad_available
            )
            note = (
                "AZURE_AI_API_BASE is required; authentication may use "
                "AZURE_AI_API_KEY or the Azure identity chain."
            )
        elif provider == "openai":
            variables = _configured_variables("OPENAI_API_KEY")
            satisfied = variables["OPENAI_API_KEY"]
            note = "OPENAI_API_KEY is required for OpenAI-hosted models."
        elif provider == "anthropic":
            variables = _configured_variables("ANTHROPIC_API_KEY")
            satisfied = variables["ANTHROPIC_API_KEY"]
            note = "ANTHROPIC_API_KEY is required for Anthropic-hosted models."
        elif provider in {"gemini", "google"}:
            variables = _configured_variables(
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
            )
            satisfied = any(variables.values())
            note = "Configure GEMINI_API_KEY or GOOGLE_API_KEY."
        elif provider in {"bedrock", "vertex_ai", "unknown"}:
            variables = {}
            satisfied = None
            note = (
                "Credential readiness cannot be determined from environment "
                "variable names alone for this provider."
            )
        else:
            variables = {}
            satisfied = None
            note = "Provider-specific credential requirements are not known."

        requirements.append(
            CredentialRequirement(
                provider=provider,
                variables=variables,
                satisfied=satisfied,
                note=note,
            )
        )
        if satisfied is False:
            blockers.append(
                PreflightIssue(
                    code="CREDENTIAL_CONFIGURATION_MISSING",
                    path="",
                    message=f"{provider} credential configuration is incomplete",
                )
            )
        elif satisfied is None:
            warnings.append(
                PreflightIssue(
                    code="CREDENTIAL_CONFIGURATION_UNKNOWN",
                    path="",
                    message=(
                        f"Credential readiness for provider {provider!r} "
                        "must be confirmed by the operator"
                    ),
                )
            )
    return requirements, blockers, warnings


def _configured_variables(*names: str) -> dict[str, bool]:
    return {name: bool(os.environ.get(name)) for name in names}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _estimate_model_calls(
    stages: list[StagePreflight],
) -> ModelCallEstimate:
    active_model_stages = [
        stage
        for stage in stages
        if stage.will_call_model
    ]
    if not active_model_stages:
        return ModelCallEstimate(
            minimum=0,
            maximum=0,
            basis="No non-reused enabled stage uses a model.",
        )
    return ModelCallEstimate(
        minimum=0,
        maximum=None,
        basis=(
            "Exact calls are not derivable before test-case generation and "
            "depend on retries, tester turns, and target behavior."
        ),
    )


def _model_role_path(role: str) -> str:
    return {
        "systematize": "/pipeline/systematize/model",
        "test_set_prompt": "/pipeline/test_set/prompt/model",
        "test_set_scenario": "/pipeline/test_set/scenario/model",
        "test_set_stratify": "/pipeline/test_set/stratify/model",
        "target": "/pipeline/inference/target/model",
        "tester": "/pipeline/inference/tester/model",
        "judge": "/pipeline/judge/model",
    }.get(role, "")


def _validation_issue(issue: ConfigValidationIssue) -> PreflightIssue:
    return PreflightIssue(
        code=issue.code.value,
        path=issue.path,
        message=issue.message,
    )


def _deduplicate_issues(
    issues: list[PreflightIssue],
) -> list[PreflightIssue]:
    result: list[PreflightIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
