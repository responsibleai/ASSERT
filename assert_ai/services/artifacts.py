# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Manifest-backed, opaque access to managed ASSERT artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from assert_ai.core.security import redact_path_prefixes, sanitize_text
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.results import ResultRepository

_ARTIFACT_ID_PREFIX = "art1_"
_ARTIFACT_ID_VERSION = 1
_CURSOR_PREFIX = "ac1_"
_CURSOR_VERSION = 1
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_MAX_PAGE_SIZE = 200
_DEFAULT_CHUNK_BYTES = 64 * 1024
_DEFAULT_MAX_CHUNK_BYTES = 256 * 1024
_DEFAULT_MAX_TEXT_LINE_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_METADATA_BYTES = 1024 * 1024


class ArtifactScope(StrEnum):
    """Result scope that owns an artifact reference."""

    SUITE = "suite"
    RUN = "run"


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ArtifactDescriptor(_ArtifactModel):
    """Path-free metadata for one manifest-backed artifact."""

    artifact_id: str
    name: str
    aliases: tuple[str, ...] = ()
    scope: ArtifactScope
    suite_id: str
    run_id: str | None = None
    media_type: str
    size_bytes: int
    modified_at: str
    sha256: str | None = None
    text: bool
    redacted: bool
    resource_uri: str


class ArtifactPage(_ArtifactModel):
    """Bounded page of artifact descriptors."""

    items: tuple[ArtifactDescriptor, ...]
    next_cursor: str | None = None


class ArtifactChunk(_ArtifactModel):
    """One bounded artifact chunk."""

    artifact: ArtifactDescriptor
    offset: int
    next_offset: int | None
    eof: bool
    offset_basis: Literal["redacted_text"]
    encoding: Literal["utf-8"]
    data: str
    bytes_returned: int
    source_size_bytes: int
    view_size_bytes: int | None = None


@dataclass(slots=True)
class _ArtifactCandidate:
    name: str
    path: Path
    scope: ArtifactScope
    suite_id: str
    run_id: str | None
    media_type: str
    text: bool
    size_bytes: int
    mtime_ns: int
    sha256: str | None = None
    aliases: list[str] = field(default_factory=list)


