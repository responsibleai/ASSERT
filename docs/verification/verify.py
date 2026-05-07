#!/usr/bin/env python3
"""verify.py - spec-vs-implementation verification harness for adaptive-eval.

Reads docs/verification/matrix.json and dispatches each row to a verifier
function based on spec_id. Emits a summary report.

Usage:
    python docs/verification/verify.py <suite_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

try:
    from rich.console import Console
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_NOT_IMPL = "NOT_IMPLEMENTED"

VERIFIERS: dict[str, Callable[[Path], tuple[str, str]]] = {}


def verifier(spec_id: str):
    def decorator(fn):
        VERIFIERS[spec_id] = fn
        return fn
    return decorator


def first_run_dir(suite_dir: Path) -> Path | None:
    for child in sorted(suite_dir.iterdir()):
        if child.is_dir() and (child / "transcripts.jsonl").exists():
            return child
    return None


def read_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


@verifier("AE-PIPE-S1")
def verify_stage1_policy(suite_dir):
    p = suite_dir / "policy.json"
    if not p.exists():
        return VERDICT_FAIL, f"missing {p.name}"
    data = json.loads(p.read_text())
    behaviors = data.get("behaviors") or data.get("policy", {}).get("behaviors") or []
    if not behaviors:
        return VERDICT_FAIL, "policy.json has no behaviors[]"
    return VERDICT_PASS, f"policy.json with {len(behaviors)} behaviors at suite root"


@verifier("AE-PIPE-S2")
def verify_stage2_seeds(suite_dir):
    p = suite_dir / "seeds.jsonl"
    if not p.exists():
        return VERDICT_FAIL, f"missing {p.name}"
    rows = read_jsonl(p)
    if not rows:
        return VERDICT_FAIL, "seeds.jsonl empty"
    return VERDICT_PASS, f"seeds.jsonl with {len(rows)} seeds at suite root"


@verifier("AE-PIPE-S3")
def verify_stage3_rollout(suite_dir):
    run = first_run_dir(suite_dir)
    if not run:
        return VERDICT_FAIL, "no run dir with transcripts.jsonl"
    rows = read_jsonl(run / "transcripts.jsonl")
    if not rows:
        return VERDICT_FAIL, "transcripts.jsonl empty"
    return VERDICT_PASS, f"transcripts.jsonl with {len(rows)} rows in {run.name}/"


@verifier("AE-PIPE-S4")
def verify_stage4_judge(suite_dir):
    run = first_run_dir(suite_dir)
    if not run:
        return VERDICT_FAIL, "no run dir"
    p = run / "scores.jsonl"
    if not p.exists():
        return VERDICT_FAIL, f"missing {p.name}"
    rows = read_jsonl(p)
    if not rows:
        return VERDICT_FAIL, "scores.jsonl empty"
    return VERDICT_PASS, f"scores.jsonl with {len(rows)} verdicts in {run.name}/"


@verifier("AE-PIPE-S5")
def verify_stage5_metrics(suite_dir):
    run = first_run_dir(suite_dir)
    if not run:
        return VERDICT_FAIL, "no run dir"
    p = run / "metrics.json"
    if not p.exists():
        return VERDICT_FAIL, f"missing {p.name}"
    data = json.loads(p.read_text())
    return VERDICT_PASS, f"metrics.json in {run.name}/ with keys {list(data.keys())[:5]}"


@verifier("AE-PIPE-ART")
def verify_artifact_layout(suite_dir):
    suite_level = ["policy.json", "seeds.jsonl"]
    run_level = ["transcripts.jsonl", "scores.jsonl"]
    missing_suite = [f for f in suite_level if not (suite_dir / f).exists()]
    if missing_suite:
        return VERDICT_FAIL, f"suite-level missing: {missing_suite}"
    run = first_run_dir(suite_dir)
    if not run:
        return VERDICT_FAIL, "no run dir"
    missing_run = [f for f in run_level if not (run / f).exists()]
    if missing_run:
        return VERDICT_FAIL, f"run-level missing: {missing_run}"
    leaked = [f for f in run_level if (suite_dir / f).exists()]
    if leaked:
        return VERDICT_FAIL, f"run-level artifact leaked to suite root: {leaked}"
    return VERDICT_PASS, "suite/run artifact split correct"


@verifier("AE-CLI-RUN")
def verify_cli_run_layout(suite_dir):
    parts = suite_dir.parts
    if "results" not in parts or "artifacts" not in parts:
        return VERDICT_FAIL, "not under artifacts/results/"
    if not first_run_dir(suite_dir):
        return VERDICT_FAIL, "no run dir under suite"
    return VERDICT_PASS, f"layout artifacts/results/{suite_dir.name}/<run>/ confirmed"


def main():
    if len(sys.argv) != 2:
        print("usage: verify.py <suite_dir>", file=sys.stderr)
        return 2
    suite_dir = Path(sys.argv[1]).resolve()
    if not suite_dir.is_dir():
        print(f"not a directory: {suite_dir}", file=sys.stderr)
        return 2

    matrix_path = Path(__file__).resolve().parent / "matrix.json"
    matrix = json.loads(matrix_path.read_text())
    rows = matrix["rows"]

    results = []
    for row in rows:
        sid = row["spec_id"]
        fn = VERIFIERS.get(sid)
        if fn is None:
            results.append((sid, row["type"], VERDICT_NOT_IMPL, "no verifier registered"))
            continue
        try:
            verdict, detail = fn(suite_dir)
        except Exception as e:
            verdict, detail = VERDICT_FAIL, f"verifier raised: {type(e).__name__}: {e}"
        results.append((sid, row["type"], verdict, detail))

    counts = {VERDICT_PASS: 0, VERDICT_FAIL: 0, VERDICT_NOT_IMPL: 0}
    for _, _, v, _ in results:
        counts[v] = counts.get(v, 0) + 1

    if HAS_RICH:
        console = Console()
        console.print(f"\n[bold]Verification report[/bold]: {suite_dir}")
        console.print(f"matrix: schema {matrix['schema_version']}, {len(rows)} rows\n")
        table = Table(show_lines=False)
        table.add_column("spec_id", style="cyan", no_wrap=True)
        table.add_column("type")
        table.add_column("verdict")
        table.add_column("detail", overflow="fold")
        for sid, typ, v, d in results:
            color = {"PASS": "green", "FAIL": "red", "NOT_IMPLEMENTED": "yellow"}[v]
            table.add_row(sid, typ, f"[{color}]{v}[/{color}]", d)
        console.print(table)
        console.print(
            f"\n[bold]Summary:[/bold] "
            f"[green]{counts[VERDICT_PASS]} PASS[/green] / "
            f"[red]{counts[VERDICT_FAIL]} FAIL[/red] / "
            f"[yellow]{counts[VERDICT_NOT_IMPL]} NOT_IMPLEMENTED[/yellow] "
            f"out of {len(rows)} rows"
        )
    else:
        print(f"\nVerification report: {suite_dir}")
        print(f"matrix: schema {matrix['schema_version']}, {len(rows)} rows\n")
        for sid, typ, v, d in results:
            print(f"  {v:<16} {sid:<22} ({typ}) {d}")
        print(f"\nSummary: {counts[VERDICT_PASS]} PASS / {counts[VERDICT_FAIL]} FAIL / "
              f"{counts[VERDICT_NOT_IMPL]} NOT_IMPLEMENTED out of {len(rows)} rows")

    return 0


if __name__ == "__main__":
    sys.exit(main())
