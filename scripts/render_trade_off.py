"""Render the trade-off chart for a 4-variant ASSERT demo.

Supports multiple demo suites via the ``--suite`` flag (default:
``bank-manager-agent-shield``; ``incident-triage-agent-v1`` is the new
4-axis demo). For each variant, computes:

  - overrefusal rate (x axis)
  - max behavior rate across all "behavior under test" judge dims
    (y axis)

Falls back to PLACEHOLDER values keyed off the documented case-study
numbers when ``scores.jsonl`` is missing for a variant — the README
explicitly flags PLACEHOLDER points so reviewers know to re-render
after fresh runs.

Output:

  --suite bank-manager-agent-shield   -> examples/bank_manager_agent_shield/artifacts/trade_off.png
  --suite incident-triage-agent-v1    -> examples/incident_triage_agent/artifacts/trade_off.png

Run from the repo root:

  python scripts/render_trade_off.py
  python scripts/render_trade_off.py --suite incident-triage-agent-v1

This script intentionally stays stdlib-friendly: only matplotlib (already
a base dependency of p2m-policy) is required.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# matplotlib is a base dependency of p2m-policy (see pyproject.toml).
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Variant:
    artifact_dir: str
    label: str
    color: str
    placeholder_overrefusal: float
    placeholder_max_behavior: float
    placeholder_source: str


@dataclass(frozen=True)
class Suite:
    name: str
    example_dir: Path
    behavior_dims: tuple[str, ...]
    overrefusal_dim: str
    variants: tuple[Variant, ...]
    title: str


# ---------------------------------------------------------------------------
# Suite catalogue.
# ---------------------------------------------------------------------------

# Variants for the bank-manager demo (PR #88 + 4-axis extension).
# Directory letters are chronological; legend ordering is by demo Act.
BANK_MANAGER_SUITE = Suite(
    name="bank-manager-agent-shield",
    example_dir=REPO_ROOT / "examples" / "bank_manager_agent_shield",
    behavior_dims=(
        "large_transfer_no_hitl_viol",
        "vip_transfer_no_approval_viol",
        "freeze_without_admin_mode_viol",
        "prompt_injection_via_tool_output_viol",
        "pii_leak_in_output_viol",
        "pii_disclosure_under_social_pressure_viol",
        "account_takeover_attempt_viol",
        "sycophantic_policy_drift_viol",
    ),
    overrefusal_dim="overrefusal",
    variants=(
        Variant("variant-a-unguarded",    "Act 1: unguarded",            "#d62728", 0.01, 0.45, "PR-#88 n=100"),
        Variant("variant-c-naive-prompt", "Act 2: naïve DO-NOT prompt",  "#ff7f0e", 0.30, 0.35, "demo-plan prediction"),
        Variant("variant-b-guarded",      "Act 3a: ACS gates",           "#1f77b4", 0.34, 0.03, "PR-#88 n=100"),
        Variant("variant-d-guarded-gepa", "Act 3b: ACS + GEPA prompt",   "#2ca02c", 0.08, 0.02, "demo-plan prediction"),
    ),
    title=(
        "Bank-manager 4-variant trade-off: behavior rate vs overrefusal\n"
        "lower-left dominates; D (ACS + GEPA-optimized prompt) is the target"
    ),
)

# Variants for the incident-triage demo (PR #43 + 4-axis extension).
# The artifact_dir names match the `run:` values in the eval configs;
# unlike bank-manager, incident-triage uses descriptive run names rather
# than chronological letters. Placeholder numbers track the PR #43 scenario
# rail (canonical headline) reported at n=200 -- see
# examples/incident_triage_agent/README.md Appendix A §4.1.
INCIDENT_TRIAGE_SUITE = Suite(
    name="incident-triage-agent-v1",
    example_dir=REPO_ROOT / "examples" / "incident_triage_agent",
    behavior_dims=(
        "policy_violation",
        "ordering_violation",
        "pager_violation",
        "channel_violation",
        "pii_leak",
        "pii_leak_under_pressure_viol",
        "xpia_relay",
        "escalation_violation",
        "alert_id_drift",
        "severity_drift_under_pressure_viol",
        "wrong_severity",
        "fabrication",
    ),
    overrefusal_dim="overrefusal",
    variants=(
        # Act 1: PR #43 scenario rail (canonical). Overrefusal 80.8%,
        # max behavior driven by `fabrication` (55.6%) at n=200.
        Variant("baseline-weak-prompt",       "Act 1: baseline weak prompt",   "#d62728", 0.808, 0.556, "PR-#43 n=200 scenario"),
        # Act 2: anti-safety naive prompt. Prediction: overrefusal drops
        # (the DO-NOT block trains the agent to comply), behavior rates
        # spike across PII, severity-drift, XPIA, fabrication.
        Variant("naive-prompt",               "Act 2: naïve DO-NOT prompt",    "#ff7f0e", 0.05, 0.85, "demo-plan prediction"),
        # Act 3a: PR #43 scenario rail (canonical). Overrefusal 83.5%
        # (within CI of Act 1), max behavior 51.0% (fabrication residual).
        Variant("guarded-with-shield",        "Act 3a: ACS gates",             "#1f77b4", 0.835, 0.51, "PR-#43 n=200 scenario"),
        # Act 3b: ACS + GEPA-optimized prompt. Prediction: overrefusal
        # drops well under the 10% budget because the GEPA prompt encodes
        # the discipline rules without relying on refusal as the only
        # safety lever; max behavior holds at or below Act 3a.
        Variant("guarded-with-shield-gepa",   "Act 3b: ACS + GEPA prompt",     "#2ca02c", 0.08, 0.45, "demo-plan prediction"),
    ),
    title=(
        "Incident-triage 4-variant trade-off: behavior rate vs overrefusal\n"
        "lower-left dominates; Act 3b (ACS + GEPA-optimized prompt) is the target"
    ),
)


SUITES: dict[str, Suite] = {
    BANK_MANAGER_SUITE.name: BANK_MANAGER_SUITE,
    INCIDENT_TRIAGE_SUITE.name: INCIDENT_TRIAGE_SUITE,
}


# ---------------------------------------------------------------------------
# Score reading.
# ---------------------------------------------------------------------------

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


def load_variant_point(suite: Suite, variant: Variant) -> tuple[float, float, str]:
    """Return (overrefusal_rate, max_behavior_rate, source_label)."""
    scores_path = (
        REPO_ROOT / "artifacts" / "results" / suite.name / variant.artifact_dir / "scores.jsonl"
    )
    if not scores_path.exists():
        return (
            variant.placeholder_overrefusal,
            variant.placeholder_max_behavior,
            f"PLACEHOLDER ({variant.placeholder_source})",
        )
    scores = list(_iter_scores(scores_path))
    if not scores:
        return (
            variant.placeholder_overrefusal,
            variant.placeholder_max_behavior,
            f"PLACEHOLDER ({variant.placeholder_source}; empty scores.jsonl)",
        )
    over = _rate(scores, suite.overrefusal_dim)
    max_bh = max((_rate(scores, d) for d in suite.behavior_dims), default=0.0)
    return over, max_bh, f"scores.jsonl (n={len(scores)})"


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------

def _frame_bounds(points: list[tuple[float, float]]) -> tuple[tuple[float, float], tuple[float, float]]:
    if not points:
        return (-0.02, 1.0), (-0.02, 1.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_hi = max(0.5, max(xs) + 0.08)
    y_hi = max(0.55, max(ys) + 0.08)
    return (-0.02, min(1.02, x_hi)), (-0.02, min(1.02, y_hi))


def render(suite: Suite, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 6.0))

    rendered_points: list[tuple[float, float]] = []
    for variant in suite.variants:
        over, max_bh, source = load_variant_point(suite, variant)
        rendered_points.append((over, max_bh))
        ax.scatter(
            [over], [max_bh],
            s=180, c=variant.color, edgecolors="black", linewidths=1.2, zorder=3,
            label=f"{variant.label} — {source}",
        )
        ax.annotate(
            variant.label,
            (over, max_bh),
            xytext=(8, 6),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold",
            color="#222222",
        )

    ax.set_xlabel("Overrefusal rate (lower is better)", fontsize=11)
    ax.set_ylabel(
        f"max behavior_rate across {len(suite.behavior_dims)} judge dims (lower is better)",
        fontsize=11,
    )
    ax.set_title(suite.title, fontsize=12)

    (x_lo, x_hi), (y_lo, y_hi) = _frame_bounds(rendered_points)
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.6, zorder=0)

    # Shade the "selectable" zone: overrefusal <= 10% (the documented GEPA
    # selection rule budget). This is the half-plane we'd accept a winner from.
    ax.axvspan(x_lo, 0.10, color="#2ca02c", alpha=0.05, zorder=0)
    ax.text(
        max(x_lo + 0.005, 0.005),
        y_hi - 0.03,
        "overrefusal ≤ 10%\n(GEPA selection budget)",
        fontsize=8,
        color="#2ca02c",
        fontweight="bold",
        verticalalignment="top",
    )

    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    fig.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=sorted(SUITES.keys()),
        default=BANK_MANAGER_SUITE.name,
        help="Which demo suite to render (default: %(default)s).",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help=(
            "Output PNG path. Defaults to "
            "<suite example dir>/artifacts/trade_off.png."
        ),
    )
    args = parser.parse_args(argv)
    suite = SUITES[args.suite]
    out_path = args.out or (suite.example_dir / "artifacts" / "trade_off.png")
    render(suite, out_path)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
