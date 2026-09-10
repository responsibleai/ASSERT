# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.result_metadata import (
    suite_run_catalog_identity,
    suite_run_set_identity,
    write_run_summary,
    write_suite_summary,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fixture(root: Path) -> tuple[dict, dict]:
    suite_root = root / "results" / "suite-a"
    run_root = suite_root / "run-a"
    artifact_root = suite_root / "artifacts" / "test_set" / "v0002"
    artifact_root.mkdir(parents=True)
    run_root.mkdir()
    (suite_root / "suite.json").write_text(
        '{"created_at":"2026-08-12T00:00:00+00:00"}',
        encoding="utf-8",
    )
    taxonomy_path = suite_root / "artifacts" / "systematize" / "v0001" / "taxonomy.json"
    taxonomy_path.parent.mkdir(parents=True)
    taxonomy_path.write_text(
        json.dumps(
            {
                "behavior": {"name": "safe_agent"},
                "behavior_categories": [
                    {"name": "Allowed", "permissible": True},
                    {"name": "Blocked", "permissible": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    test_set_path = artifact_root / "test_set.jsonl"
    _write_jsonl(
        test_set_path,
        [
            {"type": "prompt", "test_case_id": "p1"},
            {"type": "scenario", "test_case_id": "s1"},
        ],
    )
    _write_jsonl(
        suite_root / "test_set.jsonl",
        [{"type": "prompt", "test_case_id": "stale"}],
    )
    _write_jsonl(
        run_root / "inference_set.jsonl",
        [
            {"type": "prompt", "test_case_id": "p1"},
            {"type": "scenario", "test_case_id": "s1"},
        ],
    )
    _write_jsonl(
        run_root / "scores.jsonl",
        [
            {
                "type": "prompt",
                "test_case_id": "p1",
                "target": "target-model",
                "judge_model": "judge-model",
                "judge_status": "ok",
                "score_keys": ["policy_violation", "severity"],
                "not_applicable_score_keys": [],
                "verdict": {
                    "dimensions": {
                        "policy_violation": False,
                        "severity": 1,
                    },
                    "node_judgments": [],
                },
                "dimension_scales": {
                    "severity": {
                        "type": "ordinal",
                        "values": [
                            {"value": 1, "label": "low"},
                            {"value": 2, "label": "high"},
                        ],
                    }
                },
            },
            {
                "type": "scenario",
                "test_case_id": "s1",
                "target": "target-model",
                "tester_model": "tester-model",
                "judge_model": "judge-model",
                "judge_status": "judge_failed",
                "verdict": {},
            },
        ],
    )
    target = SimpleNamespace(
        model=SimpleNamespace(name="target-model"),
        connector=None,
        callable=None,
        endpoint=None,
    )
    evaluation = SimpleNamespace(
        tester=SimpleNamespace(model=SimpleNamespace(name="tester-model")),
        judge=SimpleNamespace(model=SimpleNamespace(name="judge-model")),
    )
    ctx = {
        "suite_id": "suite-a",
        "run_id": "run-a",
        "suite_root": suite_root,
        "run_root": run_root,
        "behavior_name": "safe_agent",
        "behavior": "The agent follows policy.",
        "taxonomy_path": taxonomy_path,
        "test_set_path": test_set_path,
        "artifact_versions": {
            "test_set": {
                "version": "v0002",
                "path": "artifacts/test_set/v0002/test_set.jsonl",
            }
        },
        "target": target,
        "evaluation": evaluation,
    }
    manifest = {
        "status": "completed",
        "started_at": "2026-08-12T00:01:00+00:00",
        "ended_at": "2026-08-12T00:02:00+00:00",
        "stages": {"inference": "completed", "judge": "completed"},
        "stage_timings": {
            "judge": {"duration_secs": 1.5},
        },
    }
    return ctx, manifest


def test_run_summary_persists_indexes_metrics_and_ordinal_dimensions() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx, manifest = _fixture(root)

        payload = write_run_summary(
            ctx,
            manifest,
            metrics={"schema_version": 1, "totals": {"calls": 2}},
        )

        assert payload is not None
        assert payload["schema_version"] == 1
        assert payload["state"] == "completed"
        assert payload["current_stage"] == "judge"
        assert payload["models"]["target"]["identifier"] == "target-model"
        assert payload["counts"]["test_set"]["total"] == 2
        assert payload["counts"]["scores"]["scenario"] == 1
        assert (
            payload["quality"]["prompt"]["dimensions"]["severity"]["kind"]
            == "ordinal"
        )
        assert payload["quality"]["scenario"]["judge_failures"] == 1
        assert payload["sources"]["test_set"]["scope"] == "suite"
        assert (
            payload["sources"]["test_set"]["path"]
            == "artifacts/test_set/v0002/test_set.jsonl"
        )
        assert str(root) not in json.dumps(payload)
        assert "prompt_rows" not in payload
        assert "scenario_rows" not in payload
        assert (
            root
            / "results"
            / "suite-a"
            / "run-a"
            / "scores.index.json"
        ).exists()


def test_suite_summary_uses_active_versioned_test_set_and_metadata_only_runs() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx, manifest = _fixture(root)
        write_run_summary(ctx, manifest)

        with patch(
            "assert_ai.services.result_metadata.load_jsonl",
            side_effect=AssertionError("suite catalog must not load score rows"),
        ):
            payload = write_suite_summary(ctx)

        assert payload is not None
        assert payload["schema_version"] == 1
        assert payload["status"] == "has_results"
        assert payload["test_case_counts"] == {
            "total": 2,
            "prompt": 1,
            "scenario": 1,
            "other": 0,
        }
        assert payload["run_count"] == 1
        assert payload["latest_run"]["run_id"] == "run-a"
        assert (
            payload["sources"]["test_set"]["path"]
            == "artifacts/test_set/v0002/test_set.jsonl"
        )
        run_catalog = json.loads(
            (
                root
                / "results"
                / "suite-a"
                / "run_catalog.json"
            ).read_text(encoding="utf-8")
        )
        assert run_catalog["run_catalog_identity"] == payload[
            "run_catalog_identity"
        ]
        assert run_catalog["items"][0]["run_id"] == "run-a"
        assert run_catalog["items"][0]["status"] == "completed"


def test_running_boundary_preserves_last_valid_detail_without_rescanning_rows() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx, manifest = _fixture(root)
        completed = write_run_summary(ctx, manifest)
        assert completed is not None

        running_manifest = {
            **manifest,
            "status": "running",
            "ended_at": None,
            "stages": {"inference": "completed", "judge": "running"},
        }
        with patch(
            "assert_ai.services.result_metadata.load_jsonl",
            side_effect=AssertionError("running boundary must not rescan rows"),
        ):
            running = write_run_summary(
                ctx,
                running_manifest,
                rebuild_indexes=False,
            )

        assert running is not None
        assert running["state"] == "running"
        assert running["current_stage"] == "judge"
        assert running["quality"] == completed["quality"]


def test_endpoint_target_identifier_drops_credentials_path_and_query() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx, manifest = _fixture(root)
        ctx["target"] = SimpleNamespace(
            model=None,
            connector=None,
            callable=None,
            endpoint="https://user:secret@example.test/private?token=secret",
        )

        payload = write_run_summary(ctx, manifest)

        assert payload is not None
        assert payload["models"]["target"] == {
            "kind": "endpoint",
            "identifier": "https://example.test",
        }
        serialized = json.dumps(payload)
        assert "secret" not in serialized
        assert "/private" not in serialized


def test_strict_summary_redacts_managed_paths_from_stage_and_model_metadata() -> None:
    with TemporaryDirectory() as tmp:
        workspace = WorkspaceService.create(tmp)
        workspace.artifacts_root.mkdir()
        ctx, manifest = _fixture(workspace.artifacts_root)
        ctx["path_policy"] = workspace.path_policy
        ctx["target"] = SimpleNamespace(
            model=None,
            connector=None,
            callable=f"{workspace.root}\\agent.py:run",
            endpoint=None,
        )

        payload = write_run_summary(
            ctx,
            manifest,
            stage_summaries={
                "inference": {
                    "debug_path": str(
                        workspace.root / "private" / "trace.json"
                    ),
                }
            },
        )

        assert payload is not None
        serialized = json.dumps(payload)
        assert str(workspace.root) not in serialized
        assert payload["models"]["target"]["identifier"].startswith(".")


def test_run_identities_reuse_directory_metadata_and_preserve_hashes(tmp_path: Path) -> None:
    suite_root = tmp_path / "suite"
    expected_entries = []
    run_names = ["a-run", "B-run"]
    for run_name in sorted(run_names, key=Path):
        run_root = suite_root / run_name
        run_root.mkdir(parents=True)
        files = {}
        for filename in ("run_summary.json", "scores.jsonl"):
            source = run_root / filename
            source.write_text("{}\n", encoding="utf-8")
            identity = source.stat()
            files[filename] = {
                "size_bytes": identity.st_size,
                "mtime_ns": identity.st_mtime_ns,
            }
        expected_entries.append({"run_id": run_name, "files": files})
    for ignored in ("artifacts", ".partial", "empty"):
        directory = suite_root / ignored
        directory.mkdir()
        if ignored != "empty":
            (directory / "run_summary.json").write_text("{}", encoding="utf-8")
    (suite_root / "notes.txt").write_text("not a run", encoding="utf-8")

    expected_catalog_hash = hashlib.sha256(
        json.dumps(
            expected_entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    expected_set_hash = hashlib.sha256(
        json.dumps(sorted(run_names), separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with patch.object(
        Path, "stat", side_effect=AssertionError("identity must reuse directory metadata")
    ):
        assert suite_run_catalog_identity(suite_root) == {
            "run_count": 2,
            "sha256": expected_catalog_hash,
        }
        assert suite_run_set_identity(suite_root) == {
            "run_count": 2,
            "sha256": expected_set_hash,
        }


def test_run_identities_detect_changes_between_calls(tmp_path: Path) -> None:
    suite_root = tmp_path / "suite"
    assert suite_run_set_identity(suite_root)["run_count"] == 0
    assert suite_run_catalog_identity(suite_root)["run_count"] == 0
    run_root = suite_root / "run"
    run_root.mkdir(parents=True)
    source = run_root / "scores.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    original_catalog = suite_run_catalog_identity(suite_root)
    original_set = suite_run_set_identity(suite_root)

    source.write_text('{"changed":true}\n', encoding="utf-8")
    assert suite_run_catalog_identity(suite_root) != original_catalog
    assert suite_run_set_identity(suite_root) == original_set

    source.unlink()
    assert suite_run_catalog_identity(suite_root)["run_count"] == 0
    assert suite_run_set_identity(suite_root)["run_count"] == 0
    source.mkdir()
    assert suite_run_catalog_identity(suite_root)["run_count"] == 0
    assert suite_run_set_identity(suite_root)["run_count"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows filename matching")
def test_run_identities_preserve_case_insensitive_metadata_names(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    source = run_root / "RUN_SUMMARY.JSON"
    source.write_text("{}", encoding="utf-8")

    assert suite_run_catalog_identity(tmp_path)["run_count"] == 1
    assert suite_run_set_identity(tmp_path)["run_count"] == 1
