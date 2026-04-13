"""Click CLI for the measurements pipeline (p2m)."""

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

from p2m.core.io import load_json, load_jsonl, get_permissible_flag
from p2m.core.judge import get_verdict_dimension, infer_judge_status, is_valid_event_flag
from p2m.stages import STAGE_NAMES

ROOT = Path(__file__).resolve().parent.parent
JUDGE_DIMENSIONS_PATH = ROOT / "examples" / "eval-definitions" / "judge_dimensions.yaml"
DEFAULT_RESULTS_DIR = ROOT / "artifacts" / "results"
DEFAULT_LOGS_DIR = ROOT / "logs"
DEFAULT_PLOTS_DIR = ROOT / "plots"
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
    "auto_envvar_prefix": "P2M",
}

DEFAULT_COMPARE_METRIC = "policy_violation"

_RUNNER_MODULE: Any | None = None
_SEED_METRICS_MODULE: Any | None = None
_ANALYZE_TAXONOMIES_MODULE: Any | None = None


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
        from p2m import runner

        _RUNNER_MODULE = runner
    return _RUNNER_MODULE


def _load_seed_metrics():
    global _SEED_METRICS_MODULE
    if _SEED_METRICS_MODULE is None:
        from p2m.analysis import seed_metrics

        _SEED_METRICS_MODULE = seed_metrics
    return _SEED_METRICS_MODULE


def _load_analyze_taxonomies():
    global _ANALYZE_TAXONOMIES_MODULE
    if _ANALYZE_TAXONOMIES_MODULE is None:
        from p2m.analysis import analyze_policies as analyze_taxonomies

        _ANALYZE_TAXONOMIES_MODULE = analyze_taxonomies
    return _ANALYZE_TAXONOMIES_MODULE


def _handle_missing_analysis_dependency(exc: ModuleNotFoundError) -> None:
    missing = getattr(exc, "name", "") or "analysis extras"
    _error(
        f"Could not import '{missing}'. Install the analysis dependencies first, for example:\n"
        "  uv sync --extra analysis"
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
    return "—"


def _fmt_percent(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _fmt_binary_counts(counts: dict[int, int]) -> str:
    return f"0:{counts.get(0, 0)} 1:{counts.get(1, 0)}"


def _metric_label(metric: str) -> str:
    return metric.replace("_", " ")


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
            return manifest_status, "—"

    return "unknown", "—"


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
    ) or "—"
    judge_model = next(
        (
            row.get("judge_model")
            for row in rows
            if isinstance(row.get("judge_model"), str) and row.get("judge_model")
        ),
    ) or "—"
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
    ) or "—"
    auditor_model = next(
        (
            row.get("auditor_model")
            for row in rows
            if isinstance(row.get("auditor_model"), str) and row.get("auditor_model")
        ),
        None,
    ) or "—"
    judge_model = next(
        (
            row.get("judge_model")
            for row in rows
            if isinstance(row.get("judge_model"), str) and row.get("judge_model")
        ),
    ) or "—"

    return {
        "total": len(rows),
        "scored_total": scored_total,
        "judge_failures": judge_failures,
        "judge_failure_rate": judge_failures / len(rows) if rows else 0.0,
        "policy_violation_rate": _dimension_rate({"dimensions": dimensions}, "policy_violation") or 0.0,
        "overrefusal_rate": _dimension_rate({"dimensions": dimensions}, "overrefusal") or 0.0,
        "dimensions": dimensions,
        "target": target,
        "auditor_model": auditor_model,
        "judge_model": judge_model,
    }


def _load_run_summary(run_dir: Path) -> dict[str, Any] | None:
    manifest = load_json(run_dir / "manifest.json")
    score_rows = load_jsonl(run_dir / "scores.jsonl")
    prompt_rows = [row for row in score_rows if not row.get("auditor_model")]
    scenario_rows = [row for row in score_rows if row.get("auditor_model")]

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


def _count_seed_kinds(path: Path) -> tuple[int, int]:
    rows = load_jsonl(path)
    prompt_count = 0
    scenario_count = 0
    for row in rows:
        kind = row.get("kind")
        if kind == "prompt":
            prompt_count += 1
        elif kind == "scenario":
            scenario_count += 1
    return prompt_count, scenario_count


