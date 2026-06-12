# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

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
a base dependency of assert-ai) is required.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# matplotlib is a base dependency of assert-ai (see pyproject.toml).
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
    # Demo-path emphasis. Variants on the demo path render with solid
    # markers; variants flagged as experiments render faded with a
    # "(experiment)" suffix in the legend. Default True preserves
    # existing behavior for suites that don't distinguish.
    is_demo_path: bool = True


@dataclass(frozen=True)
class Suite:
    name: str
    example_dir: Path
    behavior_dims: tuple[str, ...]
    overrefusal_dim: str
    variants: tuple[Variant, ...]
    title: str
    # Optional (from_index, to_index) into variants to draw an arrow
    # marking the demo path. None disables the arrow.
    demo_path_arrow: tuple[int, int] | None = None


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
        # Demo path: variant A (baseline) -> variant C (ACS gates).
        # Variants B (naive-prompt) and D (guarded-with-shield-gepa) are
        # documented experiments whose predictions did not land at n=200;
        # they remain on the chart for transparency but render faded.
        # See examples/incident_triage_agent/README.md Appendix B.
        Variant("baseline-weak-prompt",       "A: baseline (demo)",            "#d62728", 0.808, 0.556, "PR-#43 n=200 scenario", is_demo_path=True),
        Variant("naive-prompt",               "B: naive DO-NOT prompt",        "#ff7f0e", 0.05, 0.85, "demo-plan prediction", is_demo_path=False),
        Variant("guarded-with-shield",        "C: ACS gates (demo)",           "#1f77b4", 0.835, 0.51, "PR-#43 n=200 scenario", is_demo_path=True),
        Variant("guarded-with-shield-gepa",   "D: ACS + GEPA placeholder",     "#2ca02c", 0.08, 0.45, "demo-plan prediction", is_demo_path=False),
    ),
    title=(
        "Incident-triage trade-off: behavior rate vs overrefusal (n=200+200)\n"
        "demo path: A (baseline) → C (ACS gates); B & D shown faded as experiments"
    ),
    # Draw an arrow from A (index 0) to C (index 2) to mark the demo path.
    demo_path_arrow=(0, 2),
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
    # Prefer the committed snapshot under the example dir; fall back to the
    # gitignored runtime path at the repo root; fall back to PLACEHOLDER.
    candidate_paths = (
        suite.example_dir / "artifacts" / "results" / suite.name / variant.artifact_dir / "scores.jsonl",
        REPO_ROOT / "artifacts" / "results" / suite.name / variant.artifact_dir / "scores.jsonl",
    )
    scores_path = next((p for p in candidate_paths if p.exists()), None)
    if scores_path is None:
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
        if variant.is_demo_path:
            ax.scatter(
                [over], [max_bh],
                s=200, c=variant.color, edgecolors="black", linewidths=1.5,
                alpha=1.0, zorder=4,
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
        else:
            # Experiment: faded marker, smaller size, gray edge, parenthetical label.
            ax.scatter(
                [over], [max_bh],
                s=110, c=variant.color, edgecolors="#888888", linewidths=0.8,
                alpha=0.45, zorder=2,
                label=f"{variant.label} (experiment) — {source}",
            )
            ax.annotate(
                f"{variant.label} (experiment)",
                (over, max_bh),
                xytext=(8, 6),
                textcoords="offset points",
                fontsize=9,
                fontweight="normal",
                color="#666666",
                alpha=0.85,
            )

    # Optional demo-path arrow.
    if suite.demo_path_arrow is not None and rendered_points:
        i_from, i_to = suite.demo_path_arrow
        if 0 <= i_from < len(rendered_points) and 0 <= i_to < len(rendered_points):
            x0, y0 = rendered_points[i_from]
            x1, y1 = rendered_points[i_to]
            ax.annotate(
                "",
                xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(
                    arrowstyle="->",
                    color="#444444",
                    lw=1.6,
                    alpha=0.7,
                    shrinkA=12, shrinkB=12,
                ),
                zorder=3,
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
