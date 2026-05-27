"""Helpers for loading results artifacts and computing summary metrics."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from assert_eval.core.io import load_json, load_jsonl, row_behavior
from assert_eval.core.judge import get_verdict_dimension, infer_judge_status, is_valid_event_flag


def current_stage_status(manifest: dict[str, Any] | None) -> tuple[str, str]:
    """Return overall manifest status and the currently running stage, if any."""
    if isinstance(manifest, dict):
        manifest_status = manifest.get("status")
        if isinstance(manifest_status, str) and manifest_status:
            stages = manifest.get("stages")
            if isinstance(stages, dict):
                for stage_name, stage_status in stages.items():
                    if stage_status == "running":
                        return manifest_status, str(stage_name)
            return manifest_status, "—"
    return "unknown", "—"


def detect_dimensions(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Collect all binary verdict dimensions present in the provided rows."""
    seen: set[str] = set()
    for row in rows:
        verdict = row.get("verdict")
        if not isinstance(verdict, dict):
            continue
        dimensions = verdict.get("dimensions")
        if not isinstance(dimensions, dict):
            continue
        for key, value in dimensions.items():
            if is_valid_event_flag(value):
                seen.add(key)
    return sorted(seen)


def compute_dimension_summary(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, Any]:
    """Summarize one binary metric over a set of judged rows."""
    counts = {0: 0, 1: 0}
    total = 0
    for row in rows:
        if infer_judge_status(row) != "ok":
            continue
        value = get_verdict_dimension(row.get("verdict"), metric)
        if not is_valid_event_flag(value):
            continue
        counts[int(value)] += 1
        total += 1
    return {
        "rate": counts[1] / total if total else None,
        "counts": counts,
        "count": total,
        "flagged_count": counts[1],
        "clear_count": counts[0],
    }


def dimension_rate(metrics: dict[str, Any], metric: str) -> float | None:
    """Return the rate for one metric from a metrics payload."""
    dimensions = metrics.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    summary = dimensions.get(metric)
    if not isinstance(summary, dict):
        return None
    rate = summary.get("rate")
    return float(rate) if isinstance(rate, (int, float)) else None


def _first_str(rows: Iterable[dict[str, Any]], key: str) -> str:
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return "—"


def _compute_test_set_metrics(
    rows: list[dict[str, Any]],
    *,
    include_tester_model: bool = False,
) -> dict[str, Any] | None:
    if not rows:
        return None

    scored_rows = [row for row in rows if infer_judge_status(row) == "ok"]
    judge_failures = len(rows) - len(scored_rows)
    dimensions = {
        dim: compute_dimension_summary(scored_rows, dim)
        for dim in detect_dimensions(scored_rows)
    }

    metrics: dict[str, Any] = {
        "total": len(rows),
        "scored_total": len(scored_rows),
        "judge_failures": judge_failures,
        "judge_failure_rate": judge_failures / len(rows),
        "policy_violation_rate": dimension_rate({"dimensions": dimensions}, "policy_violation"),
        "overrefusal_rate": dimension_rate({"dimensions": dimensions}, "overrefusal"),
        "dimensions": dimensions,
        "target": _first_str(rows, "target"),
        "judge_model": _first_str(rows, "judge_model"),
    }

    if include_tester_model:
        metrics["tester_model"] = _first_str(rows, "tester_model")

    return metrics


def compute_prompt_metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compute prompt-only summary metrics."""
    return _compute_test_set_metrics(rows)


def compute_scenario_metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compute scenario-only summary metrics."""
    return _compute_test_set_metrics(rows, include_tester_model=True)


def load_run_summary(run_dir: Path) -> dict[str, Any] | None:
    """Load one run's manifest and score-derived summaries."""
    manifest = load_json(run_dir / "manifest.json")
    score_rows = load_jsonl(run_dir / "scores.jsonl")
    prompt_rows = [row for row in score_rows if not row.get("tester_model")]
    scenario_rows = [row for row in score_rows if row.get("tester_model")]

    stages = (manifest or {}).get("stages", {})
    has_scores = isinstance(stages, dict) and stages.get("judge") is not None
    has_data = bool(prompt_rows or scenario_rows)
    if not has_data and not has_scores:
        return None
    if not has_data and (manifest or {}).get("status") == "failed":
        return None

    status, current_stage = current_stage_status(manifest)
    return {
        "run_id": run_dir.name,
        "path": str(run_dir),
        "manifest": manifest,
        "status": status,
        "current_stage": current_stage,
        "started_at": (manifest or {}).get("started_at"),
        "ended_at": (manifest or {}).get("ended_at"),
        "prompt_metrics": compute_prompt_metrics(prompt_rows),
        "scenario_metrics": compute_scenario_metrics(scenario_rows),
        "prompt_rows": prompt_rows,
        "scenario_rows": scenario_rows,
    }


