"""Aggregate ABCD violation rates across 3 SUT models for bank_manager_agent_shield.

Reads all 12 scores.jsonl files under artifacts/results/bank-manager-agent-shield/
and prints one ABCD table per model, with rows = judge dimensions and
columns = variants (A/B/C/D), cells = violation rate (% of judged cases that
were flagged violated on that dimension).

Usage:
    python examples/bank_manager_agent_shield/aggregate_abcd.py
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS_ROOT = Path("artifacts/results/bank-manager-agent-shield")

# Model slugs as they appear in run-dir names (dots replaced with dashes by the
# sweep script). Edit this list when you add new models.
MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-5-4-mini",
    "gpt-4-1-mini",
    "gpt-4-1",
    "gpt-5",
    "deepseek-v4-flash",
]

VARIANTS = [
    ("A", "variant-a-unguarded"),
    ("B", "variant-b-guarded"),
    ("C", "variant-c-naive-prompt"),
    ("D", "variant-d-guarded-gepa"),
]

# Dimension display order. `policy_violation` is the rolled-up flag; the other
# 10 are per-behavior judge axes (5 core + overrefusal + 4 newer dims in C/D).
DIMENSIONS = [
    "policy_violation",
    "large_transfer_no_hitl_viol",
    "vip_transfer_no_approval_viol",
    "freeze_without_admin_mode_viol",
    "prompt_injection_via_tool_output_viol",
    "pii_leak_in_output_viol",
    "account_takeover_attempt_viol",
    "pii_disclosure_under_social_pressure_viol",
    "sycophantic_policy_drift_viol",
    "fabricated_financial_fact_viol",
    "overrefusal",
]


def load_scores(model: str, variant_slug: str) -> list[dict]:
    """Load scored rows from disk for one (model, variant) combo."""
    path = RESULTS_ROOT / f"{variant_slug}-{model}" / "scores.jsonl"
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def violation_rates(rows: list[dict]) -> tuple[dict[str, float], int, int]:
    """Compute per-dimension violation rate across rows where the judge produced a verdict."""
    rates: dict[str, float] = {}
    total = len(rows)
    judged = 0
    counts: dict[str, int] = {d: 0 for d in DIMENSIONS}
    for row in rows:
        verdict = row.get("verdict") or {}
        dims = verdict.get("dimensions") if isinstance(verdict, dict) else None
        if not isinstance(dims, dict):
            continue
        judged += 1
        for dim in DIMENSIONS:
            if dims.get(dim) is True:
                counts[dim] += 1
    for dim in DIMENSIONS:
        rates[dim] = (counts[dim] / judged * 100.0) if judged else float("nan")
    return rates, judged, total


def render_table(model: str) -> None:
    print(f"\n=== {model} — ABCD violation rates (% of judged cases flagged) ===")
    # Header
    col_w = 9
    label_w = 44
    header = "Dimension".ljust(label_w) + "".join(v.center(col_w) for v, _ in VARIANTS)
    print(header)
    print("-" * len(header))

    # Per-variant rates
    per_variant: dict[str, tuple[dict[str, float], int, int]] = {}
    for code, slug in VARIANTS:
        per_variant[code] = violation_rates(load_scores(model, slug))

    # Row per dimension
    for dim in DIMENSIONS:
        row = dim.ljust(label_w)
        for code, _ in VARIANTS:
            rates, judged, _ = per_variant[code]
            v = rates[dim]
            cell = "—".center(col_w) if judged == 0 else f"{v:5.1f}%".center(col_w)
            row += cell
        print(row)

    # Footer with N judged / N total
    print("-" * len(header))
    footer = "judged / total".ljust(label_w)
    for code, _ in VARIANTS:
        _, judged, total = per_variant[code]
        footer += f"{judged}/{total}".center(col_w)
    print(footer)


def main() -> None:
    for model in MODELS:
        render_table(model)
    print()


if __name__ == "__main__":
    main()
