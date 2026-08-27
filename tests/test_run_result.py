# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
import yaml

from assert_ai.core.model_client import LLMInputError
from assert_ai.core.run_control import RunCancelled, RunControl
from assert_ai.core.run_result import RunState
from assert_ai.core.workspace import WorkspaceService
from assert_ai.runner import (
    run_pipeline,
    run_pipeline_document_result,
    run_pipeline_result,
)


def test_invalid_config_returns_typed_failure_and_legacy_exit_code() -> None:
    with TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "invalid.yaml"
        config_path.write_text("pipeline: {}\n", encoding="utf-8")

        result = run_pipeline_result(config=str(config_path))

        assert result.state == RunState.FAILED
        assert result.exit_code == 1
        assert result.error_code == "CONFIG_INVALID"
        assert result.failed_stage is None
        assert result.suite_id is None
        assert run_pipeline(config=str(config_path)) == 1


def test_suite_only_success_returns_managed_identity_and_serializes() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "config.yaml"
        results = root / "results"
        config_path.write_text(
            "\n".join(
                [
                    "suite: suite-only",
                    f"results_dir: {results}",
                    "pipeline:",
                    "  inference:",
                    "    enabled: false",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_pipeline_result(config=str(config_path))

        assert result.state == RunState.COMPLETED
        assert result.exit_code == 0
        assert result.suite_id == "suite-only"
        assert result.run_id is None
        assert result.suite_root == (results / "suite-only").resolve()
        assert result.run_root is None
        assert result.to_dict()["suite_root"] == str((results / "suite-only").resolve())


def test_stage_failure_returns_failed_stage_without_raw_exception_text() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "config.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "suite: suite-a",
                    "run: run-a",
                    f"results_dir: {root / 'results'}",
                    "pipeline:",
                    "  inference:",
                    "    target:",
                    "      callable: agent:run",
                    "    test_set_path: fixture.jsonl",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        async def fail_stage(*_: object, **__: object) -> dict:
            raise RuntimeError("sensitive target detail")

        with patch("assert_ai.stages.inference.run", new=fail_stage):
            result = run_pipeline_result(config=str(config_path))

        assert result.state == RunState.FAILED
        assert result.exit_code == 1
        assert result.failed_stage == "inference"
        assert result.error_code == "RUN_FAILED"
        assert result.error_message == "Unexpected error while running inference"
        assert result.run_root == (
            root / "results" / "suite-a" / "run-a"
        ).resolve()
        manifest = json.loads(
            (result.run_root / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "failed"
        assert manifest["stages"]["inference"] == "failed"


def test_classified_stage_error_hides_strict_workspace_root() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace = WorkspaceService.create(root)
        workspace.configs_root.mkdir()
        config_path = workspace.configs_root / "config.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "suite: suite-a",
                    "run: run-a",
                    "pipeline:",
                    "  inference:",
                    "    target:",
                    "      callable: agent:run",
                    "    test_set_path: fixture.jsonl",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        async def fail_stage(*_: object, **__: object) -> dict:
            raise LLMInputError(f"invalid request from {workspace.root}")

        with patch("assert_ai.stages.inference.run", new=fail_stage):
            result = run_pipeline_result(
                config="config.yaml",
                path_policy=workspace.path_policy,
            )

        assert result.state == RunState.FAILED
        assert result.error_message == "invalid request from ."


def test_document_run_preserves_logical_config_base_and_snapshot(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceService.create(tmp_path)
    logical_path = workspace.configs_root / "nested" / "config.yaml"
    document = {
        "suite": "suite-a",
        "run": "run-a",
        "pipeline": {
            "inference": {
                "target": {"callable": "agent:run"},
                "test_set_path": "fixture.jsonl",
            }
        },
    }

    async def complete_stage(*_: object, **__: object) -> dict:
        return {}

    with patch("assert_ai.stages.inference.run", new=complete_stage):
        result = run_pipeline_document_result(
            document=document,
            config_path=str(logical_path),
            path_policy=workspace.path_policy,
        )

    assert result.state is RunState.COMPLETED
    assert result.run_root is not None
    snapshot = yaml.safe_load(
        (result.run_root / "config.yaml").read_text(encoding="utf-8")
    )
    assert snapshot == document


def test_unexpected_setup_failure_is_returned_not_raised() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "config.yaml"
        config_path.write_text(
            "suite: suite-a\npipeline:\n  inference:\n    enabled: false\n",
            encoding="utf-8",
        )

        with patch(
            "assert_ai.runner._write_suite_metadata",
            side_effect=OSError("disk failure"),
        ):
            result = run_pipeline_result(config=str(config_path))

        assert result.state == RunState.FAILED
        assert result.exit_code == 1
        assert result.error_code == "INTERNAL"
        assert result.error_message == "Unexpected pipeline setup error"


def test_cooperative_cancellation_writes_terminal_manifest_and_events(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    results = tmp_path / "results"
    config_path.write_text(
        "\n".join(
            [
                "suite: suite-a",
                "run: run-a",
                f"results_dir: {results}",
                "pipeline:",
                "  inference:",
                "    target:",
                "      callable: agent:run",
                "    test_set_path: fixture.jsonl",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class Observer:
        def __init__(self) -> None:
            self.events: list[tuple[str, object]] = []

        def pipeline_started(self, event: object) -> None:
            self.events.append(("pipeline_started", event))

        def stage_planned(self, event: object) -> None:
            self.events.append(("stage_planned", event))

        def stage_started(self, event: object) -> None:
            self.events.append(("stage_started", event))

        def stage_progress(self, event: object) -> None:
            self.events.append(("stage_progress", event))

        def stage_finished(self, event: object) -> None:
            self.events.append(("stage_finished", event))

        def pipeline_finished(self, event: object) -> None:
            self.events.append(("pipeline_finished", event))

    observer = Observer()
    with patch("assert_ai.stages.inference.run") as stage:
        result = run_pipeline_result(
            config=str(config_path),
            control=RunControl(cancel_requested=lambda: True),
            observer=observer,
        )

    assert result.state is RunState.CANCELLED
    assert result.exit_code == 130
    assert result.failed_stage == "inference"
    stage.assert_not_called()
    manifest = json.loads(
        (results / "suite-a" / "run-a" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "cancelled"
    assert manifest["stages"]["inference"] == "cancelled"
    assert [name for name, _ in observer.events] == [
        "pipeline_started",
        "stage_planned",
        "stage_started",
        "stage_progress",
        "stage_finished",
        "pipeline_finished",
    ]


def test_run_control_acknowledges_cancellation_once() -> None:
    acknowledged: list[str | None] = []
    control = RunControl(
        cancel_requested=lambda: True,
        cancel_acknowledged=acknowledged.append,
    )

    with pytest.raises(RunCancelled):
        control.raise_if_cancelled(stage="inference")
    with pytest.raises(RunCancelled):
        control.raise_if_cancelled(stage="judge")

    assert acknowledged == ["inference"]
