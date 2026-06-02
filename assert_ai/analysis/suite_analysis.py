# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Suite-level analysis that combines metrics across stages.

Computes judge rates with confidence intervals, inference health,
test-case outcome analysis, and per-behavior breakdowns for a complete suite.
Produces both structured data and human-readable summaries designed
for terminal display.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from assert_ai.core.io import load_jsonl, row_behavior

from assert_ai.analysis.stability import (
    compute_tester_variation,
    compute_repeatability,
    format_tester_variation,
    format_repeatability,
)
from assert_ai.analysis.inference_metrics import compute_inference_metrics
from assert_ai.analysis.stats import binary_rate_ci, macro_rate


MIN_BEHAVIOR_SUPPORT = 5  # suppress per-behavior rates below this


def _get_dimension(row: dict[str, Any], dim: str) -> bool | None:
    verdict = row.get("verdict")
    if not isinstance(verdict, dict):
        return None
    dimensions = verdict.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    val = dimensions.get(dim)
    if isinstance(val, bool):
        return val
    return None


def _parse_run_label(run_id: str) -> dict[str, str]:
    """Extract human-readable tester/target/judge from run directory name."""
    label: dict[str, str] = {"short": "", "raw": run_id}
    # Detect replicate runs
    if run_id.startswith("tester_interaction_"):
        suffix = run_id.replace("tester_interaction_", "")
        parts = suffix.rsplit("_", 1)
        model = parts[0].replace("-tester", "")
        rep = parts[1] if len(parts) > 1 else ""
        label["short"] = f"{model} {rep}"
        label["type"] = "replicate"
    elif "_retry" in run_id:
        base = run_id.split("-tester")[0] if "-tester" in run_id else run_id[:15]
        label["short"] = f"{base} retry"
        label["type"] = "retry"
    else:
        tester = run_id.split("-tester")[0] if "-tester" in run_id else run_id.split("_")[0]
        label["short"] = tester
        label["type"] = "main"
    return label


def _run_quality(inference: dict[str, Any], judge: dict[str, Any]) -> str:
    """Assign a quality level based on run health indicators."""
    has_serious = (
        inference.get("invalid_tester_rate", 0) >= 0.05
        or inference.get("error_rate", 0) >= 0.05
        or judge.get("judge_failure_rate", 0) >= 0.05
        or inference.get("completion_rate", 1.0) < 0.90
    )
    if has_serious:
        return "low"
    if inference.get("invalid_tester_rate", 0) > 0 or judge.get("judge_failures", 0) > 0:
        return "medium"
    return "high"


def compute_judge_metrics(
    score_rows: list[dict[str, Any]],
    *,
    dimensions: list[str] | None = None,
    n_boot: int = 2000,
) -> dict[str, Any]:
    """Compute judge-stage metrics with CIs and macro averages."""
    if not score_rows:
        return {"total": 0}

    ok_rows = [r for r in score_rows if r.get("judge_status") == "ok"]
    judge_failures = len(score_rows) - len(ok_rows)

    if dimensions is None:
        dims: set[str] = set()
        for row in ok_rows:
            verdict = row.get("verdict")
            if isinstance(verdict, dict):
                d = verdict.get("dimensions")
                if isinstance(d, dict):
                    dims.update(d.keys())
        dimensions = sorted(dims)

    dim_results: dict[str, Any] = {}
    for dim in dimensions:
        outcomes: list[bool] = []
        test_set: list[str] = []
        for row in ok_rows:
            val = _get_dimension(row, dim)
            if val is not None:
                outcomes.append(val)
                test_set.append(str(row.get("test_case_id", "")))

        overall = binary_rate_ci(outcomes, groups=test_set, n_boot=n_boot)

        # Per-behavior rates
        by_behavior: dict[str, list[bool]] = defaultdict(list)
        for row in ok_rows:
            val = _get_dimension(row, dim)
            if val is not None:
                b = (row_behavior(row) or "unknown")
                by_behavior[b].append(val)

        behavior_rates: dict[str, dict[str, Any]] = {}
        for b, vals in sorted(by_behavior.items()):
            pos = sum(vals)
            behavior_rates[b] = {
                "count": len(vals),
                "positive": pos,
                "rate": pos / len(vals) if vals else None,
                "sufficient": len(vals) >= MIN_BEHAVIOR_SUPPORT,
            }

        macro = macro_rate(
            {b: s for b, s in behavior_rates.items() if s["sufficient"]},
            min_support=MIN_BEHAVIOR_SUPPORT,
        )

        dim_results[dim] = {
            "micro": overall,
            "macro": macro,
            "by_behavior": behavior_rates,
        }

    return {
        "total": len(score_rows),
        "scored": len(ok_rows),
        "judge_failures": judge_failures,
        "judge_failure_rate": judge_failures / len(score_rows) if score_rows else 0.0,
        "dimensions": dim_results,
    }


