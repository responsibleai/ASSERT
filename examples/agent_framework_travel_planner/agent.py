# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Bridge to the Microsoft Agent Framework travel-planning workflow.

The workflow itself lives in the Agent Framework repository, next to the
Azure AI evaluation demo it extends:

    agent-framework/python/samples/demos/workflow_evaluation_assert/assert_target.py

That module builds the same seven-agent fan-out/fan-in travel planner used by
``workflow_evaluation`` (hotel / flight / activity search -> booking aggregation ->
booking confirmation -> payment -> coordinator) and exposes ``chat`` as the
ASSERT ``target.callable`` entry point. This file only locates that checkout and
re-exports the entry point, so the workflow has exactly one source of truth.

Setup:
    git clone https://github.com/microsoft/agent-framework
    # then either place it next to this repo, or set:
    export AGENT_FRAMEWORK_REPO=/path/to/agent-framework

Usage:
    assert-ai run --config examples/agent_framework_travel_planner/eval_config.yaml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DEMO_SUBPATH = Path("python") / "samples" / "demos" / "workflow_evaluation_assert"


def _candidate_repos() -> list[Path]:
    """Return plausible agent-framework checkout locations, best guess first."""
    candidates: list[Path] = []
    configured = os.environ.get("AGENT_FRAMEWORK_REPO")
    if configured:
        candidates.append(Path(configured).expanduser())
    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            repo_root.parent / "agent-framework",
            repo_root.parent / "agent_framework",
            repo_root / "agent-framework",
        ]
    )
    return candidates


def _resolve_demo_dir() -> Path:
    for repo in _candidate_repos():
        demo_dir = repo / _DEMO_SUBPATH
        if (demo_dir / "assert_target.py").is_file():
            return demo_dir
    searched = "\n  ".join(str(c / _DEMO_SUBPATH) for c in _candidate_repos())
    raise RuntimeError(
        "Could not find the Agent Framework workflow demo.\n"
        "Clone https://github.com/microsoft/agent-framework and set "
        "AGENT_FRAMEWORK_REPO to the checkout root.\n"
        f"Searched:\n  {searched}"
    )


_DEMO_DIR = _resolve_demo_dir()
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

from assert_target import build_workflow, chat, get_workflow  # noqa: E402,F401

__all__ = ["build_workflow", "chat", "get_workflow"]


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(chat("Plan a 3-day trip to Paris from December 15-18, 2025 under $2000.")))
