"""Minimal sequential runner for the p2m stage pipeline."""

from __future__ import annotations

import asyncio
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

from dotenv import load_dotenv

from p2m.config import (
    ConfigError,
    PIPELINE_STAGE_ORDER,
    load_config,
    load_runtime_context,
)
from p2m.core.artifact_cache import (
    activate_latest_artifacts,
    activate_artifact_plan,
    discard_artifact_plan,
    finalize_artifact_plan,
    is_cacheable_stage,
    override_cacheable_output_paths,
    prepare_artifact_plan,
    refresh_compatibility_files,
    supports_artifact_cache,
    update_latest,
)
from p2m.core.config_model import RunManifest, SuiteMetadata
from p2m.core.io import write_json
from p2m.core.model_client import (
    LLMAuthError,
    LLMInputError,
    LLMProviderError,
    LLMRateLimitError,
    UsageAccumulator,
    track_usage,
)
from p2m.stages import STAGES

load_dotenv()

log = logging.getLogger(__name__)


def _load_context(
    *,
    config: str,
) -> dict[str, Any]:
    """Load one config file into runtime context."""
    cfg_path = Path(config).resolve()
    raw = load_config(cfg_path)
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
    if usage is None or usage.calls == 0:
        return ""
    parts = [
        f"{usage.calls} call{'s' if usage.calls != 1 else ''}",
        f"{_format_token_count(usage.input_tokens)} in / "
        f"{_format_token_count(usage.output_tokens)} out",
    ]
    if usage.input_tokens > 0:
        pct = 100.0 * usage.cached_input_tokens / usage.input_tokens
        parts.append(f"{pct:.1f}% cached")
    return " | " + " · ".join(parts)


def _build_run_metrics(
    stage_usage: dict[str, dict[str, Any]],
    total_elapsed: float,
) -> dict[str, Any]:
    """Aggregate per-stage usage into the metrics.json payload."""
    totals = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    per_model: dict[str, dict[str, int]] = {}
    for stage_payload in stage_usage.values():
        totals["calls"] += stage_payload.get("calls", 0)
        totals["input_tokens"] += stage_payload.get("input_tokens", 0)
        totals["output_tokens"] += stage_payload.get("output_tokens", 0)
        totals["cached_input_tokens"] += stage_payload.get("cached_input_tokens", 0)
        totals["cache_creation_input_tokens"] += stage_payload.get(
            "cache_creation_input_tokens", 0
        )
        for model, model_stats in (stage_payload.get("per_model") or {}).items():
            bucket = per_model.setdefault(
                model,
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            )
            for key, value in model_stats.items():
                bucket[key] = bucket.get(key, 0) + value
    totals["cache_hit_rate"] = (
        totals["cached_input_tokens"] / totals["input_tokens"]
        if totals["input_tokens"] > 0
        else 0.0
    )
    return {
        "schema_version": 1,
        "elapsed_s": round(total_elapsed, 3),
        "stages": stage_usage,
        "per_model": per_model,
        "totals": totals,
    }


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


