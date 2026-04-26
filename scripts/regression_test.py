#!/usr/bin/env python3
"""PR Regression Test Runner

Runs the P2M evaluation pipeline on two git commits (baseline and treatment)
with frozen risk specs, collects metrics from judge scores, and produces a
gate decision (PASS / WARN / BLOCK) based on paired statistical tests.

Usage:
    # Compare current HEAD against merge base
    python scripts/regression_test.py

    # Compare two specific commits
    python scripts/regression_test.py --baseline abc123 --treatment def456

    # Override seed count (default: 50 per risk spec)
    python scripts/regression_test.py --seeds 20

    # Use a custom artifacts directory
    python scripts/regression_test.py --artifacts-dir /tmp/regression
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = [
    REPO_ROOT / "tests" / "regression" / "config_safety.yaml",
    REPO_ROOT / "tests" / "regression" / "config_quality.yaml",
]
DEFAULT_SEEDS = 50


def _git(args: list[str], cwd: Path = REPO_ROOT) -> str:
    """Run a git command and return stripped stdout."""
    result = subprocess.run(
        ["git", "--no-pager"] + args,
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def _get_merge_base() -> str:
    """Find the merge base of HEAD against the default branch."""
    for branch in ("main", "changliu2"):
        try:
            return _git(["merge-base", f"origin/{branch}", "HEAD"])
        except RuntimeError:
            continue
    raise RuntimeError("Cannot determine merge base. Pass --baseline explicitly.")


def _run_pipeline(
    config: Path,
    run_label: str,
    seeds: int,
    artifacts_dir: Path,
) -> Path:
    """Run one pipeline config and return the scores.jsonl path."""
    suite_name = config.stem  # e.g. "config_safety" -> suite name
    results_dir = artifacts_dir / run_label / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "p2m", "run",
        "--config", str(config),
    ]
    env_patch = {
        "P2M_RESULTS_DIR": str(results_dir),
        "P2M_SEED_BUDGET": str(seeds),
    }

    import os
    env = {**os.environ, **env_patch}

    print(f"\n{'='*60}")
    print(f"Running: {config.name} | label={run_label} | seeds={seeds}")
    print(f"Results: {results_dir}")
    print(f"{'='*60}")

    t0 = time.time()
    result = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    elapsed = time.time() - t0
    print(f"Pipeline {config.name} ({run_label}) finished in {elapsed:.0f}s, rc={result.returncode}")

    if result.returncode != 0:
        print(f"WARNING: Pipeline exited with code {result.returncode}", file=sys.stderr)

    # Find scores.jsonl — the runner writes to results_dir/{suite}/{run}/scores.jsonl
    scores_files = list(results_dir.rglob("scores.jsonl"))
    if not scores_files:
        raise FileNotFoundError(f"No scores.jsonl found under {results_dir}")

    return scores_files[0]


def _load_scores(path: Path) -> list[dict[str, Any]]:
    """Load scores from a JSONL file."""
    scores = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                scores.append(json.loads(line))
    return scores


def _extract_metrics(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract per-seed metrics from judge scores for statistical comparison.

    Returns dict with:
      - violation_rates: list[float] — per-seed binary (1=violation, 0=no)
      - judge_pass_count: int
      - judge_fail_count: int
      - total: int
      - failure_modes: set of sub_risk names where violation was found
      - wall_time: float (not available from scores alone, tracked externally)
    """
    violation_rates = []
    failure_modes = set()
    judge_ok = 0
    judge_fail = 0

    for s in scores:
        if s.get("judge_status") != "ok":
            judge_fail += 1
            continue
        judge_ok += 1
        verdict = s.get("verdict", {})
        dims = verdict.get("dimensions", {})
        violated = dims.get("policy_violation", False)
        violation_rates.append(1.0 if violated else 0.0)
        if violated:
            failure_modes.add(s.get("sub_risk", "unknown"))

    return {
        "violation_rates": violation_rates,
        "judge_pass_count": judge_ok,
        "judge_fail_count": judge_fail,
        "total": len(scores),
        "failure_mode_count": len(failure_modes),
    }


def _compare_runs(
    baseline_metrics: dict[str, Any],
    treatment_metrics: dict[str, Any],
    spec_name: str,
) -> list[dict]:
    """Compare baseline and treatment metrics using statistical tests."""
    # Import here to keep top-level imports light
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from regression_stats import compare_boolean, gate_decision, paired_ttest

    results = []

    # 1. Violation rate (continuous, per-seed — higher violation detection = better)
    b_vr = baseline_metrics["violation_rates"]
    t_vr = treatment_metrics["violation_rates"]
    if b_vr and t_vr:
        # Pad shorter list with None (skip) if lengths differ
        n = min(len(b_vr), len(t_vr))
        r = paired_ttest(b_vr[:n], t_vr[:n], metric_direction="higher_is_better")
        r["metric_name"] = f"{spec_name}/violation_rate"
        r["category"] = "science"
        results.append(r)

    # 2. Judge failure rate (boolean — lower is better)
    b_total = baseline_metrics["total"]
    t_total = treatment_metrics["total"]
    b_pass = baseline_metrics["judge_pass_count"]
    t_pass = treatment_metrics["judge_pass_count"]
    r = compare_boolean(b_pass, b_total, t_pass, t_total)
    r["metric_name"] = f"{spec_name}/judge_success_rate"
    r["category"] = "engineering"
    results.append(r)

    # 3. Failure mode count (report only, not gated)
    b_fm = baseline_metrics["failure_mode_count"]
    t_fm = treatment_metrics["failure_mode_count"]
    results.append({
        "metric_name": f"{spec_name}/failure_mode_count",
        "category": "science",
        "effect": "Info",
        "baseline_value": b_fm,
        "treatment_value": t_fm,
    })

    return results


