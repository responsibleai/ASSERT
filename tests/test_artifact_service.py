# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from assert_ai.core.io import write_json
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.artifacts import ArtifactRepository
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.results import ResultRepository
from tests.result_catalog_fixture import create_result_catalog_fixture


def _repository(tmp_path: Path) -> tuple[ArtifactRepository, str, str]:
    workspace = WorkspaceService.create(tmp_path)
    fixture = create_result_catalog_fixture(
        workspace.artifacts_root,
        suite_count=1,
        runs_per_suite=1,
        large_test_case_count=5,
    )
    run_root = (
        workspace.results_root
        / fixture.large_suite_id
        / fixture.large_run_id
    )
    write_json(
        run_root / "manifest.json",
        {
            "status": "completed",
            "authorization": "not-a-real-secret",
            "workspace": str(workspace.root),
        },
    )
    (run_root / "config.yaml").write_text(
        "pipeline: {}\napi_key: not-a-real-secret\n",
        encoding="utf-8",
    )
    (run_root / "binary.bin").write_bytes(b"\x00\x01\x02")

    results = ResultRepository(
        workspace.results_root,
        path_policy=workspace.path_policy,
        default_page_size=2,
        max_page_size=10,
    )
    return (
        ArtifactRepository(
            workspace,
            results,
            default_page_size=2,
            max_page_size=10,
            default_chunk_bytes=16,
            max_chunk_bytes=2048,
        ),
        fixture.large_suite_id,
        fixture.large_run_id,
    )


def test_artifact_catalog_uses_opaque_path_free_ids(tmp_path: Path) -> None:
    repository, suite_id, run_id = _repository(tmp_path)

    page = repository.list_artifacts(suite_id, run_id=run_id)

    assert page.items
    assert page.next_cursor is not None
    assert all(item.artifact_id.startswith("art1_") for item in page.items)
    serialized = json.dumps(page.model_dump(mode="json"))
    assert str(tmp_path) not in serialized
    assert "config.yaml" not in {
        item.artifact_id for item in page.items
    }


def test_artifact_text_chunks_are_redacted_and_resumable(tmp_path: Path) -> None:
    repository, suite_id, run_id = _repository(tmp_path)
    descriptor = repository.find_artifact(
        suite_id,
        "config",
        run_id=run_id,
    )

    chunks: list[str] = []
    offset = 0
    while True:
        chunk = repository.read_artifact_chunk(
            descriptor.artifact_id,
            offset=offset,
            chunk_size=16,
        )
        chunks.append(chunk.data)
        if chunk.eof:
            break
        assert chunk.next_offset is not None
        assert chunk.next_offset > offset
        offset = chunk.next_offset

    content = "".join(chunks)
    assert "not-a-real-secret" not in content
    assert "[REDACTED]" in content
    assert "pipeline" in content
    assert yaml.safe_load(content)["api_key"] == "[REDACTED]"

    manifest = repository.find_artifact(
        suite_id,
        "manifest",
        run_id=run_id,
    )
    manifest_chunk = repository.read_artifact_chunk(
        manifest.artifact_id,
        chunk_size=2048,
    )
    assert json.loads(manifest_chunk.data)["authorization"] == "[REDACTED]"
    assert str(tmp_path) not in manifest_chunk.data


def test_artifact_binary_chunks_are_not_exposed(tmp_path: Path) -> None:
    repository, suite_id, run_id = _repository(tmp_path)
    run_root = repository.workspace.results_root / suite_id / run_id
    summary = json.loads((run_root / "run_summary.json").read_text(encoding="utf-8"))
    summary["sources"]["binary"] = {
        "scope": "run",
        "path": "binary.bin",
    }
    write_json(run_root / "run_summary.json", summary)

    descriptor = repository.find_artifact(
        suite_id,
        "binary",
        run_id=run_id,
    )
    with pytest.raises(ServiceError) as exc_info:
        repository.read_artifact_chunk(descriptor.artifact_id)

    assert exc_info.value.code is ServiceErrorCode.CAPABILITY_DISABLED


def test_oversized_text_artifacts_are_not_quadratically_streamed(
    tmp_path: Path,
) -> None:
    repository, suite_id, run_id = _repository(tmp_path)
    run_root = repository.workspace.results_root / suite_id / run_id
    large_path = run_root / "large.log"
    large_path.write_text("x" * 1025, encoding="utf-8")
    summary = json.loads((run_root / "run_summary.json").read_text(encoding="utf-8"))
    summary["sources"]["large"] = {
        "scope": "run",
        "path": "large.log",
    }
    write_json(run_root / "run_summary.json", summary)
    repository.max_text_artifact_bytes = 1024

    descriptor = repository.find_artifact(
        suite_id,
        "large",
        run_id=run_id,
    )
    with pytest.raises(ServiceError) as exc_info:
        repository.read_artifact_chunk(descriptor.artifact_id)

    assert exc_info.value.code is ServiceErrorCode.ARTIFACT_TOO_LARGE


def test_artifact_ids_and_cursors_fail_stale_after_change(
    tmp_path: Path,
) -> None:
    repository, suite_id, run_id = _repository(tmp_path)
    page = repository.list_artifacts(
        suite_id,
        run_id=run_id,
        page_size=1,
    )
    assert page.next_cursor is not None
    descriptor = repository.find_artifact(
        suite_id,
        "config",
        run_id=run_id,
    )
    config_path = repository.workspace.results_root / suite_id / run_id / "config.yaml"
    config_path.write_text("pipeline: {}\nchanged: true\n", encoding="utf-8")

    with pytest.raises(ServiceError) as exc_info:
        repository.read_artifact_chunk(descriptor.artifact_id)
    assert exc_info.value.code is ServiceErrorCode.STALE_ETAG

    with pytest.raises(ServiceError) as exc_info:
        repository.list_artifacts(
            suite_id,
            run_id=run_id,
            cursor=page.next_cursor,
            page_size=1,
        )
    assert exc_info.value.code is ServiceErrorCode.STALE_CURSOR


def test_artifact_manifest_cannot_escape_workspace(tmp_path: Path) -> None:
    repository, suite_id, run_id = _repository(tmp_path)
    run_root = repository.workspace.results_root / suite_id / run_id
    summary = json.loads((run_root / "run_summary.json").read_text(encoding="utf-8"))
    summary["sources"]["escape"] = {
        "scope": "run",
        "path": "../../../outside.txt",
    }
    write_json(run_root / "run_summary.json", summary)

    with pytest.raises(ServiceError) as exc_info:
        repository.list_artifacts(suite_id, run_id=run_id)

    assert exc_info.value.code is ServiceErrorCode.WORKSPACE_VIOLATION
