# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Minimal sequential runner for the ASSERT stage pipeline."""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import find_dotenv, load_dotenv

from assert_ai.config import (
    ConfigError,
    PIPELINE_STAGE_ORDER,
    load_config,
    load_runtime_context,
)
from assert_ai.core.artifact_cache import (
    activate_latest_artifacts,
    activate_artifact_plan,
    discard_artifact_plan,
    finalize_artifact_plan,
    is_cacheable_stage,
    override_cacheable_output_paths,
    prepare_artifact_plan,
    preview_artifact_plan,
    refresh_compatibility_files,
    supports_artifact_cache,
    update_latest,
)
from assert_ai.core.azure_auth import refresh_azure_auth_mode
from assert_ai.core.config_model import RunManifest, SuiteMetadata
from assert_ai.core.io import write_json
from assert_ai.core.model_client import (
    LLMAuthError,
    LLMInputError,
    LLMProviderError,
    LLMRateLimitError,
    UsageAccumulator,
    track_usage,
)
from assert_ai.core.runtime_safety import (
    ManifestHeartbeat,
    PipelineWatchdog,
    run_stage_coro,
)
from assert_ai.display import label_metric
from assert_ai.stages import STAGES

# Walk up from cwd so the user's project `.env` is found when assert-ai is
# installed as a wheel. Bare `load_dotenv()` walks up from this file's
# directory, which lives inside the venv's site-packages and misses the
# project `.env`.
load_dotenv(find_dotenv(usecwd=True))

# Force-resolve the Azure auth mode now that ``.env`` has populated the
# environment. Without this, ``model_client``'s lazy resolution would
# fire on the first request (which is fine) — but doing it here lets
# entrypoints log the resolved mode up-front and surfaces missing
# ``azure-identity`` early.
refresh_azure_auth_mode(force=True)

log = logging.getLogger(__name__)

_USAGE_COUNTER_KEYS = (
    "requests",
    "calls",
    "missing_usage_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
)


class _MetricsFormatError(ValueError):
    """Existing metrics.json cannot be merged without risking data loss."""


def _set_nested(raw: dict[str, Any], path: list[str], value: Any) -> None:
    cursor = raw
    for part in path[:-1]:
        next_value = cursor.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ValueError(f"override path {'.'.join(path)} crosses non-mapping key '{part}'")
        cursor = next_value
    cursor[path[-1]] = value


def _apply_config_overrides(raw: dict[str, Any], overrides: list[str] | None) -> dict[str, Any]:
    if not overrides:
        return raw
    raw = dict(raw)
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"invalid override '{override}': expected key=value")
        key, raw_value = override.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid override '{override}': key is empty")
        value = yaml.safe_load(raw_value)
        if key == "test_set.sample_size":
            total = int(value)
            prompt_size = (total + 1) // 2
            scenario_size = total // 2
            _set_nested(raw, ["pipeline", "test_set", "prompt", "sample_size"], prompt_size)
            _set_nested(raw, ["pipeline", "test_set", "scenario", "sample_size"], scenario_size)
            continue
        path = key.split(".")
        if path[0] in {"systematize", "test_set", "inference", "judge"}:
            path = ["pipeline", *path]
        _set_nested(raw, path, value)
    return raw


def _load_context(
    *,
    config: str,
    overrides: list[str] | None = None,
) -> dict[str, Any]:
    """Load one config file into runtime context."""
    cfg_path = Path(config).resolve()
    raw = _apply_config_overrides(load_config(cfg_path), overrides)
    return load_runtime_context(raw, cfg_path, stage_modules=STAGES)


