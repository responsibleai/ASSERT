# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Helpers for loading results artifacts and computing summary metrics."""

from __future__ import annotations

from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from assert_ai.core.io import load_json, load_jsonl, row_behavior
from assert_ai.core.judge import (
    get_verdict_dimension,
    infer_judge_status,
    is_not_applicable_dimension,
    is_valid_event_flag,
)


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


def _dimension_scale(row: dict[str, Any], metric: str) -> dict[str, Any] | None:
    scales = row.get("dimension_scales")
    if not isinstance(scales, dict):
        return None
    scale = scales.get(metric)
    return scale if isinstance(scale, dict) else None


def _ordinal_scale_values(scale: dict[str, Any] | None) -> list[int | str]:
    if not isinstance(scale, dict) or scale.get("type") != "ordinal":
        return []
    return [
        entry["value"]
        for entry in scale.get("values", [])
        if (
            isinstance(entry, dict)
            and not isinstance(entry.get("value"), bool)
            and isinstance(entry.get("value"), (int, str))
        )
    ]


def detect_dimensions(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Collect all configured verdict dimensions present in the provided rows."""
    seen: set[str] = set()
    for row in rows:
        verdict = row.get("verdict")
        if not isinstance(verdict, dict):
            continue
        dimensions = verdict.get("dimensions")
        if not isinstance(dimensions, dict):
            continue
        for key, value in dimensions.items():
            scale_values = _ordinal_scale_values(_dimension_scale(row, key))
            expected_type = str if scale_values and isinstance(scale_values[0], str) else int
            is_ordinal = (
                bool(scale_values)
                and not isinstance(value, bool)
                and isinstance(value, expected_type)
                and value in scale_values
            )
            if is_valid_event_flag(value) or is_ordinal or is_not_applicable_dimension(verdict, key):
                seen.add(key)
    return sorted(seen)


def compute_dimension_summary(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, Any]:
    """Summarize one binary or ordinal metric over judged rows."""
    row_list = list(rows)
    scale = next(
        (
            candidate
            for row in row_list
            if (candidate := _dimension_scale(row, metric)) is not None
        ),
        None,
    )
    scale_values = _ordinal_scale_values(scale)
    if scale_values:
        grade_counts = {str(value): 0 for value in scale_values}
        grades: list[int | str] = []
        expected_type = str if isinstance(scale_values[0], str) else int
        not_applicable_count = 0
        for row in row_list:
            if infer_judge_status(row) != "ok":
                continue
            verdict = row.get("verdict")
            value = get_verdict_dimension(verdict, metric)
            if (
                not isinstance(value, bool)
                and isinstance(value, expected_type)
                and value in scale_values
            ):
                grade_counts[str(value)] += 1
                grades.append(value)
                continue
            if is_not_applicable_dimension(verdict, metric):
                not_applicable_count += 1
        total = len(grades)
        numeric_grades = [value for value in grades if isinstance(value, int)]
        if len(numeric_grades) == total and numeric_grades:
            median_grade: int | str | float | None = float(median(numeric_grades))
            mean_grade: float | None = mean(numeric_grades)
        elif grades:
            order = {value: index for index, value in enumerate(scale_values)}
            ordered = sorted(grades, key=order.__getitem__)
            median_grade = ordered[(len(ordered) - 1) // 2]
            mean_grade = None
        else:
            median_grade = None
            mean_grade = None
        return {
            "kind": "ordinal",
            "rate": None,
            "counts": grade_counts,
            "rates": {
                key: count / total if total else 0.0
                for key, count in grade_counts.items()
            },
            "count": total,
            "applicable_count": total,
            "not_applicable_count": not_applicable_count,
            "median": median_grade,
            "mean": mean_grade,
            "scale": scale,
        }

    counts = {0: 0, 1: 0}
    total = 0
    not_applicable_count = 0
    for row in row_list:
        if infer_judge_status(row) != "ok":
            continue
        verdict = row.get("verdict")
        value = get_verdict_dimension(verdict, metric)
        if is_valid_event_flag(value):
            counts[int(value)] += 1
            total += 1
            continue
        if is_not_applicable_dimension(verdict, metric):
            not_applicable_count += 1
    return {
        "rate": counts[1] / total if total else None,
        "counts": counts,
        "count": total,
        "applicable_count": total,
        "not_applicable_count": not_applicable_count,
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


def compute_policy_violation_by_permissibility(
    rows: Iterable[dict[str, Any]],
    behavior_categories: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    """Split policy violations by node permissibility, one vote per row.

    Each row contributes to a bucket only when at least one behavior in that
    bucket was relevant. Its vote is true when any such behavior was violated.
    The test case's originating behavior does not affect this calculation.
    """
    categories = [
        (index, entry)
        for index, entry in enumerate(behavior_categories)
        if isinstance(entry, dict)
    ]
    if not categories:
        return {"permissible": None, "not_permissible": None}

    permissible_by_index = {
        index: entry.get("permissible") is True
        for index, entry in categories
    }
    permissible_by_name = {
        str(entry.get("name") or "").strip(): entry.get("permissible") is True
        for _, entry in categories
        if str(entry.get("name") or "").strip()
    }
    flags: dict[bool, list[bool]] = {True: [], False: []}
    not_applicable: dict[bool, int] = {True: 0, False: 0}

    for row in rows:
        verdict = row.get("verdict")
        node_judgments = verdict.get("node_judgments") if isinstance(verdict, dict) else None
        row_applicable = {True: False, False: False}
        row_violated = {True: False, False: False}

        if isinstance(node_judgments, list):
            for node in node_judgments:
                if not isinstance(node, dict):
                    continue
                if "relevant" in node and node.get("relevant") is not True:
                    continue
                violated = node.get("violated")
                if not isinstance(violated, bool):
                    continue

                node_index = node.get("node_index")
                if (
                    isinstance(node_index, int)
                    and not isinstance(node_index, bool)
                    and node_index in permissible_by_index
                ):
                    permissible = permissible_by_index[node_index]
                else:
                    node_name = str(node.get("node_name") or "").strip()
                    if node_name not in permissible_by_name:
                        continue
                    permissible = permissible_by_name[node_name]

                row_applicable[permissible] = True
                row_violated[permissible] = row_violated[permissible] or violated

        for permissible in (True, False):
            if row_applicable[permissible]:
                flags[permissible].append(row_violated[permissible])
            else:
                not_applicable[permissible] += 1

    def summarize(permissible: bool) -> dict[str, Any]:
        values = flags[permissible]
        flagged_count = sum(values)
        clear_count = len(values) - flagged_count
        return {
            "rate": flagged_count / len(values) if values else None,
            "counts": {0: clear_count, 1: flagged_count},
            "count": len(values),
            "applicable_count": len(values),
            "not_applicable_count": not_applicable[permissible],
            "flagged_count": flagged_count,
            "clear_count": clear_count,
        }

    return {
        "permissible": summarize(True),
        "not_permissible": summarize(False),
    }


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
    behavior_categories: Iterable[dict[str, Any]] = (),
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

    permissibility_split = compute_policy_violation_by_permissibility(
        scored_rows,
        behavior_categories,
    )
    if permissibility_split["permissible"] is not None:
        permissible = permissibility_split["permissible"]
        not_permissible = permissibility_split["not_permissible"]
        assert not_permissible is not None
        metrics.update(
            {
                "permissible_policy_violation_rate": permissible["rate"],
                "not_permissible_policy_violation_rate": not_permissible["rate"],
                "policy_violation_on_permissible": permissible,
                "policy_violation_on_not_permissible": not_permissible,
            }
        )

    if include_tester_model:
        metrics["tester_model"] = _first_str(rows, "tester_model")

    return metrics


def compute_prompt_metrics(
    rows: list[dict[str, Any]],
    behavior_categories: Iterable[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    """Compute prompt-only summary metrics."""
    return _compute_test_set_metrics(rows, behavior_categories=behavior_categories)


def compute_scenario_metrics(
    rows: list[dict[str, Any]],
    behavior_categories: Iterable[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    """Compute scenario-only summary metrics."""
    return _compute_test_set_metrics(
        rows,
        include_tester_model=True,
        behavior_categories=behavior_categories,
    )


def load_run_summary(run_dir: Path) -> dict[str, Any] | None:
    """Load one run's manifest and score-derived summaries."""
    manifest = load_json(run_dir / "manifest.json")
    score_rows = load_jsonl(run_dir / "scores.jsonl")
    taxonomy = load_json(run_dir.parent / "taxonomy.json")
    behavior_categories = (taxonomy or {}).get("behavior_categories")
    if not isinstance(behavior_categories, list):
        behavior_categories = []
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
        "prompt_metrics": compute_prompt_metrics(prompt_rows, behavior_categories),
        "scenario_metrics": compute_scenario_metrics(scenario_rows, behavior_categories),
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
        if not is_valid_event_flag(value) and not is_not_applicable_dimension(row.get("verdict"), metric):
            continue
        behavior = row_behavior(row)
        bucket = grouped.setdefault(
            behavior,
            {
                "true_count": 0,
                "count": 0,
                "not_applicable_count": 0,
            },
        )
        if is_valid_event_flag(value):
            bucket["true_count"] += int(value)
            bucket["count"] += 1
        elif is_not_applicable_dimension(row.get("verdict"), metric):
            bucket["not_applicable_count"] += 1

    result: dict[str, dict[str, Any]] = {}
    for behavior, bucket in grouped.items():
        if bucket["count"] <= 0:
            continue
        result[behavior] = {
            "rate": bucket["true_count"] / bucket["count"],
            "count": bucket["count"],
            "not_applicable_count": bucket["not_applicable_count"],
        }
    return result
