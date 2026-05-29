"""Aggregate per-dimension violation rates for the bank-manager build-demo
variants. Pass --runs to point at either variant A only or both A+C."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


DIMS = [
    "policy_violation",
    "overrefusal",
    "prompt_injection_via_memo_to_account_viol",
    "fabricated_transfer_id_viol",
    "approval_token_replay_viol",
]


def _load(path: pathlib.Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _report(label: str, rows: list[dict]) -> dict[str, float]:
    n = len(rows)
    print(f"\n=== {label} (n={n}) ===")
    rates: dict[str, float] = {}
    for d in DIMS:
        fires = sum(1 for r in rows if r.get("verdict", {}).get("dimensions", {}).get(d) is True)
        pct = 100.0 * fires / n if n else 0.0
        rates[d] = pct
        print(f"  {d:60s}  {fires:3d}/{n}  {pct:5.1f}%")
    # also per-stratum failure-rate breakdown if metadata is present
    by_axis: dict[str, list[dict]] = {}
    for r in rows:
        tcm = r.get("test_case_metadata") or r.get("metadata") or {}
        axis = (tcm.get("failure_mode_axis")
                or r.get("stratification_value")
                or r.get("stratum")
                or "(unknown)")
        by_axis.setdefault(str(axis), []).append(r)
    if by_axis and len(by_axis) > 1:
        print(f"  --- by stratum ---")
        for ax, rs in sorted(by_axis.items()):
            print(f"  stratum: {ax}  (n={len(rs)})")
            for d in DIMS:
                fires = sum(1 for r in rs if r.get("verdict", {}).get("dimensions", {}).get(d) is True)
                p = 100.0 * fires / len(rs) if rs else 0
                print(f"      {d:56s}  {fires}/{len(rs)}  {p:5.1f}%")
    return rates


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="artifacts/results/bank-manager-build-demo")
    ap.add_argument("--variant-a", default="variant-a-build-demo-unguarded")
    ap.add_argument("--variant-c", default="variant-c-build-demo-guarded")
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    a_path = root / args.variant_a / "scores.jsonl"
    c_path = root / args.variant_c / "scores.jsonl"

    a_rows = _load(a_path) if a_path.exists() else []
    c_rows = _load(c_path) if c_path.exists() else []

    rates_a = _report(f"Variant A (unguarded)  {a_path}", a_rows) if a_rows else None
    rates_c = _report(f"Variant C (guarded)    {c_path}", c_rows) if c_rows else None

    if rates_a and rates_c:
        print("\n=== Side-by-side ===")
        header = f"  {'dimension':60s}  {'A %':>7s}  {'C %':>7s}  {'delta (A-C)':>12s}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for d in DIMS:
            a = rates_a[d]
            c = rates_c[d]
            print(f"  {d:60s}  {a:6.1f}%  {c:6.1f}%  {a - c:>+10.1f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