class ArtifactRepository:
    """Resolve only artifacts named by ASSERT summaries and manifests."""

    def __init__(
        self,
        workspace: WorkspaceService,
        results: ResultRepository,
        *,
        default_page_size: int = _DEFAULT_PAGE_SIZE,
        max_page_size: int = _DEFAULT_MAX_PAGE_SIZE,
        default_chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
        max_chunk_bytes: int = _DEFAULT_MAX_CHUNK_BYTES,
        max_text_line_bytes: int = _DEFAULT_MAX_TEXT_LINE_BYTES,
        max_metadata_bytes: int = _DEFAULT_MAX_METADATA_BYTES,
        max_text_artifact_bytes: int = 1024 * 1024,
    ) -> None:
        if default_page_size < 1:
            raise ValueError("default_page_size must be positive")
        if max_page_size < default_page_size:
            raise ValueError("max_page_size must be >= default_page_size")
        if default_chunk_bytes < 1:
            raise ValueError("default_chunk_bytes must be positive")
        if max_chunk_bytes < default_chunk_bytes:
            raise ValueError("max_chunk_bytes must be >= default_chunk_bytes")
        if max_text_line_bytes < max_chunk_bytes:
            raise ValueError("max_text_line_bytes must be >= max_chunk_bytes")
        if max_metadata_bytes < 1:
            raise ValueError("max_metadata_bytes must be positive")
        if max_text_artifact_bytes < max_chunk_bytes:
            raise ValueError(
                "max_text_artifact_bytes must be >= max_chunk_bytes"
            )
        self.workspace = workspace
        self.results = results
        self.default_page_size = default_page_size
        self.max_page_size = max_page_size
        self.default_chunk_bytes = default_chunk_bytes
        self.max_chunk_bytes = max_chunk_bytes
        self.max_text_line_bytes = max_text_line_bytes
        self.max_metadata_bytes = max_metadata_bytes
        self.max_text_artifact_bytes = max_text_artifact_bytes

    def list_artifacts(
        self,
        suite_id: str,
        *,
        run_id: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> ArtifactPage:
        candidates = self._catalog(suite_id, run_id=run_id)
        descriptors = [self._descriptor(candidate) for candidate in candidates]
        identity = _catalog_identity(candidates)
        offset = 0
        if cursor is not None:
            payload = _decode_cursor(cursor)
            if (
                payload.get("suite_id") != suite_id
                or payload.get("run_id") != run_id
                or payload.get("catalog_sha256") != identity
            ):
                raise ServiceError(
                    ServiceErrorCode.STALE_CURSOR,
                    "Artifact cursor no longer matches this catalog",
                )
            offset = int(payload["offset"])
            if offset < 0 or offset > len(descriptors):
                raise ServiceError(
                    ServiceErrorCode.STALE_CURSOR,
                    "Artifact cursor is no longer valid",
                )

        limit = self._page_size(page_size)
        items = tuple(descriptors[offset : offset + limit])
        next_offset = offset + len(items)
        next_cursor = None
        if next_offset < len(descriptors):
            next_cursor = _encode_cursor(
                suite_id=suite_id,
                run_id=run_id,
                catalog_sha256=identity,
                offset=next_offset,
            )
        return ArtifactPage(items=items, next_cursor=next_cursor)

    def get_artifact(self, artifact_id: str) -> ArtifactDescriptor:
        return self._descriptor(self._resolve_artifact_id(artifact_id))

    def find_artifact(
        self,
        suite_id: str,
        name: str,
        *,
        run_id: str | None = None,
    ) -> ArtifactDescriptor:
        for candidate in self._catalog(suite_id, run_id=run_id):
            if name == candidate.name or name in candidate.aliases:
                return self._descriptor(candidate)
        target = f"{suite_id}/{run_id}" if run_id is not None else suite_id
        raise ServiceError(
            ServiceErrorCode.NOT_FOUND,
            f"Artifact not found: {target}/{name}",
        )

    def read_artifact_chunk(
        self,
        artifact_id: str,
        *,
        offset: int = 0,
        chunk_size: int | None = None,
    ) -> ArtifactChunk:
        if offset < 0:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "offset must be non-negative",
            )
        candidate = self._resolve_artifact_id(artifact_id)
        descriptor = self._descriptor(candidate)
        limit = self._chunk_size(chunk_size)
        path = self._revalidate_candidate(candidate)

        if not candidate.text:
            raise ServiceError(
                ServiceErrorCode.CAPABILITY_DISABLED,
                (
                    "Binary artifact reads are disabled because their contents "
                    "cannot be safely redacted"
                ),
            )
        if candidate.size_bytes > self.max_text_artifact_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                (
                    "Text artifact exceeds the generic read limit; "
                    "use the result-specific paginated tools"
                ),
                details={
                    "size_bytes": candidate.size_bytes,
                    "max_text_artifact_bytes": self.max_text_artifact_bytes,
                },
            )
        data, next_offset, eof, view_size = self._read_redacted_text(
            path,
            offset=offset,
            chunk_size=limit,
        )
        self._assert_unchanged(candidate, path)
        return ArtifactChunk(
            artifact=descriptor,
            offset=offset,
            next_offset=next_offset,
            eof=eof,
            offset_basis="redacted_text",
            encoding="utf-8",
            data=data.decode("utf-8"),
            bytes_returned=len(data),
            source_size_bytes=candidate.size_bytes,
            view_size_bytes=view_size,
        )

    def _catalog(
        self,
        suite_id: str,
        *,
        run_id: str | None,
    ) -> list[_ArtifactCandidate]:
        self._validate_identifier(suite_id, "suite_id")
        if run_id is not None:
            self._validate_identifier(run_id, "run_id")
        suite_dir = self._managed_dir(
            self.results.results_root / suite_id,
            expected_root=self.results.results_root,
            field_name="artifact suite",
        )
        if run_id is None:
            summary = self.results.get_suite(suite_id)
            candidates = self._suite_candidates(suite_dir, summary)
        else:
            run_dir = self._managed_dir(
                suite_dir / run_id,
                expected_root=suite_dir,
                field_name="artifact run",
            )
            summary = self.results.load_run_detail(suite_id, run_id)
            candidates = self._run_candidates(
                suite_dir,
                run_dir,
                summary,
            )
        candidates.sort(key=lambda candidate: candidate.name)
        return candidates

    def _suite_candidates(
        self,
        suite_dir: Path,
        summary: dict[str, Any],
    ) -> list[_ArtifactCandidate]:
        candidates: list[_ArtifactCandidate] = []
        self._add_known_file(
            candidates,
            name="suite_summary",
            path=suite_dir / "suite_summary.json",
            expected_root=suite_dir,
            suite_id=suite_dir.name,
        )
        self._add_known_file(
            candidates,
            name="suite_metadata",
            path=suite_dir / "suite.json",
            expected_root=suite_dir,
            suite_id=suite_dir.name,
        )
        self._add_known_file(
            candidates,
            name="latest_artifacts",
            path=suite_dir / "latest.json",
            expected_root=suite_dir,
            suite_id=suite_dir.name,
        )
        self._add_summary_references(
            candidates,
            summary,
            suite_dir=suite_dir,
            run_dir=None,
            suite_id=suite_dir.name,
            run_id=None,
        )
        self._add_active_artifact_versions(
            candidates,
            summary.get("artifact_versions"),
            suite_dir=suite_dir,
            suite_id=suite_dir.name,
        )
        return candidates

    def _run_candidates(
        self,
        suite_dir: Path,
        run_dir: Path,
        summary: dict[str, Any],
    ) -> list[_ArtifactCandidate]:
        candidates: list[_ArtifactCandidate] = []
        for name, filename in (
            ("run_summary", "run_summary.json"),
            ("manifest", "manifest.json"),
            ("config", "config.yaml"),
            ("metrics", "metrics.json"),
            ("artifact_versions", "artifacts.json"),
        ):
            self._add_known_file(
                candidates,
                name=name,
                path=run_dir / filename,
                expected_root=run_dir,
                suite_id=suite_dir.name,
                run_id=run_dir.name,
            )
        self._add_summary_references(
            candidates,
            summary,
            suite_dir=suite_dir,
            run_dir=run_dir,
            suite_id=suite_dir.name,
            run_id=run_dir.name,
        )
        self._add_active_artifact_versions(
            candidates,
            summary.get("artifact_versions"),
            suite_dir=suite_dir,
            suite_id=suite_dir.name,
            run_id=run_dir.name,
        )
        self._add_viewer_artifacts(
            candidates,
            suite_dir=suite_dir,
            run_dir=run_dir,
        )
        return candidates

    def _add_summary_references(
        self,
        candidates: list[_ArtifactCandidate],
        summary: dict[str, Any],
        *,
        suite_dir: Path,
        run_dir: Path | None,
        suite_id: str,
        run_id: str | None,
    ) -> None:
        sources = summary.get("sources")
        if isinstance(sources, dict):
            for name, reference in sorted(sources.items()):
                if not isinstance(name, str) or not isinstance(reference, dict):
                    continue
                path, expected_root = self._reference_path(
                    reference,
                    suite_dir=suite_dir,
                    run_dir=run_dir,
                )
                self._add_known_file(
                    candidates,
                    name=name,
                    path=path,
                    expected_root=expected_root,
                    suite_id=suite_id,
                    run_id=run_id,
                    sha256=_optional_sha256(reference.get("sha256")),
                )
                index_reference = reference.get("index")
                if isinstance(index_reference, dict):
                    index_path, index_root = self._reference_path(
                        index_reference,
                        suite_dir=suite_dir,
                        run_dir=run_dir,
                    )
                    self._add_known_file(
                        candidates,
                        name=f"{name}_index",
                        path=index_path,
                        expected_root=index_root,
                        suite_id=suite_id,
                        run_id=run_id,
                    )

        indexes = summary.get("indexes")
        if isinstance(indexes, dict):
            for name, reference in sorted(indexes.items()):
                if not isinstance(name, str) or not isinstance(reference, dict):
                    continue
                path, expected_root = self._reference_path(
                    reference,
                    suite_dir=suite_dir,
                    run_dir=run_dir,
                )
                self._add_known_file(
                    candidates,
                    name=f"{name}_index",
                    path=path,
                    expected_root=expected_root,
                    suite_id=suite_id,
                    run_id=run_id,
                )

    def _add_active_artifact_versions(
        self,
        candidates: list[_ArtifactCandidate],
        artifact_versions: Any,
        *,
        suite_dir: Path,
        suite_id: str,
        run_id: str | None = None,
    ) -> None:
        if not isinstance(artifact_versions, dict):
            return
        for stage_name, reference in sorted(artifact_versions.items()):
            if not isinstance(stage_name, str) or not isinstance(reference, dict):
                continue
            artifact_dir_raw = reference.get("artifact_dir")
            if not isinstance(artifact_dir_raw, str):
                continue
            artifact_dir = suite_dir / _relative_parts(artifact_dir_raw)
            metadata_raw = reference.get("metadata_path")
            metadata_path = (
                suite_dir / _relative_parts(metadata_raw)
                if isinstance(metadata_raw, str)
                else artifact_dir / "artifact.json"
            )
            self._add_known_file(
                candidates,
                name=f"{stage_name}_artifact_metadata",
                path=metadata_path,
                expected_root=suite_dir,
                suite_id=suite_id,
                run_id=run_id,
            )
            metadata = self._load_metadata(
                self._managed_file(
                    metadata_path,
                    expected_root=suite_dir,
                    field_name=f"{stage_name} artifact metadata",
                    must_exist=False,
                ),
                label=f"{stage_name} artifact metadata",
            )
            files = metadata.get("files") if isinstance(metadata, dict) else None
            hashes = (
                metadata.get("file_hashes")
                if isinstance(metadata, dict)
                else None
            )
            if not isinstance(files, dict):
                primary_raw = reference.get("path")
                if isinstance(primary_raw, str):
                    self._add_known_file(
                        candidates,
                        name=stage_name,
                        path=suite_dir / _relative_parts(primary_raw),
                        expected_root=suite_dir,
                        suite_id=suite_id,
                        run_id=run_id,
                    )
                continue
            for output_name, filename in sorted(files.items()):
                if not isinstance(output_name, str) or not isinstance(filename, str):
                    continue
                self._add_known_file(
                    candidates,
                    name=f"{stage_name}_{output_name}",
                    aliases=(stage_name,) if output_name == stage_name else (),
                    path=artifact_dir / _relative_parts(filename),
                    expected_root=suite_dir,
                    suite_id=suite_id,
                    run_id=run_id,
                    sha256=(
                        _optional_sha256(hashes.get(output_name))
                        if isinstance(hashes, dict)
                        else None
                    ),
                )

    def _add_viewer_artifacts(
        self,
        candidates: list[_ArtifactCandidate],
        *,
        suite_dir: Path,
        run_dir: Path,
    ) -> None:
        manifest_path = run_dir / ".viewer" / "viewer_run_manifest.json"
        self._add_known_file(
            candidates,
            name="viewer_manifest",
            path=manifest_path,
            expected_root=run_dir,
            suite_id=suite_dir.name,
            run_id=run_dir.name,
        )
        safe_manifest_path = self._managed_file(
            manifest_path,
            expected_root=run_dir,
            field_name="viewer manifest",
            must_exist=False,
        )
        manifest = self._load_metadata(
            safe_manifest_path,
            label="viewer manifest",
        )
        if not isinstance(manifest, dict):
            return
        for section_name in ("source_files", "derived_files"):
            section = manifest.get(section_name)
            if not isinstance(section, dict):
                continue
            for artifact_name, reference in sorted(section.items()):
                if not isinstance(artifact_name, str) or not isinstance(reference, dict):
                    continue
                raw_path = reference.get("path")
                if not isinstance(raw_path, str):
                    continue
                self._add_known_file(
                    candidates,
                    name=f"viewer_{Path(artifact_name).stem}",
                    path=run_dir / _relative_parts(raw_path, allow_parent=True),
                    expected_root=suite_dir,
                    suite_id=suite_dir.name,
                    run_id=run_dir.name,
                )

    def _add_known_file(
        self,
        candidates: list[_ArtifactCandidate],
        *,
        name: str,
        path: Path,
        expected_root: Path,
        suite_id: str,
        run_id: str | None = None,
        aliases: tuple[str, ...] = (),
        sha256: str | None = None,
    ) -> None:
        managed = self._managed_file(
            path,
            expected_root=expected_root,
            field_name=f"artifact {name}",
            must_exist=False,
        )
        if not managed.is_file():
            return
        resolved = managed.resolve()
        for candidate in candidates:
            if candidate.path == resolved:
                for alias in (name, *aliases):
                    if alias != candidate.name and alias not in candidate.aliases:
                        candidate.aliases.append(alias)
                if candidate.sha256 is None:
                    candidate.sha256 = sha256
                return
        stat_result = resolved.stat()
        media_type, text = _media_type(resolved)
        candidates.append(
            _ArtifactCandidate(
                name=name,
                aliases=list(aliases),
                path=resolved,
                scope=ArtifactScope.RUN if run_id is not None else ArtifactScope.SUITE,
                suite_id=suite_id,
                run_id=run_id,
                media_type=media_type,
                text=text,
                size_bytes=stat_result.st_size,
                mtime_ns=stat_result.st_mtime_ns,
                sha256=sha256,
            )
        )

    def _reference_path(
        self,
        reference: dict[str, Any],
        *,
        suite_dir: Path,
        run_dir: Path | None,
    ) -> tuple[Path, Path]:
        scope = reference.get("scope")
        raw_path = reference.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Artifact metadata contains an invalid path reference",
            )
        if scope == "run" and run_dir is not None:
            root = run_dir
        elif scope == "suite":
            root = suite_dir
        elif scope == "workspace":
            root = self.workspace.root
        else:
            raise ServiceError(
                ServiceErrorCode.WORKSPACE_VIOLATION,
                "Artifact metadata references an unsupported scope",
            )
        return root / _relative_parts(raw_path), root

    def _resolve_artifact_id(self, artifact_id: str) -> _ArtifactCandidate:
        payload = _decode_artifact_id(artifact_id)
        suite_id = str(payload["suite_id"])
        run_id = payload.get("run_id")
        name = str(payload["name"])
        candidates = self._catalog(
            suite_id,
            run_id=str(run_id) if isinstance(run_id, str) else None,
        )
        candidate = next(
            (item for item in candidates if item.name == name),
            None,
        )
        if candidate is None:
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                "Artifact ID no longer resolves to a managed artifact",
            )
        if (
            candidate.size_bytes != payload.get("size_bytes")
            or candidate.mtime_ns != payload.get("mtime_ns")
        ):
            raise ServiceError(
                ServiceErrorCode.STALE_ETAG,
                "Artifact changed since this ID was issued",
                details={
                    "suite_id": candidate.suite_id,
                    "run_id": candidate.run_id,
                    "name": candidate.name,
                },
            )
        return candidate

    def _descriptor(self, candidate: _ArtifactCandidate) -> ArtifactDescriptor:
        artifact_id = _encode_artifact_id(candidate)
        return ArtifactDescriptor(
            artifact_id=artifact_id,
            name=candidate.name,
            aliases=tuple(sorted(candidate.aliases)),
            scope=candidate.scope,
            suite_id=candidate.suite_id,
            run_id=candidate.run_id,
            media_type=candidate.media_type,
            size_bytes=candidate.size_bytes,
            modified_at=datetime.fromtimestamp(
                candidate.mtime_ns / 1_000_000_000,
                tz=timezone.utc,
            ).isoformat(),
            sha256=candidate.sha256,
            text=candidate.text,
            redacted=candidate.text,
            resource_uri=f"assert://artifact/{artifact_id}",
        )

    def _read_redacted_text(
        self,
        path: Path,
        *,
        offset: int,
        chunk_size: int,
    ) -> tuple[bytes, int | None, bool, int | None]:
        output = bytearray()
        view_position = 0
        exhausted = False
        has_more = False

        with path.open("rb") as handle:
            while True:
                line = handle.readline(self.max_text_line_bytes + 1)
                if not line:
                    exhausted = True
                    break
                if (
                    len(line) > self.max_text_line_bytes
                    and not line.endswith((b"\n", b"\r"))
                ):
                    raise ServiceError(
                        ServiceErrorCode.ARTIFACT_TOO_LARGE,
                        "Text artifact contains a line larger than the configured limit",
                    )
                try:
                    sanitized = self._sanitize_text_line(
                        line.decode("utf-8")
                    ).encode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ServiceError(
                        ServiceErrorCode.RUN_FAILED,
                        "Text artifact is not valid UTF-8",
                    ) from exc
                line_end = view_position + len(sanitized)
                if line_end <= offset:
                    view_position = line_end
                    continue

                start = max(0, offset - view_position)
                if (
                    start < len(sanitized)
                    and start > 0
                    and sanitized[start] & 0xC0 == 0x80
                ):
                    raise ServiceError(
                        ServiceErrorCode.INVALID_ARGUMENT,
                        "offset must align with a UTF-8 character boundary",
                    )
                remaining = chunk_size - len(output)
                piece = _valid_utf8_prefix(sanitized[start : start + remaining])
                output.extend(piece)
                consumed = start + len(piece)
                view_position = line_end
                if consumed < len(sanitized) or len(output) >= chunk_size:
                    has_more = consumed < len(sanitized) or bool(handle.read(1))
                    break

        if offset > view_position and exhausted:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "offset exceeds the redacted artifact view",
            )
        eof = not has_more
        next_offset = None if eof else offset + len(output)
        view_size = view_position if eof else None
        return bytes(output), next_offset, eof, view_size

    def _revalidate_candidate(self, candidate: _ArtifactCandidate) -> Path:
        suite_root = self.workspace.results_root / candidate.suite_id
        run_root = (
            suite_root / candidate.run_id
            if candidate.run_id is not None
            else None
        )
        if run_root is not None and candidate.path.is_relative_to(run_root.resolve()):
            expected_root = run_root
        elif candidate.path.is_relative_to(suite_root.resolve()):
            expected_root = suite_root
        else:
            expected_root = self.workspace.root
        path = self._managed_file(
            candidate.path,
            expected_root=expected_root,
            field_name=f"artifact {candidate.name}",
            must_exist=True,
        )
        self._assert_unchanged(candidate, path)
        return path

    def _sanitize_text_line(self, text: str) -> str:
        return redact_path_prefixes(
            sanitize_text(text),
            (self.workspace.root,),
        )

    def _load_metadata(
        self,
        path: Path,
        *,
        label: str,
    ) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            stat_result = path.stat()
            if stat_result.st_size > self.max_metadata_bytes:
                raise ServiceError(
                    ServiceErrorCode.ARTIFACT_TOO_LARGE,
                    f"{label.title()} exceeds the configured metadata limit",
                )
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ServiceError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                f"{label.title()} is not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                f"{label.title()} must contain a JSON object",
            )
        return payload

    @staticmethod
    def _assert_unchanged(candidate: _ArtifactCandidate, path: Path) -> None:
        stat_result = path.stat()
        if (
            stat_result.st_size != candidate.size_bytes
            or stat_result.st_mtime_ns != candidate.mtime_ns
        ):
            raise ServiceError(
                ServiceErrorCode.STALE_ETAG,
                "Artifact changed while it was being read",
            )

    def _managed_dir(
        self,
        path: Path,
        *,
        expected_root: Path,
        field_name: str,
    ) -> Path:
        try:
            managed = self.workspace.path_policy.resolve_managed_output(
                path,
                field_name=field_name,
                expected_root=expected_root,
                reject_links=True,
            )
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.WORKSPACE_VIOLATION,
                str(exc),
            ) from exc
        if not managed.is_dir():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"{field_name.title()} not found",
            )
        return managed

    def _managed_file(
        self,
        path: Path,
        *,
        expected_root: Path,
        field_name: str,
        must_exist: bool,
    ) -> Path:
        try:
            managed = self.workspace.path_policy.resolve_managed_output(
                path,
                field_name=field_name,
                expected_root=expected_root,
                reject_links=True,
            )
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.WORKSPACE_VIOLATION,
                str(exc),
            ) from exc
        if must_exist and not managed.is_file():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Artifact not found: {field_name}",
            )
        return managed

    def _page_size(self, requested: int | None) -> int:
        if requested is None:
            return self.default_page_size
        if requested < 1 or requested > self.max_page_size:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"page_size must be between 1 and {self.max_page_size}",
            )
        return requested

    def _chunk_size(self, requested: int | None) -> int:
        if requested is None:
            return self.default_chunk_bytes
        if requested < 4 or requested > self.max_chunk_bytes:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"chunk_size must be between 4 and {self.max_chunk_bytes}",
            )
        return requested

    @staticmethod
    def _validate_identifier(value: str, field_name: str) -> None:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"{field_name} contains unsupported characters",
            )


