# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Concurrency / throughput benchmark harness.

Drives the full ASSERT pipeline against a base benchmark config, with
``--test_set`` and ``--concurrency`` as the two knobs. Everything else
(target, dimensions, judge dimensions) stays identical across runs so
results are comparable.

Each invocation:

1. Materializes a per-run working dir under ``artifacts/benchmark/<run_id>/``.
2. Copies the base YAML and inlines the behavior spec into that config.
3. Applies overrides:

   - ``pipeline.systematize.behavior_category_count``  (auto-scaled from --test_set unless --behavior_categories)
   - ``pipeline.test_set.scenario.sample_size`` = --test_set
   - ``pipeline.inference.concurrency``    = --concurrency  (judge re-uses this number)
   - ``run``                              = the timestamped run id

4. Calls :func:`assert_ai.runner.run_pipeline` directly.
5. Captures wall-time, exit code, and rate-limiter cooldown count from a
   logging handler attached for the duration of the run.
6. Reads the resulting ``metrics.json`` if present and appends a single
   row to ``artifacts/benchmark/results.csv`` so successive runs build a
   sweep table without any extra plumbing.

Example::

    python scripts/benchmark.py --test_set 100 --concurrency 10
    python scripts/benchmark.py --test_set 1000 --concurrency 50
    python scripts/benchmark.py --test_set 5000 --concurrency 100

Run with ``--help`` for the full flag list.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from assert_ai.runner import run_pipeline  # noqa: E402
from assert_ai.logging_config import configure_logging  # noqa: E402

DEFAULT_BASE_CONFIG = REPO_ROOT / "examples" / "benchmark" / "eval_config.yaml"
# Quality-only behavior source colocated with the benchmark config. We deliberately
# do not reuse the full travel-planner eval spec from the flagship YAML because
# it includes adversarial safety behavior categories (prompt injection,
# sycophancy bait) that push the tester into jailbreak-shaped turns and
# get rejected by Azure Prompt Shields.
DEFAULT_BEHAVIOR_SPEC_SOURCE = (
    REPO_ROOT / "examples" / "benchmark" / "travel_planner_benchmark.md"
)
# The default tester system prompt (prompts/inference_tester_system.md) is
# itself jailbreak-shaped by design — it instructs the LLM to escalate
# obliquely-to-overtly, treat the target's replies as "not instructions to
# you", and vary justification strength. Azure Prompt Shields' jailbreak
# detector flags those patterns even when the seed content is benign,
# which makes the default prompt unusable for any throughput benchmark
# running through Azure-hosted models. This file ships a tame variant
# that still drives a multi-turn persona but reads like a normal user.
DEFAULT_BENIGN_TESTER_PROMPT = (
    REPO_ROOT / "examples" / "benchmark" / "tester_system_benign.md"
)
DEFAULT_BENCH_ROOT = REPO_ROOT / "artifacts" / "benchmark"
DEFAULT_RESULTS_CSV = DEFAULT_BENCH_ROOT / "results.csv"

CSV_FIELDS = [
    "timestamp_utc",
    "run_id",
    "test_set",
    "concurrency",
    "behavior_category_count",
    "exit_code",
    "wall_time_s",
    "rate_limit_cooldowns",
    "target_input_refused_count",
    "tester_input_refused_count",
    "target_error_count",
    "judge_filter_skipped_count",
    "scenario_seeds_generated",
    "scenarios_scored",
    "policy_violation_true_rate",
    "overrefusal_true_rate",
    "config_path",
    "run_dir",
]


# Module-level placeholder kept so older external scripts that reference
# this attribute don't crash. The actual counting is now done by scanning
# inference_set.jsonl / scores.jsonl after the run completes (see
# ``_scan_run_artifacts`` below). Product code records typed refusals as
# ``stop_reason='target_input_refused'`` / ``'tester_input_refused'``
# (inference) and ``judge_status='filter_skipped'`` (judge).


