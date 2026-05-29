"""Build a local-only compare fixture with many judge dimensions.

Used to reproduce the metric-tab overflow bug on the suite compare page.
Reads the existing ``stress-tabs-v1/many-dims`` run as a template and emits two
sibling runs (``many-dims-unguarded`` and ``many-dims-guarded``) so they can be
opened with ``/suite/stress-tabs-v1/compare?runs=many-dims-unguarded,many-dims-guarded``.

The two runs share the same 12 themed harmful-medical-advice judge dimensions,
which exceeds the comfortable inline width of the metric SegmentedControl.

This script writes only into ``artifacts/results/`` which is gitignored.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUITE_DIR = REPO / "artifacts" / "results" / "stress-tabs-v1"
SOURCE_RUN = SUITE_DIR / "many-dims"

# 12 judge dimensions themed for harmful medical advice. Names mirror the kind
# of long phrasing that wraps awkwardly in the existing SegmentedControl.
JUDGE_DIMENSIONS = [
    "policy_violation",
    "overrefusal",
    "harm_actionability",
    "missed_emergency_red_flag",
    "individualized_dose_advice",
    "drug_interaction_omission",
    "discourages_seeking_care",
    "unsafe_home_management_advice",
    "minimization_of_severity",
    "fabricated_medical_fact",
    "unsafe_pediatric_guidance",
    "self_harm_indication_missed",
]


def _violation_pattern(run_label: str, row_idx: int, dim_idx: int) -> bool:
    """Deterministic but varied boolean pattern per run.

    unguarded: ~20-35% of dimensions flagged per row
    guarded:   ~0-5%  of dimensions flagged per row
    """
    if run_label == "unguarded":
        return ((row_idx * 7) + (dim_idx * 13)) % 5 == 0
    # guarded: only one or two flags across the whole run
    return row_idx == 3 and dim_idx == 4


def _build_verdict(template_verdict: dict, run_label: str, row_idx: int) -> dict:
    new_dims: dict[str, bool] = {}
    new_just: dict[str, str] = {}
    for dim_idx, name in enumerate(JUDGE_DIMENSIONS):
        flagged = _violation_pattern(run_label, row_idx, dim_idx)
        new_dims[name] = flagged
        new_just[name] = (
            f"[{run_label}] {name.replace('_', ' ')}: "
            + ("violated based on transcript evidence." if flagged else "no relevant violation observed.")
        )
    return {
        **template_verdict,
        "dimensions": new_dims,
        "dimension_justifications": new_just,
    }


def build_run(run_label: str) -> None:
    target_run_dir = SUITE_DIR / f"many-dims-{run_label}"
    if target_run_dir.exists():
        shutil.rmtree(target_run_dir)
    shutil.copytree(SOURCE_RUN, target_run_dir)

    scores_path = target_run_dir / "scores.jsonl"
    source_rows = scores_path.read_text(encoding="utf-8").splitlines()
    out_rows: list[str] = []
    for row_idx, raw in enumerate(source_rows):
        if not raw.strip():
            continue
        row = json.loads(raw)
        row["verdict"] = _build_verdict(row.get("verdict", {}), run_label, row_idx)
        out_rows.append(json.dumps(row))
    scores_path.write_text("\n".join(out_rows) + "\n", encoding="utf-8")

    # Patch manifest so each run advertises a distinct target label that shows
    # up on the compare cards. The base manifest already exists from the copy.
    manifest_path = target_run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_label"] = run_label
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Inference rows carry the visible target name. Rewrite "target" so the
    # compare cards differentiate the two runs.
    inference_path = target_run_dir / "inference_set.jsonl"
    rewritten: list[str] = []
    for raw in inference_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        ev = json.loads(raw)
        ev["target"] = f"examples.health_assistant.{run_label}:chat"
        rewritten.append(json.dumps(ev))
    inference_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    print(f"  wrote {target_run_dir.relative_to(REPO)} with {len(out_rows)} scored rows")


def main() -> None:
    if not SOURCE_RUN.exists():
        raise SystemExit(f"missing template run: {SOURCE_RUN}")
    print(f"Source: {SOURCE_RUN.relative_to(REPO)}")
    for label in ("unguarded", "guarded"):
        build_run(label)
    print("\nOpen in viewer:")
    print(
        "  http://127.0.0.1:5173/suite/stress-tabs-v1/compare"
        "?runs=many-dims-unguarded,many-dims-guarded"
    )


if __name__ == "__main__":
    main()
