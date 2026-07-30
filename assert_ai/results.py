# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Helpers for loading results artifacts and computing summary metrics."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
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

log = logging.getLogger(__name__)

# Above this share of unscored rows, the reported rates describe a small enough
# slice of the suite that quoting them without the coverage is misleading.
COVERAGE_WARN_THRESHOLD = 0.10


def compute_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise how much of ``rows`` actually produced a usable verdict.

    Rows that could not be judged are excluded from every rate denominator. That
    exclusion is not random: a provider content filter rejects the *most*
    adversarial transcripts, which are the ones most likely to contain a real
    violation. So a run whose worst rows were dropped reports a low violation
    rate and reads as a pass. Reporting the denominator alongside the rate is
    what makes that visible.

    ``infer_judge_status`` collapses every non-ok status to ``judge_failed``, so
    the per-status breakdown reads the raw ``judge_status`` field to keep
    ``filter_skipped`` distinguishable from a judge error.
    """
    total = len(rows)
    by_status: Counter[str] = Counter()
    for row in rows:
        inferred = infer_judge_status(row)
        if inferred == "ok":
            by_status["ok"] += 1
            continue
        raw = row.get("judge_status")
        if isinstance(raw, str) and raw and raw != "ok":
            by_status[raw] += 1
        else:
            by_status[inferred] += 1

    scored = by_status.get("ok", 0)
    excluded = total - scored
    return {
        "total": total,
        "scored": scored,
        "excluded": excluded,
        "scored_rate": (scored / total) if total else 0.0,
        "excluded_rate": (excluded / total) if total else 0.0,
        "by_status": dict(by_status),
        "below_threshold": bool(total) and (excluded / total) > COVERAGE_WARN_THRESHOLD,
    }


def format_coverage(coverage: dict[str, Any]) -> str:
    """Render coverage as a single line to print directly above the rates."""
    total = coverage.get("total", 0)
    scored = coverage.get("scored", 0)
    rate = coverage.get("scored_rate", 0.0) * 100.0
    line = f"Scored {scored}/{total} ({rate:.1f}%)"
    excluded_statuses = {
        status: count
        for status, count in (coverage.get("by_status") or {}).items()
        if status != "ok" and count
    }
    if excluded_statuses:
        detail = " · ".join(
            f"{count} {status}" for status, count in sorted(excluded_statuses.items())
        )
        line += f"   ! {detail}"
    return line


def compute_judge_agreement(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Chance-corrected agreement between judges, per dimension, across a run.

    Returns ``None`` for single-judge runs, which have no agreement to measure.

    A 2-1 split and a 3-0 consensus otherwise produce identical output, so the
    one signal indicating how much to trust a verdict is lost. The existing
    ``multi_judge.agreement`` field is raw percent agreement on one dimension of
    one row; it does not subtract the agreement expected by chance, which on a
    skewed base rate is most of it.

    Kappa is a property of the run, not of a row: it needs many items to
    estimate the marginal category distribution, so votes are pooled across all
    rows here rather than computed per row.
    """
    from assert_ai.analysis.stats import KAPPA_WARN_THRESHOLD, fleiss_kappa

    per_dimension: dict[str, list[list[Any]]] = {}
    judge_counts: set[int] = set()

    for row in rows:
        envelope = row.get("multi_judge")
        if not isinstance(envelope, dict):
            continue
        votes = envelope.get("votes")
        if not isinstance(votes, dict):
            continue
        for dimension, dimension_votes in votes.items():
            if not isinstance(dimension_votes, list) or len(dimension_votes) < 2:
                continue
            per_dimension.setdefault(dimension, []).append(list(dimension_votes))
            judge_counts.add(len(dimension_votes))

    if not per_dimension:
        return None

    by_dimension: dict[str, Any] = {}
    for dimension, ratings in sorted(per_dimension.items()):
        kappa = fleiss_kappa(ratings)
        by_dimension[dimension] = {
            "kappa": round(kappa, 4) if kappa is not None else None,
            "items": len(ratings),
            "raters": len(ratings[0]) if ratings else 0,
            "low_agreement": bool(kappa is not None and kappa < KAPPA_WARN_THRESHOLD),
        }
        if kappa is not None and kappa < KAPPA_WARN_THRESHOLD:
            log.warning(
                "Low inter-rater agreement on '%s' (Fleiss kappa=%.2f over %d rows, "
                "%d judges). The consensus verdict for this dimension is not a "
                "reliable one.",
                dimension,
                kappa,
                len(ratings),
                len(ratings[0]),
            )

    return {
        "method": "fleiss_kappa",
        "judges": sorted(judge_counts),
        "by_dimension": by_dimension,
    }


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