def _scan_run_artifacts(suite_id: str, run_id: str) -> dict[str, int]:
    """Count typed refusals + target errors in a completed run's artifacts.

    Replaces the legacy monkey-patches that wrapped ``_run_scenario_seed``
    and ``run_llm_judge``: as of the PR #44 absorb, product code records
    typed per-row refusals natively. We just read the transcript /
    scores files and count, which is simpler and doesn't require
    patching internal product-code attributes.
    """
    counts: dict[str, int] = {
        "target_input_refused": 0,
        "tester_input_refused": 0,
        "target_error": 0,
        "judge_filter_skipped": 0,
    }
    if not suite_id or not run_id:
        return counts
    run_dir = REPO_ROOT / "artifacts" / "results" / suite_id / run_id

    inference_set_path = run_dir / "inference_set.jsonl"
    if inference_set_path.exists():
        for line in inference_set_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            stop = row.get("stop_reason")
            if stop in counts:
                counts[stop] += 1

    scores_path = run_dir / "scores.jsonl"
    if scores_path.exists():
        for line in scores_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("judge_status") == "filter_skipped":
                counts["judge_filter_skipped"] += 1

    return counts


def _silence_asyncio_close_noise() -> None:
    """Drop the harmless ``Event loop is closed`` tracebacks that fire
    when ``httpx.AsyncClient`` connections are garbage-collected after
    the benchmark's event loop has already shut down.

    The pattern is well-known cleanup ordering noise reported many times
    against httpx and litellm: the connection pool's ``aclose()`` is
    scheduled on a loop that no longer exists, so it raises
    ``RuntimeError: Event loop is closed`` and asyncio prints
    ``Task exception was never retrieved`` plus the full traceback —
    once per orphaned connection. It happens *after* the run is
    logically finished, so the exit code, inference rows, scores, metrics
    and CSV row are already written.

    We attach a filter to the ``asyncio`` logger that only drops records
    matching the specific signature of this pattern (logger=asyncio,
    level>=ERROR, message mentions ``AsyncClient.aclose`` and
    ``Event loop is closed``). Any other asyncio error continues to
    surface unchanged.
    """

    class _CloseNoiseFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.name != "asyncio" or record.levelno < logging.ERROR:
                return True
            msg = record.getMessage()
            if (
                "Task exception was never retrieved" in msg
                and "AsyncClient.aclose" in msg
                and "Event loop is closed" in msg
            ):
                return False
            exc = record.exc_info
            if (
                exc
                and exc[0] is RuntimeError
                and exc[1] is not None
                and "Event loop is closed" in str(exc[1])
                and "AsyncClient.aclose" in msg
            ):
                return False
            return True

    logging.getLogger("asyncio").addFilter(_CloseNoiseFilter())


