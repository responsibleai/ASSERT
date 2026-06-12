# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Click CLI for the measurements pipeline (ASSERT)."""

from __future__ import annotations

import difflib
import json

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import click
import yaml
from click.shell_completion import CompletionItem
from rich.console import Console
from rich.table import Table

from assert_ai.core.config_model import DEFAULT_INFERENCE_CONCURRENCY
from assert_ai.core.io import load_json, load_jsonl, get_permissible_flag, row_behavior
from assert_ai.core.judge import get_verdict_dimension, infer_judge_status, is_valid_event_flag
from assert_ai.display import label_metric, label_run_status, label_stage, label_stage_status, label_status
from assert_ai.logging_config import configure_logging
from assert_ai.stages import STAGE_NAMES

ROOT = Path(__file__).resolve().parent.parent
JUDGE_DIMENSIONS_PATH = ROOT / "examples" / "eval-definitions" / "judge_dimensions.yaml"
DEFAULT_RESULTS_DIR = ROOT / "artifacts" / "results"

CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
    "auto_envvar_prefix": "ASSERT_AI",
}

DEFAULT_COMPARE_METRIC = "policy_violation"

_RUNNER_MODULE: Any | None = None
_TEST_SET_METRICS_MODULE: Any | None = None



class SuggestingGroup(click.Group):
    """Click group that offers close-command suggestions on typos."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = super().get_command(ctx, cmd_name)
        if command is not None or ctx.resilient_parsing:
            return command

        suggestions = difflib.get_close_matches(cmd_name, self.list_commands(ctx), n=3, cutoff=0.45)
        suggestion_text = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise click.UsageError(f"No such command '{cmd_name}'.{suggestion_text}", ctx=ctx)


def _load_runner_module():
    global _RUNNER_MODULE
    if _RUNNER_MODULE is None:
        from assert_ai import runner

        _RUNNER_MODULE = runner
    return _RUNNER_MODULE


def _load_test_set_metrics():
    global _TEST_SET_METRICS_MODULE
    if _TEST_SET_METRICS_MODULE is None:
        from assert_ai.analysis import test_set_metrics

        _TEST_SET_METRICS_MODULE = test_set_metrics
    return _TEST_SET_METRICS_MODULE


def _handle_missing_analysis_dependency(exc: ModuleNotFoundError) -> None:
    missing = getattr(exc, "name", "") or "analysis extras"
    _error(
        f"Could not import '{missing}'. Install the analysis dependencies first, for example:\n"
        "  python -m pip install -e \".[analysis]\""
    )


def _load_analysis_module(loader: Callable[[], Any]) -> Any:
    try:
        return loader()
    except ModuleNotFoundError as exc:
        _handle_missing_analysis_dependency(exc)


def _console(*, no_color: bool = False) -> Console:
    return Console(highlight=False, color_system=None if no_color else "auto")


def _error(message: str) -> None:
    click.echo(message, err=True)
    raise SystemExit(1)


def _echo_json(payload: Any) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _format_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if isinstance(value, str) and value:
        return value
    return "-"


def _fmt_percent(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def _fmt_binary_counts(counts: dict[int, int]) -> str:
    return f"0:{counts.get(0, 0)} 1:{counts.get(1, 0)}"


def _fmt_flagged_pass(counts: dict[int, int]) -> str:
    """Render the flagged/pass counts using the viewer's terminology."""
    return f"{counts.get(1, 0)} flagged / {counts.get(0, 0)} pass"


def _metric_label(metric: str) -> str:
    return label_metric(metric)


def _resolve_results_dir(results_dir: Path) -> Path:
    path = results_dir.expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _complete_suite(ctx: click.Context, _: click.Parameter, incomplete: str) -> list[CompletionItem]:
    results_dir = _resolve_results_dir(Path(ctx.params.get("results_dir") or DEFAULT_RESULTS_DIR))
    if not results_dir.exists():
        return []
    items: list[CompletionItem] = []
    for path in sorted(results_dir.iterdir()):
        if not path.is_dir():
            continue
        if incomplete and not path.name.startswith(incomplete):
            continue
        items.append(CompletionItem(path.name))
    return items


def _complete_run(ctx: click.Context, _: click.Parameter, incomplete: str) -> list[CompletionItem]:
    results_dir = _resolve_results_dir(Path(ctx.params.get("results_dir") or DEFAULT_RESULTS_DIR))
    suite = ctx.params.get("suite")
    if not suite:
        return []
    suite_dir = results_dir / str(suite)
    if not suite_dir.exists():
        return []
    items: list[CompletionItem] = []
    for path in sorted(suite_dir.iterdir()):
        if not path.is_dir():
            continue
        if incomplete and not path.name.startswith(incomplete):
            continue
        items.append(CompletionItem(path.name))
    return items


