"""Smoke-cell runner — engineering CI helper.

Wraps `p2m run` with a light budget override and isolated suite/run ids so
many cells can execute in parallel against the same set of example configs
without trampling each other's artifacts. Always exits 0; the classifier
(`scripts/smoke_classify.py`) decides the final cell verdict.

Engineer-owned flesh-out points are flagged with `# TODO(eng)` comments.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Light-budget overrides (CLI-injected via `p2m run --set k=v`). Keep small —
# slow cells (crewai, neurosan) need to fit in 8-min runner timeout.
LIGHT_BUDGET = {
    "pipeline.policy.behavior_count": 3,
    "pipeline.seeds.prompt.sample_size": 3,
    "pipeline.seeds.scenario.sample_size": 3,
    "pipeline.rollout.max_turns": 4,
    "pipeline.rollout.concurrency": 2,
    "pipeline.judge.model.name": "azure/gpt-5.4-mini",
}


@dataclass
class CellResult:
    label: str
    config: str
    suite: str
    run: str
    exit_code: int
    elapsed_s: float
    log_path: str
    artifacts_root: str
    n_seeds: int | None = None
    n_scores: int | None = None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label", required=True, help="Short cell label (e.g. phoenix-openai)")
    p.add_argument("--config", required=True, help="Path to eval_*.yaml config")
    p.add_argument("--suite", required=True, help="Suite id override (must be unique per cell+run)")
    p.add_argument("--run", required=True, help="Run id override (must be unique per cell+attempt)")
    p.add_argument("--budget", choices=["light", "full"], default="light",
                   help="Budget profile. `light` applies LIGHT_BUDGET overrides.")
    p.add_argument("--artifacts-dir", type=Path, required=True,
                   help="Per-cell artifacts dir; cell.log + result.json land here.")
    p.add_argument("--result-json", type=Path, required=True,
                   help="Where to write the structured CellResult.")
    p.add_argument("--p2m-results-root", type=Path, default=Path("artifacts/results"),
                   help="Root where p2m writes its native artifacts.")
    return p.parse_args(argv)


def _build_overrides(budget: str) -> list[str]:
    if budget == "full":
        return []
    out: list[str] = []
    for k, v in LIGHT_BUDGET.items():
        out.extend(["--set", f"{k}={v}"])
    return out


def _count_jsonl_lines(path: Path) -> int | None:
    if not path.exists():
        return None
    return sum(1 for _ in path.read_text(encoding="utf-8").splitlines() if _.strip())


def _collect_artifacts_state(p2m_root: Path, suite: str, run: str) -> tuple[int | None, int | None]:
    """Return (n_seeds, n_scores) by inspecting the canonical artifact paths.

    Both count files because `pass` here is "did we get scoring at all", not
    "did the science look good". Science quality is the science.yml job.
    """
    run_dir = p2m_root / suite / run
    return (
        _count_jsonl_lines(run_dir / "seeds.jsonl"),
        _count_jsonl_lines(run_dir / "scores.jsonl"),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    log_path = args.artifacts_dir / "cell.log"
    cmd = [
        sys.executable, "-m", "p2m.cli", "run",
        "--config", args.config,
        "--suite", args.suite,
        "--run", args.run,
        *_build_overrides(args.budget),
    ]

    started = time.monotonic()
    with log_path.open("wb") as log:
        log.write(f"$ {' '.join(cmd)}\n".encode())
        log.flush()
        proc = subprocess.run(  # noqa: S603 — args are constructed locally
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    elapsed = round(time.monotonic() - started, 1)

    n_seeds, n_scores = _collect_artifacts_state(args.p2m_results_root, args.suite, args.run)

    result = CellResult(
        label=args.label,
        config=args.config,
        suite=args.suite,
        run=args.run,
        exit_code=proc.returncode,
        elapsed_s=elapsed,
        log_path=str(log_path),
        artifacts_root=str(args.p2m_results_root / args.suite / args.run),
        n_seeds=n_seeds,
        n_scores=n_scores,
    )
    args.result_json.parent.mkdir(parents=True, exist_ok=True)
    args.result_json.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")

    print(f"[smoke_cell] {args.label}: exit={proc.returncode} elapsed={elapsed}s "
          f"seeds={n_seeds} scores={n_scores}")
    # ALWAYS exit 0 — smoke_classify.py decides the final verdict.
    return 0


if __name__ == "__main__":
    sys.exit(main())
