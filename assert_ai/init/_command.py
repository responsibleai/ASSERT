"""Click command definition for ``assert-ai init``."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

log = logging.getLogger(__name__)


@click.command(short_help="Design an eval config with an LLM assistant")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=Path("eval_config.yaml"),
    show_default=True,
    help="Output file path.",
)
@click.option(
    "--describe",
    type=str,
    default=None,
    help="One-line description of the system to evaluate (skips initial question).",
)
@click.option(
    "--from", "seed_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Seed from an existing config (edit/extend mode).",
)
@click.option(
    "--behavior",
    "behavior_preset",
    type=str,
    default=None,
    help="Use a built-in behavior preset name.",
)
@click.option(
    "--judge-preset",
    type=str,
    default=None,
    help="Use a built-in judge preset name.",
)
@click.option(
    "--dimensions",
    type=str,
    default=None,
    help='Hint dimension axes for the LLM to elaborate (e.g. "user_role, language").',
)
@click.option(
    "--model",
    type=str,
    default="azure/gpt-5.4-mini",
    show_default=True,
    help=(
        "Model the design agent itself uses to drive the init conversation "
        "(LiteLLM model string). This is NOT the target model and NOT the "
        "pipeline default_model."
    ),
)
@click.option(
    "--default-model",
    "default_model_hint",
    type=str,
    default=None,
    help=(
        "Pre-seed the proposed config's pipeline default_model (LiteLLM "
        "model string). The design agent will confirm rather than re-ask."
    ),
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path),
    default=Path(".env"),
    show_default=True,
    help="Dotenv file for credentials.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Single-shot mode (no conversation).",
)
@click.option(
    "--max-turns",
    type=int,
    default=30,
    show_default=True,
    help="Maximum conversation turns.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing output file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print YAML to stdout, don't write file.",
)
@click.option("--no-color", is_flag=True, help="Disable colored terminal output.")
def init(
    output: Path,
    describe: str | None,
    seed_path: Path | None,
    behavior_preset: str | None,
    judge_preset: str | None,
    dimensions: str | None,
    model: str,
    default_model_hint: str | None,
    env_file: Path,
    non_interactive: bool,
    max_turns: int,
    force: bool,
    dry_run: bool,
    no_color: bool,
) -> None:
    """Design an eval config interactively with an LLM assistant.

    The assistant asks clarifying questions about your agent/system,
    eval goals, and constraints, then proposes a complete eval.yaml.
    """
    from dotenv import load_dotenv
    from rich.console import Console
    from rich.syntax import Syntax

    from assert_ai.init._design_agent import run_design_loop
    from assert_ai.init._emit import emit_config

    # Load env vars for LLM credentials
    if env_file.exists():
        load_dotenv(env_file, override=False)

    # Refresh the Azure auth mode now that ``.env`` has populated the
    # environment so the init LLM call respects the dotenv-supplied
    # ``ASSERT_AZURE_USE_AAD`` / ``AZURE_API_KEY`` rather than whichever
    # state was frozen at module import.
    from assert_ai.core.azure_auth import log_resolved_azure_auth_mode, refresh_azure_auth_mode

    refresh_azure_auth_mode(force=True)
    # Emit the resolved auth-mode line AFTER refresh so it reflects the
    # value the upcoming LLM call will use, and AFTER the parent ``cli``
    # group has configured logging so ``--quiet``/``--output json`` apply.
    log_resolved_azure_auth_mode()

    # Auto-detect non-interactive when stdin is not a TTY
    if not sys.stdin.isatty():
        non_interactive = True

    if non_interactive and not describe and not seed_path:
        _error("--non-interactive requires --describe or --from")

    if not dry_run and output.exists() and not force:
        _error(f"{output} already exists. Use --force to overwrite.")

    console = Console(highlight=False, color_system=None if no_color else "auto", stderr=True)
    log.info("Starting eval config designer")

    # Load seed config if provided
    seed_yaml: str | None = None
    if seed_path is not None:
        seed_yaml = seed_path.read_text(encoding="utf-8")

    yaml_result = run_design_loop(
        model=model,
        describe=describe,
        seed_yaml=seed_yaml,
        seed_path=seed_path,
        behavior_preset=behavior_preset,
        judge_preset=judge_preset,
        dimension_hints=dimensions,
        default_model_hint=default_model_hint,
        non_interactive=non_interactive,
        max_turns=max_turns,
        console=console,
        no_color=no_color,
    )

    if yaml_result is None:
        log.info("No config generated.")
        raise SystemExit(1)

    if dry_run:
        out_console = Console(highlight=False, color_system=None if no_color else "auto")
        out_console.print(Syntax(yaml_result, "yaml", theme="monokai"))
        return

    emit_config(yaml_result, output, force=force)
    log.info("Written to %s", output)
    log.info("Next: assert-ai run --config %s", output)


def _error(message: str) -> None:
    click.echo(f"[init] error: {message}", err=True)
    raise SystemExit(1)