def analyze_suite(
    suite_dir: str | Path,
    *,
    n_boot: int = 2000,
) -> dict[str, Any]:
    """Run full analysis on a suite directory."""
    suite_path = Path(suite_dir)

    run_dirs = []
    for child in sorted(suite_path.iterdir()):
        if child.is_dir() and (child / "scores.jsonl").exists():
            run_dirs.append(child)

    if not run_dirs:
        return {"error": f"No runs with scores.jsonl found in {suite_path}"}

    all_results: dict[str, Any] = {"suite": suite_path.name, "runs": {}}
    all_scored_for_variation: list[dict[str, Any]] = []
    runs_by_tester: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)

    for run_dir in run_dirs:
        run_id = run_dir.name
        inference_rows = load_jsonl(run_dir / "inference_set.jsonl")
        score_rows = load_jsonl(run_dir / "scores.jsonl")

        inference = compute_inference_metrics(inference_rows)
        judge = compute_judge_metrics(score_rows, n_boot=n_boot)

        label = _parse_run_label(run_id)
        quality = _run_quality(inference, judge)

        tester_model = ""
        for row in score_rows:
            am = row.get("tester_model", "")
            if am:
                tester_model = str(am)
                break
        if not tester_model:
            tester_model = label["short"]

        all_results["runs"][run_id] = {
            "label": label,
            "tester_model": tester_model,
            "trust": quality,
            "n_seeds": len(score_rows),
            "inference": inference,
            "judge": judge,
        }

        for row in score_rows:
            if row.get("judge_status") == "ok":
                verdict = row.get("verdict", {})
                dims = verdict.get("dimensions", {}) if isinstance(verdict, dict) else {}
                all_scored_for_variation.append({
                    "test_case_id": row.get("test_case_id", ""),
                    "run": run_id,
                    "tester_model": tester_model,
                    "policy_violation": bool(dims.get("policy_violation", False)),
                    "behavior": row_behavior(row),
                })

        runs_by_tester[tester_model].append((run_id, score_rows))

    # Cross-tester variation — main runs only
    main_scored = [r for r in all_scored_for_variation
                   if all_results["runs"].get(r["run"], {}).get("label", {}).get("type") == "main"]
    if len(set(r.get("tester_model", "") for r in main_scored)) > 1:
        all_results["tester_variation"] = compute_tester_variation(main_scored)

    # Repeatability (same tester, multiple runs)
    for tester, run_list in runs_by_tester.items():
        if len(run_list) < 2:
            continue
        rep_rows = []
        for run_id, score_rows in run_list:
                for row in score_rows:
                    if row.get("judge_status") == "ok":
                        verdict = row.get("verdict", {})
                        dims = verdict.get("dimensions", {}) if isinstance(verdict, dict) else {}
                        rep_rows.append({
                            "test_case_id": row.get("test_case_id", ""),
                            "run": run_id,
                            "policy_violation": bool(dims.get("policy_violation", False)),
                        })
        if rep_rows:
            all_results.setdefault("repeatability", {})[tester] = compute_repeatability(rep_rows)

    return all_results