def run_pipeline(
    *,
    config: str,
    force_stages: list[str] | None = None,
    strict: bool = False,
) -> int:
    """Execute the configured stages sequentially and persist suite/run metadata."""
    # Suppress litellm's internal async logging warnings — they fire because
    # litellm creates async coroutines for logging callbacks that never get
    # awaited in our synchronous runner context. Harmless but alarming.
    warnings.filterwarnings("ignore", message="coroutine.*was never awaited", category=RuntimeWarning)

    # Suppress httpx AsyncClient.aclose() "Event loop is closed" tracebacks.
    # These fire when LangGraph's async HTTP clients are garbage-collected
    # after asyncio.run() closes the event loop. We can't intercept them via
    # the event loop exception handler (it's already torn down), so we filter
    # stderr writes that match the pattern.
    _real_stderr = sys.stderr

    class _FilteredStderr:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self._suppressing = False

        def write(self, text):
            if "Event loop is closed" in text or "AsyncClient.aclose" in text:
                self._suppressing = True
                return len(text)
            if self._suppressing:
                # Suppress continuation lines of the traceback
                if text.startswith("  ") or text.startswith("Traceback") or text.startswith("future:") or text.startswith("Task exception"):
                    return len(text)
                self._suppressing = False
            return self._wrapped.write(text)

        def flush(self):
            self._wrapped.flush()

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    sys.stderr = _FilteredStderr(sys.stderr)

    try:
        ctx = _load_context(config=config)
        ctx["strict"] = strict
    except (ConfigError, ValueError) as exc:
        log.error(f"[config error] {exc}")
        return 1

    requested_force_stages = set(force_stages or [])
    configured_stage_names = {stage_name for stage_name, _ in ctx["stages"]}
    invalid_forced = sorted(requested_force_stages.difference(configured_stage_names))
    if invalid_forced:
        joined = ", ".join(invalid_forced)
        log.error(f"[config error] --force-stage stage(s) not present in config: {joined}")
        return 1

    # Cascade: forcing an upstream stage logically invalidates every stage
    # downstream of it. Without this, `--force-stage test_set` regenerates test_set
    # but inference silently keeps the old inference rows (its resume cache keys on
    # test_case_id, and test case ids are deterministic so they collide with the prior
    # run's content). Same hazard for judge against scores.jsonl. Computing
    # the closure here keeps the workflow `--force-stage <upstream>` honest
    # without forcing users to remember the full downstream chain.
    if requested_force_stages:
        forced_indices = [
            PIPELINE_STAGE_ORDER.index(name)
            for name in requested_force_stages
            if name in PIPELINE_STAGE_ORDER
        ]
        if forced_indices:
            min_forced_index = min(forced_indices)
            cascade = {
                name
                for name in PIPELINE_STAGE_ORDER[min_forced_index:]
                if name in configured_stage_names
            }
            requested_force_stages = requested_force_stages.union(cascade)

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

    for stage_name, module, raw_cfg in stages_to_run:
        if manifest is not None and module.SCOPE == "run":
            manifest.stages[stage_name] = "running"
            _record_run_artifacts(manifest, ctx, run_root)
            _write_manifest(manifest, run_root)
        _print_stage_start(stage_name, ctx, raw_cfg)
        stage_start = time.monotonic()
        stage_result: dict[str, Any] = {}
        # Pass the per-stage "was this forced" flag through ctx so stages
        # like inference/judge can distinguish a real cache-mismatch warning
        # from a redundant one (the user already opted into discarding via
        # --force-stage, possibly via cascade). Stages that don't read
        # _stage_forced ignore it.
        ctx["_stage_forced"] = stage_name in requested_force_stages
        usage_acc: UsageAccumulator | None = None
        try:
            with track_usage() as usage_acc:
                stage_result = asyncio.run(module.run(ctx, raw_cfg)) or {}
            stage_errored_count = int(
                ((stage_result or {}).get("_summary") or {}).get("errored_count", 0) or 0
            )
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
            if os.environ.get("P2M_VERBOSE_ERRORS") == "1":
                log.debug("Full traceback:", exc_info=True)
            else:
                log.info("(set P2M_VERBOSE_ERRORS=1 to see the full traceback)")
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
        if usage_acc is not None and usage_acc.calls > 0:
            stage_payload = usage_acc.to_dict()
            stage_payload["elapsed_s"] = round(elapsed, 3)
            stage_usage[stage_name] = stage_payload
        if ok:
            _print_stage_done(stage_name, elapsed, stage_result.get("_summary"), usage_acc)
        else:
            log.error(f"[{stage_name}] \u2717 Failed ({elapsed:.1f}s)")

        if manifest is not None and module.SCOPE == "run":
            manifest.stages[stage_name] = "completed" if ok else "failed"
            manifest.status = "running" if ok else "failed"
            _record_run_artifacts(manifest, ctx, run_root)
            _write_manifest(manifest, run_root)

        if ok and module.SCOPE == "suite":
            _write_suite_metadata(ctx)

        if not ok:
            failed_stage = stage_name
            break

    total_elapsed = time.monotonic() - pipeline_start
    metrics_written = False
    if run_root is not None and stage_usage:
        try:
            metrics_path = run_root / "metrics.json"
            payload = _build_run_metrics(stage_usage, total_elapsed)
            write_json(metrics_path, payload)
            metrics_written = True
            totals = payload["totals"]
            if totals["calls"]:
                cache_pct = 100.0 * totals["cache_hit_rate"]
                log.info(
                    "Token usage: "
                    f"{totals['calls']} calls · "
                    f"{_format_token_count(totals['input_tokens'])} in / "
                    f"{_format_token_count(totals['output_tokens'])} out · "
                    f"{cache_pct:.1f}% cached"
                )
        except Exception:  # noqa: BLE001
            log.debug("Failed to write metrics.json", exc_info=True)

    if failed_stage is None:
        log.info(f"Pipeline completed ({total_elapsed:.1f}s)")
        if run_root is not None:
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
                log.info(f"  uv run p2m results status {suite_id} {run_id}")
    else:
        log.error(f"Pipeline failed at {failed_stage} ({total_elapsed:.1f}s)")

    if manifest is None:
        return 0 if failed_stage is None else 1

    manifest.ended_at = datetime.now(timezone.utc).isoformat()
    manifest.status = "completed" if failed_stage is None else "failed"
    _record_run_artifacts(manifest, ctx, run_root)
    _write_manifest(manifest, run_root)
    return 0 if failed_stage is None else 1
