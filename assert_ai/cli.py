# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Click CLI for the measurements pipeline (ASSERT)."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import time

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

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




def _local_sandbox_run_dir(*, target: str, snapshot_manifest: Path) -> Path:
    """Return the default local sandbox run directory for a start command."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(str(snapshot_manifest.expanduser().resolve()).encode("utf-8")).hexdigest()[:8]
    return Path("artifacts") / "local-agents" / "sandboxes" / f"{target}-{timestamp}-{digest}"


def _local_snapshot_output_dir(*, target: str, discovery_path: Path) -> Path:
    """Return the default local snapshot output directory for a create command."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(str(discovery_path.expanduser().resolve()).encode("utf-8")).hexdigest()[:8]
    return Path("artifacts") / "local-agents" / "snapshots" / f"{target}-{timestamp}-{digest}"


def _local_sandbox_name(*, target: str, snapshot_manifest: Path) -> str:
    """Return a short Docker Sandbox-safe name for a local-agent run."""

    digest = hashlib.sha256(str(snapshot_manifest.expanduser().resolve()).encode("utf-8")).hexdigest()[:8]
    prefix = "oc" if target == "openclaw" else "la"
    return f"{prefix}-{digest}"


def _local_sandbox_display_model(provider_route: str, model: str) -> str:
    parts = model.split("-")
    pretty_model = " ".join([parts[0].upper(), *parts[1:]]) if parts else model
    if provider_route == "copilot":
        return f"{pretty_model} via Copilot"
    return pretty_model


def _local_sandbox_model_ref(*, provider_route: str, model: str) -> str:
    return f"{provider_route}/{model}={_local_sandbox_display_model(provider_route, model)}"


def _format_elapsed(seconds: float) -> str:
    """Render elapsed wall time for CLI summaries."""

    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours}h {minutes}m {remainder:.1f}s"
    if minutes:
        return f"{minutes}m {remainder:.1f}s"
    return f"{seconds:.2f}s"

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


def _handle_missing_acs_dependency(exc: ModuleNotFoundError) -> None:
    missing = getattr(exc, "name", "") or "ACS extra"
    _error(
        f"Could not import ACS dependency '{missing}'. Install the ACS extra first, for example:\n"
        "  python -m pip install -e \".[acs]\""
    )


def _load_acs_symbol(name: str) -> Any:
    try:
        import assert_ai.integrations.acs as acs

        return getattr(acs, name)
    except ModuleNotFoundError as exc:
        _handle_missing_acs_dependency(exc)


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


def _resolve_acs_run_dir(run_dir: Path | None, suite: str | None, run_id: str | None) -> Path:
    has_run_dir = run_dir is not None
    has_suite_run = suite is not None or run_id is not None
    if has_run_dir and has_suite_run:
        _error("Use either --run-dir or --suite with --run, not both.")
    if not has_run_dir and not has_suite_run:
        _error("Provide either --run-dir or both --suite and --run.")
    if has_suite_run and (suite is None or run_id is None):
        _error("Use --suite and --run together, or provide --run-dir.")

    if run_dir is not None:
        resolved = run_dir.expanduser()
        if not resolved.is_absolute():
            resolved = resolved.resolve()
    else:
        resolved = DEFAULT_RESULTS_DIR / str(suite) / str(run_id)

    if not resolved.exists():
        _error(f"Run directory not found: {resolved}")
    if not resolved.is_dir():
        _error(f"Run path is not a directory: {resolved}")
    if not (resolved / "scores.jsonl").is_file():
        _error(f"Run directory does not contain scores.jsonl: {resolved}")
    return resolved


def _default_acs_out_dir(summary: Any) -> Path:
    suite_id = str(getattr(summary, "suite_id", "") or "policy")
    return ROOT / "artifacts" / "acs" / suite_id


def _print_acs_artifacts(artifacts: Any) -> None:
    click.echo("Wrote ACS policy:")
    click.echo(f"  manifest: {artifacts.manifest_path}")
    click.echo(f"  rego:     {artifacts.rego_path}")
    click.echo(f"  report:   {artifacts.report_path}")
    guarded_points = ", ".join(str(point) for point in artifacts.guarded_points) or "-"
    click.echo(f"Guarded points: {guarded_points}")
    if artifacts.warnings:
        click.echo("Warnings:")
        for warning in artifacts.warnings:
            click.echo(f"  - {warning}")


def _print_acs_validation_totals(report: Any) -> None:
    click.echo(
        f"Validation: handled {report.handled}/{report.total} "
        f"(reacted, incl. warn); strongly blocked {report.strong_blocked}/{report.total} "
        f"(deny/escalate); handled_rate {_fmt_percent(report.handled_rate)}"
    )


