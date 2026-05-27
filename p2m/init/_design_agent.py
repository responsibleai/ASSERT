"""Design-agent conversation loop (stub — wired in later commits)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


def run_design_loop(
    *,
    model: str,
    describe: str | None,
    seed_yaml: str | None,
    behavior_preset: str | None,
    judge_preset: str | None,
    dimension_hints: str | None,
    non_interactive: bool,
    max_turns: int,
    console: Console,
    no_color: bool,
) -> str | None:
    """Run the multi-turn design conversation. Returns YAML string or None."""
    raise NotImplementedError("Design agent loop not yet implemented (commit 5)")