def compute_judge_fingerprint(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Identify the judge configuration that produced these scores.

    Swapping a judge model or editing the judge prompt moves measured rates on
    an unchanged target. Without something to compare against, that shift reads
    as a real change in the system under test - someone switches judge model to
    cut cost, rates move several points, and the move gets attributed to the
    target.

    The fingerprint is a stable digest of what would change the measurement, so
    two runs of the same suite can be checked for comparability before their
    numbers are put side by side.
    """
    judge_model = _first_str(rows, "judge_model")
    prompt_hashes = sorted(
        {
            value
            for row in rows
            for value in [row.get("judge_prompt_sha") or row.get("judge_system_prompt_sha")]
            if isinstance(value, str) and value
        }
    )
    dimension_names = sorted(detect_dimensions(rows))
    judge_counts = sorted(
        {
            envelope["n"]
            for row in rows
            for envelope in [row.get("multi_judge")]
            if isinstance(envelope, dict) and isinstance(envelope.get("n"), int)
        }
    )

    material = json.dumps(
        {
            "judge_model": judge_model,
            "prompt_hashes": prompt_hashes,
            "dimensions": dimension_names,
            "judges": judge_counts,
        },
        sort_keys=True,
    )
    return {
        "judge_model": judge_model,
        "prompt_hashes": prompt_hashes,
        "dimensions": dimension_names,
        "judges": judge_counts,
        "fingerprint": hashlib.sha256(material.encode("utf-8")).hexdigest()[:16],
    }


def warn_if_judge_changed(
    current: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> bool:
    """Warn when the judge configuration differs between two runs of a suite."""
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return False
    if current.get("fingerprint") == previous.get("fingerprint"):
        return False
    log.warning(
        "Judge configuration changed since the previous run (%s -> %s). Rates "
        "from these two runs are not directly comparable; a difference may come "
        "from the judge rather than the target.",
        previous.get("judge_model") or "unknown",
        current.get("judge_model") or "unknown",
    )
    return True


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
    coverage = compute_coverage(rows)
    if coverage["below_threshold"]:
        log.warning(
            "%s - rates below describe only the scored rows. Excluded rows are "
            "not a random sample: content filters reject the most adversarial "
            "transcripts, so the true rate is likely higher than reported.",
            format_coverage(coverage),
        )
    dimensions = {
        dim: compute_dimension_summary(scored_rows, dim)
        for dim in detect_dimensions(scored_rows)
    }

    metrics: dict[str, Any] = {
        "total": len(rows),
        "scored_total": len(scored_rows),
        "coverage": coverage,
        "judge_failures": judge_failures,
        "judge_failure_rate": judge_failures / len(rows),
        "policy_violation_rate": dimension_rate({"dimensions": dimensions}, "policy_violation"),
        "overrefusal_rate": dimension_rate({"dimensions": dimensions}, "overrefusal"),
        "dimensions": dimensions,
        "target": _first_str(rows, "target"),
        "judge_model": _first_str(rows, "judge_model"),
    }

    agreement = compute_judge_agreement(scored_rows)
    if agreement is not None:
        metrics["judge_agreement"] = agreement

    fingerprint = compute_judge_fingerprint(rows)
    if fingerprint is not None:
        metrics["judge_fingerprint"] = fingerprint

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