class _RateLimitCounter(logging.Handler):
    """Counts ``Rate limiter:`` log records emitted during a benchmark run.

    The model client logs ``Rate limiter: model X cooled down for Ys`` at
    WARNING when a 429 initiates or escalates a cooldown. Counting those
    records gives a cheap signal of how hard we hit provider limits at a
    given concurrency without instrumenting the model client itself.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        msg = record.getMessage()
        if "Rate limiter:" in msg and "cooled down" in msg:
            self.count += 1


def _scale_behavior_category_count(test_set: int) -> int:
    """Aim for ~10 test_set per behavior; clamp into a tractable range.

    Below the floor the systematize stage produces too few categories to
    cover the behavior; above the ceiling the taxonomy LLM has to invent
    too many distinct failure modes in a single call.
    """
    target = math.ceil(test_set / 10)
    return max(6, min(50, target))


def _override_config(
    base: dict[str, Any],
    *,
    test_set: int,
    concurrency: int,
    behavior_category_count: int,
    run_id: str,
) -> dict[str, Any]:
    cfg = dict(base)
    cfg["run"] = run_id

    pipeline = dict(cfg.get("pipeline") or {})

    taxonomy = dict(pipeline.get("taxonomy") or {})
    taxonomy["behavior_category_count"] = behavior_category_count
    pipeline["taxonomy"] = taxonomy

    test_set_block = dict(pipeline.get("test_set") or {})
    # Scenario-only by design: the inference/judge concurrency knob is what
    # this benchmark exercises, and scenario inferences are the heavier
    # multi-turn shape that makes that knob bite. Strip any prompt block
    # that may be in the base config.
    test_set_block.pop("prompt", None)
    scenario = dict(test_set_block.get("scenario") or {})
    scenario["sample_size"] = test_set
    test_set_block["scenario"] = scenario
    pipeline["test_set"] = test_set_block

    inference = dict(pipeline.get("inference") or {})
    inference["concurrency"] = concurrency
    pipeline["inference"] = inference

    cfg["pipeline"] = pipeline
    return cfg


def _prepare_run_dir(
    bench_root: Path,
    run_id: str,
    base_config: Path,
    behavior_spec_source: Path,
    overrides: dict[str, Any],
) -> Path:
    run_dir = bench_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if not behavior_spec_source.exists():
        raise FileNotFoundError(
            f"Behavior spec markdown not found at {behavior_spec_source}. "
            "Pass --behavior-spec to point at the right .md file."
        )
    behavior_spec_text = behavior_spec_source.read_text(encoding="utf-8").strip()
    cfg = dict(overrides)
    behavior = dict(cfg.get("behavior") or {})
    behavior["name"] = behavior.get("name") or behavior_spec_source.stem
    behavior["description"] = behavior_spec_text
    cfg["behavior"] = behavior

    target_config = run_dir / "eval_config.yaml"
    target_config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    return target_config


def _load_metrics_summary(suite_id: str, run_id: str) -> dict[str, Any]:
    """Pull headline outcome numbers from this run's score artifacts.

    Returns empty values rather than failing -- a non-zero exit code from
    the pipeline is the authoritative success signal.

    All four outcome fields are sourced from the run's ``scores.jsonl``
    via :func:`assert_ai.results.load_run_summary`. ``scenario_seeds_generated``
    is the count of scenario rows that reached the judge stage in this
    run; ``scenarios_scored`` is the subset that judge successfully
    scored (i.e. ``judge_status == "ok"``). For partial-test_set runs that
    skip cache finalization, this keeps the CSV row internally
    consistent with the other per-run columns (cooldowns, target_errors,
    wall_time_s) instead of reading the suite-root compatibility
    ``test_set.jsonl`` which only refreshes on full-success runs.

    The runner's ``metrics.json`` is token-usage telemetry only and is
    not consulted here.
    """
    # Imported lazily so this script keeps working in environments where
    # the package isn't fully installed (e.g. running via ``python
    # scripts/benchmark.py`` directly from the repo root).
    from assert_ai.results import load_run_summary

    run_dir = REPO_ROOT / "artifacts" / "results" / suite_id / run_id

    summary: dict[str, Any] = {
        "scenario_seeds_generated": "",
        "scenarios_scored": "",
        "policy_violation_true_rate": "",
        "overrefusal_true_rate": "",
    }

    if run_dir.exists():
        try:
            run_summary = load_run_summary(run_dir)
        except Exception:  # noqa: BLE001 - never let a summary failure abort the CSV append
            run_summary = None
        scenario_metrics = (run_summary or {}).get("scenario_metrics")
        if isinstance(scenario_metrics, dict):
            total = scenario_metrics.get("total")
            if isinstance(total, int):
                summary["scenario_seeds_generated"] = total
            scored = scenario_metrics.get("scored_total")
            if isinstance(scored, int):
                summary["scenarios_scored"] = scored
            pv = scenario_metrics.get("policy_violation_rate")
            if isinstance(pv, (int, float)):
                summary["policy_violation_true_rate"] = pv
            ov = scenario_metrics.get("overrefusal_rate")
            if isinstance(ov, (int, float)):
                summary["overrefusal_true_rate"] = ov

    return summary


def _append_results_row(csv_path: Path, row: dict[str, Any]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def _build_run_id(test_set: int, concurrency: int, custom: str | None) -> str:
    if custom:
        return custom
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bench-s{test_set}-c{concurrency}-{stamp}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full ASSERT pipeline at a chosen (test_set, concurrency) point.",
    )
    parser.add_argument(
        "--test_set",
        type=int,
        required=True,
        help="Total scenario test_set to generate (sets pipeline.test_set.scenario.sample_size). 1..100000.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        required=True,
        help="Inference concurrency (judge re-uses this value). Must be >= 1.",
    )
    parser.add_argument(
        "--behavior_categories",
        type=int,
        default=None,
        help=(
            "Override pipeline.systematize.behavior_category_count. Default scales with --test_set "
            "(roughly 10 test_set per behavior, clamped to [6, 50])."
        ),
    )
    parser.add_argument(
        "--base-config",
        type=Path,
        default=DEFAULT_BASE_CONFIG,
        help=f"Base YAML to template from (default: {DEFAULT_BASE_CONFIG.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--behavior-spec",
        "--behavior-md",
        dest="behavior_spec",
        type=Path,
        default=DEFAULT_BEHAVIOR_SPEC_SOURCE,
        help=(
            "Source markdown to inline into behavior.description in the generated "
            f"config (default: {DEFAULT_BEHAVIOR_SPEC_SOURCE.relative_to(REPO_ROOT)})."
        ),
    )
    parser.add_argument(
        "--bench-root",
        type=Path,
        default=DEFAULT_BENCH_ROOT,
        help=f"Where per-run working dirs live (default: {DEFAULT_BENCH_ROOT.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--results-csv",
        type=Path,
        default=DEFAULT_RESULTS_CSV,
        help=f"Where to append the summary row (default: {DEFAULT_RESULTS_CSV.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Override the auto-generated run id (e.g. for re-running with a fixed name).",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip appending the summary row to results.csv.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging from assert_ai for the duration of the run.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Also write all log output to this file (DEBUG level).",
    )
    parser.add_argument(
        "--tester-prompt",
        type=Path,
        default=DEFAULT_BENIGN_TESTER_PROMPT,
        help=(
            "Path to a markdown file used to override the inference tester "
            "system prompt for the duration of this benchmark run. Defaults "
            f"to {DEFAULT_BENIGN_TESTER_PROMPT.relative_to(REPO_ROOT)} (a "
            "tame variant that doesn't trip Azure Prompt Shields). Pass "
            "--no-tester-override to use the product default instead."
        ),
    )
    parser.add_argument(
        "--no-tester-override",
        action="store_true",
        help=(
            "Skip the benign tester-prompt swap and use the product's "
            "default inference tester system prompt. Likely to fail on "
            "Azure-hosted models with default content filters."
        ),
    )
    parser.add_argument(
        "--no-tolerate-content-filter",
        action="store_true",
        help=(
            "DEPRECATED: this flag is now a no-op. Per-row tolerance for "
            "Azure Prompt Shields and other input refusals is handled "
            "natively by product code (target_input_refused / "
            "tester_input_refused / judge filter_skipped). Kept for "
            "backward compatibility."
        ),
    )
    parser.add_argument(
        "--force-stage",
        action="append",
        default=[],
        help="Forwarded to run_pipeline. Repeat to force multiple stages.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.test_set < 1 or args.test_set > 100_000:
        print(f"--test_set must be in [1, 100000], got {args.test_set}", file=sys.stderr)
        return 2
    if args.concurrency < 1:
        print(f"--concurrency must be >= 1, got {args.concurrency}", file=sys.stderr)
        return 2

    if not args.base_config.exists():
        print(f"Base config not found: {args.base_config}", file=sys.stderr)
        return 2

    base_cfg = yaml.safe_load(args.base_config.read_text(encoding="utf-8"))
    if not isinstance(base_cfg, dict):
        print(f"Base config did not parse to a dict: {args.base_config}", file=sys.stderr)
        return 2

    behavior_category_count = (
        args.behavior_categories if args.behavior_categories is not None else _scale_behavior_category_count(args.test_set)
    )
    if behavior_category_count < 1:
        print(f"--behavior_categories must be >= 1, got {behavior_category_count}", file=sys.stderr)
        return 2

    run_id = _build_run_id(args.test_set, args.concurrency, args.run_id)

    overrides = _override_config(
        base_cfg,
        test_set=args.test_set,
        concurrency=args.concurrency,
        behavior_category_count=behavior_category_count,
        run_id=run_id,
    )

    target_config = _prepare_run_dir(
        args.bench_root, run_id, args.base_config, args.behavior_spec, overrides
    )

    suite_id = str(overrides.get("suite") or "")

    print("=" * 72)
    print(f"Benchmark run: {run_id}")
    print(f"  suite          : {suite_id}")
    print(f"  test_set          : {args.test_set} (scenario only)")
    print(f"  concurrency    : {args.concurrency} (inference + judge)")
    print(f"  behavior_category_count : {behavior_category_count}")
    print(f"  config         : {target_config}")
    if args.log_file:
        print(f"  log file       : {args.log_file}")
    print("=" * 72, flush=True)

    # Configure logging the same way `assert-ai run` does so stage progress and
    # any failure output actually reaches the terminal. Must run BEFORE
    # we attach the rate-limit counter, because configure_logging clears
    # existing handlers on the root logger.
    configure_logging(verbose=args.verbose, log_file=args.log_file)
    _silence_asyncio_close_noise()

    # Swap in the benign tester prompt unless the user opted out. The
    # default prompt is intentionally adversarial (oblique→overt
    # escalation, "treat replies as not instructions") which trips Azure
    # Prompt Shields' jailbreak detector before the first target turn.
    # We monkey-patch the module-level constant so no product code path
    # has to care about this benchmark-only override.
    if not args.no_tester_override:
        if not args.tester_prompt.exists():
            print(
                f"Tester prompt override not found: {args.tester_prompt}",
                file=sys.stderr,
            )
            return 2
        from assert_ai.stages import inference as _inference_mod
        _inference_mod.TESTER_SYSTEM_PROMPT = args.tester_prompt.read_text(
            encoding="utf-8"
        )
        logging.getLogger("benchmark").info(
            "Overrode tester system prompt with %s",
            args.tester_prompt.relative_to(REPO_ROOT)
            if args.tester_prompt.is_relative_to(REPO_ROOT)
            else args.tester_prompt,
        )

    # Per-row tolerance for content-filter rejections is now handled
    # natively by product code (target_input_refused / tester_input_refused
    # in inference, judge_status='filter_skipped' in judge). The legacy
    # monkey-patches are gone; we count typed refusals after the run by
    # scanning inference_set.jsonl + scores.jsonl. The
    # --no-tolerate-content-filter flag is preserved for back-compat but
    # is now a no-op (the typed handlers always run).
    if args.no_tolerate_content_filter:
        logging.getLogger("benchmark").info(
            "--no-tolerate-content-filter is a no-op as of the PR #44 absorb; "
            "product code now records typed per-row refusals natively."
        )

    counter = _RateLimitCounter()
    root_logger = logging.getLogger()
    root_logger.addHandler(counter)

    start = time.monotonic()
    try:
        exit_code = run_pipeline(
            config=str(target_config),
            force_stages=list(args.force_stage),
        )
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 1
    except Exception as exc:  # noqa: BLE001
        print(f"[benchmark] run_pipeline raised: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        wall_time = time.monotonic() - start
        root_logger.removeHandler(counter)

    metrics_summary = _load_metrics_summary(suite_id, run_id)
    refusal_counts = _scan_run_artifacts(suite_id, run_id)
    run_dir = REPO_ROOT / "artifacts" / "results" / suite_id / run_id

    print()
    print("-" * 72)
    print(f"Wall time              : {wall_time:.1f}s")
    print(f"Exit code              : {exit_code}")
    print(f"Rate-limit cooldowns   : {counter.count}")
    if refusal_counts["target_input_refused"]:
        print(f"Target input refused   : {refusal_counts['target_input_refused']}")
    if refusal_counts["tester_input_refused"]:
        print(f"Tester input refused  : {refusal_counts['tester_input_refused']}")
    if refusal_counts["target_error"]:
        print(f"Target errors          : {refusal_counts['target_error']}")
    if refusal_counts["judge_filter_skipped"]:
        print(f"Judge filter skipped   : {refusal_counts['judge_filter_skipped']}")
    if metrics_summary.get("scenarios_scored") not in ("", None):
        print(f"Scenarios scored       : {metrics_summary['scenarios_scored']}")
    print(f"Run dir                : {run_dir}")
    print("-" * 72)

    if not args.no_csv:
        row = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id,
            "test_set": args.test_set,
            "concurrency": args.concurrency,
            "behavior_category_count": behavior_category_count,
            "exit_code": exit_code,
            "wall_time_s": round(wall_time, 2),
            "rate_limit_cooldowns": counter.count,
            "target_input_refused_count": refusal_counts["target_input_refused"],
            "tester_input_refused_count": refusal_counts["tester_input_refused"],
            "target_error_count": refusal_counts["target_error"],
            "judge_filter_skipped_count": refusal_counts["judge_filter_skipped"],
            "config_path": str(target_config),
            "run_dir": str(run_dir),
            **metrics_summary,
        }
        _append_results_row(args.results_csv, row)
        print(f"Appended summary to    : {args.results_csv}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
