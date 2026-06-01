"""Populate per-axis × per-variant rates into the README and storyline docs.

Reads scores.jsonl from each (axis, variant) artifact directory and replaces
TBD placeholders in the target markdown files with formatted percentages.

Usage:
    python scripts/populate_bank_manager_results.py \\
        --art-root artifacts/results \\
        --target examples/bank_manager/README.md \\
        --target C:/path/to/storyline.md \\
        --table-marker primary    # 'primary' or 'fallback'

The script is idempotent — it expects table cells that match the regex
``\\| _TBD_ / _TBD_ \\|`` and replaces them column-by-column for each axis row.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ART_ROOT = REPO_ROOT / "artifacts" / "results"

# (axis-suite-suffix, axis-name-as-it-appears-in-table) — the README rows
AXES = [
    ("bank-manager-tool-misuse", "tool-misuse"),
    ("bank-manager-prompt-injection", "prompt-injection"),
    ("bank-manager-info-leakage", "info-leakage"),
    ("bank-manager-quality-emergent", "quality-emergent"),
]

VARIANTS = [
    "variant-a-unguarded",
    "variant-b-donot",
    "variant-c-acs",
    "variant-d-combo",
]


def _pct(val: float | None) -> str:
    if val is None:
        return "_n/a_"
    return f"{val:.0%}" if val == 0 else f"{val:.1%}"


def aggregate(suite: str, variant: str, art_root: Path) -> tuple[float | None, float | None]:
    """Return (policy_violation_rate, overrefusal_rate) for one (axis, variant)."""
    scores = art_root / suite / variant / "scores.jsonl"
    if not scores.exists():
        return (None, None)
    totals = {"policy_violation": 0, "overrefusal": 0}
    hits = {"policy_violation": 0, "overrefusal": 0}
    for line in scores.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("judge_status") != "ok":
            continue
        dims = (row.get("verdict") or {}).get("dimensions") or {}
        for k in ("policy_violation", "overrefusal"):
            val = dims.get(k)
            if val is None:
                continue
            totals[k] += 1
            if bool(val):
                hits[k] += 1
    pv = (hits["policy_violation"] / totals["policy_violation"]) if totals["policy_violation"] else None
    or_ = (hits["overrefusal"] / totals["overrefusal"]) if totals["overrefusal"] else None
    return (pv, or_)


def build_rows(art_root: Path) -> dict[str, dict[str, tuple[float | None, float | None]]]:
    out: dict[str, dict[str, tuple[float | None, float | None]]] = {}
    for suite, axis_name in AXES:
        out[axis_name] = {}
        for variant in VARIANTS:
            out[axis_name][variant] = aggregate(suite, variant, art_root)
    return out


def render_row(axis_name: str, vals: dict[str, tuple[float | None, float | None]], suite_for_display: str) -> str:
    cells = [f"`{suite_for_display}`"]
    for variant in VARIANTS:
        pv, or_ = vals.get(variant, (None, None))
        cells.append(f"{_pct(pv)} / {_pct(or_)}")
    return "| " + " | ".join(cells) + " |"


def render_max_security_row(rows: dict[str, dict[str, tuple[float | None, float | None]]]) -> str:
    security_axes = ["tool-misuse", "prompt-injection", "info-leakage"]
    cells = ["**max security violation (rows 1-3)**"]
    for variant in VARIANTS:
        vals = [rows[axis][variant][0] for axis in security_axes if rows.get(axis, {}).get(variant, (None, None))[0] is not None]
        cell = f"**{_pct(max(vals))}**" if vals else "**_n/a_**"
        cells.append(cell)
    return "| " + " | ".join(cells) + " |"


def substitute_table(text: str, *, marker: str, rows: dict[str, dict[str, tuple[float | None, float | None]]]) -> str:
    """Replace the table between BEGIN/END markers."""
    begin = f"<!-- BEGIN {marker.upper()} TABLE"
    end = f"<!-- END {marker.upper()} TABLE -->"
    start_idx = text.find(begin)
    end_idx = text.find(end)
    if start_idx == -1 or end_idx == -1:
        print(f"[populate] markers '{begin}' / '{end}' not found — skipping substitution")
        return text
    # find end of BEGIN comment line
    begin_eol = text.find("\n", start_idx)
    if begin_eol == -1:
        return text
    new_table_lines = [
        "",
        "| Axis | A · unguarded viol / refusal | B · DO-NOT viol / refusal | C · ACS viol / refusal | D · combo viol / refusal |",
        "|---|---:|---:|---:|---:|",
    ]
    for suite, axis_name in AXES:
        new_table_lines.append(render_row(axis_name, rows[axis_name], suite))
    new_table_lines.append(render_max_security_row(rows))
    new_table_lines.append("")
    new_table = "\n".join(new_table_lines)
    return text[: begin_eol + 1] + new_table + "\n" + text[end_idx:]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--art-root", type=Path, default=DEFAULT_ART_ROOT)
    p.add_argument("--target", action="append", required=True,
                   help="Markdown file with TBD placeholders to populate. Repeat for multiple files.")
    p.add_argument("--table-marker", default="primary", choices=["primary", "fallback"],
                   help="Which table to replace inside the target file.")
    p.add_argument("--dry-run", action="store_true", help="Print the generated table; don't write.")
    args = p.parse_args()

    rows = build_rows(args.art_root)

    # Print the table for verification
    print(f"\n=== {args.table_marker} table from {args.art_root} ===")
    print("| Axis | A | B | C | D |")
    print("|---|---|---|---|---|")
    for suite, axis_name in AXES:
        print(render_row(axis_name, rows[axis_name], suite))
    print(render_max_security_row(rows))

    if args.dry_run:
        return 0

    for target_str in args.target:
        target_path = Path(target_str)
        if not target_path.exists():
            print(f"[populate] target {target_path} does not exist — skipping")
            continue
        original = target_path.read_text(encoding="utf-8")
        updated = substitute_table(original, marker=args.table_marker, rows=rows)
        if updated == original:
            print(f"[populate] {target_path}: no change")
        else:
            target_path.write_text(updated, encoding="utf-8")
            print(f"[populate] {target_path}: updated")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
