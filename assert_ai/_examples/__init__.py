# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Bundled, pip-installable example evaluations.

These examples ship inside the ``assert-ai`` wheel so that
``assert-ai run --example <name>`` works from a plain ``pip install`` with no
git clone. Each entry maps a short, hyphenated name to a bundled
``eval_config.yaml`` and records the extra(s) required to run it.

The canonical, browsable source for every example also lives under the
repository ``examples/`` tree; the copies here are the runnable subset wired so
their ``target.callable`` paths resolve as installed package modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class BundledExample:
    name: str
    summary: str
    config: str  # path relative to this package
    extras: tuple[str, ...]  # pip extras required to run it ("" = base install)

    @property
    def install_hint(self) -> str:
        if not self.extras:
            return "pip install assert-ai"
        joined = ",".join(self.extras)
        # Quote so the brackets survive zsh / PowerShell globbing.
        return f'pip install "assert-ai[{joined}]"'


# Registry of runnable bundled examples. Keep names hyphenated and stable —
# they are a public CLI surface (`assert-ai run --example <name>`).
EXAMPLES: dict[str, BundledExample] = {
    "health-assistant": BundledExample(
        name="health-assistant",
        summary="Prompt Agent (system prompt only); runs on a base install.",
        config="health_assistant/eval_config.yaml",
        extras=(),
    ),
    "travel-planner-langgraph": BundledExample(
        name="travel-planner-langgraph",
        summary="Multi-tool LangGraph agent with OpenTelemetry trace capture.",
        config="travel_planner_langgraph/eval_config.yaml",
        extras=("langgraph", "otel"),
    ),
}


def resolve_example(name: str) -> Path:
    """Return the on-disk path to a bundled example's config.

    Raises ``KeyError`` if the name is unknown.
    """
    example = EXAMPLES[name]
    # Wheels install unzipped, so the traversable is a real filesystem path.
    return Path(resources.files(__package__).joinpath(example.config))


def available_names() -> list[str]:
    return list(EXAMPLES)