def _enforce_acs_validation_gate(report: Any, *, fail_on_allow: bool, require_block: bool) -> None:
    """Fail the command per the requested validation gate, strictest first."""
    uncovered = getattr(report, "uncovered_behaviors", ())
    if (require_block or fail_on_allow) and uncovered:
        _error(
            "ACS validation could not replay any example for behavior finding(s): "
            f"{', '.join(uncovered)}. The policy was not exercised against them "
            "(no inference evidence); re-run with the matching inference set."
        )
    if require_block and not report.fully_blocked:
        _error(
            f"ACS policy did not strongly block {report.not_blocked} known-bad example(s) "
            "(only deny/escalate counts as a block)."
        )
    if fail_on_allow and not report.ok:
        _error(f"ACS policy allowed {report.failed} known-bad example(s).")


def _print_acs_validation_by_point(report: Any) -> None:
    grouped: dict[str, dict[str, int]] = {}
    for case in report.cases:
        point = str(case.intervention_point or "-")
        bucket = grouped.setdefault(point, {"total": 0, "handled": 0, "strong": 0})
        bucket["total"] += 1
        bucket["handled"] += int(bool(case.handled))
        bucket["strong"] += int(bool(case.strong_block))

    if not grouped:
        click.echo("Validation cases: 0")
        return

    table = Table(title="Validation by intervention point", box=None, show_header=True, show_edge=False, pad_edge=False)
    table.add_column("Point", style="cyan", no_wrap=True)
    table.add_column("Handled", style="white", no_wrap=True)
    table.add_column("Strong blocked", style="white", no_wrap=True)
    for point, bucket in sorted(grouped.items()):
        table.add_row(
            point,
            f"{bucket['handled']}/{bucket['total']}",
            str(bucket["strong"]),
        )
    _console().print(table)


def _print_acs_validation_cases(report: Any) -> None:
    table = Table(title="Validation cases", box=None, show_header=True, show_edge=False, pad_edge=False)
    table.add_column("Intervention point", style="cyan", no_wrap=True)
    table.add_column("Behavior", style="white")
    table.add_column("Dimension", style="white", no_wrap=True)
    table.add_column("Decision", style="white", no_wrap=True)
    table.add_column("Strong block", style="white", no_wrap=True)
    for case in report.cases:
        table.add_row(
            str(case.intervention_point),
            str(case.behavior),
            str(case.dimension),
            str(case.decision),
            "yes" if case.strong_block else "no",
        )
    _console().print(table)


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

    started_at = time.perf_counter()
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
        click.echo(f"elapsed: {_format_elapsed(time.perf_counter() - started_at)}")
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
        elif runtime.get("binary"):
            binary_state = "found" if runtime.get("valid") else "not found"
            click.echo(f"   runtime: {runtime['binary']} ({binary_state})")
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
        external_count = len(agent.get("external_references") or [])
        if external_count:
            click.echo(f"   external references: {external_count}")
            if show_paths:
                for reference in (agent.get("external_references") or [])[:5]:
                    click.echo(f"     {reference['kind']} from {reference['source']}: {reference['path']}")
        copy_roots = agent.get("suggested_copy_roots") or []
        if copy_roots:
            click.echo("   suggested extra roots:")
            for root in copy_roots:
                click.echo(f"     --include-root {root['source']}")
        click.echo(f"   status: {agent.get('summary') or agent.get('status')}")
        click.echo("")

    if output_path is not None:
        click.echo(f"Wrote discovery manifest: {output_path}")
    if any(agent.get("status") == "ready" for agent in agents):
        click.echo("Next:")
        click.echo("  assert-ai local snapshot create --from <discovery.json> --target <agent-id> [--include-root <path>]")
    click.echo(f"elapsed: {_format_elapsed(time.perf_counter() - started_at)}")


@local.group("snapshot", cls=SuggestingGroup, short_help="Create copied local-agent snapshots")
def local_snapshot():
    """Create copied snapshots from explicit local roots."""