def count_test_case_types(path: Path) -> tuple[int, int]:
    """Count prompt and scenario rows in a test_set JSONL file."""
    rows = load_jsonl(path)
    prompt_count = 0
    scenario_count = 0
    for row in rows:
        row_type = row.get("type")
        if row_type == "prompt":
            prompt_count += 1
        elif row_type == "scenario":
            scenario_count += 1
    return prompt_count, scenario_count


def load_suite_summary(suite_dir: Path) -> dict[str, Any] | None:
    """Load one suite's metadata, runs, and high-level status."""
    suite_meta = load_json(suite_dir / "suite.json")
    taxonomy = load_json(suite_dir / "taxonomy.json")
    if suite_meta is None and taxonomy is None:
        return None

    run_summaries = []
    for child in sorted(suite_dir.iterdir()) if suite_dir.exists() else []:
        if not child.is_dir():
            continue
        run_summary = load_run_summary(child)
        if run_summary is not None:
            run_summaries.append(run_summary)

    has_results = any(
        run_summary.get("prompt_metrics") is not None
        or run_summary.get("scenario_metrics") is not None
        for run_summary in run_summaries
    )
    prompt_test_case_count, scenario_test_case_count = count_test_case_types(suite_dir / "test_set.jsonl")

    behavior_name = suite_dir.name
    behavior_block = (taxonomy or {}).get("behavior")
    if isinstance(behavior_block, dict) and isinstance(behavior_block.get("name"), str) and behavior_block.get("name"):
        behavior_name = behavior_block["name"]

    if has_results:
        status = "has_results"
    elif prompt_test_case_count or scenario_test_case_count:
        status = "test_set_ready"
    else:
        status = "systematized"

    return {
        "suite_id": suite_dir.name,
        "path": str(suite_dir),
        "behavior_name": behavior_name,
        "behavior_category_count": len((taxonomy or {}).get("behavior_categories") or []),
        "prompt_test_case_count": prompt_test_case_count,
        "scenario_test_case_count": scenario_test_case_count,
        "run_count": len(run_summaries),
        "runs": run_summaries,
        "status": status,
        "created_at": (suite_meta or {}).get("created_at"),
        "has_systematization": (suite_dir / "systematization.json").exists(),
    }


def load_all_suites(results_dir: Path) -> list[dict[str, Any]]:
    """Load all readable suites under a results directory."""
    if not results_dir.exists():
        return []
    suites = []
    for child in sorted(results_dir.iterdir()):
        if not child.is_dir():
            continue
        suite_summary = load_suite_summary(child)
        if suite_summary is not None:
            suites.append(suite_summary)
    suites.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return suites


def iter_run_dirs_for_viewer_rebuild(
    *,
    results_root: Path,
    suite: str | None,
    run: str | None,
) -> list[Path]:
    """Return the run directories targeted by a viewer rebuild command."""

    def is_run_dir(path: Path) -> bool:
        return path.is_dir() and (
            (path / "inference_set.jsonl").exists()
            or (path / "scores.jsonl").exists()
            or (path / "manifest.json").exists()
        )

    if suite and run:
        run_dir = results_root / suite / run
        if not is_run_dir(run_dir):
            raise ValueError(f"Run not found: {suite}/{run}")
        return [run_dir]

    if suite:
        suite_dir = results_root / suite
        if not suite_dir.is_dir():
            raise ValueError(f"Suite not found: {suite}")
        return sorted(path for path in suite_dir.iterdir() if is_run_dir(path))

    run_dirs: list[Path] = []
    for suite_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        run_dirs.extend(sorted(path for path in suite_dir.iterdir() if is_run_dir(path)))
    return run_dirs


def behavior_metric_map(
    rows: Iterable[dict[str, Any]],
    metric: str,
) -> dict[str, dict[str, Any]]:
    """Group one metric by behavior for compare/delta views."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if infer_judge_status(row) != "ok":
            continue
        value = get_verdict_dimension(row.get("verdict"), metric)
        if not is_valid_event_flag(value):
            continue
        behavior = row_behavior(row)
        bucket = grouped.setdefault(
            behavior,
            {
                "true_count": 0,
                "count": 0,
            },
        )
        bucket["true_count"] += int(value)
        bucket["count"] += 1

    result: dict[str, dict[str, Any]] = {}
    for behavior, bucket in grouped.items():
        if bucket["count"] <= 0:
            continue
        result[behavior] = {
            "rate": bucket["true_count"] / bucket["count"],
            "count": bucket["count"],
        }
    return result