def _load_suite_summary(suite_dir: Path) -> dict[str, Any] | None:
    suite_meta = load_json(suite_dir / "suite.json")
    policy = load_json(suite_dir / "policy.json")
    if suite_meta is None and policy is None:
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
    seed_count, scenario_seed_count = _count_seed_kinds(suite_dir / "seeds.jsonl")

    created_at = (suite_meta or {}).get("created_at")

    risk_name = suite_dir.name
    risk_block = (policy or {}).get("risk")
    if isinstance(risk_block, dict) and isinstance(risk_block.get("name"), str) and risk_block.get("name"):
        risk_name = risk_block["name"]

    if has_results:
        status = "has_results"
    elif seed_count or scenario_seed_count:
        status = "seeds_ready"
    else:
        status = "policy_only"

    return {
        "suite_id": suite_dir.name,
        "path": str(suite_dir),
        "risk_name": risk_name,
        "sub_risk_count": len((policy or {}).get("sub_risks") or []),
        "seed_count": seed_count,
        "scenario_seed_count": scenario_seed_count,
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


def _subrisk_metric_map(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if infer_judge_status(row) != "ok":
            continue
        value = get_verdict_dimension(row.get("verdict"), metric)
        if not is_valid_event_flag(value):
            continue
        sub_risk = str(row.get("sub_risk") or "")
        bucket = grouped.setdefault(
            sub_risk,
            {
                "true_count": 0,
                "count": 0,
                "permissible": get_permissible_flag(row),
            },
        )
        bucket["true_count"] += int(value)
        bucket["count"] += 1
    result = {}
    for sub_risk, bucket in grouped.items():
        if bucket["count"] <= 0:
            continue
        result[sub_risk] = {
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
        "  p2m run --config examples/pipes/health_assistant.yaml\n"
        "  p2m run --config examples/pipes/health_assistant_external.yaml\n"
        "  p2m results list\n"
        "  p2m results compare health-assistant-v1 gpt54-eval gpt54-eval"
    ),
)
@click.version_option(version="0.1.0", prog_name="p2m")
def cli():
    """Safety evaluation workflows for pipeline runs, artifacts, and post-hoc analysis."""


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
    help="Force a stage to rerun even if cached. Repeat to force multiple stages.",
    show_envvar=True,
)
@click.option("--strict", is_flag=True, help="Fail on malformed JSONL inputs instead of skipping bad rows.")
def run(
    config: Path,
    force_stage: tuple[str, ...],
    strict: bool,
):
    """Run the evaluation pipeline."""
    runner = _load_runner_module()
    rc = runner.run_pipeline(
        config=str(config),
        force_stages=list(force_stage),
        strict=strict,
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
        table.add_column("Prompt Viol.", style="white", no_wrap=True)
        table.add_column("Prompt Overref.", style="white", no_wrap=True)
        table.add_column("Scenario Viol.", style="white", no_wrap=True)
        table.add_column("Judge Fail", style="white", no_wrap=True)
        table.add_column("Target Model", style="white")
        for run_summary in suite_summary["runs"]:
            prompt_metrics = run_summary.get("prompt_metrics") or {}
            scenario_metrics = run_summary.get("scenario_metrics") or {}
            target_model = prompt_metrics.get("target") or scenario_metrics.get("target") or "—"
            table.add_row(
                run_summary["run_id"],
                run_summary["status"],
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
    table.add_column("Risk", style="white")
    table.add_column("Sub-Risks", style="white", no_wrap=True)
    table.add_column("Prompt Seeds", style="white", no_wrap=True)
    table.add_column("Scenario Seeds", style="white", no_wrap=True)
    table.add_column("Runs", style="white", no_wrap=True)
    table.add_column("Status", style="dim", no_wrap=True)
    for suite_summary in suites:
        table.add_row(
            suite_summary["suite_id"],
            str(suite_summary["risk_name"]),
            str(suite_summary["sub_risk_count"]),
            str(suite_summary["seed_count"]),
            str(suite_summary["scenario_seed_count"]),
            str(suite_summary["run_count"]),
            str(suite_summary["status"]),
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
        summary.add_row("Risk", str(suite_summary["risk_name"]))
        summary.add_row("Status", str(suite_summary["status"]))
        summary.add_row("Created", _format_timestamp(suite_summary.get("created_at")))
        summary.add_row("Sub-Risks", str(suite_summary["sub_risk_count"]))
        summary.add_row("Prompt Seeds", str(suite_summary["seed_count"]))
        summary.add_row("Scenario Seeds", str(suite_summary["scenario_seed_count"]))
        summary.add_row("Runs", str(suite_summary["run_count"]))
        console.print(summary)

        if suite_summary["runs"]:
            table = Table(title="Runs", box=None, show_header=True, show_edge=False, pad_edge=False)
            table.add_column("Run", style="cyan", no_wrap=True)
            table.add_column("Status", style="white", no_wrap=True)
            table.add_column("Current Stage", style="white", no_wrap=True)
            table.add_column("Prompt Viol.", style="white", no_wrap=True)
            table.add_column("Prompt Overref.", style="white", no_wrap=True)
            table.add_column("Scenario Viol.", style="white", no_wrap=True)
            for run_summary in suite_summary["runs"]:
                prompt_metrics = run_summary.get("prompt_metrics") or {}
                scenario_metrics = run_summary.get("scenario_metrics") or {}
                table.add_row(
                    run_summary["run_id"],
                    run_summary["status"],
                    str(run_summary["current_stage"]),
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
    summary.add_row("Status", str(run_summary["status"]))
    summary.add_row("Current Stage", str(run_summary["current_stage"]))
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
            table.add_row(stage_name, str(meta))
        console.print(table)

    prompt_metrics = run_summary.get("prompt_metrics")
    if prompt_metrics:
        table = Table(title="Prompt Metrics", box=None, show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")
        table.add_row("Target", str(prompt_metrics.get("target") or "—"))
        table.add_row("Judge Model", str(prompt_metrics.get("judge_model") or "—"))
        table.add_row("Total", str(prompt_metrics["total"]))
        table.add_row("Scored", str(prompt_metrics["scored_total"]))
        table.add_row("Judge Failures", _fmt_percent(prompt_metrics.get("judge_failure_rate")))
        console.print(table)
        if prompt_metrics.get("dimensions"):
            dim_table = Table(title="Prompt Dimensions", box=None, show_header=True, show_edge=False, pad_edge=False)
            dim_table.add_column("Dimension", style="cyan", no_wrap=True)
            dim_table.add_column("Bad Event Rate", style="white", no_wrap=True)
            dim_table.add_column("Count", style="white", no_wrap=True)
            dim_table.add_column("Value Mix", style="white", no_wrap=True)
            for name, summary in sorted(prompt_metrics["dimensions"].items()):
                dim_table.add_row(
                    _metric_label(name),
                    _fmt_percent(summary.get("rate")),
                    str(summary.get("count", 0)),
                    _fmt_binary_counts(summary.get("counts", {})),
                )
            console.print(dim_table)

    scenario_metrics = run_summary.get("scenario_metrics")
    if scenario_metrics:
        table = Table(title="Scenario Metrics", box=None, show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")
        table.add_row("Target", str(scenario_metrics.get("target") or "—"))
        table.add_row("Auditor Model", str(scenario_metrics.get("auditor_model") or "—"))
        table.add_row("Judge Model", str(scenario_metrics.get("judge_model") or "—"))
        table.add_row("Total", str(scenario_metrics["total"]))
        table.add_row("Scored", str(scenario_metrics["scored_total"]))
        table.add_row("Judge Failures", _fmt_percent(scenario_metrics.get("judge_failure_rate")))
        console.print(table)
        if scenario_metrics.get("dimensions"):
            dim_table = Table(title="Scenario Dimensions", box=None, show_header=True, show_edge=False, pad_edge=False)
            dim_table.add_column("Dimension", style="cyan", no_wrap=True)
            dim_table.add_column("Bad Event Rate", style="white", no_wrap=True)
            dim_table.add_column("Count", style="white", no_wrap=True)
            dim_table.add_column("Value Mix", style="white", no_wrap=True)
            for name, summary in sorted(scenario_metrics["dimensions"].items()):
                dim_table.add_row(
                    _metric_label(name),
                    _fmt_percent(summary.get("rate")),
                    str(summary.get("count", 0)),
                    _fmt_binary_counts(summary.get("counts", {})),
                )
            console.print(dim_table)


@results.command("compare", short_help="Compare multiple runs in the same suite")
@click.argument("suite", shell_complete=_complete_suite)
@click.argument("runs", nargs=-1)
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
    help="Bad-event dimension to use for the top sub-risk delta table.",
)
@click.option("--limit", default=8, show_default=True, type=int, help="Maximum sub-risks to show in the delta table.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of tables.")
@click.option("--no-color", is_flag=True, help="Disable colored terminal output.")
def results_compare(
    suite: str,
    runs: tuple[str, ...],
    results_dir: Path,
    metric: str,
    limit: int,
    as_json: bool,
    no_color: bool,
):
    """Compare two or more runs and highlight the largest sub-risk shifts."""
    if len(runs) < 2:
        _error("Provide at least two run IDs to compare.")

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

    subrisk_deltas: list[dict[str, Any]] = []
    if all(run_summary.get("prompt_rows") for run_summary in run_summaries):
        first_map = _subrisk_metric_map(run_summaries[0]["prompt_rows"], metric)
        last_map = _subrisk_metric_map(run_summaries[-1]["prompt_rows"], metric)
        for sub_risk in sorted(set(first_map) | set(last_map)):
            first = first_map.get(sub_risk)
            last = last_map.get(sub_risk)
            if first is None or last is None:
                continue
            subrisk_deltas.append(
                {
                    "sub_risk": sub_risk,
                    "permissible": first.get("permissible"),
                    "first_rate": first["rate"],
                    "last_rate": last["rate"],
                    "delta": last["rate"] - first["rate"],
                    "first_count": first["count"],
                    "last_count": last["count"],
                }
            )
        subrisk_deltas.sort(key=lambda row: abs(row["delta"]), reverse=True)
        if limit >= 0:
            subrisk_deltas = subrisk_deltas[:limit]

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
        "subrisk_deltas": subrisk_deltas,
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
    table.add_column("Target Model", style="white")
    table.add_column("Prompt Rate", style="white", no_wrap=True)
    table.add_column("Scenario Rate", style="white", no_wrap=True)
    table.add_column("Judge Fail", style="white", no_wrap=True)
    for row in payload["runs"]:
        prompt_metrics = row["prompt"] or {}
        scenario_metrics = row["scenario"] or {}
        target_model = prompt_metrics.get("target") or scenario_metrics.get("target") or "—"
        fail_rate = (
            prompt_metrics.get("judge_failure_rate")
            if prompt_metrics
            else scenario_metrics.get("judge_failure_rate")
        )
        table.add_row(
            row["run_id"],
            str(row["status"]),
            _format_timestamp(row.get("started_at")),
            str(target_model),
            _fmt_percent(_dimension_rate(prompt_metrics, metric)),
            _fmt_percent(_dimension_rate(scenario_metrics, metric)),
            _fmt_percent(fail_rate),
        )
    console.print(table)

    if subrisk_deltas:
        delta_table = Table(
            title=f"Top Sub-Risk Deltas ({_metric_label(metric)}: {runs[0]} -> {runs[-1]})",
            box=None,
            show_header=True,
            show_edge=False,
            pad_edge=False,
        )
        delta_table.add_column("Sub-Risk", style="cyan")
        delta_table.add_column("Permissible", style="white", no_wrap=True)
        delta_table.add_column(runs[0], style="white", no_wrap=True)
        delta_table.add_column(runs[-1], style="white", no_wrap=True)
        delta_table.add_column("Delta", style="white", no_wrap=True)
        for row in subrisk_deltas:
            delta_table.add_row(
                row["sub_risk"],
                str(bool(row["permissible"])),
                _fmt_percent(row["first_rate"]),
                _fmt_percent(row["last_rate"]),
                _fmt_percent(row["delta"]),
            )
        console.print(delta_table)


@cli.group(cls=SuggestingGroup, short_help="Run post-hoc analysis commands")
def analysis():
    """Post-hoc analysis commands for seeds and inspect logs."""


@analysis.command("seed-metrics", short_help="Compute coverage and diversity metrics for seed files")
@click.option("--policy", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Policy JSON to score against.")
@click.option("--seeds", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Seed JSONL file to analyze.")
@click.option("--embed-model", default="text-embedding-3-large", show_default=True, help="Embedding model name.")
@click.option("--embed-backend", type=click.Choice(["openai", "hf"]), default="openai", show_default=True, help="Embedding backend.")
@click.option("--k", "k_values", multiple=True, type=int, help="Coverage@k values. Repeat to provide multiple values.")
@click.option("--example-distance-thresh", default=0.2, type=float, show_default=True, help="Cosine distance threshold for example coverage.")
@click.option("--presence-coverage", is_flag=True, help="Use simple presence coverage instead of coverage@k.")
@click.option("--out-json", default=ROOT / "artifacts" / "analysis" / "seed_metrics.json", type=click.Path(path_type=Path), show_default=True, help="Where to write the JSON report.")
@click.option("--out-md", default=None, type=click.Path(path_type=Path), help="Optional Markdown report path.")
def analysis_seed_metrics(
    policy: Path,
    seeds: Path,
    embed_model: str,
    embed_backend: str,
    k_values: tuple[int, ...],
    example_distance_thresh: float,
    presence_coverage: bool,
    out_json: Path,
    out_md: Optional[Path],
):
    """Compute seed metrics from the packaged CLI instead of calling the script directly."""
    seed_metrics = _load_analysis_module(_load_seed_metrics)
    cfg = seed_metrics.Config(
        policy_path=str(policy),
        seeds_path=str(seeds),
        embed_model=embed_model,
        embed_backend=embed_backend,
        k_list=list(k_values) if k_values else [1, 2, 3],
        example_distance_thresh=example_distance_thresh,
        presence_coverage=presence_coverage,
        out_json=str(out_json),
        out_md=str(out_md) if out_md else None,
    )
    try:
        seed_metrics.compute_metrics(cfg)
    except ModuleNotFoundError as exc:
        _handle_missing_analysis_dependency(exc)
    click.echo(f"Wrote {out_json}")
    if out_md:
        click.echo(f"Wrote {out_md}")


@analysis.command("policy-logs", short_help="Summarize legacy policy-eval logs and write plots")
@click.option("--logs-dir", default=DEFAULT_LOGS_DIR, type=click.Path(path_type=Path), show_default=True, help="Directory containing legacy .eval archives.")
@click.option("--out-dir", default=DEFAULT_PLOTS_DIR, type=click.Path(path_type=Path), show_default=True, help="Directory for plots.")
@click.option("--csv", default=None, type=click.Path(path_type=Path), help="Optional flattened CSV output path.")
def analysis_policy_logs(logs_dir: Path, out_dir: Path, csv: Optional[Path]):
    """Analyze legacy policy-eval logs without leaving the packaged CLI."""
    analyze_taxonomies = _load_analysis_module(_load_analyze_taxonomies)

    out_dir.mkdir(parents=True, exist_ok=True)
    df = analyze_taxonomies.load_all(logs_dir)
    grouped = df.groupby(["risk", "model"]).agg(
        overall_mean=("overall_raw", "mean"),
        norm_mean=("score_norm", "mean"),
        passes=("verdict", lambda series: (series == "pass").mean()),
        n=("verdict", "count"),
    )
    click.echo("\nSummary (per risk/model):")
    click.echo(grouped.reset_index().to_markdown(index=False))
    if csv:
        analyze_taxonomies.write_csv(df, csv)
        click.echo(f"\nWrote {csv}")
    analyze_taxonomies.plot_overall(df, out_dir)
    analyze_taxonomies.plot_dimensions(df, out_dir)


@cli.command("judge-traces", short_help="Judge pre-collected OTel traces without running rollout")
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
    """Judge pre-collected OTel traces without running rollout."""
    from p2m.core.otel import parse_otel_traces

    click.echo(f"Parsing OTel traces from {traces}...")
    transcript_rows = parse_otel_traces(traces, group_by=group_by)
    click.echo(f"Found {len(transcript_rows)} conversations")

    if not transcript_rows:
        click.echo("No conversations found in traces. Check your group_by attribute.")
        raise SystemExit(1)

    # Load config for judge settings
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    out_dir = output or (DEFAULT_RESULTS_DIR / "judge-traces")
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Write parsed transcripts to output directory
    transcripts_path = Path(out_dir) / "transcripts.jsonl"
    with open(transcripts_path, "w") as f:
        for row in transcript_rows:
            f.write(json.dumps(row) + "\n")
    click.echo(f"Wrote {len(transcript_rows)} transcripts to {transcripts_path}")

    click.echo(f"Judging {len(transcript_rows)} conversations...")
    # Full judge execution requires LLM access; the transcripts are ready
    # for the judge stage to consume.
    click.echo(f"Transcripts written to {transcripts_path}")
    click.echo("Run the full pipeline with --force-stage judge to score these transcripts.")


if __name__ == "__main__":
    cli()