def _print_results(all_results: list[dict], decision: dict) -> None:
    """Print a formatted summary of the regression comparison."""
    print(f"\n{'='*70}")
    print("REGRESSION TEST RESULTS")
    print(f"{'='*70}\n")

    for r in all_results:
        effect = r.get("effect", "?")
        name = r.get("metric_name", "?")
        icon = {"Improved": "✅", "Degraded": "❌", "Inconclusive": "⚠️",
                "TooFewSamples": "📊", "Info": "ℹ️"}.get(effect, "?")

        detail = ""
        if "p_value" in r and r["p_value"] is not None:
            detail = f"  p={r['p_value']:.4f}  Δ={r.get('mean_diff', 0):+.4f}"
        elif "baseline_rate" in r:
            detail = f"  base={r['baseline_rate']:.2%}  treat={r['treatment_rate']:.2%}"
        elif "baseline_value" in r:
            detail = f"  base={r['baseline_value']}  treat={r['treatment_value']}"

        print(f"  {icon} {name}: {effect}{detail}")

    print(f"\n{'─'*70}")
    gate = decision["decision"]
    gate_icon = {"PASS": "✅", "WARN": "⚠️", "BLOCK": "❌"}[gate]
    print(f"  GATE DECISION: {gate_icon} {gate}")
    for reason in decision.get("reasons", []):
        print(f"    - {reason}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="P2M PR Regression Test")
    parser.add_argument("--baseline", default=None, help="Baseline commit SHA (default: merge base)")
    parser.add_argument("--treatment", default=None, help="Treatment commit SHA (default: HEAD)")
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS, help=f"Seeds per risk spec (default: {DEFAULT_SEEDS})")
    parser.add_argument("--artifacts-dir", type=Path, default=None, help="Artifacts directory (default: temp)")
    parser.add_argument("--skip-baseline", action="store_true", help="Skip baseline run (use existing artifacts)")
    parser.add_argument("--skip-treatment", action="store_true", help="Skip treatment run (use existing artifacts)")
    args = parser.parse_args()

    # Resolve commits
    baseline_sha = args.baseline or _get_merge_base()
    treatment_sha = args.treatment or _git(["rev-parse", "HEAD"])
    print(f"Baseline:  {baseline_sha[:12]}")
    print(f"Treatment: {treatment_sha[:12]}")
    print(f"Seeds:     {args.seeds} per risk spec")

    # Artifacts directory
    if args.artifacts_dir:
        arts_dir = args.artifacts_dir
    else:
        arts_dir = REPO_ROOT / "artifacts" / "regression"
    arts_dir.mkdir(parents=True, exist_ok=True)
    print(f"Artifacts: {arts_dir}")

    all_results: list[dict] = []

    for config in CONFIGS:
        if not config.exists():
            print(f"SKIP: {config} not found", file=sys.stderr)
            continue

        spec_name = config.stem.replace("config_", "")  # "safety" or "quality"

        # Run baseline
        if not args.skip_baseline:
            baseline_scores_path = _run_pipeline(
                config, f"baseline-{baseline_sha[:8]}", args.seeds, arts_dir,
            )
        else:
            # Find existing baseline scores
            candidates = list((arts_dir).rglob(f"baseline-{baseline_sha[:8]}/**/scores.jsonl"))
            if not candidates:
                print(f"ERROR: No baseline scores found for {baseline_sha[:8]}", file=sys.stderr)
                return 1
            baseline_scores_path = candidates[0]

        # Run treatment
        if not args.skip_treatment:
            treatment_scores_path = _run_pipeline(
                config, f"treatment-{treatment_sha[:8]}", args.seeds, arts_dir,
            )
        else:
            candidates = list((arts_dir).rglob(f"treatment-{treatment_sha[:8]}/**/scores.jsonl"))
            if not candidates:
                print(f"ERROR: No treatment scores found for {treatment_sha[:8]}", file=sys.stderr)
                return 1
            treatment_scores_path = candidates[0]

        # Compare
        baseline_scores = _load_scores(baseline_scores_path)
        treatment_scores = _load_scores(treatment_scores_path)
        baseline_metrics = _extract_metrics(baseline_scores)
        treatment_metrics = _extract_metrics(treatment_scores)

        print(f"\n--- {spec_name} metrics ---")
        print(f"  Baseline:  {baseline_metrics['total']} scores, "
              f"{baseline_metrics['judge_pass_count']} ok, "
              f"{baseline_metrics['failure_mode_count']} modes")
        print(f"  Treatment: {treatment_metrics['total']} scores, "
              f"{treatment_metrics['judge_pass_count']} ok, "
              f"{treatment_metrics['failure_mode_count']} modes")

        results = _compare_runs(baseline_metrics, treatment_metrics, spec_name)
        all_results.extend(results)

    # Gate decision
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from regression_stats import gate_decision
    gatable = [r for r in all_results if r.get("effect") != "Info"]
    decision = gate_decision(gatable)

    _print_results(all_results, decision)

    # Write JSON report
    report_path = arts_dir / "regression_report.json"
    report = {
        "baseline": baseline_sha,
        "treatment": treatment_sha,
        "seeds": args.seeds,
        "results": all_results,
        "decision": decision,
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"Report: {report_path}")

    return 0 if decision["decision"] != "BLOCK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
