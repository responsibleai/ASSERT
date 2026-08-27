# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Immutable artifact selections consumed by evaluation jobs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from assert_ai.core.artifact_cache import ARTIFACTS_DIR, file_sha256
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.errors import ServiceError, ServiceErrorCode

_MAX_METADATA_BYTES = 1_048_576
_ARTIFACT_FILES = {
    "systematize": {
        "taxonomy": "taxonomy.json",
        "systematization": "systematization.json",
    },
    "test_set": {
        "test_set": "test_set.jsonl",
        "stratification": "stratification.json",
    },
}


class ArtifactPin(BaseModel):
    """Content-bound reference to one immutable suite artifact version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    metadata_sha256: str
    file_hashes: dict[str, str] = Field(default_factory=dict)


def load_artifact_pin(
    workspace: WorkspaceService,
    *,
    suite_id: str,
    stage_name: str,
    version: str,
) -> ArtifactPin:
    """Load and verify every file belonging to one immutable artifact."""

    expected_files = _ARTIFACT_FILES.get(stage_name)
    if expected_files is None:
        raise ValueError(f"unsupported pinned artifact stage: {stage_name}")
    suite_root = workspace.path_policy.resolve_managed_output(
        workspace.results_root / suite_id,
        field_name="pinned artifact suite",
        expected_root=workspace.results_root,
        reject_links=True,
    )
    artifact_dir = workspace.path_policy.resolve_managed_output(
        suite_root / ARTIFACTS_DIR / stage_name / version,
        field_name=f"pinned {stage_name} artifact",
        expected_root=suite_root,
        reject_links=True,
    )
    metadata_path = workspace.path_policy.resolve_managed_output(
        artifact_dir / "artifact.json",
        field_name=f"pinned {stage_name} artifact metadata",
        expected_root=artifact_dir,
        reject_links=True,
    )
    metadata_bytes = _stable_read_metadata(metadata_path)
    try:
        metadata = json.loads(metadata_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _unavailable(stage_name, version, "metadata is invalid") from exc
    if not isinstance(metadata, dict):
        raise _unavailable(stage_name, version, "metadata is invalid")
    if (
        metadata.get("artifact_type") != stage_name
        or metadata.get("version") != version
        or metadata.get("files") != expected_files
        or not isinstance(metadata.get("file_hashes"), dict)
    ):
        raise _unavailable(stage_name, version, "metadata is incomplete")

    actual_hashes: dict[str, str] = {}
    recorded_hashes = metadata["file_hashes"]
    for output_key, filename in expected_files.items():
        output_path = workspace.path_policy.resolve_managed_output(
            artifact_dir / filename,
            field_name=f"pinned {stage_name} artifact file",
            expected_root=artifact_dir,
            reject_links=True,
        )
        actual_hash = _stable_file_sha256(output_path)
        if recorded_hashes.get(output_key) != actual_hash:
            raise _unavailable(
                stage_name,
                version,
                f"{output_key} content does not match metadata",
            )
        actual_hashes[output_key] = actual_hash

    return ArtifactPin(
        version=version,
        metadata_sha256="sha256:" + hashlib.sha256(metadata_bytes).hexdigest(),
        file_hashes=actual_hashes,
    )


def _stable_read_metadata(path: Path) -> bytes:
    try:
        before = path.stat()
        if before.st_size > _MAX_METADATA_BYTES:
            raise ValueError("metadata is too large")
        with path.open("rb") as handle:
            value = handle.read(_MAX_METADATA_BYTES + 1)
        after = path.stat()
    except (OSError, ValueError) as exc:
        raise ServiceError(
            ServiceErrorCode.PREFLIGHT_FAILED,
            "Pinned artifact metadata is unavailable",
        ) from exc
    if (
        len(value) > _MAX_METADATA_BYTES
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise ServiceError(
            ServiceErrorCode.PREFLIGHT_FAILED,
            "Pinned artifact metadata changed while it was being read",
        )
    return value


def _stable_file_sha256(path: Path) -> str:
    try:
        before = path.stat()
        digest = file_sha256(path)
        after = path.stat()
    except OSError as exc:
        raise ServiceError(
            ServiceErrorCode.PREFLIGHT_FAILED,
            "Pinned artifact content is unavailable",
        ) from exc
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise ServiceError(
            ServiceErrorCode.PREFLIGHT_FAILED,
            "Pinned artifact content changed while it was being hashed",
        )
    return digest


def _unavailable(
    stage_name: str,
    version: str,
    reason: str,
) -> ServiceError:
    return ServiceError(
        ServiceErrorCode.PREFLIGHT_FAILED,
        f"Artifact {stage_name} {version} cannot be pinned because {reason}",
    )
