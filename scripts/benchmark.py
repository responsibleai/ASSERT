"""Concurrency / throughput benchmark harness.

Drives the full p2m pipeline against a base benchmark config, with
``--seeds`` and ``--concurrency`` as the two knobs. Everything else
(target, factors, judge dimensions) stays identical across runs so
results are comparable.

Each invocation:

1. Materializes a per-run working dir under ``artifacts/benchmark/<run_id>/``.
2. Copies the base YAML and the concept markdown into that dir.
3. Applies overrides:

   - ``pipeline.policy.behavior_count``  (auto-scaled from --seeds unless --behaviors)
   - ``pipeline.seeds.scenario.sample_size`` = --seeds
   - ``pipeline.rollout.concurrency``    = --concurrency  (judge re-uses this number)
   - ``run``                              = the timestamped run id

4. Calls :func:`p2m.runner.run_pipeline` directly.
5. Captures wall-time, exit code, and rate-limiter cooldown count from a
   logging handler attached for the duration of the run.
6. Reads the resulting ``metrics.json`` if present and appends a single
   row to ``artifacts/benchmark/results.csv`` so successive runs build a
   sweep table without any extra plumbing.

Example::

    python scripts/benchmark.py --seeds 100 --concurrency 10
    python scripts/benchmark.py --seeds 1000 --concurrency 50
    python scripts/benchmark.py --seeds 5000 --concurrency 100

Run with ``--help`` for the full flag list.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from p2m.runner import run_pipeline  # noqa: E402
from p2m.logging_config import configure_logging  # noqa: E402
from p2m.core.model_client import LLMInputError  # noqa: E402
from p2m.core.transcript import (  # noqa: E402
    AddMessageEdit,
    Message,
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)

DEFAULT_BASE_CONFIG = REPO_ROOT / "examples" / "benchmark" / "eval_config.yaml"
# Quality-only concept colocated with the benchmark config. We deliberately
# don't reuse examples/travel_planner_langgraph/travel_planner_eval.md
# because that spec includes adversarial safety behaviors (prompt injection,
# sycophancy bait) that push the auditor into jailbreak-shaped turns and
# get rejected by Azure Prompt Shields.
DEFAULT_CONCEPT_SOURCE = (
    REPO_ROOT / "examples" / "benchmark" / "travel_planner_benchmark.md"
)
# The default auditor system prompt (prompts/rollout_auditor_system.md) is
# itself jailbreak-shaped by design — it instructs the LLM to escalate
# obliquely-to-overtly, treat the target's replies as "not instructions to
# you", and vary justification strength. Azure Prompt Shields' jailbreak
# detector flags those patterns even when the seed content is benign,
# which makes the default prompt unusable for any throughput benchmark
# running through Azure-hosted models. This file ships a tame variant
# that still drives a multi-turn persona but reads like a normal user.
DEFAULT_BENIGN_AUDITOR_PROMPT = (
    REPO_ROOT / "examples" / "benchmark" / "auditor_system_benign.md"
)
DEFAULT_BENCH_ROOT = REPO_ROOT / "artifacts" / "benchmark"
DEFAULT_RESULTS_CSV = DEFAULT_BENCH_ROOT / "results.csv"

CSV_FIELDS = [
    "timestamp_utc",
    "run_id",
    "seeds",
    "concurrency",
    "behavior_count",
    "exit_code",
    "wall_time_s",
    "rate_limit_cooldowns",
    "content_filter_blocked",
    "target_error_tolerated",
    "scenario_seeds_generated",
    "scenarios_scored",
    "policy_violation_true_rate",
    "overrefusal_true_rate",
    "config_path",
    "run_dir",
]


# Module-level counter for content-filter-blocked seeds. The lenient wrapper
# (see _install_content_filter_tolerance below) increments this per blocked
# seed so the benchmark summary can report it.
_CONTENT_FILTER_BLOCKS: dict[str, int] = {"count": 0}

# Module-level counter for seeds whose target raised (and which the rollout
# stage would otherwise treat as fatal). The lenient wrapper rewrites the
# stop_reason so the worker post-processing doesn't synthesize a stage-killer
# RuntimeError, then increments this counter. High counts at high concurrency
# are a real signal that the target can't keep up — surface it in the CSV.
_TARGET_ERROR_TOLERATED: dict[str, int] = {"count": 0}


def _is_content_policy_violation(exc: BaseException | None) -> bool:
    """Return True when ``exc`` (or its chain) is a provider content-filter error.

    Azure surfaces filter rejections via at least two paths:

    1. ``litellm.ContentPolicyViolationError`` (subclass of BadRequestError),
       used for the standard ResponsibleAIPolicyViolation path including
       Prompt Shields jailbreak detection.
    2. A plain ``litellm.BadRequestError`` whose message contains "flagged
       as potentially violating our usage policy" — Azure's reasoning-model
       moderation pipeline returns this without the structured subclass.

    We accept both. Class checks use names (no hard import of litellm) so
    the benchmark stays usable when the underlying provider isn't Azure.
    """
    filter_phrases = (
        "content management policy",
        "flagged as potentially violating our usage policy",
        "jailbreak",
        "responsibleaipolicyviolation",
    )
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        cls_name = type(cur).__name__
        if cls_name == "ContentPolicyViolationError":
            return True
        if cls_name == "BadRequestError":
            msg = str(cur).lower()
            if any(phrase in msg for phrase in filter_phrases):
                return True
        msg = str(cur).lower()
        if any(phrase in msg for phrase in filter_phrases):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _install_content_filter_tolerance() -> None:
    """Wrap ``_run_scenario_seed`` so content-filter rejections don't fail the stage.

    Azure Prompt Shields will deterministically reject ~5–10% of synthetic
    benign personas as suspected jailbreak attempts (the role-play framing
    pattern-matches its training set even when the actual content is
    innocuous). For a throughput benchmark we want those seeds to be
    counted and skipped, not to abort the whole run.

    We intercept :func:`p2m.stages.rollout._run_scenario_seed` and, when
    its underlying LLM call raises ``LLMInputError`` caused by litellm's
    ``ContentPolicyViolationError``, return a synthetic ``Transcript``
    with ``stop_reason="content_filter_blocked"`` and a single system
    note explaining the block. The rollout worker then treats the seed
    as a normal completion and the stage continues.

    Idempotent: calling this twice is a no-op after the first install.
    """
    from p2m.stages import rollout as _rollout_mod

    if getattr(_rollout_mod, "_BENCHMARK_LENIENT_INSTALLED", False):
        return

    _orig = _rollout_mod._run_scenario_seed

    async def _lenient_run_scenario_seed(
        *,
        seed: dict[str, Any],
        target: Any,
        evaluation: Any,
        max_tokens: int,
        config_path: Path | None,
    ) -> Transcript:
        try:
            transcript = await _orig(
                seed=seed,
                target=target,
                evaluation=evaluation,
                max_tokens=max_tokens,
                config_path=config_path,
            )
        except LLMInputError as exc:
            if not _is_content_policy_violation(exc):
                raise
            seed_id = str(seed.get("seed_id") or "?")
            auditor_model = ""
            if evaluation and evaluation.auditor and evaluation.auditor.model:
                auditor_model = str(evaluation.auditor.model.name or "")
            target_label = ""
            if target and target.model:
                target_label = str(target.model.name or "")
            elif target:
                target_label = str(
                    target.connector or target.callable or target.endpoint or ""
                )
            transcript = Transcript(
                metadata=TranscriptMetadata(
                    kind="scenario",
                    seed_id=seed_id,
                    concept=str(seed.get("concept") or ""),
                    target=target_label,
                    auditor_model=auditor_model,
                    factors=(seed.get("factors") or {}) if isinstance(seed.get("factors"), dict) else None,
                ),
                events=[
                    TranscriptEvent(
                        view=["system", "combined"],
                        actor="system",
                        edit=AddMessageEdit(
                            message=Message(
                                role="system",
                                content=(
                                    "[CONTENT FILTER BLOCKED] Auditor turn "
                                    "rejected by provider content filter "
                                    "(jailbreak / policy). Seed skipped for "
                                    "benchmark continuity.\n"
                                    f"Underlying error: {exc}"
                                ),
                            )
                        ),
                    )
                ],
                stop_reason="content_filter_blocked",
            )
            _CONTENT_FILTER_BLOCKS["count"] += 1
            logging.getLogger("benchmark").warning(
                "[rollout] seed %s blocked by content filter — recorded and skipped (total blocks: %d)",
                seed_id,
                _CONTENT_FILTER_BLOCKS["count"],
            )
            return transcript

        # The original ran to completion. The rollout stage's worker
        # post-processing treats `stop_reason == "target_error"` as a
        # stage-fatal condition (it synthesises a RuntimeError that
        # short-circuits the whole stage at line ~1075). For benchmarking
        # we want to count those seeds and continue, so we rename the
        # stop reason to a sentinel the post-processing won't match.
        if transcript.stop_reason == "target_error":
            seed_id = str(seed.get("seed_id") or "?")
            transcript.stop_reason = "target_error_tolerated"
            _TARGET_ERROR_TOLERATED["count"] += 1
            logging.getLogger("benchmark").warning(
                "[rollout] seed %s ended with target_error — recorded and tolerated (total: %d)",
                seed_id,
                _TARGET_ERROR_TOLERATED["count"],
            )
        return transcript

    _rollout_mod._run_scenario_seed = _lenient_run_scenario_seed
    _rollout_mod._BENCHMARK_LENIENT_INSTALLED = True


def _install_judge_skip_blocked() -> None:
    """Make the judge stage no-op on content-filter-blocked transcripts.

    A blocked transcript has no real conversation to score. Sending its
    (essentially empty) XML to the judge LLM either yields garbage
    verdicts or — if the judge model also has Prompt Shields on — gets
    re-rejected and kills the stage.

    We intercept :func:`p2m.stages.judge.run_llm_judge` and short-circuit
    when the supplied transcript's ``stop_reason`` is
    ``"content_filter_blocked"``, returning a synthetic verdict that
    flags the row as filter-skipped without hitting the judge model.
    """
    from p2m.stages import judge as _judge_mod

    if getattr(_judge_mod, "_BENCHMARK_LENIENT_INSTALLED", False):
        return

    _orig_judge = _judge_mod.run_llm_judge

    async def _lenient_run_llm_judge(*args: Any, **kwargs: Any) -> dict[str, Any]:
        transcript = kwargs.get("transcript")
        if transcript is not None and getattr(transcript, "stop_reason", None) == "content_filter_blocked":
            return {
                "judge_status": "filter_skipped",
                "judge_error": "content_filter_blocked: rollout was blocked by content filter; judge skipped",
                "verdict": {},
                "multi_judge": None,
            }
        try:
            return await _orig_judge(*args, **kwargs)
        except LLMInputError as exc:
            # The judge LLM itself was rejected by Prompt Shields (the
            # transcript content tripped the content filter when sent to
            # the judge model). Treat this exactly like a pre-blocked
            # rollout: synthesize a filter-skipped verdict and keep the
            # benchmark moving.
            if not _is_content_policy_violation(exc):
                raise
            seed_id = ""
            if transcript is not None and getattr(transcript, "metadata", None) is not None:
                seed_id = str(getattr(transcript.metadata, "seed_id", "") or "")
            _CONTENT_FILTER_BLOCKS["count"] += 1
            logging.getLogger("benchmark").warning(
                "[judge] seed %s blocked by content filter during judging — recorded and skipped (total blocks: %d)",
                seed_id or "?",
                _CONTENT_FILTER_BLOCKS["count"],
            )
            return {
                "judge_status": "filter_skipped",
                "judge_error": (
                    "content_filter_blocked_during_judge: the judge LLM "
                    "rejected the transcript on content-policy grounds; "
                    f"underlying error: {exc}"
                ),
                "verdict": {},
                "multi_judge": None,
            }

    _judge_mod.run_llm_judge = _lenient_run_llm_judge
    _judge_mod._BENCHMARK_LENIENT_INSTALLED = True


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
    logically finished, so the exit code, transcripts, scores, metrics
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


def _scale_behavior_count(seeds: int) -> int:
    """Aim for ~10 seeds per behavior; clamp into a tractable range.

    Below the floor the policy stage produces too few categories to
    cover the concept; above the ceiling the policy LLM has to invent
    too many distinct failure modes in a single call.
    """
    target = math.ceil(seeds / 10)
    return max(6, min(50, target))


def _override_config(
    base: dict[str, Any],
    *,
    seeds: int,
    concurrency: int,
    behavior_count: int,
    run_id: str,
) -> dict[str, Any]:
    cfg = dict(base)
    cfg["run"] = run_id

    pipeline = dict(cfg.get("pipeline") or {})

    policy = dict(pipeline.get("policy") or {})
    policy["behavior_count"] = behavior_count
    pipeline["policy"] = policy

    seeds_block = dict(pipeline.get("seeds") or {})
    # Scenario-only by design: the rollout/judge concurrency knob is what
    # this benchmark exercises, and scenario rollouts are the heavier
    # multi-turn shape that makes that knob bite. Strip any prompt block
    # that may be in the base config.
    seeds_block.pop("prompt", None)
    scenario = dict(seeds_block.get("scenario") or {})
    scenario["sample_size"] = seeds
    seeds_block["scenario"] = scenario
    pipeline["seeds"] = seeds_block

    rollout = dict(pipeline.get("rollout") or {})
    rollout["concurrency"] = concurrency
    pipeline["rollout"] = rollout

    cfg["pipeline"] = pipeline
    return cfg


def _prepare_run_dir(
    bench_root: Path,
    run_id: str,
    base_config: Path,
    concept_source: Path,
    overrides: dict[str, Any],
) -> Path:
    run_dir = bench_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    target_config = run_dir / "eval_config.yaml"
    target_config.write_text(yaml.safe_dump(overrides, sort_keys=False), encoding="utf-8")

    # The config loader looks for <concept_name>.md next to the config
    # file. Copy the source markdown alongside so the resolution succeeds
    # without leaking benchmark-specific paths into the loader.
    if not concept_source.exists():
        raise FileNotFoundError(
            f"Concept markdown not found at {concept_source}. "
            "Pass --concept-md to point at the right .md file."
        )
    concept_name = overrides.get("concept", {}).get("name") or concept_source.stem
    shutil.copy2(concept_source, run_dir / f"{concept_name}.md")

    return target_config


def _load_metrics_summary(suite_id: str, run_id: str) -> dict[str, Any]:
    """Pull headline numbers from the run's metrics.json if it exists.

    Returns empty values rather than failing — a non-zero exit code from
    the pipeline is the authoritative success signal.
    """
    metrics_path = (
        REPO_ROOT
        / "artifacts"
        / "results"
        / suite_id
        / run_id
        / "metrics.json"
    )
    summary: dict[str, Any] = {
        "scenario_seeds_generated": "",
        "scenarios_scored": "",
        "policy_violation_true_rate": "",
        "overrefusal_true_rate": "",
    }
    if not metrics_path.exists():
        return summary
    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return summary

    scenario_metrics = data.get("scenario_metrics") or data.get("scenario") or {}
    if isinstance(scenario_metrics, dict):
        summary["scenarios_scored"] = scenario_metrics.get("count", "")
        dims = scenario_metrics.get("dimensions") or scenario_metrics.get("by_dimension") or {}
        if isinstance(dims, dict):
            pv = dims.get("policy_violation") or {}
            if isinstance(pv, dict):
                summary["policy_violation_true_rate"] = pv.get("true_rate", "")
            ov = dims.get("overrefusal") or {}
            if isinstance(ov, dict):
                summary["overrefusal_true_rate"] = ov.get("true_rate", "")

    seed_summary = data.get("seed_metrics") or data.get("seeds") or {}
    if isinstance(seed_summary, dict):
        summary["scenario_seeds_generated"] = seed_summary.get(
            "scenario_count", seed_summary.get("count", "")
        )

    return summary


def _append_results_row(csv_path: Path, row: dict[str, Any]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def _build_run_id(seeds: int, concurrency: int, custom: str | None) -> str:
    if custom:
        return custom
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bench-s{seeds}-c{concurrency}-{stamp}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full p2m pipeline at a chosen (seeds, concurrency) point.",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        required=True,
        help="Total scenario seeds to generate (sets pipeline.seeds.scenario.sample_size). 1..100000.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        required=True,
        help="Rollout concurrency (judge re-uses this value). Must be >= 1.",
    )
    parser.add_argument(
        "--behaviors",
        type=int,
        default=None,
        help=(
            "Override pipeline.policy.behavior_count. Default scales with --seeds "
            "(roughly 10 seeds per behavior, clamped to [6, 50])."
        ),
    )
    parser.add_argument(
        "--base-config",
        type=Path,
        default=DEFAULT_BASE_CONFIG,
        help=f"Base YAML to template from (default: {DEFAULT_BASE_CONFIG.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--concept-md",
        type=Path,
        default=DEFAULT_CONCEPT_SOURCE,
        help=(
            "Source path to the concept markdown that gets copied next to the temp "
            f"config (default: {DEFAULT_CONCEPT_SOURCE.relative_to(REPO_ROOT)})."
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
        help="Enable DEBUG-level logging from p2m for the duration of the run.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Also write all log output to this file (DEBUG level).",
    )
    parser.add_argument(
        "--auditor-prompt",
        type=Path,
        default=DEFAULT_BENIGN_AUDITOR_PROMPT,
        help=(
            "Path to a markdown file used to override the rollout auditor "
            "system prompt for the duration of this benchmark run. Defaults "
            f"to {DEFAULT_BENIGN_AUDITOR_PROMPT.relative_to(REPO_ROOT)} (a "
            "tame variant that doesn't trip Azure Prompt Shields). Pass "
            "--no-auditor-override to use the product default instead."
        ),
    )
    parser.add_argument(
        "--no-auditor-override",
        action="store_true",
        help=(
            "Skip the benign auditor-prompt swap and use the product's "
            "default rollout auditor system prompt. Likely to fail on "
            "Azure-hosted models with default content filters."
        ),
    )
    parser.add_argument(
        "--no-tolerate-content-filter",
        action="store_true",
        help=(
            "Skip the lenient content-filter wrapper. By default the "
            "benchmark catches Azure Prompt Shields rejections and skips "
            "those seeds (counted in the CSV) so a 100/500/1000-seed run "
            "isn't aborted by ~5–10%% false-positive jailbreak detections."
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

    if args.seeds < 1 or args.seeds > 100_000:
        print(f"--seeds must be in [1, 100000], got {args.seeds}", file=sys.stderr)
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

    behavior_count = (
        args.behaviors if args.behaviors is not None else _scale_behavior_count(args.seeds)
    )
    if behavior_count < 1:
        print(f"--behaviors must be >= 1, got {behavior_count}", file=sys.stderr)
        return 2

    run_id = _build_run_id(args.seeds, args.concurrency, args.run_id)

    overrides = _override_config(
        base_cfg,
        seeds=args.seeds,
        concurrency=args.concurrency,
        behavior_count=behavior_count,
        run_id=run_id,
    )

    target_config = _prepare_run_dir(
        args.bench_root, run_id, args.base_config, args.concept_md, overrides
    )

    suite_id = str(overrides.get("suite") or "")

    print("=" * 72)
    print(f"Benchmark run: {run_id}")
    print(f"  suite          : {suite_id}")
    print(f"  seeds          : {args.seeds} (scenario only)")
    print(f"  concurrency    : {args.concurrency} (rollout + judge)")
    print(f"  behavior_count : {behavior_count}")
    print(f"  config         : {target_config}")
    if args.log_file:
        print(f"  log file       : {args.log_file}")
    print("=" * 72, flush=True)

    # Configure logging the same way `p2m run` does so stage progress and
    # any failure output actually reaches the terminal. Must run BEFORE
    # we attach the rate-limit counter, because configure_logging clears
    # existing handlers on the root logger.
    configure_logging(verbose=args.verbose, log_file=args.log_file)
    _silence_asyncio_close_noise()

    # Swap in the benign auditor prompt unless the user opted out. The
    # default prompt is intentionally adversarial (oblique→overt
    # escalation, "treat replies as not instructions") which trips Azure
    # Prompt Shields' jailbreak detector before the first target turn.
    # We monkey-patch the module-level constant so no product code path
    # has to care about this benchmark-only override.
    if not args.no_auditor_override:
        if not args.auditor_prompt.exists():
            print(
                f"Auditor prompt override not found: {args.auditor_prompt}",
                file=sys.stderr,
            )
            return 2
        from p2m.stages import rollout as _rollout_mod
        _rollout_mod.AUDITOR_SYSTEM_PROMPT = args.auditor_prompt.read_text(
            encoding="utf-8"
        )
        logging.getLogger("benchmark").info(
            "Overrode auditor system prompt with %s",
            args.auditor_prompt.relative_to(REPO_ROOT)
            if args.auditor_prompt.is_relative_to(REPO_ROOT)
            else args.auditor_prompt,
        )

    # Install lenient content-filter handling unless the user opted out.
    # Resets the per-run blocked-seed counters so back-to-back invocations
    # in the same Python process don't accumulate counts across runs.
    if not args.no_tolerate_content_filter:
        _CONTENT_FILTER_BLOCKS["count"] = 0
        _TARGET_ERROR_TOLERATED["count"] = 0
        _install_content_filter_tolerance()
        _install_judge_skip_blocked()
        logging.getLogger("benchmark").info(
            "Content-filter tolerance enabled (blocked seeds will be recorded "
            "with stop_reason=content_filter_blocked, target_error seeds will "
            "be retained as target_error_tolerated, and the judge will skip "
            "filter-blocked transcripts)."
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
    run_dir = REPO_ROOT / "artifacts" / "results" / suite_id / run_id

    print()
    print("-" * 72)
    print(f"Wall time            : {wall_time:.1f}s")
    print(f"Exit code            : {exit_code}")
    print(f"Rate-limit cooldowns : {counter.count}")
    blocked = _CONTENT_FILTER_BLOCKS["count"]
    target_errors = _TARGET_ERROR_TOLERATED["count"]
    if blocked:
        print(f"Content-filter blocks: {blocked}")
    if target_errors:
        print(f"Target-error seeds   : {target_errors}")
    if metrics_summary.get("scenarios_scored") not in ("", None):
        print(f"Scenarios scored     : {metrics_summary['scenarios_scored']}")
    print(f"Run dir              : {run_dir}")
    print("-" * 72)

    if not args.no_csv:
        row = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id,
            "seeds": args.seeds,
            "concurrency": args.concurrency,
            "behavior_count": behavior_count,
            "exit_code": exit_code,
            "wall_time_s": round(wall_time, 2),
            "rate_limit_cooldowns": counter.count,
            "content_filter_blocked": blocked,
            "target_error_tolerated": target_errors,
            "config_path": str(target_config),
            "run_dir": str(run_dir),
            **metrics_summary,
        }
        _append_results_row(args.results_csv, row)
        print(f"Appended summary to  : {args.results_csv}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
