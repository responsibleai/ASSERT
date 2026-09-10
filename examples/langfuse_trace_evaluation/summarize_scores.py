#!/usr/bin/env python3
"""Summarise an ASSERT scores.jsonl into a per-session verdict table.

Development aid for the demo. Prints which dimensions fired on each imported
Langfuse session, plus the judge's own justification text for one session.

Usage:
    python summarize_scores.py <run_dir> [--detail SESSION_ID]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--detail", default=None)
    args = ap.parse_args()

    rows = [
        json.loads(line)
        for line in (args.run_dir / "scores.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    keys: list[str] = []
    for row in rows:
        for k in row.get("score_keys") or []:
            if k not in keys:
                keys.append(k)

    header = f"{'session':<20}" + "".join(f"{k[:18]:<20}" for k in keys)
    print(header)
    print("-" * len(header))
    for row in sorted(rows, key=lambda r: r["test_case_id"]):
        verdict = row.get("verdict") or {}
        dimensions = verdict.get("dimensions") or verdict
        cells = ""
        for k in keys:
            v = dimensions.get(k)
            if isinstance(v, dict):
                v = v.get("value")
            cells += f"{('FAIL' if v is True else 'pass' if v is False else '-'):<20}"
        print(f"{row['test_case_id']:<20}{cells}")

    if args.detail:
        row = next((r for r in rows if r["test_case_id"] == args.detail), None)
        if row is None:
            raise SystemExit(f"no row with test_case_id={args.detail}")
        print("\n" + json.dumps(row, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
