"""Outcome stability analysis across multiple runs.

Provides two distinct analyses:
1. Repeatability — same tester/target/judge, different inferences of same test_set.
   Answers: "How much does inference stochasticity affect outcomes?"
2. Cross-tester variation — different testers on same test_set.
   Answers: "How much does tester choice affect outcomes?"

These are different questions and must not be conflated.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _compute_group_stats(
    rows: list[dict[str, Any]],
    *,
    seed_key: str,
    run_key: str,
    outcome_key: str,
) -> dict[str, Any]:
    """Shared computation for seed-level outcome stats within a group of runs."""
    by_seed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_run: dict[str, list[bool]] = defaultdict(list)

    for row in rows:
        sid = str(row.get(seed_key, ""))
        run = str(row.get(run_key, ""))
        outcome = bool(row.get(outcome_key, False))
        by_seed[sid].append({"run": run, "outcome": outcome})
        by_run[run].append(outcome)

    n_seeds = len(by_seed)
    if n_seeds == 0:
        return {"n_seeds": 0, "n_runs": 0}

    n_runs = len(by_run)

    # Only count test_set present in all runs for agreement stats
    full_coverage_seeds = {
        sid: entries for sid, entries in by_seed.items()
        if len(entries) == n_runs
    }
    n_full = len(full_coverage_seeds)

    n_always_violate = 0
    n_always_clear = 0
    n_mixed = 0

    for sid, entries in full_coverage_seeds.items():
        outcomes = [e["outcome"] for e in entries]
        if all(outcomes):
            n_always_violate += 1
        elif not any(outcomes):
            n_always_clear += 1
        else:
            n_mixed += 1

    run_rates = {}
    for run, outcomes in sorted(by_run.items()):
        run_rates[run] = {
            "rate": sum(outcomes) / len(outcomes) if outcomes else 0.0,
            "count": len(outcomes),
            "positive": sum(outcomes),
        }

    run_rate_values = [r["rate"] for r in run_rates.values()]
    run_rate_range = max(run_rate_values) - min(run_rate_values) if len(run_rate_values) > 1 else 0.0

    return {
        "n_seeds": n_seeds,
        "n_seeds_full_coverage": n_full,
        "n_runs": n_runs,
        "n_always_violate": n_always_violate,
        "n_always_clear": n_always_clear,
        "n_mixed": n_mixed,
        "agreement_rate": round((n_always_violate + n_always_clear) / n_full, 4) if n_full else None,
        "run_rates": run_rates,
        "run_rate_range": round(run_rate_range, 4),
    }


def compute_tester_variation(
    scored_rows: list[dict[str, Any]],
    *,
    seed_key: str = "test_case_id",
    tester_key: str = "tester_model",
    run_key: str = "run",
    outcome_key: str = "policy_violation",
) -> dict[str, Any]:
    """Compute how much tester choice affects outcomes on the same test_set.

    Groups rows by tester model, computes per-tester violation rates,
    and reports the spread.
    """
    by_tester: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored_rows:
        tester = str(row.get(tester_key) or row.get(run_key, ""))
        by_tester[tester].append(row)

    tester_stats = {}
    for tester, rows in sorted(by_tester.items()):
        outcomes = [bool(r.get(outcome_key, False)) for r in rows]
        n = len(outcomes)
        pos = sum(outcomes)
        tester_stats[tester] = {
            "rate": pos / n if n else 0.0,
            "count": n,
            "positive": pos,
        }

    rates = [s["rate"] for s in tester_stats.values()]
    return {
        "n_testers": len(tester_stats),
        "testers": tester_stats,
        "rate_range": round(max(rates) - min(rates), 4) if len(rates) > 1 else 0.0,
        "rate_min": round(min(rates), 4) if rates else None,
        "rate_max": round(max(rates), 4) if rates else None,
    }


def compute_repeatability(
    scored_rows: list[dict[str, Any]],
    *,
    seed_key: str = "test_case_id",
    run_key: str = "run",
    outcome_key: str = "policy_violation",
) -> dict[str, Any]:
    """Compute seed-level repeatability across runs of the SAME configuration.

    Only pass rows from runs that share the same tester, target, and judge.
    """
    return _compute_group_stats(
        scored_rows, seed_key=seed_key, run_key=run_key, outcome_key=outcome_key,
    )


def format_tester_variation(result: dict[str, Any]) -> str:
    """Format cross-tester variation as a human-readable summary."""
    if result.get("n_testers", 0) == 0:
        return "No tester data."

    lines = []
    lines.append(f"Cross-tester variation ({result['n_testers']} testers, main runs only)")
    lines.append(f"  Violation rate range: {result['rate_min']:.0%} – {result['rate_max']:.0%} "
                 f"(spread: {result['rate_range']:.0%})")
    for tester, stats in sorted(result["testers"].items(), key=lambda x: -x[1]["rate"]):
        lines.append(f"    {tester}: {stats['positive']}/{stats['count']} ({stats['rate']:.0%})")
    return "\n".join(lines)


def format_repeatability(result: dict[str, Any]) -> str:
    """Format repeatability results as a human-readable summary."""
    if result.get("n_seeds", 0) == 0:
        return "No repeatability data."
    if result.get("n_runs", 0) < 2:
        return "Need ≥2 runs of same configuration for repeatability."

    n_full = result["n_seeds_full_coverage"]
    lines = []
    lines.append(f"Repeatability ({result['n_runs']} runs, "
                 f"{n_full} shared test_set)")

    if result.get("agreement_rate") is not None:
        lines.append(f"  Unanimous outcome: {result['agreement_rate']:.0%} "
                     f"({result['n_always_violate'] + result['n_always_clear']}/{n_full} "
                     f"test_set always agree)")
        lines.append(f"    Always violate: {result['n_always_violate']}, "
                     f"always clear: {result['n_always_clear']}, "
                     f"mixed: {result['n_mixed']}")

    for run, stats in sorted(result["run_rates"].items()):
        # If run has more test_set than shared, note it
        if stats["count"] > n_full:
            lines.append(f"    {run}: {stats['positive']}/{stats['count']} on all test_set, "
                         f"shared-seed subset used for agreement")
        else:
            lines.append(f"    {run}: {stats['positive']}/{stats['count']} ({stats['rate']:.0%})")

    return "\n".join(lines)