def format_suite_summary(results: dict[str, Any]) -> str:
    """Format full suite analysis as a human-readable report."""
    lines = [f"=== Suite: {results.get('suite', '?')} ===", ""]

    runs = results.get("runs", {})
    main_runs = {k: v for k, v in runs.items() if v.get("label", {}).get("type") == "main"}
    other_runs = {k: v for k, v in runs.items() if v.get("label", {}).get("type") != "main"}

    # 1. Main runs comparison table
    if main_runs:
        lines.append("Main runs")
        lines.append(f"{'Tester':<22} {'Scored':>6} {'Taxonomy violations':>18} "
                     f"{'Invalid turns':>13} {'Run quality':>11}")
        lines.append("─" * 76)

        for run_id, rd in sorted(main_runs.items(), key=lambda x: -(
            x[1]["judge"].get("dimensions", {}).get("policy_violation", {}).get("micro", {}).get("rate") or 0
        )):
            label = rd.get("label", {}).get("short", run_id[:20])
            judge = rd["judge"]
            inference = rd["inference"]
            scored = judge.get("scored", 0)

            pv_data = judge.get("dimensions", {}).get("policy_violation", {}).get("micro", {})
            pv_rate = pv_data.get("rate")
            pv_pos = pv_data.get("n_positive", 0)
            pv_str = f"{pv_pos}/{scored} ({pv_rate:.0%})" if pv_rate is not None else "n/a"

            inv_aud = inference.get("invalid_tester_rate", 0)
            inv_str = f"{inv_aud:.0%}" if inv_aud > 0 else "—"
            quality = rd.get("trust", "?")

            lines.append(f"{label:<22} {scored:>6} {pv_str:>18} "
                         f"{inv_str:>13} {quality:>11}")
        lines.append("")

    # 2. Cross-tester variation (main runs only) — show early for context
    if "tester_variation" in results:
        lines.append(format_tester_variation(results["tester_variation"]))
        lines.append("")

    # 3. Repeatability (per tester group)
    if "repeatability" in results:
        for tester, rep in sorted(results["repeatability"].items()):
            n_shared = rep.get("n_test_cases_full_coverage", 0)
            if n_shared < 5:
                lines.append(f"Test-case-level repeatability: {tester}")
                lines.append(f"  Too few shared test_set ({n_shared}) for reliable estimate.")
            else:
                lines.append(format_repeatability(rep))
            lines.append("")

    # 4. Non-main runs (compact, if any)
    if other_runs:
        lines.append("Non-main runs (replicates/retries)")
        for run_id, rd in sorted(other_runs.items()):
            label = rd.get("label", {}).get("short", run_id[:20])
            run_type = rd.get("label", {}).get("type", "?")
            judge = rd["judge"]
            scored = judge.get("scored", 0)
            pv_data = judge.get("dimensions", {}).get("policy_violation", {}).get("micro", {})
            pv_pos = pv_data.get("n_positive", 0)
            pv_rate = pv_data.get("rate")
            quality = rd.get("trust", "?")
            pv_str = f"{pv_pos}/{scored} ({pv_rate:.0%})" if pv_rate is not None else "n/a"
            lines.append(f"  {label} ({run_type}): {pv_str}  run quality: {quality}")
        lines.append("")

    # 5. Per-run detail (main runs only, with behavior breakdowns)
    for run_id, rd in sorted(main_runs.items()):
        judge = rd["judge"]
        inference = rd["inference"]
        quality = rd.get("trust", "?")
        label = rd.get("label", {}).get("short", run_id)

        lines.append(f"--- {label} ({run_id}) ---")
        if quality == "low":
            lines.append("  ⚠ DATA QUALITY WARNING — interpret with caution")

        # Inference (compact — only show stop reasons if there are issues)
        sr = inference.get("stop_reasons", {})
        lines.append(f"  Inference: {inference['total']} inference rows, "
                     f"{inference['completion_rate']:.0%} non-error termination")
        if inference.get("invalid_tester_rate", 0) > 0 or inference.get("error_rate", 0) > 0:
            sr_parts = [f"{k}={v}" for k, v in sorted(sr.items())]
            lines.append(f"  Stop reasons: {', '.join(sr_parts)}")

        # Judge dimensions
        for dim, data in sorted(judge.get("dimensions", {}).items()):
            micro = data["micro"]
            if micro["rate"] is None:
                continue

            rate_str = f"{micro['n_positive']}/{micro['n']} ({micro['rate']:.0%})"
            ci_str = f" 90% CI [{micro['ci_lower']:.0%}, {micro['ci_upper']:.0%}]"

            lines.append(f"  {dim}: {rate_str}{ci_str}")

            # Per-behavior: skip if all zero
            by_b = data.get("by_behavior", {})
            any_positive = any(s["positive"] > 0 for s in by_b.values())

            if not any_positive:
                lines.append(f"    (0 observed across {len(by_b)} behavior_categories)")
                continue

            # Show behavior_categories with sufficient support, sorted by contribution
            substantial = [(b, s) for b, s in by_b.items() if s["sufficient"] and s["positive"] > 0]
            sparse = [(b, s) for b, s in by_b.items() if not s["sufficient"]]

            if substantial:
                for b, s in sorted(substantial, key=lambda x: -x[1]["positive"]):
                    lines.append(f"    {b}: {s['positive']}/{s['count']} ({s['rate']:.0%})")
            if sparse:
                sparse_pos = sum(s["positive"] for _, s in sparse)
                sparse_total = sum(s["count"] for _, s in sparse)
                lines.append(f"    ({len(sparse)} sparse behavior_categories, n<{MIN_BEHAVIOR_SUPPORT}: "
                             f"{sparse_pos} violations in {sparse_total} cases)")

        lines.append("")

    return "\n".join(lines)
