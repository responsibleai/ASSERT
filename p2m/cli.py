"""Click CLI for the measurements pipeline (p2m)."""

from __future__ import annotations

from pathlib import Path

import click

from p2m.stages import STAGE_NAMES

CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "auto_envvar_prefix": "P2M",
}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version="0.1.0", prog_name="p2m")
def cli():
    """p2m command line interface."""


@cli.command("run")
@click.option(
    "--config",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to eval.yaml. Auto-discovered in cwd if omitted.",
    show_envvar=True,
)
@click.option(
    "--stage",
    multiple=True,
    type=click.Choice(STAGE_NAMES, case_sensitive=False),
    help="Run only specific stage(s). Repeat for multiple.",
    show_envvar=True,
)
@click.option(
    "--from",
    "from_stage",
    default=None,
    type=click.Choice(STAGE_NAMES, case_sensitive=False),
    help="Run from this stage onward.",
    show_envvar=True,
)
@click.option(
    "--force-stage",
    multiple=True,
    type=click.Choice(STAGE_NAMES, case_sensitive=False),
    help="Force a stage to rerun even if cached.",
    show_envvar=True,
)
@click.option("--resume", is_flag=True, help="Resume an existing run.")
def run(
    config: Path | None,
    stage: tuple[str, ...],
    from_stage: str | None,
    force_stage: tuple[str, ...],
    resume: bool,
):
    """Run the p2m evaluation pipeline."""
    from p2m import runner

    if stage and from_stage is not None:
        click.echo("--stage and --from cannot be used together.", err=True)
        raise SystemExit(1)

    if config is None:
        config = Path("eval.yaml")
        if not config.exists():
            click.echo("No eval.yaml found in current directory. Use --config to specify.", err=True)
            raise SystemExit(1)

    if not config.exists():
        click.echo(f"Config not found: {config}", err=True)
        raise SystemExit(1)

    rc = runner.run_pipeline(
        config=str(config.resolve()),
        force_stages=list(force_stage),
        stage_filter=list(stage) or None,
        from_stage=from_stage,
        resume=resume,
    )
    raise SystemExit(rc)


if __name__ == "__main__":
    cli()
