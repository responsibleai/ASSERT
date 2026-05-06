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
from p2m.core.config_model import RunManifest, SuiteMetadata
from p2m.core.io import write_json
from p2m.core.model_client import (
    LLMAuthError,
    LLMInputError,
    LLMProviderError,
    LLMRateLimitError,
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


def _print_stage_start(stage_name: str, ctx: dict[str, Any], raw_cfg: dict[str, Any]) -> None:
    """Print a human-readable stage header."""
    risk = ctx.get("risk") or ctx.get("concept") or ""
    if stage_name == "policy":
        label = risk.replace("\n", " ").strip()
        if len(label) > 80:
            label = label[:77] + "..."
        policy_model = ""
        if isinstance(raw_cfg.get("model"), dict):
            policy_model = raw_cfg["model"].get("name", "")
        model_suffix = f" ({policy_model})" if policy_model else ""
        log.info(f'  Generating behavior taxonomy for "{label}"{model_suffix}')
    elif stage_name == "systematization":
        log.info(f"  Refining policy structure...")
    elif stage_name == "design":
        level_count = raw_cfg.get("level_count")
        factor_count = 0
        factors = ctx.get("factors") or []
        if isinstance(factors, list):
            factor_count = len(factors)
        # Always-present "behavior" factor is generated automatically.
        # Surface it in the count for accuracy.
        synthetic_behavior_factor = 1
        total_factors = factor_count + synthetic_behavior_factor
        design_model = ""
        if isinstance(raw_cfg.get("model"), dict):
            design_model = raw_cfg["model"].get("name", "")
        model_suffix = f" ({design_model})" if design_model else ""
        if level_count and factor_count:
            log.info(f"  Designing seed-coverage grid: {total_factors} factors x {level_count} levels each{model_suffix}...")
        elif factor_count:
            log.info(f"  Designing seed-coverage grid: {total_factors} factors{model_suffix}...")
        else:
            log.info(f"  Designing seed-coverage grid (behavior factor only){model_suffix}...")
    elif stage_name == "seeds":
        prompt_budget = 0
        scenario_budget = 0
        if isinstance(raw_cfg.get("prompt"), dict):
            prompt_budget = raw_cfg["prompt"].get("budget", 0) or raw_cfg["prompt"].get("sample_size", 0)
        if isinstance(raw_cfg.get("scenario"), dict):
            scenario_budget = raw_cfg["scenario"].get("budget", 0) or raw_cfg["scenario"].get("sample_size", 0)
        # Read behavior count from the policy output. Fall back to the
        # legacy `sub_risks` key for any pre-merge artifacts on disk.
        behavior_count = 0
        policy_path = Path(ctx["suite_root"]) / "policy.json"
        if policy_path.exists():
            try:
                policy_data = json.loads(policy_path.read_text(encoding="utf-8"))
                behavior_count = len(
                    policy_data.get("behaviors")
                    or policy_data.get("sub_risks")
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
        if detail and behavior_count:
            detail += f" from {behavior_count} behaviors)"
        elif detail:
            detail += ")"
        seed_models = set()
        for kind_key in ("prompt", "scenario"):
            kind_cfg = raw_cfg.get(kind_key)
            if isinstance(kind_cfg, dict) and isinstance(kind_cfg.get("model"), dict):
                seed_models.add(kind_cfg["model"].get("name", ""))
        seed_models.discard("")
        model_suffix = f" ({', '.join(sorted(seed_models))})" if seed_models else ""
        log.info(f"  Generating test cases{detail}{model_suffix}...")
    elif stage_name == "rollout":
        target = ctx.get("target")
        target_name = ""
        if target and target.model:
            target_name = target.model.name or ""
        if target and target.callable:
            target_name = target.callable or target_name
        auditor_name = ""
        if isinstance(raw_cfg.get("auditor"), dict) and isinstance(raw_cfg["auditor"].get("model"), dict):
            auditor_name = raw_cfg["auditor"]["model"].get("name", "")
        if auditor_name and target_name:
            log.info(f"  Running test cases (auditor: {auditor_name} \u2192 target: {target_name})...")
        elif target_name:
            log.info(f"  Running test cases against target ({target_name})...")
        else:
            log.info(f"  Running test cases against target...")
    elif stage_name == "judge":
        eval_cfg = ctx.get("evaluation")
        judge_model_obj = eval_cfg.judge.model if eval_cfg else None
        # judge.model is a ModelConfig dataclass post-init; reach for .name
        # rather than letting the dataclass repr leak into the header.
        if judge_model_obj is not None and hasattr(judge_model_obj, "name"):
            judge_model = judge_model_obj.name or ""
        elif isinstance(judge_model_obj, str):
            judge_model = judge_model_obj
        else:
            judge_model = ""
        if judge_model:
            log.info(f"  Scoring transcripts with judge ({judge_model})...")
        else:
            log.info(f"  Scoring transcripts...")
    else:
        log.info(f"  {stage_name}...")


def _print_stage_done(stage_name: str, elapsed: float, summary: dict[str, Any] | None) -> None:
    """Print a human-readable stage completion summary."""
    s = summary or {}
    if stage_name == "policy":
        # Prefer the new-science key; fall back to legacy for pre-merge artifacts.
        count = s.get("behavior_count") or s.get("sub_risk_count", 0)
        names = s.get("behavior_names") or s.get("sub_risk_names") or []
        preview = ", ".join(names[:3])
        if len(names) > 3:
            preview += f", ... (+{count - 3} more)"
        if preview:
            log.info(f"  \u2713 Generated {count} behaviors: {preview} ({elapsed:.1f}s)")
        else:
            log.info(f"  \u2713 Generated policy ({elapsed:.1f}s)")
    elif stage_name == "design":
        factor_sizes = s.get("factor_sizes") or {}
        if factor_sizes:
            sizes_text = ", ".join(
                f"{name}={size}" for name, size in factor_sizes.items()
            )
            log.info(f"  \u2713 Designed coverage grid ({sizes_text}) ({elapsed:.1f}s)")
        else:
            log.info(f"  \u2713 Designed coverage grid ({elapsed:.1f}s)")
    elif stage_name == "seeds":
        total = s.get("total", 0)
        prompts = s.get("prompts", 0)
        scenarios = s.get("scenarios", 0)
        parts = []
        if prompts:
            parts.append(f"{prompts} prompt{'s' if prompts != 1 else ''}")
        if scenarios:
            parts.append(f"{scenarios} scenario{'s' if scenarios != 1 else ''}")
        detail = " (" + ", ".join(parts) + ")" if parts else ""
        log.info(f"  \u2713 Generated {total} test cases{detail} ({elapsed:.1f}s)")
    elif stage_name == "rollout":
        count = s.get("count", 0)
        cached = s.get("cached_count", 0)
        new = s.get("new_count", count)
        if cached and new:
            extra = f" ({new} new, {cached} cached)"
        elif cached and not new:
            extra = f" ({cached} cached)"
        else:
            extra = ""
        log.info(f"  \u2713 Completed {count} rollouts{extra} ({elapsed:.1f}s)")
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
        log.info(f"  \u2713 Scored {count} transcripts{cache_extra}{extra} ({elapsed:.1f}s)")
    else:
        log.info(f"  {stage_name} done ({elapsed:.1f}s)")


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
        log.error("[config error] %s", exc)
        return 1

    requested_force_stages = set(force_stages or [])
    configured_stage_names = {stage_name for stage_name, _ in ctx["stages"]}
    invalid_forced = sorted(requested_force_stages.difference(configured_stage_names))
    if invalid_forced:
        joined = ", ".join(invalid_forced)
        log.error("[config error] --force-stage stage(s) not present in config: %s", joined)
        return 1

    # Cascade: forcing an upstream stage logically invalidates every stage
    # downstream of it. Without this, `--force-stage seeds` regenerates seeds
    # but rollout silently keeps the old transcripts (its resume cache keys on
    # seed_id, and seed ids are deterministic so they collide with the prior
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

    stages_to_run: list[tuple[str, Any, dict[str, Any]]] = []
    for stage_name, raw_cfg in ctx["stages"]:
        if not raw_cfg.get("enabled", True):
            continue

        module = STAGES[stage_name]

        if module.SCOPE == "suite" and module.SUITE_OUTPUT and stage_name not in requested_force_stages:
            output_path = Path(ctx["suite_root"]) / module.SUITE_OUTPUT
            if output_path.exists():
                log.info(f"  Skipping {stage_name} (output already exists, use --force-stage {stage_name} to regenerate)")
                continue

        stages_to_run.append((stage_name, module, raw_cfg))

    suite_root = Path(ctx["suite_root"])
    suite_root.mkdir(parents=True, exist_ok=True)
    _write_suite_metadata(ctx)
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

    for stage_name, module, raw_cfg in stages_to_run:
        if manifest is not None and module.SCOPE == "run":
            manifest.stages[stage_name] = "running"
            _write_manifest(manifest, run_root)
        _print_stage_start(stage_name, ctx, raw_cfg)
        stage_start = time.monotonic()
        stage_result: dict[str, Any] = {}
        # Pass the per-stage "was this forced" flag through ctx so stages
        # like rollout/judge can distinguish a real cache-mismatch warning
        # from a redundant one (the user already opted into discarding via
        # --force-stage, possibly via cascade). Stages that don't read
        # _stage_forced ignore it.
        ctx["_stage_forced"] = stage_name in requested_force_stages
        try:
            stage_result = asyncio.run(module.run(ctx, raw_cfg)) or {}
            ok = True
        except (LLMAuthError, LLMInputError, LLMRateLimitError, LLMProviderError) as exc:
            # Classified LLM errors already carry a clean, actionable message.
            # Print just that message; suppress the multi-screen litellm/httpx
            # traceback unless the user opts into verbose output.
            ok = False
            log.error("[error] %s", exc)
            if os.environ.get("P2M_VERBOSE_ERRORS") == "1":
                log.debug("Full traceback:", exc_info=True)
            else:
                log.info("(set P2M_VERBOSE_ERRORS=1 to see the full traceback)")
        except Exception:  # noqa: BLE001
            ok = False
            log.error("Unexpected error", exc_info=True)

        elapsed = time.monotonic() - stage_start
        if ok:
            _print_stage_done(stage_name, elapsed, stage_result.get("_summary"))
        else:
            log.error("%s failed (%.1fs)", stage_name, elapsed)

        if manifest is not None and module.SCOPE == "run":
            manifest.stages[stage_name] = "completed" if ok else "failed"
            manifest.status = "running" if ok else "failed"
            _write_manifest(manifest, run_root)

        if ok and module.SCOPE == "suite":
            _write_suite_metadata(ctx)

        if not ok:
            failed_stage = stage_name
            break

    total_elapsed = time.monotonic() - pipeline_start
    if failed_stage is None:
        log.info(f"  pipeline completed ({total_elapsed:.1f}s)")
        if run_root is not None:
            log.info(f"\n  Results:")
            scores_path = run_root / "scores.jsonl"
            metrics_path = run_root / "metrics.json"
            if scores_path.exists():
                log.info(f"    Scores:  {scores_path}")
            if metrics_path.exists():
                log.info(f"    Metrics: {metrics_path}")
            log.info(f"    Run dir: {run_root}")
            suite_id = ctx.get('suite_id', '')
            run_id = ctx.get('run_id', '')
            if suite_id and run_id:
                log.info(f"\n  Inspect results:")
                log.info(f"    uv run p2m results status {suite_id} {run_id}")
    else:
        log.error("pipeline failed at %s (%.1fs)", failed_stage, total_elapsed)

    if manifest is None:
        return 0 if failed_stage is None else 1

    manifest.ended_at = datetime.now(timezone.utc).isoformat()
    manifest.status = "completed" if failed_stage is None else "failed"
    _write_manifest(manifest, run_root)
    return 0 if failed_stage is None else 1