def _media_type(path: Path) -> tuple[str, bool]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "application/x-ndjson", True
    if suffix in {".yaml", ".yml"}:
        return "application/yaml", True
    guessed, _ = mimetypes.guess_type(path.name)
    media_type = guessed or "application/octet-stream"
    text = (
        media_type.startswith("text/")
        or media_type in {"application/json", "application/xml"}
        or suffix in {".md", ".log", ".txt"}
    )
    return media_type, text


def _relative_parts(value: str, *, allow_parent: bool = False) -> Path:
    if not value or "\x00" in value:
        raise ServiceError(
            ServiceErrorCode.WORKSPACE_VIOLATION,
            "Artifact metadata contains an invalid relative path",
        )
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute():
        raise ServiceError(
            ServiceErrorCode.WORKSPACE_VIOLATION,
            "Artifact metadata contains an absolute path",
        )
    if not allow_parent and any(part == ".." for part in path.parts):
        raise ServiceError(
            ServiceErrorCode.WORKSPACE_VIOLATION,
            "Artifact metadata contains parent traversal",
        )
    return path


def _optional_sha256(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return None
    return value.lower()


def _valid_utf8_prefix(data: bytes) -> bytes:
    if not data:
        return data
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError as exc:
        if exc.reason == "unexpected end of data":
            return data[: exc.start]
        raise ServiceError(
            ServiceErrorCode.RUN_FAILED,
            "Text artifact is not valid UTF-8",
        ) from exc


def _encode_artifact_id(candidate: _ArtifactCandidate) -> str:
    payload = json.dumps(
        {
            "v": _ARTIFACT_ID_VERSION,
            "suite_id": candidate.suite_id,
            "run_id": candidate.run_id,
            "name": candidate.name,
            "size_bytes": candidate.size_bytes,
            "mtime_ns": candidate.mtime_ns,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_ARTIFACT_ID_PREFIX}{token}"


def _decode_artifact_id(artifact_id: str) -> dict[str, Any]:
    if (
        not artifact_id.startswith(_ARTIFACT_ID_PREFIX)
        or len(artifact_id) > 4096
    ):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid artifact ID",
        )
    token = artifact_id[len(_ARTIFACT_ID_PREFIX) :]
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(token + padding).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid artifact ID",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _ARTIFACT_ID_VERSION
        or not isinstance(payload.get("suite_id"), str)
        or payload.get("run_id") is not None
        and not isinstance(payload.get("run_id"), str)
        or not isinstance(payload.get("name"), str)
        or not isinstance(payload.get("size_bytes"), int)
        or not isinstance(payload.get("mtime_ns"), int)
    ):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid artifact ID",
        )
    return payload


def _catalog_identity(candidates: list[_ArtifactCandidate]) -> str:
    payload = [
        {
            "name": candidate.name,
            "aliases": sorted(candidate.aliases),
            "size_bytes": candidate.size_bytes,
            "mtime_ns": candidate.mtime_ns,
        }
        for candidate in candidates
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _encode_cursor(
    *,
    suite_id: str,
    run_id: str | None,
    catalog_sha256: str,
    offset: int,
) -> str:
    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "suite_id": suite_id,
            "run_id": run_id,
            "catalog_sha256": catalog_sha256,
            "offset": offset,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_CURSOR_PREFIX}{token}"


def _decode_cursor(cursor: str) -> dict[str, Any]:
    if not cursor.startswith(_CURSOR_PREFIX) or len(cursor) > 4096:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid artifact cursor",
        )
    token = cursor[len(_CURSOR_PREFIX) :]
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(token + padding).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid artifact cursor",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or not isinstance(payload.get("suite_id"), str)
        or payload.get("run_id") is not None
        and not isinstance(payload.get("run_id"), str)
        or not isinstance(payload.get("catalog_sha256"), str)
        or not isinstance(payload.get("offset"), int)
    ):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid artifact cursor",
        )
    return payload