@local_snapshot.command("create", short_help="Copy local-agent roots into a snapshot directory")
@click.option("--config", "config_path", default=None, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Agent runtime-config YAML (the agent declares what to copy). Primary path. Mutually exclusive with --from/--target.")
@click.option("--from", "discovery_path", default=None, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Discovery JSON manifest from `assert-ai local discover`. Use with --target.")
@click.option("--target", default=None, help="Agent ID from the discovery manifest. Use with --from.")
@click.option("--include-root", "include_roots", multiple=True, help="Extra root to include (discovery path). Destination is derived from the folder name. Repeat for multiple roots.")
@click.option("--copy-root", "copy_roots", multiple=True, help="Advanced root mapping as SOURCE:DEST (discovery path). Repeat for multiple roots.")
@click.option("--output-dir", default=None, type=click.Path(path_type=Path), help="Directory where the snapshot and manifest will be written. Defaults to artifacts/local-agents/snapshots/.")
@click.option("--show-paths", is_flag=True, help="Write local absolute paths in the manifest instead of redacted placeholders.")
def local_snapshot_create(
    config_path: Path | None,
    discovery_path: Path | None,
    target: str | None,
    include_roots: tuple[str, ...],
    copy_roots: tuple[str, ...],
    output_dir: Path | None,
    show_paths: bool,
):
    """Copy user-approved roots into a reviewable local-agent snapshot.

    Two modes:
    - --config <agent.yaml>: the agent declares its own roots (primary path).
    - --from <discovery.json> --target <id>: discovery-driven (legacy/advanced).
    """
    from assert_ai.local_snapshots import create_local_agent_snapshot, create_snapshot_from_config

    if config_path and (discovery_path or target):
        _error("use either --config or --from/--target, not both")
        return
    if not config_path and not (discovery_path and target):
        _error("provide --config <agent.yaml>, or --from <discovery.json> with --target <id>")
        return

    started_at = time.perf_counter()
    try:
        if config_path:
            from assert_ai.local_agent_config import load_agent_config

            config = load_agent_config(config_path)
            resolved_target = config.id
            resolved_output_dir = output_dir or _local_snapshot_output_dir(target=resolved_target, discovery_path=config_path)
            result = create_snapshot_from_config(
                config=config,
                output_dir=resolved_output_dir,
                redact_paths=not show_paths,
            )
        else:
            assert discovery_path is not None and target is not None
            resolved_output_dir = output_dir or _local_snapshot_output_dir(target=target, discovery_path=discovery_path)
            result = create_local_agent_snapshot(
                discovery_path=discovery_path,
                target=target,
                copy_root_specs=copy_roots,
                include_root_specs=include_roots,
                output_dir=resolved_output_dir,
                redact_paths=not show_paths,
            )
    except ValueError as exc:
        _error(str(exc))
        return

    manifest = result.manifest
    copied_count = len(manifest.get("copied_roots") or [])
    excluded_count = len(manifest.get("excluded_files") or [])
    files_copied = sum(int(root.get("files_copied") or 0) for root in manifest.get("copied_roots") or [])
    click.echo("Created local-agent snapshot")
    click.echo(f"  target: {manifest.get('target')}")
    click.echo(f"  snapshot root: {result.snapshot_root}")
    click.echo(f"  manifest: {result.manifest_path}")
    click.echo(f"  copied roots: {copied_count}")
    click.echo(f"  files copied: {files_copied}")
    click.echo(f"  excluded files: {excluded_count}")
    click.echo(f"  elapsed: {_format_elapsed(time.perf_counter() - started_at)}")


@local.group("spec", cls=SuggestingGroup, short_help="Build ASSERT specs from sandbox snapshots")
def local_spec():
    """Build ASSERT eval specs from copied local-agent sandbox state."""


@local_spec.command("build", short_help="Build ASSERT config from sandbox state")
@click.option("--sandbox-state", "state_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Sandbox state JSON from `assert-ai local sandbox start`.")
@click.option("--include", "include_patterns", multiple=True, help="Extra sandbox-relative glob to include as behavior/context source. Repeatable.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None, help="Directory for agent-spec.json, agent-spec.md, and eval_config.yaml. Defaults next to the sandbox state.")
def local_spec_build(state_path: Path, include_patterns: tuple[str, ...], output_dir: Path | None):
    """Build a normal ASSERT eval config from the copied sandbox snapshot."""
    from assert_ai.local_specs import build_local_agent_spec

    started_at = time.perf_counter()
    resolved_output_dir = output_dir or (state_path.parent / "spec")
    try:
        result = build_local_agent_spec(
            state_path=state_path,
            output_dir=resolved_output_dir,
            include=list(include_patterns),
        )
    except ValueError as exc:
        _error(str(exc))
        return

    click.echo("Built local-agent ASSERT spec")
    click.echo(f"  sources: {result.source_count}")
    click.echo(f"  spec: {result.spec_json_path}")
    click.echo(f"  summary: {result.spec_markdown_path}")
    click.echo(f"  config: {result.eval_config_path}")
    click.echo(f"  elapsed: {_format_elapsed(time.perf_counter() - started_at)}")
    click.echo("Next:")
    click.echo(f"  assert-ai run --config {result.eval_config_path}")


@local.group("sandbox", cls=SuggestingGroup, short_help="Start local-agent sandbox endpoints")
def local_sandbox():
    """Start sandboxed local-agent endpoints from copied snapshots."""


@local_sandbox.command("start", short_help="Start a sandbox endpoint from a snapshot")
@click.option("--snapshot", "snapshot_manifest", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Snapshot manifest from `assert-ai local snapshot create`.")
@click.option("--config", "agent_config_path", default=None, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Agent runtime-config YAML (the agent's self-report). Primary generic path: derives target, launch, endpoint, and routing from this one file.")
@click.option("--target", default=None, help="Agent ID expected in the snapshot manifest. Derived from --config when omitted.")
@click.option("--runtime-config", "runtime_config_path", default=None, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Low-level runtime launch-config YAML. Advanced; prefer --config.", hidden=True)
@click.option("--backend", type=click.Choice(["docker", "docker-run", "command"], case_sensitive=False), default="docker", show_default=True, help="Sandbox backend to use. Advanced option.", hidden=True)
@click.option("--command", "command_text", default=None, help="Debug backend command. Required only for --backend command. If URL templates use {port}, print the port on the first stdout line.", hidden=True)
@click.option("--endpoint-url", default="http://127.0.0.1:18081", show_default=True, help="Endpoint URL or URL template. Use {port} when a command backend prints a dynamic port.", hidden=True)
@click.option("--health-url", default=None, help="Optional health URL or URL template to wait for before writing state.", hidden=True)
@click.option("--rampart-root", default=None, type=click.Path(path_type=Path), help="Path to the existing OpenClaw Docker sandbox runner. Advanced option.", hidden=True)
@click.option("--runner-root", default=None, type=click.Path(path_type=Path), help="Path to ASSERT local sandbox helper scripts. Advanced option.", hidden=True)
@click.option("--sandbox-name", default=None, type=str, help="Name for the local sandbox instance. Advanced option.", hidden=True)
@click.option("--provider", type=click.Choice(["mock", "copilot"], case_sensitive=False), default="mock", show_default=True, help="Model provider for the sandboxed runtime.")
@click.option("--provider-route", type=click.Choice(["copilot"], case_sensitive=False), default="copilot", show_default=True, help="Live provider route to generate. Advanced option.", hidden=True)
@click.option("--model-ref", default=None, help="Provider/model mapping for the sandboxed runtime. Advanced option.", hidden=True)
@click.option("--endpoint-port", default=18081, show_default=True, type=int, help="Loopback port for the sandbox endpoint bridge when using --backend docker. Advanced option.", hidden=True)
@click.option("--auth-proxy-port", default=12435, show_default=True, type=int, help="Loopback auth proxy port when using --backend docker. Advanced option.", hidden=True)
@click.option("--mock-openai-port", default=18080, show_default=True, type=int, help="Loopback mock OpenAI provider port when using --backend docker --provider mock. Advanced option.", hidden=True)
@click.option("--docker-command", default="docker.exe", show_default=True, help="Docker CLI command for Docker Sandbox operations. Advanced option.", hidden=True)
@click.option("--auth-proxy-config", default=None, type=click.Path(path_type=Path), help="Auth proxy config for --provider live. Advanced option.", hidden=True)
@click.option("--skip-build", is_flag=True, help="Skip Docker Sandbox build/install phase when reusing an existing sandbox. Advanced option.", hidden=True)
@click.option("--dry-run", is_flag=True, help="Prepare state/config and show the sandbox plan without launching Docker.")
@click.option("--protocol", type=click.Choice(["assert", "openai_chat"], case_sensitive=False), default="assert", show_default=True, help="Endpoint protocol for the generated ASSERT target config. Advanced option.", hidden=True)
@click.option("--model", default=None, help="Model name for the sandboxed target runtime.")
@click.option("--api-key-env", default=None, help="Environment variable name containing endpoint auth; the value is not written to artifacts. Advanced option.", hidden=True)
@click.option("--stream/--no-stream", default=False, show_default=True, help="Whether the generated endpoint target config should request streaming. Advanced option.", hidden=True)
@click.option("--output-dir", default=None, type=click.Path(path_type=Path), help="Directory where sandbox state, staged snapshot, logs, and target config will be written. Advanced option.", hidden=True)
@click.option("--show-paths", is_flag=True, help="Write local absolute paths in state instead of redacted placeholders. Advanced option.", hidden=True)
def local_sandbox_start(
    snapshot_manifest: Path,
    agent_config_path: Path | None,
    target: str | None,
    runtime_config_path: Path | None,
    backend: str,
    command_text: str | None,
    endpoint_url: str,
    health_url: str | None,
    rampart_root: Path | None,
    runner_root: Path | None,
    sandbox_name: str | None,
    provider: str,
    provider_route: str,
    model_ref: str | None,
    endpoint_port: int,
    auth_proxy_port: int,
    mock_openai_port: int,
    docker_command: str,
    auth_proxy_config: Path | None,
    skip_build: bool,
    dry_run: bool,
    protocol: str,
    model: str | None,
    api_key_env: str | None,
    stream: bool,
    output_dir: Path | None,
    show_paths: bool,
):
    """Stage a copied snapshot and start a local sandbox endpoint."""
    from assert_ai.local_sandbox import (
        DockerSandboxBackend,
        build_descriptor_from_runtime_config,
        build_runtime_config_from_agent_config,
        identity_mounts_for_runtime_command,
        load_runtime_config,
        start_local_sandbox,
        start_openclaw_docker_sandbox,
        start_plain_docker_sandbox,
    )

    started_at = time.perf_counter()

    # --config is the primary generic path: the agent's self-report drives
    # target, launch, endpoint, and routing. Derive everything from it up front.
    agent_config = None
    if agent_config_path is not None:
        if runtime_config_path is not None:
            _error("use either --config or --runtime-config, not both")
            return
        from assert_ai.local_agent_config import load_agent_config

        try:
            agent_config = load_agent_config(agent_config_path)
        except ValueError as exc:
            _error(str(exc))
            return
        if target is None:
            target = agent_config.id
        if agent_config.endpoint is not None:
            if agent_config.endpoint.protocol:
                protocol = agent_config.endpoint.protocol
            if agent_config.endpoint.model and model is None:
                model = agent_config.endpoint.model
            ep_port = agent_config.endpoint.port
            if endpoint_url == "http://127.0.0.1:18081":
                if backend == "docker-run":
                    parsed = urlparse(agent_config.endpoint.url or "")
                    endpoint_url = f"http://127.0.0.1:{endpoint_port}{parsed.path or ''}"
                elif ep_port:
                    endpoint_url = f"http://127.0.0.1:{ep_port}"
        if agent_config.model_routing and agent_config.model_routing.resolved_provider and provider == "mock":
            provider = "copilot"

    if target is None:
        _error("provide --config <agent.yaml> or --target <id>")
        return

    run_dir = output_dir or _local_sandbox_run_dir(target=target, snapshot_manifest=snapshot_manifest)
    generated_sandbox_name = sandbox_name or _local_sandbox_name(target=target, snapshot_manifest=snapshot_manifest)

    try:
        if backend == "command":
            if not command_text:
                raise ValueError("--command is required when --backend command")
            result = start_local_sandbox(
                snapshot_manifest_path=snapshot_manifest,
                target=target,
                backend=backend,
                command=command_text,
                endpoint_url=endpoint_url,
                health_url=health_url,
                protocol=protocol,
                model=model,
                api_key_env=api_key_env,
                stream=stream,
                output_dir=run_dir,
                redact_paths=not show_paths,
            )
            prepared_only = False
        elif backend == "docker-run":
            if agent_config is None:
                raise ValueError("--backend docker-run requires --config")
            runtime_config = build_runtime_config_from_agent_config(agent_config)
            if not runtime_config.runtime_command:
                raise ValueError("agent config must include launch.command for --backend docker-run")
            if not runtime_config.identity_staging:
                raise ValueError("agent config must declare roots for --backend docker-run")
            runtime_port = agent_config.endpoint.port if agent_config.endpoint and agent_config.endpoint.port else runtime_config.endpoint_port
            endpoint_auth_env_name = f"ASSERT_LOCAL_AGENT_ENDPOINT_TOKEN_{_local_sandbox_name(target=target, snapshot_manifest=snapshot_manifest).replace('-', '_').upper()}"
            # docker-run is bound to localhost for local dogfood. This is a
            # non-secret endpoint guard value; provider credentials stay behind
            # the host-side auth proxy.
            endpoint_auth_value = "assert-local-dev"
            os.environ[endpoint_auth_env_name] = endpoint_auth_value
            endpoint_auth_env_file = run_dir / "endpoint_auth.env"
            endpoint_auth_env_file.parent.mkdir(parents=True, exist_ok=True)
            endpoint_auth_env_file.write_text(f"export {endpoint_auth_env_name}={endpoint_auth_value}\n", encoding="utf-8")
            endpoint_auth_env_file.chmod(0o600)
            docker_container_env = {
                "API_SERVER_ENABLED": "true",
                "API_SERVER_HOST": "0.0.0.0",
                "API_SERVER_PORT": str(runtime_port),
                "API_SERVER_KEY": endpoint_auth_value,
            }
            for entry in runtime_config.identity_staging:
                container_path = entry.get("container_path", "")
                if Path(container_path).name == ".hermes":
                    docker_container_env.setdefault("HERMES_HOME", container_path)
                if Path(container_path).name == ".openclaw":
                    docker_container_env.setdefault("OPENCLAW_HOME", str(Path(container_path).parent))
            endpoint_host_port = endpoint_port
            try:
                parsed_endpoint = urlparse(endpoint_url)
                if parsed_endpoint.port:
                    endpoint_host_port = parsed_endpoint.port
            except ValueError:
                pass
            result = start_plain_docker_sandbox(
                snapshot_manifest_path=snapshot_manifest,
                target=target,
                runtime_command=runtime_config.runtime_command,
                identity_staging=runtime_config.identity_staging,
                endpoint_url=endpoint_url,
                runtime_port=runtime_port,
                host_port=endpoint_host_port,
                health_url=health_url or f"http://127.0.0.1:{endpoint_host_port}/health",
                model_routing=runtime_config.model_routing,
                auth_proxy_port=auth_proxy_port,
                provider_route=runtime_config.provider_route or "copilot",
                extra_mounts=identity_mounts_for_runtime_command(runtime_config.runtime_command),
                container_env=docker_container_env,
                protocol=protocol,
                model=model,
                api_key_env=endpoint_auth_env_name,
                api_key_env_file=endpoint_auth_env_file,
                output_dir=run_dir,
                redact_paths=not show_paths,
            )
            prepared_only = False
        else:
            if agent_config is not None or runtime_config_path is not None:
                from dataclasses import replace

                if agent_config is not None:
                    runtime_config = build_runtime_config_from_agent_config(agent_config)
                    if rampart_root is not None:
                        runtime_config = replace(runtime_config, rampart_root=str(rampart_root))
                else:
                    assert runtime_config_path is not None
                    runtime_config = load_runtime_config(runtime_config_path)
                descriptor = build_descriptor_from_runtime_config(runtime_config)
                config_endpoint_url = endpoint_url
                if endpoint_url == "http://127.0.0.1:18081":
                    config_endpoint_url = f"http://127.0.0.1:{runtime_config.endpoint_port}"
                docker_provider = "live" if provider.lower() == "copilot" else provider.lower()
                result = DockerSandboxBackend(descriptor=descriptor).start(
                    snapshot_manifest_path=snapshot_manifest,
                    target=target,
                    output_dir=run_dir,
                    endpoint_url=config_endpoint_url,
                    provider=docker_provider,
                    protocol=protocol,
                    model=model if protocol == "openai_chat" else None,
                    api_key_env=api_key_env,
                    stream=stream,
                    redact_paths=not show_paths,
                    dry_run=dry_run,
                )
                prepared_only = dry_run
                endpoint_url = config_endpoint_url
            else:
                requested_provider = provider.lower()
                docker_provider = "live" if requested_provider == "copilot" else requested_provider
                docker_provider_route = "copilot" if requested_provider == "copilot" else provider_route
                docker_model_ref = model_ref
                if docker_provider == "live" and docker_model_ref is None:
                    if model is None:
                        raise ValueError("--model is required when --provider copilot")
                    docker_model_ref = _local_sandbox_model_ref(provider_route=docker_provider_route, model=model)
                if docker_provider == "mock" and docker_model_ref is None:
                    docker_model_ref = "openai/mock-model=Mock Model"
                if docker_model_ref is None:
                    raise ValueError("--model-ref is required for the selected provider")
                endpoint_model = model if protocol == "openai_chat" else None
                result = start_openclaw_docker_sandbox(
                    snapshot_manifest_path=snapshot_manifest,
                    target=target,
                    output_dir=run_dir,
                    runner_root=runner_root,
                    rampart_root=rampart_root,
                    sandbox_name=generated_sandbox_name,
                    provider=docker_provider,
                    provider_route=docker_provider_route,
                    model_ref=docker_model_ref,
                    endpoint_port=endpoint_port,
                    auth_proxy_port=auth_proxy_port,
                    mock_openai_port=mock_openai_port,
                    docker_command=docker_command,
                    auth_proxy_config=auth_proxy_config,
                    skip_build=skip_build,
                    protocol=protocol,
                    model=endpoint_model,
                    api_key_env=api_key_env,
                    stream=stream,
                    redact_paths=not show_paths,
                    dry_run=dry_run,
                )
                prepared_only = dry_run
    except ValueError as exc:
        _error(str(exc))
        return

    click.echo("Prepared local-agent sandbox" if prepared_only else "Started local-agent sandbox")
    click.echo(f"  target: {target}")
    click.echo(f"  backend: {backend}")
    click.echo(f"  endpoint: {result.endpoint_url}")
    click.echo(f"  state: {result.state_path}")
    click.echo(f"  target config: {result.config_path}")
    if result.process is not None:
        click.echo(f"  pid: {result.process.pid}")
    click.echo(f"  elapsed: {_format_elapsed(time.perf_counter() - started_at)}")


@local_sandbox.command("smoke", short_help="Smoke test a started sandbox endpoint")
@click.option("--state", "state_path", default=None, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Sandbox state JSON from `assert-ai local sandbox start`. Defaults to the single running sandbox.")
@click.option(
    "--message",
    default=None,
    help=(
        "Smoke-test message to send. If omitted for an OpenClaw sandbox, runs a configured-workspace smoke "
        "that surfaces first-run/stock setup failures."
    ),
)
@click.option("--timeout", "timeout_seconds", default=240.0, show_default=True, type=float, help="HTTP timeout in seconds for the smoke request.")
def local_sandbox_smoke(state_path: Path | None, message: str | None, timeout_seconds: float):
    """Send a POST to a started sandbox endpoint."""
    from assert_ai.local_sandbox import find_running_sandbox_state, smoke_local_sandbox

    started_at = time.perf_counter()
    try:
        resolved_state = state_path or find_running_sandbox_state()
        result = smoke_local_sandbox(resolved_state, message=message, timeout_seconds=timeout_seconds)
    except ValueError as exc:
        _error(str(exc))
        return

    raw_events = result.get("events")
    events = raw_events if isinstance(raw_events, list) else []
    click.echo(f"Sandbox smoke: {result.get('status')}")
    click.echo(f"  endpoint: {result.get('agent_endpoint')}")
    click.echo(f"  response: {result.get('response')}")
    raw_check = result.get("configured_workspace_check")
    configured_workspace_check = raw_check if isinstance(raw_check, dict) else {}
    if configured_workspace_check:
        click.echo(f"  configured workspace: {configured_workspace_check.get('status')}")
        failure_signals = configured_workspace_check.get("failure_signals")
        if isinstance(failure_signals, list) and failure_signals:
            click.echo(f"  failure signals: {', '.join(str(item) for item in failure_signals)}")
    raw_metadata = result.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    provider_value = metadata.get("provider")
    model_value = metadata.get("model")
    if provider_value:
        click.echo(f"  provider: {provider_value}")
    if model_value:
        click.echo(f"  model: {model_value}")
    click.echo(f"  events: {len(events)}")
    click.echo(f"  elapsed: {_format_elapsed(time.perf_counter() - started_at)}")
    if result.get("status") != "ok":
        reason = "configured workspace check failed" if configured_workspace_check.get("status") == "failed" else "sandbox smoke failed"
        _error(reason)


@local_sandbox.command("stop", short_help="Stop a started sandbox endpoint")
@click.option("--state", "state_path", default=None, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Sandbox state JSON from `assert-ai local sandbox start`. Defaults to the single running sandbox.")
@click.option("--timeout", "timeout_seconds", default=5.0, show_default=True, type=float, help="Seconds to wait before force-killing sandbox processes.")
def local_sandbox_stop(state_path: Path | None, timeout_seconds: float):
    """Terminate processes recorded in a sandbox state file."""
    from assert_ai.local_sandbox import find_running_sandbox_state, stop_local_sandbox

    started_at = time.perf_counter()
    try:
        resolved_state = state_path or find_running_sandbox_state()
        result = stop_local_sandbox(resolved_state, timeout_seconds=timeout_seconds)
    except ValueError as exc:
        _error(str(exc))
        return

    raw_processes = result.get("processes")
    processes = raw_processes if isinstance(raw_processes, list) else []
    click.echo("Stopped local-agent sandbox")
    click.echo(f"  state: {resolved_state}")
    click.echo(f"  processes: {len(processes)}")
    click.echo(f"  elapsed: {_format_elapsed(time.perf_counter() - started_at)}")


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
    started_at = time.perf_counter()
    rc = runner.run_pipeline(
        config=str(config),
        force_stages=list(force_stage),
        strict=strict,
        overrides=list(overrides),
        concurrency=concurrency,
    )
    click.echo(f"elapsed: {_format_elapsed(time.perf_counter() - started_at)}")
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


@cli.group(cls=SuggestingGroup, short_help="Generate and validate ACS policies from ASSERT findings")
def acs():
    """Generate deployable ACS policies from ASSERT findings.

    Runtime guarding is available from Python via the ``guard_target(...)`` API.
    """


@acs.command("generate", short_help="Generate a deployable ACS policy from an ASSERT run")
@click.option(
    "--run-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Run directory containing scores.jsonl.",
)
@click.option("--suite", default=None, shell_complete=_complete_suite, help="Suite ID under artifacts/results.")
@click.option("--run", "run_id", default=None, shell_complete=_complete_run, help="Run ID under the selected suite.")
@click.option(
    "--out",
    "out_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory for the generated ACS policy.",
)
@click.option("--min-rate", type=click.FloatRange(min=0.0, max=1.0), default=0.0, show_default=True, help="Minimum violation rate to include.")
@click.option("--min-count", type=click.IntRange(min=0), default=1, show_default=True, help="Minimum scored cases to include.")
@click.option("--model", default=None, help="Language model ID to pass to the ACS generator.")
@click.option(
    "--lm-kind",
    type=click.Choice(["assert", "openai-compatible"], case_sensitive=False),
    default="assert",
    show_default=True,
    help="Language model adapter for policy generation.",
)
@click.option("--strict/--no-strict", default=False, show_default=True, help="Enable strict ACS artifact validation.")
@click.option("--validate/--no-validate", "run_validation", default=True, show_default=True, help="Validate the generated policy against known-bad findings.")
@click.option("--fail-on-allow", is_flag=True, help="Exit nonzero if validation allows (does not react to) any known-bad example. A warn still counts as reacting.")
@click.option("--require-block", is_flag=True, help="Exit nonzero unless every known-bad example is strongly blocked (deny/escalate). A warn is not a block.")
def acs_generate(
    run_dir: Path | None,
    suite: str | None,
    run_id: str | None,
    out_dir: Path | None,
    min_rate: float,
    min_count: int,
    model: str | None,
    lm_kind: str,
    strict: bool,
    run_validation: bool,
    fail_on_allow: bool,
    require_block: bool,
):
    """Generate a deployable ACS policy from an ASSERT run's findings."""
    resolved_run_dir = _resolve_acs_run_dir(run_dir, suite, run_id)
    load_findings = _load_acs_symbol("load_findings")
    generate_policy = _load_acs_symbol("generate_policy")

    try:
        summary = load_findings(resolved_run_dir, min_rate=min_rate, min_count=min_count)
    except (FileNotFoundError, ValueError) as exc:
        _error(str(exc))

    if not summary.has_findings:
        click.echo("Warning: no ASSERT findings met thresholds; generated policy will be a benign baseline.")

    policy_out_dir = out_dir.expanduser() if out_dir is not None else _default_acs_out_dir(summary)
    try:
        artifacts = generate_policy(
            summary,
            out_dir=policy_out_dir,
            lm_kind=lm_kind,
            model=model,
            strict=strict,
        )
    except ModuleNotFoundError as exc:
        _handle_missing_acs_dependency(exc)
    except ValueError as exc:
        _error(str(exc))

    _print_acs_artifacts(artifacts)

    if run_validation:
        validate_policy = _load_acs_symbol("validate_policy")
        try:
            report = validate_policy(artifacts.manifest_path, summary)
        except ModuleNotFoundError as exc:
            _handle_missing_acs_dependency(exc)
        except (FileNotFoundError, ValueError) as exc:
            _error(str(exc))
        _print_acs_validation_totals(report)
        _print_acs_validation_by_point(report)
        _enforce_acs_validation_gate(report, fail_on_allow=fail_on_allow, require_block=require_block)


@acs.command("validate", short_help="Validate an ACS manifest against an ASSERT run")
@click.option(
    "--manifest",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="ACS manifest.yaml to validate.",
)
@click.option(
    "--run-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Run directory containing scores.jsonl.",
)
@click.option("--suite", default=None, shell_complete=_complete_suite, help="Suite ID under artifacts/results.")
@click.option("--run", "run_id", default=None, shell_complete=_complete_run, help="Run ID under the selected suite.")
@click.option("--min-rate", type=click.FloatRange(min=0.0, max=1.0), default=0.0, show_default=True, help="Minimum violation rate to include.")
@click.option("--min-count", type=click.IntRange(min=0), default=1, show_default=True, help="Minimum scored cases to include.")
@click.option("--max-cases", type=click.IntRange(min=1), default=None, help="Maximum known-bad examples to replay.")
@click.option("--fail-on-allow", is_flag=True, help="Exit nonzero if validation allows (does not react to) any known-bad example. A warn still counts as reacting.")
@click.option("--require-block", is_flag=True, help="Exit nonzero unless every known-bad example is strongly blocked (deny/escalate). A warn is not a block.")
def acs_validate(
    manifest: Path,
    run_dir: Path | None,
    suite: str | None,
    run_id: str | None,
    min_rate: float,
    min_count: int,
    max_cases: int | None,
    fail_on_allow: bool,
    require_block: bool,
):
    """Validate an ACS manifest against an ASSERT run's known-bad findings."""
    resolved_run_dir = _resolve_acs_run_dir(run_dir, suite, run_id)
    load_findings = _load_acs_symbol("load_findings")
    validate_policy = _load_acs_symbol("validate_policy")

    try:
        summary = load_findings(resolved_run_dir, min_rate=min_rate, min_count=min_count)
        report = validate_policy(manifest, summary, max_cases=max_cases)
    except ModuleNotFoundError as exc:
        _handle_missing_acs_dependency(exc)
    except (FileNotFoundError, ValueError) as exc:
        _error(str(exc))

    _print_acs_validation_cases(report)
    _print_acs_validation_totals(report)
    _enforce_acs_validation_gate(report, fail_on_allow=fail_on_allow, require_block=require_block)


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
        yaml.safe_load(f)

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