def _load_dimensions() -> dict[str, Any]:
    if not JUDGE_DIMENSIONS_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(JUDGE_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _complete_metric(_: click.Context, __: click.Parameter, incomplete: str) -> list[CompletionItem]:
    dims = _load_dimensions()
    items = sorted(dims.keys())
    return [CompletionItem(name) for name in items if not incomplete or name.startswith(incomplete)]


def _current_stage_status(manifest: dict[str, Any] | None) -> tuple[str, str]:
    if isinstance(manifest, dict):
        manifest_status = manifest.get("status")
        if isinstance(manifest_status, str) and manifest_status:
            stages = manifest.get("stages")
            if isinstance(stages, dict):
                for stage_name, stage_status in stages.items():
                    if stage_status == "running":
                        return manifest_status, str(stage_name)
            return manifest_status, "-"

    return "unknown", "-"


def _detect_dimensions(rows: Iterable[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for row in rows:
        verdict = row.get("verdict")
        if not isinstance(verdict, dict):
            continue
        dimensions = verdict.get("dimensions")
        if not isinstance(dimensions, dict):
            continue
        for key, value in dimensions.items():
            if is_valid_event_flag(value):
                seen.add(key)
    return sorted(seen)


def _compute_dimension_summary(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, Any]:
    counts = {0: 0, 1: 0}
    total = 0
    for row in rows:
        if infer_judge_status(row) != "ok":
            continue
        value = get_verdict_dimension(row.get("verdict"), metric)
        if not is_valid_event_flag(value):
            continue
        counts[int(value)] += 1
        total += 1
    return {
        "rate": counts[1] / total if total else 0.0,
        "counts": counts,
        "count": total,
        "flagged_count": counts[1],
        "clear_count": counts[0],
    }


def _dimension_rate(metrics: dict[str, Any], metric: str) -> float | None:
    dimensions = metrics.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    summary = dimensions.get(metric)
    if not isinstance(summary, dict):
        return None
    rate = summary.get("rate")
    return float(rate) if isinstance(rate, (int, float)) else None


def _compute_prompt_metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    scored_rows = [row for row in rows if infer_judge_status(row) == "ok"]
    scored_total = len(scored_rows)
    judge_failures = len(rows) - scored_total

    dimensions: dict[str, dict[str, Any]] = {}
    for dim in _detect_dimensions(scored_rows):
        dimensions[dim] = _compute_dimension_summary(scored_rows, dim)

    target = next(
        (
            row.get("target")
            for row in rows
            if isinstance(row.get("target"), str) and row.get("target")
        ),
    ) or "-"
    judge_model = next(
        (
            row.get("judge_model")
            for row in rows
            if isinstance(row.get("judge_model"), str) and row.get("judge_model")
        ),
    ) or "-"
    permissible_rows = [row for row in scored_rows if get_permissible_flag(row, default=False)]
    not_permissible_rows = [row for row in scored_rows if not get_permissible_flag(row, default=False)]

    return {
        "total": len(rows),
        "scored_total": scored_total,
        "judge_failures": judge_failures,
        "judge_failure_rate": judge_failures / len(rows) if rows else 0.0,
        "policy_violation_rate": _dimension_rate({"dimensions": dimensions}, "policy_violation") or 0.0,
        "overrefusal_rate": _dimension_rate({"dimensions": dimensions}, "overrefusal") or 0.0,
        "permissible_overrefusal_rate": _compute_dimension_summary(permissible_rows, "overrefusal")["rate"],
        "not_permissible_policy_violation_rate": _compute_dimension_summary(
            not_permissible_rows,
            "policy_violation",
        )["rate"],
        "dimensions": dimensions,
        "target": target,
        "judge_model": judge_model,
    }


def _compute_scenario_metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    scored_rows = [row for row in rows if infer_judge_status(row) == "ok"]
    scored_total = len(scored_rows)
    judge_failures = len(rows) - scored_total

    dimensions: dict[str, dict[str, Any]] = {}
    for dim in _detect_dimensions(scored_rows):
        dimensions[dim] = _compute_dimension_summary(scored_rows, dim)

    target = next(
        (
            row.get("target")
            for row in rows
            if isinstance(row.get("target"), str) and row.get("target")
        ),
        None,
    ) or "-"
    tester_model = next(
        (
            row.get("tester_model")
            for row in rows
            if isinstance(row.get("tester_model"), str) and row.get("tester_model")
        ),
        None,
    ) or "-"
    judge_model = next(
        (
            row.get("judge_model")
            for row in rows
            if isinstance(row.get("judge_model"), str) and row.get("judge_model")
        ),
    ) or "-"

    return {
        "total": len(rows),
        "scored_total": scored_total,
        "judge_failures": judge_failures,
        "judge_failure_rate": judge_failures / len(rows) if rows else 0.0,
        "policy_violation_rate": _dimension_rate({"dimensions": dimensions}, "policy_violation") or 0.0,
        "overrefusal_rate": _dimension_rate({"dimensions": dimensions}, "overrefusal") or 0.0,
        "dimensions": dimensions,
        "target": target,
        "tester_model": tester_model,
        "judge_model": judge_model,
    }


def _load_run_summary(run_dir: Path) -> dict[str, Any] | None:
    manifest = load_json(run_dir / "manifest.json")
    score_rows = load_jsonl(run_dir / "scores.jsonl")
    prompt_rows = [row for row in score_rows if not row.get("tester_model")]
    scenario_rows = [row for row in score_rows if row.get("tester_model")]

    stages = (manifest or {}).get("stages", {})
    has_scores = isinstance(stages, dict) and stages.get("judge") is not None
    has_data = bool(prompt_rows or scenario_rows)
    if not has_data and not has_scores:
        return None
    if not has_data and (manifest or {}).get("status") == "failed":
        return None

    status, current_stage = _current_stage_status(manifest)
    return {
        "run_id": run_dir.name,
        "path": str(run_dir),
        "manifest": manifest,
        "status": status,
        "current_stage": current_stage,
        "started_at": (manifest or {}).get("started_at"),
        "ended_at": (manifest or {}).get("ended_at"),
        "prompt_metrics": _compute_prompt_metrics(prompt_rows),
        "scenario_metrics": _compute_scenario_metrics(scenario_rows),
        "prompt_rows": prompt_rows,
        "scenario_rows": scenario_rows,
    }


def _count_test_case_types(path: Path) -> tuple[int, int]:
    rows = load_jsonl(path)
    prompt_count = 0
    scenario_count = 0
    for row in rows:
        row_type = row.get("type")
        if row_type == "prompt":
            prompt_count += 1
        elif row_type == "scenario":
            scenario_count += 1
    return prompt_count, scenario_count


def _load_suite_summary(suite_dir: Path) -> dict[str, Any] | None:
    suite_meta = load_json(suite_dir / "suite.json")
    taxonomy = load_json(suite_dir / "taxonomy.json")
    if suite_meta is None and taxonomy is None:
        return None

    run_summaries = []
    for child in sorted(suite_dir.iterdir()) if suite_dir.exists() else []:
        if not child.is_dir():
            continue
        run_summary = _load_run_summary(child)
        if run_summary is not None:
            run_summaries.append(run_summary)

    has_results = any(
        (run_summary.get("prompt_metrics") is not None) or (run_summary.get("scenario_metrics") is not None)
        for run_summary in run_summaries
    )
    prompt_test_case_count, scenario_test_case_count = _count_test_case_types(suite_dir / "test_set.jsonl")

    created_at = (suite_meta or {}).get("created_at")

    behavior_name = suite_dir.name
    behavior_block = (taxonomy or {}).get("behavior")
    if isinstance(behavior_block, dict) and isinstance(behavior_block.get("name"), str) and behavior_block.get("name"):
        behavior_name = behavior_block["name"]

    if has_results:
        status = "has_results"
    elif prompt_test_case_count or scenario_test_case_count:
        status = "test_set_ready"
    else:
        status = "systematized"

    return {
        "suite_id": suite_dir.name,
        "path": str(suite_dir),
        "behavior_name": behavior_name,
        "behavior_category_count": len((taxonomy or {}).get("behavior_categories") or []),
        "prompt_test_case_count": prompt_test_case_count,
        "scenario_test_case_count": scenario_test_case_count,
        "run_count": len(run_summaries),
        "runs": run_summaries,
        "status": status,
        "created_at": created_at,
        "has_systematization": (suite_dir / "systematization.json").exists(),
    }


def _load_all_suites(results_dir: Path) -> list[dict[str, Any]]:
    if not results_dir.exists():
        return []
    suites = []
    for child in sorted(results_dir.iterdir()):
        if not child.is_dir():
            continue
        suite_summary = _load_suite_summary(child)
        if suite_summary is not None:
            suites.append(suite_summary)
    suites.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return suites


def _behavior_category_metric_map(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if infer_judge_status(row) != "ok":
            continue
        value = get_verdict_dimension(row.get("verdict"), metric)
        if not is_valid_event_flag(value):
            continue
        behavior_category = row_behavior(row)
        bucket = grouped.setdefault(
            behavior_category,
            {
                "true_count": 0,
                "count": 0,
                "permissible": get_permissible_flag(row),
            },
        )
        bucket["true_count"] += int(value)
        bucket["count"] += 1
    result = {}
    for behavior_category, bucket in grouped.items():
        if bucket["count"] <= 0:
            continue
        result[behavior_category] = {
            "rate": bucket["true_count"] / bucket["count"],
            "count": bucket["count"],
            "permissible": bucket["permissible"],
        }
    return result


@click.group(
    cls=SuggestingGroup,
    context_settings=CONTEXT_SETTINGS,
    epilog=(
        "\b\n"
        "Examples:\n"
        "  assert-ai run --config examples/pipes/health_assistant.yaml\n"
        "  assert-ai run --config examples/pipes/health_assistant_external.yaml\n"
        "  assert-ai results list\n"
        "  assert-ai results compare health-assistant-v1 gpt54-eval gpt54-eval\n"
        "  assert-ai results compare-suites suite-a/run-1 suite-b/run-1 suite-c/run-1"
    ),
)
@click.version_option(version="0.1.0", prog_name="assert-ai")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug-level logging.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress info-level output; show only warnings and errors.")
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Write all log output to a file (in addition to stderr).",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Log output format. Use 'json' for CI pipelines.",
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, log_file: Path | None, output_format: str):
    """Safety evaluation workflows for pipeline runs, artifacts, and post-hoc analysis."""
    ctx.ensure_object(dict)
    ctx.obj["logging_configured"] = True
    configure_logging(
        verbose=verbose,
        quiet=quiet,
        log_file=log_file,
        json_output=(output_format == "json"),
    )


# -- local agent setup ------------------------------------------------------
@cli.group(cls=SuggestingGroup, short_help="Discover and prepare local agent targets")
def local():
    """Local agent setup commands for sandboxed target evaluation."""


@local.command("discover", short_help="Find local agent installs/configs")
@click.option(
    "--target",
    type=click.Choice(["all", "openclaw", "hermes", "claude-code", "codex", "opencode", "gemini"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Local agent family to discover.",
)
@click.option("--home", type=click.Path(path_type=Path), default=None, help="Home directory to inspect instead of the current user home.")
@click.option("--runtime-path", type=click.Path(path_type=Path), default=None, help="Explicit runtime/package path for targets that support it.")
@click.option("--workspace", "workspace_path", type=click.Path(path_type=Path), default=None, help="Explicit workspace/context path for targets that support it.")
@click.option("--source-bundle", "source_bundle_path", type=click.Path(path_type=Path), default=None, help="Explicit source-bundle manifest path.")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None, help="Write discovery JSON manifest to this path.")
@click.option("--show-paths", is_flag=True, help="Print local absolute paths instead of redacted path placeholders.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON only.")
def local_discover(
    target: str,
    home: Path | None,
    runtime_path: Path | None,
    workspace_path: Path | None,
    source_bundle_path: Path | None,
    output_path: Path | None,
    show_paths: bool,
    as_json: bool,
):
    """Find local agent installs/configs and write a reviewable manifest."""
    from assert_ai.local_agents import discover_local_agents

    try:
        result = discover_local_agents(
            target=target,
            home=home,
            runtime_path=runtime_path,
            workspace_path=workspace_path,
            source_bundle_path=source_bundle_path,
            redact_paths=not show_paths,
        )
    except ValueError as exc:
        _error(str(exc))
        return

    payload = result.to_json()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if as_json:
        _echo_json(payload)
        return

    agents = payload.get("agents", [])
    if not agents:
        click.echo("No local agents found.")
        if output_path is not None:
            click.echo(f"Wrote discovery manifest: {output_path}")
        return

    click.echo("Found local agents:")
    click.echo("")
    for index, agent in enumerate(agents, 1):
        click.echo(f"{index}. {agent['id']}")
        runtime = agent.get("runtime") or {}
        config = agent.get("config") or {}
        workspace = agent.get("workspace") or {}
        if runtime.get("version"):
            click.echo(f"   runtime: {agent.get('display_name', agent['id'])} {runtime['version']}")
        elif runtime.get("binary") and runtime.get("valid"):
            click.echo(f"   runtime: {runtime['binary']} (found)")
        if runtime.get("path"):
            click.echo(f"   runtime path: {runtime['path']}")
        if config.get("path"):
            click.echo(f"   config: {config['path']}")
        if workspace.get("path"):
            click.echo(f"   workspace: {workspace['path']}")
        source_bundle = agent.get("source_bundle") or {}
        if source_bundle:
            click.echo(f"   source bundle: {'found' if source_bundle.get('exists') else 'not found'}")
        candidate_count = len(agent.get("candidate_files") or [])
        if candidate_count:
            click.echo(f"   candidate files: {candidate_count}")
        excluded_count = len(agent.get("excluded_files") or [])
        if excluded_count:
            click.echo(f"   excluded files: {excluded_count} secret-looking file{'s' if excluded_count != 1 else ''}")
        click.echo(f"   status: {agent.get('summary') or agent.get('status')}")
        click.echo("")

    if output_path is not None:
        click.echo(f"Wrote discovery manifest: {output_path}")
    if any(agent.get("status") == "ready" for agent in agents):
        click.echo("Next:")
        click.echo("  assert-ai local snapshot create --from <discovery.json> --target <agent-id>")


# -- init (design an eval config with an LLM assistant) ---------------------
from assert_ai.init._command import init  # noqa: E402

cli.add_command(init)


@cli.command(short_help="Run a pipeline from a YAML config")
@click.option(
    "--config",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a YAML pipeline config.",
    show_envvar=True,
)
@click.option(
    "--force-stage",
    type=click.Choice(STAGE_NAMES, case_sensitive=False),
    multiple=True,
    help=(
        "Force a stage to rerun even if cached. Repeat to force multiple stages. "
        "Forcing an upstream stage implicitly forces every configured downstream stage, "
        "so cached inference rows and scores can't silently survive a regenerated input."
    ),
    show_envvar=True,
)
@click.option("--strict", is_flag=True, help="Fail on malformed JSONL inputs instead of skipping bad rows.")
@click.option("--override", "overrides", multiple=True, help="Override a config value, e.g. test_set.sample_size=10.")
@click.option(
    "--concurrency",
    type=click.IntRange(min=1),
    default=None,
    help=(
        "Override inference/judge fan-out for this run. Wins over "
        "pipeline.inference.concurrency in the YAML. Defaults to the value in "
        f"the config (or {DEFAULT_INFERENCE_CONCURRENCY} if unset)."
    ),
    show_envvar=True,
)
@click.option("-v", "--verbose", is_flag=True, help="Enable debug-level logging.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress info-level output; show only warnings and errors.")
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Write all log output to a file (in addition to stderr).",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Log output format. Use 'json' for CI pipelines.",
)
@click.pass_context
def run(
    ctx: click.Context,
    config: Path,
    force_stage: tuple[str, ...],
    strict: bool,
    overrides: tuple[str, ...],
    concurrency: int | None,
    verbose: bool,
    quiet: bool,
    log_file: Path | None,
    output_format: str,
):
    """Run the evaluation pipeline."""
    # Re-configure logging if flags were passed on the subcommand
    # (e.g. `assert-ai run --verbose` instead of `assert-ai --verbose run`).
    if verbose or quiet or log_file or output_format != "text":
        configure_logging(
            verbose=verbose,
            quiet=quiet,
            log_file=log_file,
            json_output=(output_format == "json"),
        )
    runner = _load_runner_module()
    rc = runner.run_pipeline(
        config=str(config),
        force_stages=list(force_stage),
        strict=strict,
        overrides=list(overrides),
        concurrency=concurrency,
    )
    raise SystemExit(rc)


@cli.group(cls=SuggestingGroup, short_help="Browse and compare generated suites and runs")
def results():
    """Inspect local artifacts under `artifacts/results/`."""


@results.command("list", short_help="List suites, or runs for one suite")
@click.option(
    "--results-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_RESULTS_DIR,
    show_default=True,
    help="Results root to scan.",
)
@click.option("--suite", default=None, shell_complete=_complete_suite, help="List runs for one suite instead of all suites.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of tables.")
@click.option("--no-color", is_flag=True, help="Disable colored terminal output.")
def results_list(results_dir: Path, suite: Optional[str], as_json: bool, no_color: bool):
    """List suites, or list runs inside one suite."""
    results_root = _resolve_results_dir(results_dir)
    if not results_root.exists():
        _error(f"Results directory not found: {results_root}")

    if suite:
        suite_dir = results_root / suite
        if not suite_dir.exists():
            _error(f"Suite not found: {suite}")
        suite_summary = _load_suite_summary(suite_dir)
        if suite_summary is None:
            _error(f"Suite has no readable artifacts: {suite}")
        if as_json:
            _echo_json({"suite": suite_summary, "runs": suite_summary["runs"]})
            return

        console = _console(no_color=no_color)
        table = Table(title=f"Runs in {suite}", box=None, show_header=True, show_edge=False, pad_edge=False)
        table.add_column("Run", style="cyan", no_wrap=True)
        table.add_column("Status", style="white", no_wrap=True)
        table.add_column("Started", style="dim", no_wrap=True)
        table.add_column("Prompt policy violations", style="white", no_wrap=True)
        table.add_column("Prompt overrefusals", style="white", no_wrap=True)
        table.add_column("Scenario policy violations", style="white", no_wrap=True)
        table.add_column("Judge failures", style="white", no_wrap=True)
        table.add_column("Target", style="white")
        for run_summary in suite_summary["runs"]:
            prompt_metrics = run_summary.get("prompt_metrics") or {}
            scenario_metrics = run_summary.get("scenario_metrics") or {}
            target_model = prompt_metrics.get("target") or scenario_metrics.get("target") or "-"
            table.add_row(
                run_summary["run_id"],
                label_run_status(run_summary["status"]),
                _format_timestamp(run_summary.get("started_at")),
                _fmt_percent(_dimension_rate(prompt_metrics, "policy_violation")),
                _fmt_percent(_dimension_rate(prompt_metrics, "overrefusal")),
                _fmt_percent(_dimension_rate(scenario_metrics, "policy_violation")),
                _fmt_percent(
                    prompt_metrics.get("judge_failure_rate")
                    if prompt_metrics
                    else scenario_metrics.get("judge_failure_rate")
                ),
                str(target_model),
            )
        console.print(table)
        return

    suites = _load_all_suites(results_root)
    if as_json:
        _echo_json({"results_dir": str(results_root), "suites": suites})
        return

    console = _console(no_color=no_color)
    table = Table(title=f"Suites ({results_root})", box=None, show_header=True, show_edge=False, pad_edge=False)
    table.add_column("Suite", style="cyan", no_wrap=True)
    table.add_column("Behavior", style="white")
    table.add_column("Behavior Categories", style="white", no_wrap=True)
    table.add_column("Prompt Test Cases", style="white", no_wrap=True)
    table.add_column("Scenario Test Cases", style="white", no_wrap=True)
    table.add_column("Runs", style="white", no_wrap=True)
    table.add_column("Status", style="dim", no_wrap=True)
    for suite_summary in suites:
        table.add_row(
            suite_summary["suite_id"],
            str(suite_summary["behavior_name"]),
            str(suite_summary["behavior_category_count"]),
            str(suite_summary["prompt_test_case_count"]),
            str(suite_summary["scenario_test_case_count"]),
            str(suite_summary["run_count"]),
            label_status(suite_summary["status"]),
        )
    console.print(table)


@results.command("status", short_help="Show a suite summary or one run in detail")
@click.argument("suite", shell_complete=_complete_suite)
@click.argument("run", required=False, shell_complete=_complete_run)
@click.option(
    "--results-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_RESULTS_DIR,
    show_default=True,
    help="Results root to inspect.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of tables.")
@click.option("--no-color", is_flag=True, help="Disable colored terminal output.")
def results_status(suite: str, run: Optional[str], results_dir: Path, as_json: bool, no_color: bool):
    """Show suite-level or run-level status information."""
    results_root = _resolve_results_dir(results_dir)
    suite_dir = results_root / suite
    if not suite_dir.exists():
        _error(f"Suite not found: {suite}")

    if run is None:
        suite_summary = _load_suite_summary(suite_dir)
        if suite_summary is None:
            _error(f"Suite has no readable artifacts: {suite}")
        if as_json:
            _echo_json(suite_summary)
            return

        console = _console(no_color=no_color)
        summary = Table(box=None, show_header=False, show_edge=False, pad_edge=False)
        summary.add_column("Field", style="cyan", no_wrap=True)
        summary.add_column("Value", style="white")
        summary.add_row("Suite", suite_summary["suite_id"])
        summary.add_row("Behavior", str(suite_summary["behavior_name"]))
        summary.add_row("Status", label_status(suite_summary["status"]))
        summary.add_row("Created", _format_timestamp(suite_summary.get("created_at")))
        summary.add_row("Behavior Categories", str(suite_summary["behavior_category_count"]))
        summary.add_row("Prompt Test Cases", str(suite_summary["prompt_test_case_count"]))
        summary.add_row("Scenario Test Cases", str(suite_summary["scenario_test_case_count"]))
        summary.add_row("Runs", str(suite_summary["run_count"]))
        console.print(summary)

        if suite_summary["runs"]:
            table = Table(title="Runs", box=None, show_header=True, show_edge=False, pad_edge=False)
            table.add_column("Run", style="cyan", no_wrap=True)
            table.add_column("Status", style="white", no_wrap=True)
            table.add_column("Current Stage", style="white", no_wrap=True)
            table.add_column("Prompt policy violations", style="white", no_wrap=True)
            table.add_column("Prompt overrefusals", style="white", no_wrap=True)
            table.add_column("Scenario policy violations", style="white", no_wrap=True)
            for run_summary in suite_summary["runs"]:
                prompt_metrics = run_summary.get("prompt_metrics") or {}
                scenario_metrics = run_summary.get("scenario_metrics") or {}
                table.add_row(
                    run_summary["run_id"],
                    label_run_status(run_summary["status"]),
                    label_stage(run_summary["current_stage"]),
                    _fmt_percent(_dimension_rate(prompt_metrics, "policy_violation")),
                    _fmt_percent(_dimension_rate(prompt_metrics, "overrefusal")),
                    _fmt_percent(_dimension_rate(scenario_metrics, "policy_violation")),
                )
            console.print(table)
        return

    run_dir = suite_dir / run
    if not run_dir.exists():
        _error(f"Run not found: {suite}/{run}")
    run_summary = _load_run_summary(run_dir)
    if run_summary is None:
        _error(f"Run has no readable artifacts: {suite}/{run}")

    payload = {
        "suite_id": suite,
        **run_summary,
    }
    if as_json:
        _echo_json(payload)
        return

    console = _console(no_color=no_color)
    summary = Table(box=None, show_header=False, show_edge=False, pad_edge=False)
    summary.add_column("Field", style="cyan", no_wrap=True)
    summary.add_column("Value", style="white")
    summary.add_row("Suite", suite)
    summary.add_row("Run", run_summary["run_id"])
    summary.add_row("Status", label_run_status(run_summary["status"]))
    summary.add_row("Current Stage", label_stage(run_summary["current_stage"]))
    summary.add_row("Started", _format_timestamp(run_summary.get("started_at")))
    summary.add_row("Ended", _format_timestamp(run_summary.get("ended_at")))
    summary.add_row("Path", run_summary["path"])
    console.print(summary)

    stage_meta = (
        run_summary.get("manifest", {}).get("stages")
        if isinstance(run_summary.get("manifest"), dict)
        else {}
    )
    if isinstance(stage_meta, dict) and stage_meta:
        table = Table(title="Stages", box=None, show_header=True, show_edge=False, pad_edge=False)
        table.add_column("Stage", style="cyan", no_wrap=True)
        table.add_column("Status", style="white", no_wrap=True)
        for stage_name, meta in stage_meta.items():
            # ``label_stage_status`` already accepts ``str | None``; wrapping
            # ``meta`` in ``str(...)`` would turn a missing/None value into the
            # literal string "None" instead of the intended em-dash placeholder.
            stage_status = meta if isinstance(meta, str) else None
            table.add_row(label_stage(stage_name), label_stage_status(stage_status))
        console.print(table)

    prompt_metrics = run_summary.get("prompt_metrics")
    if prompt_metrics:
        table = Table(title="Prompt Metrics", box=None, show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")
        table.add_row("Target", str(prompt_metrics.get("target") or "-"))
        table.add_row("Judge Model", str(prompt_metrics.get("judge_model") or "-"))
        table.add_row("Total", str(prompt_metrics["total"]))
        table.add_row("Scored", str(prompt_metrics["scored_total"]))
        table.add_row(label_metric("judge_failure_rate"), _fmt_percent(prompt_metrics.get("judge_failure_rate")))
        console.print(table)
        if prompt_metrics.get("dimensions"):
            dim_table = Table(title="Prompt Dimensions", box=None, show_header=True, show_edge=False, pad_edge=False)
            dim_table.add_column("Dimension", style="cyan", no_wrap=True)
            dim_table.add_column("Flagged rate", style="white", no_wrap=True)
            dim_table.add_column("Scored", style="white", no_wrap=True)
            dim_table.add_column("Flagged / Pass", style="white", no_wrap=True)
            for name, summary in sorted(prompt_metrics["dimensions"].items()):
                dim_table.add_row(
                    label_metric(name),
                    _fmt_percent(summary.get("rate")),
                    str(summary.get("count", 0)),
                    _fmt_flagged_pass(summary.get("counts", {})),
                )
            console.print(dim_table)

    scenario_metrics = run_summary.get("scenario_metrics")
    if scenario_metrics:
        table = Table(title="Scenario Metrics", box=None, show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")
        table.add_row("Target", str(scenario_metrics.get("target") or "-"))
        table.add_row("Tester Model", str(scenario_metrics.get("tester_model") or "-"))
        table.add_row("Judge Model", str(scenario_metrics.get("judge_model") or "-"))
        table.add_row("Total", str(scenario_metrics["total"]))
        table.add_row("Scored", str(scenario_metrics["scored_total"]))
        table.add_row(label_metric("judge_failure_rate"), _fmt_percent(scenario_metrics.get("judge_failure_rate")))
        console.print(table)
        if scenario_metrics.get("dimensions"):
            dim_table = Table(title="Scenario Dimensions", box=None, show_header=True, show_edge=False, pad_edge=False)
            dim_table.add_column("Dimension", style="cyan", no_wrap=True)
            dim_table.add_column("Flagged rate", style="white", no_wrap=True)
            dim_table.add_column("Scored", style="white", no_wrap=True)
            dim_table.add_column("Flagged / Pass", style="white", no_wrap=True)
            for name, summary in sorted(scenario_metrics["dimensions"].items()):
                dim_table.add_row(
                    label_metric(name),
                    _fmt_percent(summary.get("rate")),
                    str(summary.get("count", 0)),
                    _fmt_flagged_pass(summary.get("counts", {})),
                )
            console.print(dim_table)


@results.command("compare", short_help="Compare runs (same suite or cross-suite)")
@click.argument("args", nargs=-1)
@click.option(
    "--results-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_RESULTS_DIR,
    show_default=True,
    help="Results root to inspect.",
)
@click.option(
    "--metric",
    default=DEFAULT_COMPARE_METRIC,
    shell_complete=_complete_metric,
    show_default=True,
    help="Judge dimension to use for the top behavior-category delta table.",
)
@click.option("--limit", default=8, show_default=True, type=int, help="Maximum behavior categories to show in the delta table.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of tables.")
@click.option("--no-color", is_flag=True, help="Disable colored terminal output.")
@click.pass_context
def results_compare(
    ctx: click.Context,
    args: tuple[str, ...],
    results_dir: Path,
    metric: str,
    limit: int,
    as_json: bool,
    no_color: bool,
):
    """Compare runs. Accepts two forms:

    \b
    Within one suite:  assert-ai results compare SUITE RUN1 RUN2
    Cross-suite:       assert-ai results compare SUITE/RUN1 SUITE/RUN2
    """
    if len(args) < 2:
        _error(
            "Provide at least two arguments.\n"
            "  Within suite:  assert-ai results compare SUITE RUN1 RUN2\n"
            "  Cross-suite:   assert-ai results compare SUITE/RUN1 SUITE/RUN2"
        )

    # Detect cross-suite mode: any arg contains "/"
    if any("/" in a for a in args):
        ctx.invoke(
            results_compare_suites,
            suite_runs=args,
            results_dir=results_dir,
            metric=metric,
            as_json=as_json,
            no_color=no_color,
        )
        return

    # Within-suite mode: first arg is suite, rest are run IDs
    suite = args[0]
    runs = args[1:]
    if len(runs) < 2:
        results_root = _resolve_results_dir(results_dir)
        suite_b = results_root / runs[0] if runs else None
        if runs and suite_b and suite_b.exists():
            _error(
                f"'{runs[0]}' looks like a suite name, not a run ID.\n"
                f"Use slash format for cross-suite:\n"
                f"  assert-ai results compare {suite}/run-1 {runs[0]}/run-1"
            )
        _error("Provide at least two run IDs to compare.")

    _run_within_suite_compare(
        suite=suite,
        runs=runs,
        results_dir=results_dir,
        metric=metric,
        limit=limit,
        as_json=as_json,
        no_color=no_color,
    )


def _run_within_suite_compare(
    *,
    suite: str,
    runs: tuple[str, ...] | list[str],
    results_dir: Path,
    metric: str,
    limit: int,
    as_json: bool,
    no_color: bool,
) -> None:
    """Original within-suite comparison logic."""

    results_root = _resolve_results_dir(results_dir)
    suite_dir = results_root / suite
    if not suite_dir.exists():
        _error(f"Suite not found: {suite}")

    run_summaries: list[dict[str, Any]] = []
    for run_id in runs:
        run_summary = _load_run_summary(suite_dir / run_id)
        if run_summary is None:
            _error(f"Run not found or unreadable: {suite}/{run_id}")
        run_summaries.append(run_summary)

    available_metrics: set[str] = set()
    for run_summary in run_summaries:
        available_metrics.update(_detect_dimensions(run_summary.get("prompt_rows") or []))
    if metric not in available_metrics:
        _error(f"Metric '{metric}' was not found in the compared prompt judgments. Available: {sorted(available_metrics)}")

    behavior_category_deltas: list[dict[str, Any]] = []
    if all(run_summary.get("prompt_rows") for run_summary in run_summaries):
        first_map = _behavior_category_metric_map(run_summaries[0]["prompt_rows"], metric)
        last_map = _behavior_category_metric_map(run_summaries[-1]["prompt_rows"], metric)
        for behavior_category in sorted(set(first_map) | set(last_map)):
            first = first_map.get(behavior_category)
            last = last_map.get(behavior_category)
            if first is None or last is None:
                continue
            behavior_category_deltas.append(
                {
                    "behavior_category": behavior_category,
                    "permissible": first.get("permissible"),
                    "first_rate": first["rate"],
                    "last_rate": last["rate"],
                    "delta": last["rate"] - first["rate"],
                    "first_count": first["count"],
                    "last_count": last["count"],
                }
            )
        behavior_category_deltas.sort(key=lambda row: abs(row["delta"]), reverse=True)
        if limit >= 0:
            behavior_category_deltas = behavior_category_deltas[:limit]

    run_rows = []
    for run_summary in run_summaries:
        prompt_metrics = run_summary.get("prompt_metrics") or {}
        scenario_metrics = run_summary.get("scenario_metrics") or {}
        run_rows.append(
            {
                "run_id": run_summary["run_id"],
                "status": run_summary["status"],
                "started_at": run_summary.get("started_at"),
                "prompt": prompt_metrics,
                "scenario": scenario_metrics,
            }
        )

    payload = {
        "suite_id": suite,
        "metric": metric,
        "runs": run_rows,
        "behavior_category_deltas": behavior_category_deltas,
    }
    if as_json:
        _echo_json(payload)
        return

    console = _console(no_color=no_color)
    table = Table(
        title=f"Run Comparison ({suite}, {_metric_label(metric)})",
        box=None,
        show_header=True,
        show_edge=False,
        pad_edge=False,
    )
    table.add_column("Run", style="cyan", no_wrap=True)
    table.add_column("Status", style="white", no_wrap=True)
    table.add_column("Started", style="dim", no_wrap=True)
    table.add_column("Target", style="white")
    table.add_column(f"Prompt {_metric_label(metric).lower()} rate", style="white", no_wrap=True)
    table.add_column(f"Scenario {_metric_label(metric).lower()} rate", style="white", no_wrap=True)
    table.add_column(label_metric("judge_failure_rate"), style="white", no_wrap=True)
    for row in payload["runs"]:
        prompt_metrics = row["prompt"] or {}
        scenario_metrics = row["scenario"] or {}
        target_model = prompt_metrics.get("target") or scenario_metrics.get("target") or "-"
        fail_rate = (
            prompt_metrics.get("judge_failure_rate")
            if prompt_metrics
            else scenario_metrics.get("judge_failure_rate")
        )
        table.add_row(
            row["run_id"],
            label_run_status(row["status"] if isinstance(row.get("status"), str) else None),
            _format_timestamp(row.get("started_at")),
            str(target_model),
            _fmt_percent(_dimension_rate(prompt_metrics, metric)),
            _fmt_percent(_dimension_rate(scenario_metrics, metric)),
            _fmt_percent(fail_rate),
        )
    console.print(table)

    if behavior_category_deltas:
        delta_table = Table(
            title=f"Top behavior category deltas ({_metric_label(metric).lower()}: {runs[0]} -> {runs[-1]})",
            box=None,
            show_header=True,
            show_edge=False,
            pad_edge=False,
        )
        delta_table.add_column("Behavior category", style="cyan")
        delta_table.add_column("Permissible", style="white", no_wrap=True)
        delta_table.add_column(runs[0], style="white", no_wrap=True)
        delta_table.add_column(runs[-1], style="white", no_wrap=True)
        delta_table.add_column("Delta", style="white", no_wrap=True)
        for row in behavior_category_deltas:
            delta_table.add_row(
                row["behavior_category"],
                str(bool(row["permissible"])),
                _fmt_percent(row["first_rate"]),
                _fmt_percent(row["last_rate"]),
                _fmt_percent(row["delta"]),
            )
        console.print(delta_table)


@results.command("compare-suites", short_help="Compare runs across different suites (e.g., approach A vs B vs C)")
@click.argument("suite_runs", nargs=-1)
@click.option(
    "--results-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_RESULTS_DIR,
    show_default=True,
    help="Results root to inspect.",
)
@click.option(
    "--metric",
    default=DEFAULT_COMPARE_METRIC,
    show_default=True,
    help="Judge dimension to compare.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of tables.")
@click.option("--no-color", is_flag=True, help="Disable colored terminal output.")
def results_compare_suites(
    suite_runs: tuple[str, ...],
    results_dir: Path,
    metric: str,
    as_json: bool,
    no_color: bool,
):
    """Compare runs across different suites.

    Each argument is SUITE/RUN (e.g., travel-planner-phoenix-otel-demo/run-1).
    Useful for comparing different integration approaches on the same agent.

    \b
    Examples:
      assert-ai results compare-suites \\
        travel-planner-phoenix-otel-demo/run-1 \\
        travel-planner-litellm-callable/run-1 \\
        travel-planner-external-connector/run-1
    """
    if len(suite_runs) < 2:
        _error("Provide at least two SUITE/RUN arguments to compare.")

    results_root = _resolve_results_dir(results_dir)
    run_summaries: list[dict[str, Any]] = []
    labels: list[str] = []

    for suite_run in suite_runs:
        parts = suite_run.strip("/").split("/")
        if len(parts) == 1:
            suite_id, run_id = parts[0], "run-1"
        elif len(parts) == 2:
            suite_id, run_id = parts
        else:
            _error(f"Invalid format: '{suite_run}'. Use SUITE/RUN (e.g., my-suite/run-1).")
            return  # unreachable, for type checker

        run_dir = results_root / suite_id / run_id
        if not run_dir.exists():
            _error(f"Not found: {suite_id}/{run_id}")
        run_summary = _load_run_summary(run_dir)
        if run_summary is None:
            _error(f"No scores in {suite_id}/{run_id}")
        run_summary["suite_id"] = suite_id
        run_summaries.append(run_summary)
        labels.append(f"{suite_id}/{run_id}")

    # Count structural visibility from inference rows
    structural: list[dict[str, Any]] = []
    for i, suite_run in enumerate(suite_runs):
        parts = suite_run.strip("/").split("/")
        suite_id = parts[0]
        run_id = parts[1] if len(parts) > 1 else "run-1"
        inference_set_path = results_root / suite_id / run_id / "inference_set.jsonl"
        inference_rows = load_jsonl(inference_set_path)
        total_events = sum(len(r.get("events", [])) for r in inference_rows)
        tool_events = sum(
            1 for r in inference_rows
            for e in r.get("events", [])
            if e.get("edit", {}).get("type") == "tool_call"
        )
        msg_events = sum(
            1 for r in inference_rows
            for e in r.get("events", [])
            if e.get("edit", {}).get("type") == "add_message"
        )
        with_tools = sum(
            1 for r in inference_rows
            if any(e.get("edit", {}).get("type") == "tool_call" for e in r.get("events", []))
        )
        structural.append({
            "label": labels[i],
            "inference_rows": len(inference_rows),
            "total_events": total_events,
            "msg_events": msg_events,
            "tool_events": tool_events,
            "with_tools": with_tools,
        })

    if as_json:
        run_rows = []
        for i, run_summary in enumerate(run_summaries):
            prompt_metrics = run_summary.get("prompt_metrics") or {}
            run_rows.append({
                "label": labels[i],
                "suite_id": run_summary["suite_id"],
                "run_id": run_summary["run_id"],
                "status": run_summary["status"],
                "prompt": prompt_metrics,
                "structural": structural[i],
            })
        _echo_json({"metric": metric, "runs": run_rows})
        return

    console = _console(no_color=no_color)

    # Table 1: Judge quality
    table = Table(
        title=f"Cross-suite comparison ({_metric_label(metric).lower()})",
        box=None,
        show_header=True,
        show_edge=False,
        pad_edge=False,
    )
    table.add_column("Suite / Run", style="cyan", no_wrap=True)
    table.add_column("Total", style="white", no_wrap=True)
    table.add_column("Scored", style="white", no_wrap=True)
    table.add_column(label_metric("judge_failure_rate"), style="white", no_wrap=True)
    table.add_column(f"{_metric_label(metric)} rate", style="white", no_wrap=True)
    table.add_column("Pass rate", style="white", no_wrap=True)
    for i, run_summary in enumerate(run_summaries):
        pm = run_summary.get("prompt_metrics") or {}
        total = pm.get("total", 0)
        ok = pm.get("scored_total", 0)
        fail_rate = pm.get("judge_failure_rate")
        dim_rate = _dimension_rate(pm, metric)
        pass_rate = (1.0 - dim_rate) if dim_rate is not None else None
        table.add_row(
            labels[i],
            str(total),
            str(ok),
            _fmt_percent(fail_rate),
            _fmt_percent(dim_rate),
            _fmt_percent(pass_rate),
        )
    console.print(table)
    console.print()

    # Table 2: Structural visibility
    struct_table = Table(
        title="Structural visibility (what the judge sees)",
        box=None,
        show_header=True,
        show_edge=False,
        pad_edge=False,
    )
    struct_table.add_column("Suite / Run", style="cyan", no_wrap=True)
    struct_table.add_column("Inference rows", style="white", no_wrap=True)
    struct_table.add_column("Events", style="white", no_wrap=True)
    struct_table.add_column("Messages", style="white", no_wrap=True)
    struct_table.add_column("Tool events", style="white", no_wrap=True)
    struct_table.add_column("With tools", style="white", no_wrap=True)
    for s in structural:
        struct_table.add_row(
            s["label"],
            str(s["inference_rows"]),
            str(s["total_events"]),
            str(s["msg_events"]),
            str(s["tool_events"]),
            f"{s['with_tools']}/{s['inference_rows']}",
        )
    console.print(struct_table)


@cli.group(cls=SuggestingGroup, short_help="Run post-hoc analysis commands")
def analysis():
    """Post-hoc analysis commands for test_set and inspect logs."""


@analysis.command("test-set-metrics", short_help="Compute coverage and diversity metrics for test-set files")
@click.option("--taxonomy", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Taxonomy JSON to score against.")
@click.option("--test_set", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Test-set JSONL file to analyze.")
@click.option("--embed-model", default="text-embedding-3-large", show_default=True, help="Embedding model name.")
@click.option("--embed-backend", type=click.Choice(["openai", "hf"]), default="openai", show_default=True, help="Embedding backend.")
@click.option("--k", "k_values", multiple=True, type=int, help="Coverage@k values. Repeat to provide multiple values.")
@click.option("--example-distance-thresh", default=0.2, type=float, show_default=True, help="Cosine distance threshold for example coverage.")
@click.option("--presence-coverage", is_flag=True, help="Use simple presence coverage instead of coverage@k.")
@click.option("--out-json", default=ROOT / "artifacts" / "analysis" / "test_set_metrics.json", type=click.Path(path_type=Path), show_default=True, help="Where to write the JSON report.")
@click.option("--out-md", default=None, type=click.Path(path_type=Path), help="Optional Markdown report path.")
def analysis_test_set_metrics(
    taxonomy: Path,
    test_set: Path,
    embed_model: str,
    embed_backend: str,
    k_values: tuple[int, ...],
    example_distance_thresh: float,
    presence_coverage: bool,
    out_json: Path,
    out_md: Optional[Path],
):
    """Compute test-set metrics from the packaged CLI instead of calling the script directly."""
    test_set_metrics = _load_analysis_module(_load_test_set_metrics)
    cfg = test_set_metrics.Config(
        taxonomy_path=str(taxonomy),
        test_set_path=str(test_set),
        embed_model=embed_model,
        embed_backend=embed_backend,
        k_list=list(k_values) if k_values else [1, 2, 3],
        example_distance_thresh=example_distance_thresh,
        presence_coverage=presence_coverage,
        out_json=str(out_json),
        out_md=str(out_md) if out_md else None,
    )
    try:
        test_set_metrics.compute_metrics(cfg)
    except ModuleNotFoundError as exc:
        _handle_missing_analysis_dependency(exc)
    click.echo(f"Wrote {out_json}")
    if out_md:
        click.echo(f"Wrote {out_md}")


@cli.command("judge-traces", short_help="Judge pre-collected OTel traces without running inference")
@click.option(
    "--traces",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="OTLP JSON trace file",
)
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Config YAML (for judge settings)",
)
@click.option("--group-by", default="session.id", show_default=True, help="OTel attribute to group spans by")
@click.option("--output", default=None, type=click.Path(path_type=Path), help="Output directory for scores")
def judge_traces(traces: Path, config_path: Path, group_by: str, output: Path | None):
    """Judge pre-collected OTel traces without running inference."""
    from assert_ai.core.otel import parse_otel_traces

    click.echo(f"Parsing OTel traces from {traces}...")
    inference_rows = parse_otel_traces(traces, group_by=group_by)
    click.echo(f"Found {len(inference_rows)} conversations")

    if not inference_rows:
        click.echo("No conversations found in traces. Check your group_by attribute.")
        raise SystemExit(1)

    # Load config for judge settings
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    out_dir = output or (DEFAULT_RESULTS_DIR / "judge-traces")
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Write parsed inference rows to output directory
    inference_set_path = Path(out_dir) / "inference_set.jsonl"
    with open(inference_set_path, "w") as f:
        for row in inference_rows:
            f.write(json.dumps(row) + "\n")
    click.echo(f"Wrote {len(inference_rows)} inference rows to {inference_set_path}")

    click.echo(f"Judging {len(inference_rows)} conversations...")
    # Full judge execution requires LLM access; the inference rows are ready
    # for the judge stage to consume.
    click.echo(f"Inference set written to {inference_set_path}")
    click.echo("Run the full pipeline with --force-stage judge to score these inference rows.")


@cli.group(cls=SuggestingGroup, short_help="Browse built-in behavior and judge presets")
def library():
    """Discover and inspect the built-in preset library."""


@library.command("list", short_help="List available presets")
@click.option(
    "--kind", "-k",
    type=click.Choice(["behavior", "judge_preset"], case_sensitive=False),
    default=None,
    help="Filter by preset kind.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--no-color", is_flag=True, help="Disable colored output.")
def library_list(kind: str | None, as_json: bool, no_color: bool):
    """List all available presets in the library."""
    from assert_ai.library.loader import discover

    results = discover(kind)
    if as_json:
        _echo_json(results)
        return

    if not results:
        click.echo("No presets found.")
        return

    console = _console(no_color=no_color)
    table = Table(title="Library Presets", box=None, show_header=True, show_edge=False, pad_edge=False)
    table.add_column("Kind", style="dim", no_wrap=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Version", style="white", no_wrap=True)
    table.add_column("Tags", style="white")
    for entry in results:
        tags = ", ".join(entry.get("tags", []))
        table.add_row(entry["kind"], entry["name"], entry.get("version", ""), tags)
    console.print(table)


@library.command("show", short_help="Show details of a preset")
@click.argument("name")
@click.option(
    "--kind", "-k",
    type=click.Choice(["behavior", "judge_preset"], case_sensitive=False),
    default=None,
    help="Preset kind (auto-detected if omitted).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit raw YAML content as JSON.")
def library_show(name: str, kind: str | None, as_json: bool):
    """Show the full content of a preset by name."""
    from assert_ai.library.loader import VALID_KINDS, load_preset

    # Auto-detect kind if not specified
    if kind is None:
        for k in sorted(VALID_KINDS):
            try:
                data = load_preset(k, name)
                kind = k
                break
            except ValueError:
                continue
        else:
            _error(f"Preset {name!r} not found in any kind. Use --kind to be explicit.")
            return  # unreachable but satisfies type checker
    else:
        data = load_preset(kind, name)

    if as_json:
        _echo_json(data)
        return

    click.echo(yaml.dump(data, default_flow_style=False, sort_keys=False).rstrip())


if __name__ == "__main__":
    cli()
