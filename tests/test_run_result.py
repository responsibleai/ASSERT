# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from assert_ai.core.model_client import LLMInputError
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
