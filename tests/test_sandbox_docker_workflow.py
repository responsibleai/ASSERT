# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "sandbox-docker.yml"


def test_docker_workflow_concurrency_is_scoped_to_the_pull_request() -> None:
    """Equal branch names in different forks must not cancel each other's gate."""
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert workflow["concurrency"]["group"] == (
        "sandbox-docker-${{ github.event.pull_request.number || github.ref }}"
    )