def _write_suite_metadata(ctx: dict[str, Any]) -> None:
    """Write the minimal suite metadata payload."""
    suite_root = Path(ctx["suite_root"])
    suite_path = suite_root / "suite.json"
    existing: dict[str, Any] = {}
    if suite_path.exists():
        try:
            existing = json.loads(suite_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    meta = SuiteMetadata(
        created_at=existing.get("created_at", datetime.now(timezone.utc).isoformat()),
    )
    write_json(suite_path, meta.to_dict())


def _requested_force_stages(
    ctx: dict[str, Any],
    force_stages: list[str] | None,
) -> set[str]:
    """Validate forced stages and cascade each request through downstream stages."""

    requested = set(force_stages or [])
    configured = {stage_name for stage_name, _ in ctx["stages"]}
    invalid = sorted(requested.difference(configured))
    if invalid:
        joined = ", ".join(invalid)
        raise ConfigError(f"--force-stage stage(s) not present in config: {joined}")

    if requested:
        forced_indices = [
            PIPELINE_STAGE_ORDER.index(name)
            for name in requested
            if name in PIPELINE_STAGE_ORDER
        ]
        if forced_indices:
            min_forced_index = min(forced_indices)
            requested.update(
                name
                for name in PIPELINE_STAGE_ORDER[min_forced_index:]
                if name in configured
            )
    return requested


def estimate_pipeline_usage(
    *,
    config: str,
    force_stages: list[str] | None = None,
    overrides: list[str] | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    """Estimate configured token usage without creating artifacts or running stages."""

    ctx = _load_context(config=config, overrides=overrides)
    concurrency_ignored = False
    if concurrency is not None:
        evaluation = ctx.get("evaluation")
        inference_cfg = getattr(evaluation, "inference", None) if evaluation is not None else None
        if inference_cfg is None:
            concurrency_ignored = True
        else:
            inference_cfg.concurrency = concurrency

    requested_force_stages = _requested_force_stages(ctx, force_stages)
    ctx.setdefault("artifact_versions", {})
    cache_supported = supports_artifact_cache(ctx)
    if cache_supported:
        activate_latest_artifacts(ctx, read_only=True)
    cache_chain_reusable = True
    stages_to_run: list[tuple[str, Any, dict[str, Any]]] = []

    for stage_name, raw_cfg in ctx["stages"]:
        if not raw_cfg.get("enabled", True):
            continue

        module = STAGES[stage_name]
        if module.SCOPE == "suite":
            if cache_supported and is_cacheable_stage(stage_name):
                plan = preview_artifact_plan(
                    ctx=ctx,
                    stage_name=stage_name,
                    raw_cfg=raw_cfg,
                    forced=(
                        stage_name in requested_force_stages
                        or not cache_chain_reusable
                    ),
                )
                activate_artifact_plan(ctx, plan)
                if plan.reused:
                    continue
                cache_chain_reusable = False
                raw_cfg = override_cacheable_output_paths(stage_name, raw_cfg, plan)
            elif (
                module.SUITE_OUTPUT
                and stage_name not in requested_force_stages
                and (Path(ctx["suite_root"]) / module.SUITE_OUTPUT).exists()
            ):
                continue

        stages_to_run.append((stage_name, module, raw_cfg))

    from assert_ai.core.token_estimator import estimate_pipeline_tokens

    payload = estimate_pipeline_tokens(
        ctx,
        stages_to_run,
        forced_stages=requested_force_stages,
    ).to_dict()
    if concurrency_ignored:
        payload.setdefault("notes", []).insert(
            0,
            "Concurrency override ignored because this config has no inference stage.",
        )
    return payload


def _build_manifest(ctx: dict[str, Any]) -> RunManifest:
    """Build the initial run manifest."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        host = socket.gethostname()
    except OSError:
        host = None
    return RunManifest(
        started_at=now,
        pid=os.getpid(),
        host=host,
        heartbeat_at=now,
    )


def _write_manifest(manifest: RunManifest, run_root: Path) -> None:
    """Persist manifest to disk, refreshing heartbeat_at on every write."""
    if manifest.status == "running":
        manifest.heartbeat_at = datetime.now(timezone.utc).isoformat()
    manifest_path = run_root / "manifest.json"
    write_json(manifest_path, manifest.to_dict())


def _record_run_artifacts(manifest: RunManifest, ctx: dict[str, Any], run_root: Path) -> None:
    """Copy resolved artifact references into the run manifest and sidecar."""

    artifacts = ctx.get("artifact_versions") or {}
    if not artifacts:
        return
    manifest.artifact_versions = artifacts
    write_json(
        run_root / "artifacts.json",
        {
            "schema_version": 1,
            "artifacts": artifacts,
        },
    )


def _print_stage_start(stage_name: str, ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> None:
    """Print a human-readable stage header."""
    tag = f"[{stage_name}]"
    behavior = ctx.get("behavior") or ""
    if stage_name == "systematize":
        label = behavior.replace("\n", " ").strip()
        if len(label) > 80:
            label = label[:77] + "..."
        systematize_model = ""
        if isinstance(raw_cfg.get("model"), dict):
            systematize_model = raw_cfg["model"].get("name", "")
        model_suffix = f" ({systematize_model})" if systematize_model else ""
        log.info(f'{tag} Generating behavior taxonomy for "{label}"{model_suffix}')
    elif stage_name == "systematization":
        log.info(f"{tag} Refining taxonomy structure...")
    elif stage_name == "__legacy_stratification":
        level_count = raw_cfg.get("level_count")
        factor_count = 0
        dimensions = ctx.get("dimensions") or []
        if isinstance(dimensions, list):
            factor_count = len(dimensions)
        # Always-present "behavior" dimension is generated automatically.
        # Surface it in the count for accuracy.
        synthetic_behavior_factor = 1
        total_factors = factor_count + synthetic_behavior_factor
        stratification_model = ""
        if isinstance(raw_cfg.get("model"), dict):
            stratification_model = raw_cfg["model"].get("name", "")
        model_suffix = f" ({stratification_model})" if stratification_model else ""
        if level_count and factor_count:
            log.info(f"{tag} Building stratification coverage grid: {total_factors} dimensions x {level_count} levels each{model_suffix}...")
        elif factor_count:
            log.info(f"{tag} Building stratification coverage grid: {total_factors} dimensions{model_suffix}...")
        else:
            log.info(f"{tag} Building stratification coverage grid (behavior dimension only){model_suffix}...")
    elif stage_name == "test_set":
        prompt_budget = 0
        scenario_budget = 0
        if isinstance(raw_cfg.get("prompt"), dict):
            prompt_budget = raw_cfg["prompt"].get("budget", 0) or raw_cfg["prompt"].get("sample_size", 0)
        if isinstance(raw_cfg.get("scenario"), dict):
            scenario_budget = raw_cfg["scenario"].get("budget", 0) or raw_cfg["scenario"].get("sample_size", 0)
        behavior_category_count = 0
        taxonomy_path = Path(ctx["suite_root"]) / "taxonomy.json"
        if taxonomy_path.exists():
            try:
                policy_data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
                behavior_category_count = len(
                    policy_data.get("behavior_categories")
                    or []
                )
            except Exception:
                pass
        parts = []
        if prompt_budget:
            parts.append(f"{prompt_budget} prompt{'s' if prompt_budget != 1 else ''}")
        if scenario_budget:
            parts.append(f"{scenario_budget} scenario{'s' if scenario_budget != 1 else ''}")
        detail = f" ({' + '.join(parts)}" if parts else ""
        if detail and behavior_category_count:
            detail += f" from {behavior_category_count} behavior categories)"
        elif detail:
            detail += ")"
        test_case_models = set()
        for kind_key in ("prompt", "scenario"):
            kind_cfg = raw_cfg.get(kind_key)
            if isinstance(kind_cfg, dict) and isinstance(kind_cfg.get("model"), dict):
                test_case_models.add(kind_cfg["model"].get("name", ""))
        test_case_models.discard("")
        model_suffix = f" ({', '.join(sorted(test_case_models))})" if test_case_models else ""
        log.info(f"{tag} Generating test cases{detail}{model_suffix}...")
    elif stage_name == "inference":
        target = ctx.get("target")
        target_name = ""
        if target and target.model:
            target_name = target.model.name or ""
        if target and target.callable:
            target_name = target.callable or target_name
        tester_name = ""
        if isinstance(raw_cfg.get("tester"), dict) and isinstance(raw_cfg["tester"].get("model"), dict):
            tester_name = raw_cfg["tester"]["model"].get("name", "")
        if tester_name and target_name:
            log.info(f"{tag} Running test cases (tester: {tester_name} \u2192 target: {target_name})...")
        elif target_name:
            log.info(f"{tag} Running test cases against target ({target_name})...")
        else:
            log.info(f"{tag} Running test cases against target...")
    elif stage_name == "judge":
        eval_cfg = ctx.get("evaluation")
        judge_model_obj = eval_cfg.judge.model if eval_cfg else None
        if judge_model_obj is not None and hasattr(judge_model_obj, "name"):
            judge_model = judge_model_obj.name or ""
        elif isinstance(judge_model_obj, str):
            judge_model = judge_model_obj
        else:
            judge_model = ""
        if judge_model:
            log.info(f"{tag} Scoring inference rows with judge ({judge_model})...")
        else:
            log.info(f"{tag} Scoring inference rows...")
    else:
        log.info(f"{tag} Starting...")


def _format_token_count(value: int) -> str:
    """Compact human-friendly token count (e.g. '12.5K', '8')."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _format_usage_line(usage: UsageAccumulator | None) -> str:
    """Render a compact ' | N calls · IN→OUT tok · X% cached' suffix."""
    if usage is None or (usage.requests == 0 and usage.calls == 0):
        return ""
    request_count = usage.requests or usage.calls
    if usage.calls == 0:
        return (
            f" | {request_count} call{'s' if request_count != 1 else ''}"
            " · token usage unavailable"
        )
    parts = [f"{request_count} call{'s' if request_count != 1 else ''}"]
    if usage.input_tokens or usage.output_tokens:
        token_summary = (
            f"{_format_token_count(usage.input_tokens)} in / "
            f"{_format_token_count(usage.output_tokens)} out"
        )
        detailed_total = usage.input_tokens + usage.output_tokens
        if usage.total_tokens > detailed_total:
            token_summary += (
                f" / {_format_token_count(usage.total_tokens)} total"
            )
        parts.append(token_summary)
    else:
        parts.append(f"{_format_token_count(usage.total_tokens)} total")
    if usage.missing_usage_calls:
        parts.append(
            f"{usage.calls}/{request_count} usage reported"
        )
    if usage.input_tokens > 0:
        pct = 100.0 * usage.cached_input_tokens / usage.input_tokens
        parts.append(f"{pct:.1f}% cached")
    return " | " + " · ".join(parts)


def _build_run_metrics(
    stage_usage: dict[str, dict[str, Any]],
    total_elapsed: float,
    token_estimate: dict[str, Any] | None = None,
    run_completed: bool = True,
    run_partial: bool = False,
    existing_metrics: dict[str, Any] | None = None,
    stage_merge_modes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Merge invocation usage and aggregate the cumulative metrics payload."""
    prior_stages = {
        str(stage_name): _normalize_stage_usage(
            stage_payload,
            source=f"existing stage {stage_name}",
        )
        for stage_name, stage_payload in (
            (existing_metrics.get("stages") or {}).items()
            if existing_metrics is not None
            else ()
        )
    }
    merged_stages = dict(prior_stages)
    merge_modes = stage_merge_modes or {}
    for stage_name in set(stage_usage).union(merge_modes):
        mode = merge_modes.get(stage_name, "accumulate")
        if mode not in {"accumulate", "replace"}:
            raise ValueError(
                f"Unsupported usage merge mode for {stage_name}: {mode}"
            )
        current = stage_usage.get(stage_name)
        if mode == "replace":
            if current is None:
                merged_stages.pop(stage_name, None)
            else:
                merged_stages[stage_name] = _normalize_stage_usage(
                    current,
                    source=f"current stage {stage_name}",
                )
        elif current is not None:
            merged_stages[stage_name] = _merge_stage_usage(
                merged_stages.get(stage_name),
                current,
                stage_name=stage_name,
            )

    cumulative_totals, cumulative_per_model = _aggregate_stage_usage(
        merged_stages
    )
    invocation_stages = {
        stage_name: _normalize_stage_usage(
            payload,
            source=f"current stage {stage_name}",
        )
        for stage_name, payload in stage_usage.items()
    }
    invocation_totals, invocation_per_model = _aggregate_stage_usage(
        invocation_stages
    )

    payload: dict[str, Any] = dict(existing_metrics or {})
    payload.update(
        {
            "schema_version": 1,
            "elapsed_s": round(total_elapsed, 3),
            "stages": merged_stages,
            "per_model": cumulative_per_model,
            "totals": cumulative_totals,
            "invocation": {
                "elapsed_s": round(total_elapsed, 3),
                "stages": invocation_stages,
                "per_model": invocation_per_model,
                "totals": invocation_totals,
                "stage_merge_modes": dict(merge_modes),
            },
        }
    )
    payload.pop("token_estimate_accuracy", None)
    if token_estimate:
        payload["token_estimate"] = token_estimate
        payload["token_estimate_scope"] = "current_invocation"
        estimated_total = int(token_estimate.get("total_tokens", 0) or 0)
        actual_total = invocation_totals["total_tokens"]
        estimate_stages = token_estimate.get("stages")
        estimated_stage_names = (
            {
                str(stage_name)
                for stage_name, stage_payload in estimate_stages.items()
                if isinstance(stage_payload, dict)
                and (
                    int(stage_payload.get("calls", 0) or 0) > 0
                    or int(stage_payload.get("total_tokens", 0) or 0) > 0
                )
            }
            if isinstance(estimate_stages, dict)
            else None
        )
        actual_stage_names = {
            stage_name
            for stage_name, stage_payload in invocation_stages.items()
            if stage_payload["requests"] > 0 or stage_payload["calls"] > 0
        }
        if estimated_total > 0:
            if not run_completed:
                payload["token_estimate_accuracy"] = {
                    "status": "unavailable",
                    "reason": "pipeline_incomplete",
                    "scope": "current_invocation",
                }
            elif run_partial:
                payload["token_estimate_accuracy"] = {
                    "status": "unavailable",
                    "reason": "pipeline_partial",
                    "scope": "current_invocation",
                }
            elif invocation_totals["requests"] == 0:
                payload["token_estimate_accuracy"] = {
                    "status": "unavailable",
                    "reason": "no_usage_reported",
                    "scope": "current_invocation",
                }
            elif estimated_stage_names is None:
                payload["token_estimate_accuracy"] = {
                    "status": "unavailable",
                    "reason": "estimate_scope_unknown",
                    "scope": "current_invocation",
                }
            elif estimated_stage_names != actual_stage_names:
                payload["token_estimate_accuracy"] = {
                    "status": "unavailable",
                    "reason": "stage_scope_mismatch",
                    "scope": "current_invocation",
                    "estimated_stages": sorted(estimated_stage_names),
                    "actual_stages": sorted(actual_stage_names),
                }
            elif invocation_totals["missing_usage_calls"] > 0:
                payload["token_estimate_accuracy"] = {
                    "status": "unavailable",
                    "reason": "provider_usage_incomplete",
                    "scope": "current_invocation",
                    "usage_coverage": invocation_totals["usage_coverage"],
                }
            else:
                difference = actual_total - estimated_total
                payload["token_estimate_accuracy"] = {
                    "status": "available",
                    "scope": "current_invocation",
                    "actual_total_tokens": actual_total,
                    "estimated_total_tokens": estimated_total,
                    "difference_tokens": difference,
                    "difference_ratio": difference / estimated_total,
                    "absolute_percentage_error": abs(difference) / estimated_total,
                }
        elif invocation_totals["requests"] > 0:
            payload["token_estimate_accuracy"] = {
                "status": "unavailable",
                "reason": "no_estimated_usage",
                "scope": "current_invocation",
            }
    elif existing_metrics is not None and existing_metrics.get("token_estimate"):
        payload["token_estimate_scope"] = "prior_invocation"
        payload["token_estimate_accuracy"] = {
            "status": "unavailable",
            "reason": "estimate_scope_mismatch",
            "scope": "current_invocation",
        }
    return payload


def _read_existing_run_metrics(metrics_path: Path) -> dict[str, Any] | None:
    """Read and validate prior token metrics before a resume updates them."""
    if not metrics_path.exists():
        return None
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _MetricsFormatError(
            f"{metrics_path} is not valid JSON ({exc.msg})"
        ) from exc
    except OSError as exc:
        raise _MetricsFormatError(
            f"{metrics_path} could not be read ({exc})"
        ) from exc
    if not isinstance(payload, dict):
        raise _MetricsFormatError(
            f"{metrics_path} must contain a JSON object"
        )

    stages = payload.get("stages")
    if stages is None:
        log.warning(
            "Existing metrics.json uses a legacy schema without token stages; "
            "preserving its fields and starting cumulative token accounting now."
        )
        payload["stages"] = {}
        return payload
    if not isinstance(stages, dict):
        raise _MetricsFormatError(
            f"{metrics_path} field 'stages' must be an object"
        )
    payload["stages"] = {
        str(stage_name): _normalize_stage_usage(
            stage_payload,
            source=f"existing stage {stage_name}",
        )
        for stage_name, stage_payload in stages.items()
    }
    return payload


def _usage_counter(value: Any, *, source: str, key: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _MetricsFormatError(
            f"{source} field '{key}' must be a non-negative number"
        )
    normalized = int(value)
    if normalized != value or normalized < 0:
        raise _MetricsFormatError(
            f"{source} field '{key}' must be a non-negative integer"
        )
    return normalized


def _normalize_usage_counters(
    payload: dict[str, Any],
    *,
    source: str,
) -> dict[str, int]:
    calls = _usage_counter(payload.get("calls"), source=source, key="calls")
    requests = _usage_counter(
        payload.get("requests", calls),
        source=source,
        key="requests",
    )
    counters = {
        key: _usage_counter(payload.get(key), source=source, key=key)
        for key in _USAGE_COUNTER_KEYS
    }
    counters["calls"] = calls
    counters["requests"] = requests
    if calls > requests:
        raise _MetricsFormatError(
            f"{source} field 'calls' cannot exceed 'requests'"
        )
    if "missing_usage_calls" not in payload:
        counters["missing_usage_calls"] = requests - calls
    elif calls + counters["missing_usage_calls"] > requests:
        raise _MetricsFormatError(
            f"{source} reported calls exceed 'requests'"
        )
    if "total_tokens" not in payload:
        counters["total_tokens"] = (
            counters["input_tokens"] + counters["output_tokens"]
        )
    return counters


def _normalize_stage_usage(
    payload: Any,
    *,
    source: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _MetricsFormatError(f"{source} must be an object")
    normalized: dict[str, Any] = dict(payload)
    normalized.update(_normalize_usage_counters(payload, source=source))

    raw_models = payload.get("per_model") or {}
    if not isinstance(raw_models, dict):
        raise _MetricsFormatError(f"{source} field 'per_model' must be an object")
    normalized["per_model"] = {}
    for model, model_payload in raw_models.items():
        if not isinstance(model_payload, dict):
            raise _MetricsFormatError(
                f"{source} model '{model}' must be an object"
            )
        normalized["per_model"][str(model)] = _normalize_usage_counters(
            model_payload,
            source=f"{source} model {model}",
        )

    elapsed = payload.get("elapsed_s")
    if elapsed is not None:
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or elapsed < 0
        ):
            raise _MetricsFormatError(
                f"{source} field 'elapsed_s' must be a non-negative number"
            )
        normalized["elapsed_s"] = round(float(elapsed), 3)
    normalized["cache_hit_rate"] = (
        normalized["cached_input_tokens"] / normalized["input_tokens"]
        if normalized["input_tokens"] > 0
        else 0.0
    )
    normalized["usage_coverage"] = (
        normalized["calls"] / normalized["requests"]
        if normalized["requests"] > 0
        else 0.0
    )
    return normalized


def _merge_stage_usage(
    existing: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    stage_name: str,
) -> dict[str, Any]:
    current_normalized = _normalize_stage_usage(
        current,
        source=f"current stage {stage_name}",
    )
    if existing is None:
        return current_normalized
    existing_normalized = _normalize_stage_usage(
        existing,
        source=f"existing stage {stage_name}",
    )
    merged = dict(existing_normalized)
    for key in _USAGE_COUNTER_KEYS:
        merged[key] = existing_normalized[key] + current_normalized[key]
    merged["elapsed_s"] = round(
        float(existing_normalized.get("elapsed_s", 0.0) or 0.0)
        + float(current_normalized.get("elapsed_s", 0.0) or 0.0),
        3,
    )
    merged_models = dict(existing_normalized["per_model"])
    for model, current_model in current_normalized["per_model"].items():
        existing_model = merged_models.get(model)
        if existing_model is None:
            merged_models[model] = dict(current_model)
            continue
        merged_models[model] = {
            key: existing_model.get(key, 0) + current_model.get(key, 0)
            for key in _USAGE_COUNTER_KEYS
        }
    merged["per_model"] = merged_models
    merged["cache_hit_rate"] = (
        merged["cached_input_tokens"] / merged["input_tokens"]
        if merged["input_tokens"] > 0
        else 0.0
    )
    merged["usage_coverage"] = (
        merged["calls"] / merged["requests"]
        if merged["requests"] > 0
        else 0.0
    )
    return merged


def _aggregate_stage_usage(
    stage_usage: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    totals = {
        "requests": 0,
        "calls": 0,
        "missing_usage_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    per_model: dict[str, dict[str, int]] = {}
    for stage_payload in stage_usage.values():
        for key in _USAGE_COUNTER_KEYS:
            totals[key] += int(stage_payload.get(key, 0) or 0)
        for model, model_stats in (stage_payload.get("per_model") or {}).items():
            bucket = per_model.setdefault(
                model,
                {
                    "requests": 0,
                    "calls": 0,
                    "missing_usage_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            )
            for key, value in model_stats.items():
                bucket[key] = bucket.get(key, 0) + value
            if "total_tokens" not in model_stats:
                bucket["total_tokens"] += int(
                    model_stats.get("input_tokens", 0) or 0
                ) + int(model_stats.get("output_tokens", 0) or 0)
    totals["cache_hit_rate"] = (
        totals["cached_input_tokens"] / totals["input_tokens"]
        if totals["input_tokens"] > 0
        else 0.0
    )
    totals["usage_coverage"] = (
        totals["calls"] / totals["requests"]
        if totals["requests"] > 0
        else 0.0
    )
    return totals, per_model


def _log_token_estimate(token_estimate: dict[str, Any]) -> None:
    """Print a compact pre-run estimate and stage breakdown."""
    total = int(token_estimate.get("total_tokens", 0) or 0)
    lower = int(token_estimate.get("lower_bound_tokens", total) or total)
    upper = int(token_estimate.get("upper_bound_tokens", total) or total)
    calls = int(token_estimate.get("calls", 0) or 0)
    input_tokens = int(token_estimate.get("input_tokens", 0) or 0)
    output_tokens = int(token_estimate.get("output_tokens", 0) or 0)
    log.info(
        "Estimated token usage: "
        f"~{_format_token_count(total)} total "
        f"(likely {_format_token_count(lower)}-{_format_token_count(upper)}; "
        f"{_format_token_count(input_tokens)} in / "
        f"{_format_token_count(output_tokens)} out across "
        f"{calls} tracked call{'s' if calls != 1 else ''})"
    )
    stages = token_estimate.get("stages")
    if isinstance(stages, dict) and stages:
        breakdown = ", ".join(
            f"{name} {_format_token_count(int(stage.get('total_tokens', 0) or 0))}"
            for name, stage in stages.items()
            if isinstance(stage, dict)
        )
        if breakdown:
            log.info(f"  Estimated by stage: {breakdown}")
    for note in token_estimate.get("notes") or []:
        if isinstance(note, str) and note:
            log.info(f"  Estimate note: {note}")


def _print_stage_done(
    stage_name: str,
    elapsed: float,
    summary: dict[str, Any] | None,
    usage: UsageAccumulator | None = None,
) -> None:
    """Print a human-readable stage completion summary."""
    tag = f"[{stage_name}]"
    s = summary or {}
    suffix = _format_usage_line(usage)
    if stage_name == "systematize":
        count = s.get("behavior_category_count", 0)
        names = s.get("behavior_names") or []
        preview = ", ".join(names[:3])
        if len(names) > 3:
            preview += f", ... (+{count - 3} more)"
        if preview:
            log.info(f"{tag} \u2713 Generated {count} behavior_categories: {preview} ({elapsed:.1f}s){suffix}")
        else:
            log.info(f"{tag} \u2713 Generated taxonomy ({elapsed:.1f}s){suffix}")
    elif stage_name == "__legacy_stratification":
        factor_sizes = s.get("factor_sizes") or {}
        if factor_sizes:
            sizes_text = ", ".join(
                f"{name}={size}" for name, size in factor_sizes.items()
            )
            log.info(f"{tag} \u2713 Built stratification coverage grid ({sizes_text}) ({elapsed:.1f}s){suffix}")
        else:
            log.info(f"{tag} \u2713 Built stratification coverage grid ({elapsed:.1f}s){suffix}")
    elif stage_name == "test_set":
        total = s.get("total", 0)
        prompts = s.get("prompts", 0)
        scenarios = s.get("scenarios", 0)
        parts = []
        if prompts:
            parts.append(f"{prompts} prompt{'s' if prompts != 1 else ''}")
        if scenarios:
            parts.append(f"{scenarios} scenario{'s' if scenarios != 1 else ''}")
        detail = " (" + ", ".join(parts) + ")" if parts else ""
        log.info(f"{tag} \u2713 Generated {total} test cases{detail} ({elapsed:.1f}s){suffix}")
    elif stage_name == "inference":
        count = s.get("count", 0)
        cached = s.get("cached_count", 0)
        new = s.get("new_count", count)
        if cached and new:
            extra = f" ({new} new, {cached} cached)"
        elif cached and not new:
            extra = f" ({cached} cached)"
        else:
            extra = ""
        log.info(f"{tag} \u2713 Completed {count} inferences{extra} ({elapsed:.1f}s){suffix}")
    elif stage_name == "judge":
        count = s.get("count", 0)
        failures = s.get("failures", 0)
        errors = s.get("errors", 0)
        cached = s.get("cached_count", 0)
        new = s.get("new_count", count)
        cache_extra = ""
        if cached and new:
            cache_extra = f" ({new} new, {cached} cached)"
        elif cached and not new:
            cache_extra = f" ({cached} cached)"
        extra = ""
        if failures:
            extra += f", {failures} failures"
        if errors:
            extra += f", {errors} errors"
        log.info(f"{tag} \u2713 Scored {count} inference rows{cache_extra}{extra} ({elapsed:.1f}s){suffix}")
    else:
        log.info(f"{tag} \u2713 Done ({elapsed:.1f}s){suffix}")


# ---------------------------------------------------------------------------
# Async-cleanup noise suppression
# ---------------------------------------------------------------------------
# When LangGraph (or similar async frameworks) create httpx AsyncClient
# objects on short-lived event loops (e.g. via asyncio.run()), garbage
# collection of those clients after the loop closes produces noisy but
# harmless tracebacks. The noise reaches users through three channels;
# we install one filter per channel.


class _AsyncCleanupLoggingFilter(logging.Filter):
    """Drop log records from asyncio's task-cleanup noise.

    Targets:
      - "Task exception was never retrieved" with "Event loop is closed"
      - "Task was destroyed but it is pending!" from httpx/litellm cleanup
    """

    _NOISE_FRAGMENTS = (
        "Event loop is closed",
        "Task was destroyed but it is pending",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for fragment in self._NOISE_FRAGMENTS:
            if fragment in msg:
                return False
        return True


def _install_async_cleanup_filters() -> None:
    """Install suppression filters for all three async-cleanup noise channels."""

    # Channel 1: Logging filter — the asyncio logger emits ERROR-level
    # records for unretrieved task exceptions. These go through the root
    # logger's handlers (which write to sys.__stderr__, not sys.stderr).
    noise_filter = _AsyncCleanupLoggingFilter()
    for handler in logging.root.handlers:
        handler.addFilter(noise_filter)
    # Also filter the asyncio logger directly in case it has its own handlers.
    logging.getLogger("asyncio").addFilter(noise_filter)

    # Channel 2: sys.unraisablehook — fires when __del__ methods raise.
    # httpx AsyncClient.__del__ → aclose() → RuntimeError("Event loop is
    # closed"). Python 3.8+ routes these through sys.unraisablehook.
    _orig_hook = sys.unraisablehook

    def _quiet_unraisablehook(unraisable: sys.UnraisableHookArgs) -> None:
        exc = unraisable.exc_value
        if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
            return
        _orig_hook(unraisable)

    sys.unraisablehook = _quiet_unraisablehook

    # Channel 3: stderr write filter — last resort for anything that
    # bypasses both logging and unraisablehook (e.g. C-level writes).
    real_stderr = sys.stderr

    class _FilteredStderr:
        __slots__ = ("_wrapped", "_suppressing")

        def __init__(self, wrapped: Any) -> None:
            self._wrapped = wrapped
            self._suppressing = False

        def write(self, text: str) -> int:
            if "Event loop is closed" in text or "AsyncClient.aclose" in text:
                self._suppressing = True
                return len(text)
            if self._suppressing:
                if text.startswith(("  ", "Traceback", "future:", "Task exception", "Task was")):
                    return len(text)
                self._suppressing = False
            return self._wrapped.write(text)

        def flush(self) -> None:
            self._wrapped.flush()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._wrapped, name)

    sys.stderr = _FilteredStderr(real_stderr)


def _log_run_headline(run_root: Path) -> None:
    """Log the same headline numbers a user sees on the viewer's run page.

    Pulls scores from ``run_root/scores.jsonl`` and prints target/judge plus the
    permissibility-split rates and judge failure. Runs without a behavior
    taxonomy retain the legacy policy-violation/overrefusal fallback. Silently
    does nothing if the judge stage hasn't produced scores yet, matching the
    viewer's behavior.
    """
    # Imported lazily to avoid a hard dependency for callers that import the
    # runner without ever invoking it (e.g. test scaffolding).
    from assert_ai.results import (
        compute_prompt_metrics,
        compute_scenario_metrics,
        has_permissibility_split_data,
    )
    from assert_ai.core.io import load_json, load_jsonl

    scores_path = run_root / "scores.jsonl"
    if not scores_path.exists():
        return
    score_rows = load_jsonl(scores_path)
    if not score_rows:
        return

    prompt_rows = [row for row in score_rows if not row.get("tester_model")]
    scenario_rows = [row for row in score_rows if row.get("tester_model")]
    taxonomy = load_json(run_root.parent / "taxonomy.json")
    raw_categories = (taxonomy or {}).get("behavior_categories")
    behavior_categories = (
        [entry for entry in raw_categories if isinstance(entry, dict)]
        if isinstance(raw_categories, list)
        else []
    )
    prompt_metrics = compute_prompt_metrics(prompt_rows, behavior_categories)
    scenario_metrics = compute_scenario_metrics(scenario_rows, behavior_categories)
    primary = prompt_metrics or scenario_metrics
    if primary is None:
        return

    target = primary.get("target") or "—"
    judge = primary.get("judge_model") or "—"
    total = (prompt_metrics or {}).get("total", 0) + (scenario_metrics or {}).get("total", 0)
    scored = (prompt_metrics or {}).get("scored_total", 0) + (scenario_metrics or {}).get("scored_total", 0)

    log.info("Headline:")
    log.info(f"  Target: {target}")
    log.info(f"  Judge:  {judge}")
    log.info(f"  Total:  {total} ({scored} scored)")

    def _fmt_rate(value: Any) -> str:
        if value is None or not isinstance(value, (int, float)):
            return "—"
        return f"{value * 100:.1f}%"

    def _emit(label: str, prompt_value: Any, scenario_value: Any) -> None:
        parts: list[str] = []
        if prompt_metrics is not None:
            parts.append(f"prompt {_fmt_rate(prompt_value)}")
        if scenario_metrics is not None:
            parts.append(f"scenario {_fmt_rate(scenario_value)}")
        if parts:
            log.info(f"  {label}: {' · '.join(parts)}")

    metric_sets = (prompt_metrics or {}, scenario_metrics or {})
    has_permissibility_split = has_permissibility_split_data(*metric_sets)
    if has_permissibility_split:
        _emit(
            label_metric("not_permissible_policy_violation_rate"),
            metric_sets[0].get("not_permissible_policy_violation_rate"),
            metric_sets[1].get("not_permissible_policy_violation_rate"),
        )
        _emit(
            label_metric("permissible_policy_violation_rate"),
            metric_sets[0].get("permissible_policy_violation_rate"),
            metric_sets[1].get("permissible_policy_violation_rate"),
        )
    else:
        _emit(
            label_metric("policy_violation_rate"),
            metric_sets[0].get("policy_violation_rate"),
            metric_sets[1].get("policy_violation_rate"),
        )
        _emit(
            label_metric("overrefusal_rate"),
            metric_sets[0].get("overrefusal_rate"),
            metric_sets[1].get("overrefusal_rate"),
        )
    _emit(
        label_metric("judge_failure_rate"),
        (prompt_metrics or {}).get("judge_failure_rate"),
        (scenario_metrics or {}).get("judge_failure_rate"),
    )


def run_pipeline(
    *,
    config: str,
    force_stages: list[str] | None = None,
    strict: bool = False,
    overrides: list[str] | None = None,
    concurrency: int | None = None,
) -> int:
    """Execute the configured stages sequentially and persist suite/run metadata."""
    # Suppress litellm's internal async logging warnings — they fire because
    # litellm creates async coroutines for logging callbacks that never get
    # awaited in our synchronous runner context. Harmless but alarming.
    warnings.filterwarnings("ignore", message="coroutine.*was never awaited", category=RuntimeWarning)

    # Suppress httpx AsyncClient.aclose() "Event loop is closed" tracebacks.
    # These fire when LangGraph's async HTTP clients are garbage-collected
    # after asyncio.run() closes the event loop. The noise reaches users
    # through three separate channels — each needs its own suppression:
    #
    # Channel 1: Python logging (asyncio logger)
    #   "Task exception was never retrieved" and "Task was destroyed but it
    #   is pending!" are logged via logging.getLogger("asyncio").error().
    #   The console handler writes to sys.__stderr__ (see logging_config.py),
    #   so a stderr wrapper can't intercept them.
    #
    # Channel 2: sys.unraisablehook
    #   "Exception ignored in: ..." messages from __del__ methods that raise.
    #   These bypass both logging and sys.stderr.
    #
    # Channel 3: Direct stderr writes (fallback for anything else).
    _install_async_cleanup_filters()

    try:
        ctx = _load_context(config=config, overrides=overrides)
        ctx["strict"] = strict
    except (ConfigError, ValueError) as exc:
        log.error(f"[config error] {exc}")
        return 1

    # CLI --concurrency wins over the YAML-resolved value so a single run can be
    # widened or narrowed without editing the config. We mutate the live
    # InferenceConfig instance (it's a regular dataclass, not frozen) because
    # downstream stages read `ctx["evaluation"].inference.concurrency` directly.
    if concurrency is not None:
        evaluation = ctx.get("evaluation")
        inference_cfg = getattr(evaluation, "inference", None) if evaluation is not None else None
        if inference_cfg is not None:
            inference_cfg.concurrency = concurrency
            log.info(f"[runner] Concurrency override: {concurrency} (CLI --concurrency)")
        else:
            log.warning(
                "[runner] --concurrency ignored: this config has no inference stage to override."
            )

    try:
        requested_force_stages = _requested_force_stages(ctx, force_stages)
    except ConfigError as exc:
        log.error(f"[config error] {exc}")
        return 1

    suite_root = Path(ctx["suite_root"])
    suite_root.mkdir(parents=True, exist_ok=True)
    _write_suite_metadata(ctx)
    ctx.setdefault("artifact_versions", {})
    artifact_plans: dict[str, Any] = {}
    cache_supported = supports_artifact_cache(ctx)
    if cache_supported:
        activate_latest_artifacts(ctx)

    stages_to_run: list[tuple[str, Any, dict[str, Any]]] = []
    for stage_name, raw_cfg in ctx["stages"]:
        if not raw_cfg.get("enabled", True):
            continue

        module = STAGES[stage_name]

        if module.SCOPE == "suite":
            if cache_supported and is_cacheable_stage(stage_name):
                forced = stage_name in requested_force_stages
                plan = prepare_artifact_plan(
                    ctx=ctx,
                    stage_name=stage_name,
                    raw_cfg=raw_cfg,
                    forced=forced,
                )
                ref = activate_artifact_plan(ctx, plan)
                artifact_plans[stage_name] = plan
                if plan.reused:
                    refresh_compatibility_files(ctx, stage_name, plan.output_paths)
                    update_latest(ctx, stage_name, ref)
                    log.info(
                        f"[{stage_name}] Reused artifact {plan.version} "
                        f"(input hashes match, use --force-stage {stage_name} to regenerate)"
                    )
                    continue
                # Force cacheable stages to write into their versioned artifact
                # directory regardless of any save_dir/save_path the user set
                # in raw_cfg. Without this override, finalize_artifact_plan
                # would look for outputs in plan.output_paths and fail (or
                # silently produce stale cache entries) because the stage
                # honored the user's path instead.
                raw_cfg = override_cacheable_output_paths(stage_name, raw_cfg, plan)
            elif (
                module.SUITE_OUTPUT
                and stage_name not in requested_force_stages
            ):
                output_path = Path(ctx["suite_root"]) / module.SUITE_OUTPUT
                if output_path.exists():
                    log.info(
                        f"[{stage_name}] Skipped (output exists, use --force-stage {stage_name} to regenerate)"
                    )
                    continue

        stages_to_run.append((stage_name, module, raw_cfg))

    token_estimate_payload: dict[str, Any] | None = None
    try:
        from assert_ai.core.token_estimator import estimate_pipeline_tokens

        token_estimate_payload = estimate_pipeline_tokens(
            ctx,
            stages_to_run,
            forced_stages=requested_force_stages,
        ).to_dict()
        _log_token_estimate(token_estimate_payload)
    except ConfigError as exc:
        log.warning(f"Token estimate unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001
        # Estimation is advisory and must never prevent the configured run.
        log.warning(f"Token estimate unavailable: {exc}")

    run_root = Path(ctx["run_root"]) if ctx.get("run_root") else None
    selected_run_stage = any(module.SCOPE == "run" for _, module, _ in stages_to_run)
    manifest = None
    if selected_run_stage and run_root is not None:
        run_root.mkdir(parents=True, exist_ok=True)
        manifest = _build_manifest(ctx)
        config_path = ctx.get("config_path")
        if config_path is not None and Path(config_path).is_file():
            shutil.copy2(config_path, run_root / "config.yaml")
    failed_stage: str | None = None
    pipeline_start = time.monotonic()
    stage_usage: dict[str, dict[str, Any]] = {}
    # Stages register replacement only when they actually invalidate prior
    # output. Predeclaring every cascaded force would erase untouched
    # downstream usage when an upstream stage fails before those stages start.
    stage_usage_merge_modes: dict[str, str] = {}
    ctx["_usage_merge_modes"] = stage_usage_merge_modes

    # Start the manifest heartbeat + pipeline watchdog as daemon threads.
    # The heartbeat refreshes manifest.heartbeat_at + progress every 30s
    # so external observers see live progress during long stages (inference
    # can be 90+ minutes at high concurrency). The watchdog dumps every
    # thread's stack to the log if no progress tick fires for 10 minutes,
    # making the next hang self-diagnosing instead of requiring py-spy.
    # Both are daemons so they cannot block process exit if the runner
    # itself errors out before .stop().
    heartbeat: ManifestHeartbeat | None = None
    watchdog: PipelineWatchdog | None = None
    if manifest is not None and run_root is not None:
        heartbeat = ManifestHeartbeat(
            manifest,
            run_root,
            _write_manifest,
            interval_s=30.0,
        )
        watchdog = PipelineWatchdog(
            idle_threshold_s=600.0,
            check_interval_s=60.0,
        )
        heartbeat.attach_watchdog(watchdog)
        ctx["_heartbeat"] = heartbeat
        ctx["_watchdog"] = watchdog
        heartbeat.start()
        watchdog.start()

    try:
        return _run_stages_inner(
            ctx=ctx,
            stages_to_run=stages_to_run,
            artifact_plans=artifact_plans,
            requested_force_stages=requested_force_stages,
            cache_supported=cache_supported,
            manifest=manifest,
            run_root=run_root,
            pipeline_start=pipeline_start,
            stage_usage=stage_usage,
            stage_usage_merge_modes=stage_usage_merge_modes,
            token_estimate=token_estimate_payload,
            heartbeat=heartbeat,
            watchdog=watchdog,
        )
    finally:
        if heartbeat is not None:
            heartbeat.stop(write_final=True)
        if watchdog is not None:
            watchdog.stop()


def _run_stages_inner(
    *,
    ctx: dict[str, Any],
    stages_to_run: list[tuple[str, Any, dict[str, Any]]],
    artifact_plans: dict[str, Any],
    requested_force_stages: set[str],
    cache_supported: bool,
    manifest: RunManifest | None,
    run_root: Path | None,
    pipeline_start: float,
    stage_usage: dict[str, dict[str, Any]],
    stage_usage_merge_modes: dict[str, str],
    token_estimate: dict[str, Any] | None,
    heartbeat: ManifestHeartbeat | None,
    watchdog: PipelineWatchdog | None,
) -> int:
    """Stage execution loop. Extracted so the outer function can manage
    heartbeat/watchdog lifecycle in a single try/finally."""
    failed_stage: str | None = None
    pipeline_partial = False
    run_stage_executed = False

    for stage_name, module, raw_cfg in stages_to_run:
        if module.SCOPE == "run":
            run_stage_executed = True
        if manifest is not None and module.SCOPE == "run":
            manifest.stages[stage_name] = "running"
            manifest.stage_timings[stage_name] = {
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            _record_run_artifacts(manifest, ctx, run_root)
            _write_manifest(manifest, run_root)
        _print_stage_start(stage_name, ctx, raw_cfg)
        stage_start = time.monotonic()
        stage_result: dict[str, Any] = {}
        # Tick the watchdog so it doesn't fire mid-stage on the previous
        # stage's idle clock, and reset the heartbeat's progress payload
        # so a stage that doesn't report progress (e.g. systematize, test_set)
        # doesn't leave stale {"stage": "inference", "completed": 1000}
        # in the manifest.
        if watchdog is not None:
            watchdog.tick()
        if heartbeat is not None:
            heartbeat.clear_progress()
            heartbeat.set_progress(stage=stage_name)
        # Pass the per-stage "was this forced" flag through ctx so stages
        # like inference/judge can distinguish a real cache-mismatch warning
        # from a redundant one (the user already opted into discarding via
        # --force-stage, possibly via cascade). Stages that don't read
        # _stage_forced ignore it.
        ctx["_stage_forced"] = stage_name in requested_force_stages
        ctx.pop("_usage_merge_mode", None)
        usage_acc: UsageAccumulator | None = None
        try:
            with track_usage() as usage_acc:
                # run_stage_coro replaces asyncio.run with bounded teardown:
                # if the stage's event loop can't shut down its default
                # executor within 300s (typically because a user target left
                # background work — unclosed httpx clients, OTel exporters
                # — wedged in finalizers), we log a warning, detach the
                # executor from the interpreter's atexit join, and proceed
                # to the next stage. This is the harness's deadlock defense
                # against well-meaning but cleanup-imperfect user agents.
                stage_result = run_stage_coro(
                    module.run(ctx, raw_cfg),
                    cleanup_timeout_s=300.0,
                ) or {}
            stage_errored_count = int(
                ((stage_result or {}).get("_summary") or {}).get("errored_count", 0) or 0
            )
            pipeline_partial = pipeline_partial or stage_errored_count > 0
            if (
                cache_supported
                and module.SCOPE == "suite"
                and is_cacheable_stage(stage_name)
                and stage_name in artifact_plans
            ):
                if stage_errored_count > 0:
                    # Per-row resilience let the stage finish with a
                    # smaller-than-requested output. Skipping
                    # finalize_artifact_plan means the partial output
                    # remains in the version directory for inspection
                    # but no artifact.json sidecar is written, so
                    # _latest_matching_metadata will not match this dir
                    # on a future run with the same input hash. Without
                    # this gate, a partial test_set.jsonl / inference_set.jsonl
                    # / scores.jsonl would silently masquerade as a
                    # complete artifact and be reused forever.
                    log.warning(
                        "[%s] Stage produced a partial result (%d batch failure(s)); "
                        "skipping artifact-cache finalization. Output is in the version "
                        "directory for inspection but will NOT be reused on the next run. "
                        "Re-run to fill the gap.",
                        stage_name, stage_errored_count,
                    )
                else:
                    finalize_artifact_plan(ctx, artifact_plans[stage_name])
            ok = True
        except (LLMAuthError, LLMInputError, LLMRateLimitError, LLMProviderError) as exc:
            # Classified LLM errors already carry a clean, actionable message.
            # Print just that message; suppress the multi-screen litellm/httpx
            # traceback unless the user opts into verbose output.
            ok = False
            log.error(f"[{stage_name}] {exc}")
            if os.environ.get("ASSERT_VERBOSE_ERRORS") == "1":
                log.debug("Full traceback:", exc_info=True)
            else:
                log.info("(set ASSERT_VERBOSE_ERRORS=1 to see the full traceback)")
        except Exception:  # noqa: BLE001
            ok = False
            log.error(f"[{stage_name}] Unexpected error", exc_info=True)

        if not ok and stage_name in artifact_plans:
            # The stage failed (either before or during finalize). For non-reused
            # cacheable plans this means we allocated vNNNN/ but never wrote a
            # complete artifact.json sidecar. Without cleanup, _next_version
            # would forever increment past the abandoned slot and the stage_root
            # would accumulate dead version directories on every failed run.
            # discard_artifact_plan no-ops for reused plans so a downstream
            # failure cannot wipe a healthy upstream cache hit.
            discard_artifact_plan(ctx, artifact_plans[stage_name])

        elapsed = time.monotonic() - stage_start
        if (
            usage_acc is not None
            and (usage_acc.requests > 0 or usage_acc.calls > 0)
        ):
            stage_payload = usage_acc.to_dict()
            stage_payload["elapsed_s"] = round(elapsed, 3)
            stage_usage[stage_name] = stage_payload
        default_merge_mode = (
            "replace"
            if stage_name in requested_force_stages or module.SCOPE == "suite"
            else "accumulate"
        )
        requested_merge_mode = ctx.pop(
            "_usage_merge_mode",
            stage_result.get("_usage_merge"),
        )
        if ok and default_merge_mode == "replace":
            stage_usage_merge_modes[stage_name] = "replace"
        elif requested_merge_mode in {"accumulate", "replace"}:
            stage_usage_merge_modes[stage_name] = requested_merge_mode
        elif ok:
            stage_usage_merge_modes[stage_name] = default_merge_mode
        elif stage_name in stage_usage:
            # Failed calls still consumed tokens. If the stage did not report
            # that it invalidated prior output, retain that output's usage and
            # add the failed invocation rather than replacing it.
            stage_usage_merge_modes.setdefault(stage_name, "accumulate")
        if ok:
            _print_stage_done(stage_name, elapsed, stage_result.get("_summary"), usage_acc)
        else:
            log.error(f"[{stage_name}] \u2717 Failed ({elapsed:.1f}s)")

        # Tick the watchdog now that the stage has returned (success or
        # fail). The stage may have been silent for the entire 300s
        # bounded-cleanup wait; without this tick the watchdog would fire
        # spuriously on the *next* stage's startup. We also clear the
        # heartbeat's progress payload so it doesn't leak past the stage
        # that owned it.
        if watchdog is not None:
            watchdog.tick()
        if heartbeat is not None:
            heartbeat.clear_progress()

        if manifest is not None and module.SCOPE == "run":
            manifest.stages[stage_name] = "completed" if ok else "failed"
            manifest.status = "running" if ok else "failed"
            existing_timing = manifest.stage_timings.get(stage_name) or {}
            existing_timing["ended_at"] = datetime.now(timezone.utc).isoformat()
            existing_timing["duration_secs"] = round(elapsed, 3)
            manifest.stage_timings[stage_name] = existing_timing
            _record_run_artifacts(manifest, ctx, run_root)
            _write_manifest(manifest, run_root)

        if ok and module.SCOPE == "suite":
            _write_suite_metadata(ctx)

        if not ok:
            failed_stage = stage_name
            break

    total_elapsed = time.monotonic() - pipeline_start
    metrics_written = False
    replacement_requested = any(
        mode == "replace" for mode in stage_usage_merge_modes.values()
    )
    if run_root is not None and (
        run_stage_executed
        or stage_usage
        or replacement_requested
        or (token_estimate and not (run_root / "metrics.json").exists())
    ):
        metrics_path = run_root / "metrics.json"
        try:
            existing_metrics = _read_existing_run_metrics(metrics_path)
            payload = _build_run_metrics(
                stage_usage,
                total_elapsed,
                token_estimate=token_estimate,
                run_completed=failed_stage is None,
                run_partial=pipeline_partial,
                existing_metrics=existing_metrics,
                stage_merge_modes=stage_usage_merge_modes,
            )
            write_json(metrics_path, payload)
            metrics_written = True
            totals = payload["totals"]
            if totals["requests"] or totals["calls"]:
                cache_pct = 100.0 * totals["cache_hit_rate"]
                if totals["input_tokens"] or totals["output_tokens"]:
                    detailed_total = (
                        totals["input_tokens"] + totals["output_tokens"]
                    )
                    token_summary = (
                        f"{_format_token_count(totals['input_tokens'])} in / "
                        f"{_format_token_count(totals['output_tokens'])} out"
                    )
                    if totals["total_tokens"] > detailed_total:
                        token_summary += (
                            " / "
                            f"{_format_token_count(totals['total_tokens'])} total"
                        )
                else:
                    token_summary = (
                        f"{_format_token_count(totals['total_tokens'])} total"
                    )
                request_count = totals["requests"] or totals["calls"]
                usage_coverage = ""
                if totals["missing_usage_calls"]:
                    usage_coverage = (
                        f" · {totals['calls']}/{request_count} usage reported"
                    )
                log.info(
                    "Token usage: "
                    f"{request_count} "
                    f"call{'s' if request_count != 1 else ''} · "
                    f"{token_summary}{usage_coverage} · {cache_pct:.1f}% cached"
                )
            accuracy = payload.get("token_estimate_accuracy")
            if (
                isinstance(accuracy, dict)
                and accuracy.get("status") == "available"
            ):
                difference_ratio = float(
                    accuracy.get("difference_ratio", 0.0) or 0.0
                )
                log.info(
                    "Token estimate accuracy: "
                    f"actual {_format_token_count(int(accuracy['actual_total_tokens']))} "
                    f"vs estimated "
                    f"{_format_token_count(int(accuracy['estimated_total_tokens']))} "
                    f"({difference_ratio:+.1%})"
                )
        except _MetricsFormatError as exc:
            log.warning(
                "Token metrics were not updated because existing metrics.json "
                "could not be merged safely: %s",
                exc,
            )
        except OSError as exc:
            log.warning("Failed to write metrics.json: %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("Failed to write metrics.json")

    if failed_stage is None:
        log.info(f"Pipeline completed ({total_elapsed:.1f}s)")
        if run_root is not None:
            _log_run_headline(run_root)
            log.info("Results:")
            scores_path = run_root / "scores.jsonl"
            metrics_path = run_root / "metrics.json"
            if scores_path.exists():
                log.info(f"  Scores:  {scores_path}")
            if metrics_path.exists() or metrics_written:
                log.info(f"  Metrics: {metrics_path}")
            log.info(f"  Run dir: {run_root}")
            suite_id = ctx.get('suite_id', '')
            run_id = ctx.get('run_id', '')
            if suite_id and run_id:
                log.info("Inspect results:")
                log.info(f"  assert-ai results status {suite_id} {run_id}")
                log.info("View in browser:")
                log.info(f"  cd viewer && npm run dev    (then open http://localhost:5174/suite/{suite_id}/{run_id})")
    else:
        log.error(f"Pipeline failed at {failed_stage} ({total_elapsed:.1f}s)")

    if manifest is None:
        return 0 if failed_stage is None else 1

    manifest.ended_at = datetime.now(timezone.utc).isoformat()
    manifest.status = "completed" if failed_stage is None else "failed"
    _record_run_artifacts(manifest, ctx, run_root)
    _write_manifest(manifest, run_root)
    return 0 if failed_stage is None else 1
