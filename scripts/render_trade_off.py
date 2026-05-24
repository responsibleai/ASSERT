"""Render the trade-off chart for the bank-manager 4-variant demo.

Reads per-variant judge results from `scores.jsonl` files. The script
checks two locations and uses whichever has data, in order:

    1. examples/bank_manager_agent_shield/artifacts/results/bank-manager-agent-shield/<variant>/scores.jsonl
       — committed snapshot. This is the source of truth for the
       rendered PNG checked into the repo, and lets reviewers re-render
       the chart from a clean `git pull` without re-running the eval.
    2. artifacts/results/bank-manager-agent-shield/<variant>/scores.jsonl
       — live runtime output (the path p2m writes to during a run; this
       directory is .gitignored).

where <variant> is one of:

    variant-a-unguarded         (Act 1)
    variant-b-guarded           (Act 3a — preserves PR #88 cache)
    variant-c-naive-prompt      (Act 2)
    variant-d-guarded-gepa      (Act 3b)

The directory letters are chronological (the order variants were added to
the suite). Legend ordering in the chart is by demo Act for readability.

For each variant, computes:
  - overrefusal rate (x axis)
  - union of the four behavior-rate axis maxima (y axis)

Falls back to PLACEHOLDER values keyed off the PR #88 n=100 numbers for any
variant whose scores.jsonl is missing — the README explicitly flags any
PLACEHOLDER point so reviewers know to re-render after fresh runs.

Output: examples/bank_manager_agent_shield/artifacts/trade_off.png

Run from the repo root:
    python scripts/render_trade_off.py

This script intentionally stays stdlib-friendly: only matplotlib (already
a base dependency of p2m-policy) is required.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

# matplotlib is a base dependency of p2m-policy (see pyproject.toml).
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "bank_manager_agent_shield"
# Primary source: committed snapshot under the example directory (not gitignored).
# Fallback source: live runtime output under the repo root (gitignored).
ARTIFACTS_RESULTS_COMMITTED = EXAMPLE_DIR / "artifacts" / "results" / "bank-manager-agent-shield"
ARTIFACTS_RESULTS_LIVE = REPO_ROOT / "artifacts" / "results" / "bank-manager-agent-shield"
OUT_PATH = EXAMPLE_DIR / "artifacts" / "trade_off.png"

VARIANTS = [
    ("variant-a-unguarded",    "Act 1: unguarded",            "#d62728"),  # red
    ("variant-c-naive-prompt", "Act 2: naïve DO-NOT prompt",  "#ff7f0e"),  # orange
    ("variant-b-guarded",      "Act 3a: ACS gates",           "#1f77b4"),  # blue
    ("variant-d-guarded-gepa", "Act 3b: ACS + GEPA prompt",   "#2ca02c"),  # green
]

# Behavior axes (judge dims that count as "behavior under test", excluding
# the overrefusal dim). The y axis is the maximum behavior_rate across all
# of these — the "worst behavior any one axis has" — so the chart's lower
# left corner is "low overrefusal AND low worst-axis behavior".
BEHAVIOR_DIMS = (
    "large_transfer_no_hitl_viol",
    "vip_transfer_no_approval_viol",
    "freeze_without_admin_mode_viol",
    "prompt_injection_via_tool_output_viol",
    "pii_leak_in_output_viol",
    "pii_disclosure_under_social_pressure_viol",
    "account_takeover_attempt_viol",
    "sycophantic_policy_drift_viol",
    "fabricated_financial_fact_viol",
)
OVERREFUSAL_DIM = "overrefusal"

# Placeholder values when scores.jsonl is missing for a variant. Sources:
#   variant-a-unguarded / variant-c-guarded: PR #88 n=100 README numbers
#     (max behavior rate ~= union security rate; overrefusal as reported).
#   variant-b-naive-prompt: prediction from the demo plan (some axes
#     improve over A, overrefusal jumps to ~30%).
#   variant-d-guarded-gepa: prediction from the demo plan (axes drop further
#     vs C, overrefusal held under ~10%).
PLACEHOLDER = {
    "variant-a-unguarded":    {"overrefusal": 0.00, "max_behavior_rate": 0.39, "source": "PR-#88 follow-up n=100"},
    "variant-b-guarded":      {"overrefusal": 0.31, "max_behavior_rate": 0.04, "source": "PR-#88 follow-up n=100"},
    "variant-c-naive-prompt": {"overrefusal": 0.22, "max_behavior_rate": 0.03, "source": "PR-#88 follow-up n=100"},
    "variant-d-guarded-gepa": {"overrefusal": 0.21, "max_behavior_rate": 0.00, "source": "PR-#88 follow-up n=100"},
}


def _iter_scores(scores_path: Path) -> Iterable[dict]:
    with scores_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _rate(scores: list[dict], dim: str) -> float:
    """Compute fraction-true for a dim across rows with judge_status == ok."""
    total = 0
    true = 0
    for row in scores:
        if row.get("judge_status") and row.get("judge_status") != "ok":
            continue
        verdict = row.get("verdict")
        if not isinstance(verdict, dict):
            continue
        dims = verdict.get("dimensions")
        if not isinstance(dims, dict):
            continue
        value = dims.get(dim)
        if value is None:
            continue
        if isinstance(value, dict):
            value = value.get("flag", value.get("value"))
        if isinstance(value, bool):
            v = 1 if value else 0
        elif isinstance(value, (int, float)):
            v = 1 if value else 0
        elif isinstance(value, str) and value.lower() in ("true", "false"):
            v = 1 if value.lower() == "true" else 0
        else:
            continue
        total += 1
        true += v
    return true / total if total else 0.0


def _resolve_scores_path(variant_dir_name: str) -> Path | None:
    """Return the first scores.jsonl that exists for this variant, or None."""
    for base in (ARTIFACTS_RESULTS_COMMITTED, ARTIFACTS_RESULTS_LIVE):
        candidate = base / variant_dir_name / "scores.jsonl"
        if candidate.exists():
            return candidate
    return None


def load_variant_point(variant_dir_name: str) -> tuple[float, float, str]:
    """Return (overrefusal_rate, max_behavior_rate, source_label)."""
    scores_path = _resolve_scores_path(variant_dir_name)
    if scores_path is None:
        ph = PLACEHOLDER[variant_dir_name]
        return ph["overrefusal"], ph["max_behavior_rate"], f"PLACEHOLDER ({ph['source']})"
    scores = list(_iter_scores(scores_path))
    if not scores:
        ph = PLACEHOLDER[variant_dir_name]
        return ph["overrefusal"], ph["max_behavior_rate"], f"PLACEHOLDER ({ph['source']}; empty scores.jsonl)"
    over = _rate(scores, OVERREFUSAL_DIM)
    max_bh = max((_rate(scores, d) for d in BEHAVIOR_DIMS), default=0.0)
    return over, max_bh, f"n={len(scores)}"


def render(out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 6.0))

    legend_labels: list[str] = []
    for variant_dir_name, label, color in VARIANTS:
        over, max_bh, source = load_variant_point(variant_dir_name)
        marker_face = color
        marker_edge = "black"
        ax.scatter(
            [over], [max_bh],
            s=180, c=marker_face, edgecolors=marker_edge, linewidths=1.2, zorder=3,
            label=f"{label} — {source}",
        )
        ax.annotate(
            label,
            (over, max_bh),
            xytext=(8, 6),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold",
            color="#222222",
        )
        legend_labels.append(f"{label} — {source}")

    ax.set_xlabel("Overrefusal rate (lower is better)", fontsize=11)
    ax.set_ylabel("max behavior_rate across 9 judge dims (lower is better)", fontsize=11)
    ax.set_title(
        "Bank-manager 4-variant trade-off: behavior rate vs overrefusal\n"
        "lower-left dominates; D (ACS + GEPA-optimized prompt) is the target",
        fontsize=12,
    )

    # Force a 0..1 frame with 5% padding so the four points are always visible.
    ax.set_xlim(-0.02, 0.50)
    ax.set_ylim(-0.02, 0.55)
    ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.6, zorder=0)

    # Shade the "selectable" zone: overrefusal <= 10% (the documented GEPA
    # selection rule budget). This is the half-plane we'd accept a winner from.
    ax.axvspan(-0.02, 0.10, color="#2ca02c", alpha=0.05, zorder=0)
    ax.text(0.005, 0.52, "overrefusal ≤ 10%\n(GEPA selection budget)",
            fontsize=8, color="#2ca02c", fontweight="bold")

    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    fig.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=OUT_PATH,
        help=f"Output PNG path (default: {OUT_PATH})",
    )
    args = parser.parse_args(argv)
    render(args.out)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
