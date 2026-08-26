# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Immutable, workspace-scoped curation of generated suite artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from assert_ai.core.artifact_cache import (
    ARTIFACTS_DIR,
    LATEST_FILE,
    ArtifactFingerprint,
    ArtifactPlan,
    allocate_artifact_plan,
    discard_artifact_plan,
    finalize_artifact_plan,
    refresh_compatibility_files,
    hash_payload,
    update_latest_artifacts,
)
from assert_ai.core.io import (
    row_behavior,
    write_json,
    write_jsonl,
    write_text_atomic,
)
from assert_ai.core.jsonl_index import JsonlIndexError, scan_jsonl
from assert_ai.core.runtime_path_policy import RuntimePathError
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.job_store import JobStore
from assert_ai.services.locking import exclusive_file_lock
from assert_ai.services.result_metadata import write_suite_summary

log = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^v[0-9]{4,}$")
_LOCK_TIMEOUT_S = 10.0
_OPERATION_LEASE_S = 60.0
_MAX_TAXONOMY_BYTES = 1_048_576
_MAX_TEST_SET_BYTES = 16_777_216
_STAGE_FILES: dict[str, tuple[str, ...]] = {
    "systematize": ("taxonomy.json", "systematization.json"),
    "test_set": ("test_set.jsonl", "stratification.json"),
}


class _ServiceModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class BehaviorDescription(_ServiceModel):
    """Behavior block persisted in a taxonomy artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class DefinitionOfTerm(_ServiceModel):
    """One term definition persisted in a taxonomy artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    term: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    examples: tuple[str, ...]


class BehaviorCategory(_ServiceModel):
    """One editable behavior category."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    examples: tuple[str, ...]
    permissible: bool


class TaxonomyDocument(_ServiceModel):
    """Canonical taxonomy document accepted by curation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    behavior: BehaviorDescription
    definition_of_terms: tuple[DefinitionOfTerm, ...]
    behavior_categories: tuple[BehaviorCategory, ...]
    meta: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _unique_names(self) -> "TaxonomyDocument":
        names = [category.name for category in self.behavior_categories]
        if len(names) != len(set(names)):
            raise ValueError("behavior category names must be unique")
        return self


class TestCaseRevision(_ServiceModel):
    """Partial update for one stable test-case identity."""

    test_case_id: str = Field(min_length=1, max_length=255)
    updates: dict[str, Any]


class CuratedArtifactVersion(_ServiceModel):
    """One immutable artifact version created by a curation operation."""

    artifact_type: str
    version: str
    etag: str
    source_etag: str
    source_version: str | None = None
    artifact_ref: str
    metadata_ref: str


class CurationResult(_ServiceModel):
    """Result of one atomic suite curation operation."""

    schema_version: int = 1
    suite_id: str
    change_summary: str
    artifacts: tuple[CuratedArtifactVersion, ...]
    invalidated_stages: tuple[str, ...] = ("inference", "judge")
    affected_test_case_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ArtifactSource:
    stage_name: str
    primary_path: Path
    artifact_dir: Path
    version: str | None
    metadata: dict[str, Any] | None
    etag: str


class CurationService:
    """Create immutable taxonomy and test-set revisions."""

    def __init__(
        self,
        workspace: WorkspaceService,
        *,
        job_store: JobStore | None = None,
    ) -> None:
        self.workspace = workspace
        self.job_store = job_store

    def revise_taxonomy(
        self,
        suite_id: str,
        taxonomy: Mapping[str, Any],
        *,
        expected_etag: str,
        change_summary: str,
    ) -> CurationResult:
        """Create and atomically activate a taxonomy revision.

        The operation also rebases the active test set into a new immutable
        version. This records the taxonomy dependency without rewriting a
        completed run or forcing avoidable test-set regeneration.
        """
        suite_root = self._suite_root(suite_id)
        summary = _change_summary(change_summary)
        try:
            document = TaxonomyDocument.model_validate(dict(taxonomy))
        except ValidationError as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Taxonomy validation failed",
                details={"issues": _validation_issues(exc)},
            ) from exc
        taxonomy_payload = document.model_dump(mode="json", exclude_none=True)
        _require_json_size(
            taxonomy_payload,
            max_bytes=_MAX_TAXONOMY_BYTES,
            label="Taxonomy revision",
            indent=2,
        )

        with self._suite_mutation(suite_id, suite_root) as ensure_lock:
            source = self._active_source(suite_root, "systematize")
            _require_etag(source.etag, expected_etag)
            current = self._load_taxonomy(source.primary_path)
            current_names = tuple(
                category.name for category in current.behavior_categories
            )
            revised_names = tuple(
                category.name for category in document.behavior_categories
            )
            if revised_names != current_names:
                raise ServiceError(
                    ServiceErrorCode.INVALID_ARGUMENT,
                    "Category additions, removals, renames, and reordering "
                    "require test-set regeneration and are not supported by "
                    "revise_taxonomy",
                    details={
                        "current_categories": list(current_names),
                        "revised_categories": list(revised_names),
                    },
                )
            if document == current:
                raise ServiceError(
                    ServiceErrorCode.INVALID_ARGUMENT,
                    "The taxonomy revision does not change the active artifact",
                )

            test_set_source = self._optional_active_source(
                suite_root,
                "test_set",
            )
            plans: list[ArtifactPlan] = []
            refs: dict[str, dict[str, Any]] = {}
            sources: dict[str, _ArtifactSource] = {"systematize": source}
            created_at = _utc_now()
            try:
                taxonomy_plan = allocate_artifact_plan(
                    ctx=self._context(suite_id, suite_root),
                    stage_name="systematize",
                    fingerprint=_fingerprint(source),
                )
                plans.append(taxonomy_plan)
                write_json(
                    taxonomy_plan.output_paths["taxonomy"],
                    taxonomy_payload,
                )
                self._copy_secondary(
                    source,
                    "systematization.json",
                    taxonomy_plan.output_paths["systematization"],
                )
                taxonomy_ref = finalize_artifact_plan(
                    self._context(suite_id, suite_root),
                    taxonomy_plan,
                    provenance=_provenance(
                        source,
                        summary,
                        created_at=created_at,
                    ),
                    activate=False,
                )
                refs["systematize"] = taxonomy_ref

                if test_set_source is not None:
                    test_set_plan = allocate_artifact_plan(
                        ctx=self._context(suite_id, suite_root),
                        stage_name="test_set",
                        fingerprint=_rebased_test_set_fingerprint(
                            test_set_source,
                            taxonomy_ref,
                        ),
                    )
                    plans.append(test_set_plan)
                    self._copy_text(
                        test_set_source.primary_path,
                        test_set_plan.output_paths["test_set"],
                        max_bytes=_MAX_TEST_SET_BYTES,
                    )
                    self._copy_secondary(
                        test_set_source,
                        "stratification.json",
                        test_set_plan.output_paths["stratification"],
                    )
                    test_set_ref = finalize_artifact_plan(
                        self._context(suite_id, suite_root),
                        test_set_plan,
                        provenance={
                            **_provenance(
                                test_set_source,
                                summary,
                                created_at=created_at,
                            ),
                            "operation": "taxonomy_rebase",
                            "taxonomy_version": taxonomy_plan.version,
                            "taxonomy_etag": _file_etag(
                                taxonomy_plan.output_paths["taxonomy"]
                            ),
                        },
                        activate=False,
                    )
                    refs["test_set"] = test_set_ref
                    sources["test_set"] = test_set_source

                ensure_lock()
                return self._activate(
                    suite_id=suite_id,
                    suite_root=suite_root,
                    refs=refs,
                    plans=plans,
                    sources=sources,
                    change_summary=summary,
                )
            except BaseException:
                for plan in plans:
                    discard_artifact_plan(
                        self._context(suite_id, suite_root),
                        plan,
                    )
                raise

    def revise_test_case(
        self,
        suite_id: str,
        test_case_id: str,
        updates: Mapping[str, Any],
        *,
        expected_etag: str,
        change_summary: str,
    ) -> CurationResult:
        """Create and activate one immutable test-case revision."""
        revision = TestCaseRevision(
            test_case_id=test_case_id,
            updates=dict(updates),
        )
        return self.bulk_revise_test_cases(
            suite_id,
            (revision,),
            expected_etag=expected_etag,
            change_summary=change_summary,
        )

    def bulk_revise_test_cases(
        self,
        suite_id: str,
        revisions: Sequence[TestCaseRevision],
        *,
        expected_etag: str,
        change_summary: str,
    ) -> CurationResult:
        """Create and atomically activate one test-set revision."""
        if not revisions:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "At least one test-case revision is required",
            )
        ids = tuple(revision.test_case_id for revision in revisions)
        if len(ids) != len(set(ids)):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Each test_case_id may be revised only once per operation",
            )
        suite_root = self._suite_root(suite_id)
        summary = _change_summary(change_summary)
        with self._suite_mutation(suite_id, suite_root) as ensure_lock:
            source = self._active_source(suite_root, "test_set")
            _require_etag(source.etag, expected_etag)
            rows = self._load_test_cases(source.primary_path)
            index = {
                str(row["test_case_id"]): offset
                for offset, row in enumerate(rows)
            }
            missing = [test_case_id for test_case_id in ids if test_case_id not in index]
            if missing:
                raise ServiceError(
                    ServiceErrorCode.NOT_FOUND,
                    "One or more test cases were not found",
                    details={"test_case_ids": missing},
                )

            taxonomy_names = self._taxonomy_names(suite_root)
            changed = False
            for revision in revisions:
                updates = dict(revision.updates)
                if not updates:
                    raise ServiceError(
                        ServiceErrorCode.INVALID_ARGUMENT,
                        f"updates must not be empty for {revision.test_case_id}",
                    )
                replacement_id = updates.get("test_case_id")
                if (
                    replacement_id is not None
                    and replacement_id != revision.test_case_id
                ):
                    raise ServiceError(
                        ServiceErrorCode.INVALID_ARGUMENT,
                        "Test-case identities are immutable",
                        details={"test_case_id": revision.test_case_id},
                    )
                offset = index[revision.test_case_id]
                revised = {**rows[offset], **updates}
                revised["test_case_id"] = revision.test_case_id
                _validate_test_case(revised, taxonomy_names=taxonomy_names)
                changed = changed or revised != rows[offset]
                rows[offset] = revised

            if not changed:
                raise ServiceError(
                    ServiceErrorCode.INVALID_ARGUMENT,
                    "The test-case revisions do not change the active artifact",
                )
            _validate_test_case_set(rows)
            _require_jsonl_size(
                rows,
                max_bytes=_MAX_TEST_SET_BYTES,
                label="Test-set revision",
            )
            plan = allocate_artifact_plan(
                ctx=self._context(suite_id, suite_root),
                stage_name="test_set",
                fingerprint=_fingerprint(source),
            )
            try:
                write_jsonl(plan.output_paths["test_set"], rows)
                self._copy_secondary(
                    source,
                    "stratification.json",
                    plan.output_paths["stratification"],
                )
                ref = finalize_artifact_plan(
                    self._context(suite_id, suite_root),
                    plan,
                    provenance={
                        **_provenance(
                            source,
                            summary,
                            created_at=_utc_now(),
                        ),
                        "operation": "test_case_revision",
                        "test_case_ids": list(ids),
                    },
                    activate=False,
                )
                ensure_lock()
                return self._activate(
                    suite_id=suite_id,
                    suite_root=suite_root,
                    refs={"test_set": ref},
                    plans=[plan],
                    sources={"test_set": source},
                    change_summary=summary,
                    affected_test_case_ids=ids,
                )
            except BaseException:
                discard_artifact_plan(
                    self._context(suite_id, suite_root),
                    plan,
                )
                raise

    def _activate(
        self,
        *,
        suite_id: str,
        suite_root: Path,
        refs: dict[str, dict[str, Any]],
        plans: Sequence[ArtifactPlan],
        sources: Mapping[str, _ArtifactSource],
        change_summary: str,
        affected_test_case_ids: tuple[str, ...] = (),
    ) -> CurationResult:
        ctx = self._context(suite_id, suite_root)
        artifacts = tuple(
            CuratedArtifactVersion(
                artifact_type=plan.stage_name,
                version=plan.version,
                etag=_file_etag(
                    plan.output_paths[
                        "taxonomy"
                        if plan.stage_name == "systematize"
                        else "test_set"
                    ]
                ),
                source_etag=sources[plan.stage_name].etag,
                source_version=sources[plan.stage_name].version,
                artifact_ref=str(refs[plan.stage_name]["path"]),
                metadata_ref=str(refs[plan.stage_name]["metadata_path"]),
            )
            for plan in plans
        )
        update_latest_artifacts(ctx, refs)
        warnings: list[str] = []
        try:
            latest = self._load_json_object(
                suite_root / LATEST_FILE,
                required=True,
                max_bytes=_MAX_TAXONOMY_BYTES,
            )
        except (ServiceError, RuntimePathError) as exc:
            warning = (
                "Artifacts were activated, but active artifact metadata "
                "could not be reloaded"
            )
            log.warning("%s: %s", warning, exc)
            warnings.append(warning)
            latest = None
        latest_artifacts = latest.get("artifacts") if latest is not None else None
        ctx["artifact_versions"] = (
            dict(latest_artifacts)
            if isinstance(latest_artifacts, dict)
            else dict(refs)
        )
        for plan in plans:
            try:
                refresh_compatibility_files(
                    ctx,
                    plan.stage_name,
                    plan.output_paths,
                    preserve_local_edits=False,
                )
            except (
                OSError,
                RuntimePathError,
                ServiceError,
                TypeError,
                ValueError,
            ) as exc:
                warning = (
                    f"Activated {plan.stage_name} {plan.version}, but could not "
                    "refresh one or more legacy compatibility files"
                )
                log.warning("%s: %s", warning, exc)
                warnings.append(warning)
        try:
            if "systematize" in refs:
                ctx["taxonomy_path"] = str(
                    next(
                        plan.output_paths["taxonomy"]
                        for plan in plans
                        if plan.stage_name == "systematize"
                    )
                )
            if "test_set" in refs:
                ctx["test_set_path"] = str(
                    next(
                        plan.output_paths["test_set"]
                        for plan in plans
                        if plan.stage_name == "test_set"
                    )
                )
            write_suite_summary(ctx, rebuild_indexes=True)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            JsonlIndexError,
            RuntimePathError,
            ServiceError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            warning = "Artifacts were activated, but suite summary refresh failed"
            log.warning("%s: %s", warning, exc)
            warnings.append(warning)

        return CurationResult(
            suite_id=suite_id,
            change_summary=change_summary,
            artifacts=artifacts,
            affected_test_case_ids=affected_test_case_ids,
            warnings=tuple(warnings),
        )

    @contextmanager
    def _suite_mutation(
        self,
        suite_id: str,
        suite_root: Path,
    ) -> Iterator[Callable[[], None]]:
        lock_path = self.workspace.path_policy.resolve_managed_output(
            suite_root / ".curation.lock",
            field_name="suite curation lock",
            expected_root=suite_root,
            reject_links=True,
        )
        owner = f"curation:{uuid.uuid4().hex}"
        resource_keys = (f"suite:{suite_id}",)
        stop_renewal = threading.Event()
        lease_lost = threading.Event()
        renewal_thread: threading.Thread | None = None
        operation_acquired = False

        def ensure_lock() -> None:
            renewed = (
                self.job_store.renew_operation_locks(
                    resource_keys,
                    owner=owner,
                    lease_seconds=_OPERATION_LEASE_S,
                )
                if self.job_store is not None and not lease_lost.is_set()
                else False
            )
            if self.job_store is not None and not renewed:
                lease_lost.set()
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "The suite curation lease was lost before activation",
                )

        try:
            if self.job_store is not None:
                operation_acquired = self.job_store.acquire_operation_locks(
                    resource_keys,
                    owner=owner,
                    lease_seconds=_OPERATION_LEASE_S,
                )
                if not operation_acquired:
                    raise ServiceError(
                        ServiceErrorCode.CONFLICT,
                        "The suite is currently being changed by an evaluation "
                        "or another curation operation",
                    )
                renewal_thread = threading.Thread(
                    target=self._renew_operation_lock,
                    args=(resource_keys, owner, stop_renewal, lease_lost),
                    name=f"assert-curation-lock-{suite_id}",
                    daemon=True,
                )
                renewal_thread.start()
            with exclusive_file_lock(
                lock_path,
                timeout_s=_LOCK_TIMEOUT_S,
                conflict_message="Timed out waiting for the suite curation lock",
            ):
                yield ensure_lock
        finally:
            stop_renewal.set()
            if renewal_thread is not None:
                renewal_thread.join(
                    timeout=min(10.0, max(1.0, _OPERATION_LEASE_S))
                )
                if renewal_thread.is_alive():
                    log.error(
                        "Suite curation lease-renewal thread did not stop"
                    )
            if self.job_store is not None and operation_acquired:
                try:
                    self.job_store.release_operation_locks(
                        owner=owner,
                        resource_keys=resource_keys,
                    )
                except Exception:
                    log.exception(
                        "Failed to release the suite curation lease; "
                        "it will expire automatically"
                    )

    def _renew_operation_lock(
        self,
        resource_keys: tuple[str, ...],
        owner: str,
        stop: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        assert self.job_store is not None
        interval = max(0.05, _OPERATION_LEASE_S / 3)
        while not stop.wait(interval):
            try:
                renewed = self.job_store.renew_operation_locks(
                    resource_keys,
                    owner=owner,
                    lease_seconds=_OPERATION_LEASE_S,
                )
            except Exception:
                log.exception("Failed to renew the suite curation lease")
                lease_lost.set()
                return
            if not renewed:
                log.error("Lost the suite curation lease before activation")
                lease_lost.set()
                return

    def _suite_root(self, suite_id: str) -> Path:
        if not isinstance(suite_id, str) or not _IDENTIFIER_RE.fullmatch(suite_id):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "suite_id must contain only letters, numbers, '.', '_', or '-'",
            )
        suite_root = self.workspace.path_policy.resolve_managed_output(
            self.workspace.results_root / suite_id,
            field_name="curation suite",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        if not suite_root.is_dir():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Suite not found: {suite_id}",
            )
        return suite_root

    def _context(self, suite_id: str, suite_root: Path) -> dict[str, Any]:
        return {
            "suite_id": suite_id,
            "suite_root": str(suite_root),
            "results_root": str(self.workspace.results_root),
            "artifacts_root": str(self.workspace.artifacts_root),
            "path_policy": self.workspace.path_policy,
        }

    def _active_source(
        self,
        suite_root: Path,
        stage_name: str,
    ) -> _ArtifactSource:
        source = self._optional_active_source(suite_root, stage_name)
        if source is None:
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"No active {stage_name} artifact was found",
            )
        return source

    def _optional_active_source(
        self,
        suite_root: Path,
        stage_name: str,
    ) -> _ArtifactSource | None:
        primary_name = _STAGE_FILES[stage_name][0]
        latest = self._load_json_object(
            suite_root / LATEST_FILE,
            required=False,
            max_bytes=_MAX_TAXONOMY_BYTES,
        )
        artifacts = latest.get("artifacts") if latest is not None else None
        ref = artifacts.get(stage_name) if isinstance(artifacts, dict) else None
        if isinstance(ref, dict):
            version = ref.get("version")
            if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
                raise ServiceError(
                    ServiceErrorCode.INTERNAL,
                    f"Active {stage_name} artifact has an invalid version",
                )
            artifact_dir = self.workspace.path_policy.resolve_managed_output(
                suite_root / ARTIFACTS_DIR / stage_name / version,
                field_name=f"active {stage_name} artifact directory",
                expected_root=suite_root,
                reject_links=True,
            )
            primary_path = self.workspace.path_policy.resolve_managed_output(
                artifact_dir / primary_name,
                field_name=f"active {stage_name} artifact",
                expected_root=artifact_dir,
                reject_links=True,
            )
            metadata = self._load_json_object(
                artifact_dir / "artifact.json",
                required=True,
                max_bytes=_MAX_TAXONOMY_BYTES,
            )
            if not primary_path.is_file():
                raise ServiceError(
                    ServiceErrorCode.NOT_FOUND,
                    f"Active {stage_name} artifact is missing",
                )
            if (
                metadata.get("artifact_type") != stage_name
                or metadata.get("version") != version
            ):
                raise ServiceError(
                    ServiceErrorCode.CONFIG_INVALID,
                    f"Active {stage_name} artifact metadata is inconsistent",
                )
            etag = _file_etag(primary_path)
            file_hashes = metadata.get("file_hashes")
            primary_key = (
                "taxonomy" if stage_name == "systematize" else "test_set"
            )
            expected_hash = (
                file_hashes.get(primary_key)
                if isinstance(file_hashes, dict)
                else None
            )
            if (
                isinstance(expected_hash, str)
                and re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                and etag != f"sha256:{expected_hash}"
            ):
                raise ServiceError(
                    ServiceErrorCode.CONFIG_INVALID,
                    f"Active {stage_name} artifact failed its integrity check",
                )
            return _ArtifactSource(
                stage_name=stage_name,
                primary_path=primary_path,
                artifact_dir=artifact_dir,
                version=version,
                metadata=metadata,
                etag=etag,
            )

        primary_path = self.workspace.path_policy.resolve_managed_output(
            suite_root / primary_name,
            field_name=f"legacy {stage_name} artifact",
            expected_root=suite_root,
            reject_links=True,
        )
        if not primary_path.is_file():
            return None
        return _ArtifactSource(
            stage_name=stage_name,
            primary_path=primary_path,
            artifact_dir=suite_root,
            version=None,
            metadata=None,
            etag=_file_etag(primary_path),
        )

    def _load_taxonomy(self, path: Path) -> TaxonomyDocument:
        raw = self._load_json_object(
            path,
            required=True,
            max_bytes=_MAX_TAXONOMY_BYTES,
        )
        try:
            return TaxonomyDocument.model_validate(raw)
        except ValidationError as exc:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                "The active taxonomy is invalid",
                details={"issues": _validation_issues(exc)},
            ) from exc

    def _load_test_cases(self, path: Path) -> list[dict[str, Any]]:
        if path.stat().st_size > _MAX_TEST_SET_BYTES:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                f"Test set exceeds the {_MAX_TEST_SET_BYTES}-byte curation limit",
            )
        try:
            scan = scan_jsonl(path, allow_trailing_partial=False)
        except (OSError, JsonlIndexError) as exc:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                "The active test set is not valid JSONL",
            ) from exc
        rows = [dict(record.row) for record in scan.records]
        _validate_test_case_set(rows)
        return rows

    def _taxonomy_names(self, suite_root: Path) -> set[str]:
        source = self._optional_active_source(suite_root, "systematize")
        if source is None:
            return set()
        taxonomy = self._load_taxonomy(source.primary_path)
        return {
            category.name
            for category in taxonomy.behavior_categories
        }

    def _load_json_object(
        self,
        path: Path,
        *,
        required: bool,
        max_bytes: int,
    ) -> dict[str, Any] | None:
        path = self.workspace.path_policy.resolve_managed_output(
            path,
            field_name="curation JSON artifact",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        if not path.is_file():
            if required:
                raise ServiceError(
                    ServiceErrorCode.NOT_FOUND,
                    f"Required artifact is missing: {path.name}",
                )
            return None
        if path.stat().st_size > max_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                f"Artifact exceeds the {max_bytes}-byte curation limit",
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                f"Artifact is not valid JSON: {path.name}",
            ) from exc
        if not isinstance(value, dict):
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                f"Artifact must contain a JSON object: {path.name}",
            )
        return value

    def _copy_secondary(
        self,
        source: _ArtifactSource,
        name: str,
        destination: Path,
    ) -> None:
        path = self.workspace.path_policy.resolve_managed_output(
            source.artifact_dir / name,
            field_name=f"{source.stage_name} companion artifact",
            expected_root=source.artifact_dir,
            reject_links=True,
        )
        if not path.is_file():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Required companion artifact is missing: {name}",
            )
        self._copy_text(path, destination, max_bytes=_MAX_TEST_SET_BYTES)

    @staticmethod
    def _copy_text(
        source: Path,
        destination: Path,
        *,
        max_bytes: int,
    ) -> None:
        if source.stat().st_size > max_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                f"Artifact exceeds the {max_bytes}-byte curation limit",
            )
        try:
            text = source.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                f"Artifact is not valid UTF-8: {source.name}",
            ) from exc
        write_text_atomic(destination, text)


def _fingerprint(source: _ArtifactSource) -> ArtifactFingerprint:
    hashes = source.metadata.get("hashes") if source.metadata else None
    config_hash = (
        hashes.get("config_hash")
        if isinstance(hashes, dict)
        and isinstance(hashes.get("config_hash"), str)
        else source.etag.removeprefix("sha256:")
    )
    input_hash = (
        hashes.get("input_hash")
        if isinstance(hashes, dict)
        and isinstance(hashes.get("input_hash"), str)
        else source.etag.removeprefix("sha256:")
    )
    behavior_hash = (
        hashes.get("behavior_hash")
        if isinstance(hashes, dict)
        and isinstance(hashes.get("behavior_hash"), str)
        else None
    )
    inputs = source.metadata.get("inputs") if source.metadata else None
    descriptor = (
        dict(inputs)
        if isinstance(inputs, dict)
        else {
            "curated_legacy_source": True,
            "source_etag": source.etag,
        }
    )
    return ArtifactFingerprint(
        stage_name=source.stage_name,
        behavior_hash=behavior_hash,
        config_hash=config_hash,
        input_hash=input_hash,
        descriptor=descriptor,
    )


def _rebased_test_set_fingerprint(
    source: _ArtifactSource,
    taxonomy_ref: Mapping[str, Any],
) -> ArtifactFingerprint:
    fingerprint = _fingerprint(source)
    descriptor = deepcopy(fingerprint.descriptor)
    dependencies = descriptor.setdefault("dependencies", {})
    if not isinstance(dependencies, dict):
        dependencies = {}
        descriptor["dependencies"] = dependencies
    dependencies["taxonomy"] = {
        "artifact_type": taxonomy_ref.get("artifact_type", "systematize"),
        "version": taxonomy_ref.get("version"),
        "input_hash": taxonomy_ref.get("input_hash"),
        "path": taxonomy_ref.get("path"),
    }
    input_hash = hash_payload(
        {
            "stage_name": "test_set",
            "behavior_hash": fingerprint.behavior_hash,
            "config_hash": fingerprint.config_hash,
            "dependencies": dependencies,
            "prompts": descriptor.get("prompts", {}),
        }
    )
    return ArtifactFingerprint(
        stage_name="test_set",
        behavior_hash=fingerprint.behavior_hash,
        config_hash=fingerprint.config_hash,
        input_hash=input_hash,
        descriptor=descriptor,
    )


def _provenance(
    source: _ArtifactSource,
    change_summary: str,
    *,
    created_at: str,
) -> dict[str, Any]:
    return {
        "operation": "curation",
        "edited_from": {
            "artifact_type": source.stage_name,
            "version": source.version,
            "etag": source.etag,
        },
        "edited_at": created_at,
        "change_summary": change_summary,
    }


def _validate_test_case_set(rows: Sequence[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        _validate_test_case(row, taxonomy_names=set())
        test_case_id = str(row["test_case_id"])
        if test_case_id in seen:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                f"Duplicate test_case_id: {test_case_id}",
            )
        seen.add(test_case_id)


def _validate_test_case(
    row: Mapping[str, Any],
    *,
    taxonomy_names: set[str],
) -> None:
    test_case_id = row.get("test_case_id")
    if (
        not isinstance(test_case_id, str)
        or not test_case_id
        or len(test_case_id) > 255
    ):
        raise ServiceError(
            ServiceErrorCode.CONFIG_INVALID,
            "Every test case requires a stable string test_case_id",
        )
    row_type = row.get("type")
    if row_type not in {"prompt", "scenario"}:
        raise ServiceError(
            ServiceErrorCode.CONFIG_INVALID,
            f"Test case {test_case_id} type must be prompt or scenario",
        )
    behavior = row_behavior(dict(row))
    if taxonomy_names and behavior and behavior not in taxonomy_names:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"Test case {test_case_id} references an unknown behavior category",
            details={"behavior": behavior},
        )
    try:
        json.dumps(row, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"Test case {test_case_id} is not valid JSON",
        ) from exc


def _require_etag(current_etag: str, expected_etag: str) -> None:
    normalized = expected_etag.strip()
    if normalized and not normalized.startswith("sha256:"):
        normalized = f"sha256:{normalized}"
    if not normalized:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "expected_etag is required",
        )
    if normalized != current_etag:
        raise ServiceError(
            ServiceErrorCode.STALE_ETAG,
            "The artifact changed after it was read",
            details={"current_etag": current_etag},
        )


def _require_json_size(
    value: Any,
    *,
    max_bytes: int,
    label: str,
    indent: int | None = None,
) -> None:
    try:
        size_bytes = len(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=indent,
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"{label} is not valid JSON",
        ) from exc
    if size_bytes > max_bytes:
        raise ServiceError(
            ServiceErrorCode.ARTIFACT_TOO_LARGE,
            f"{label} exceeds the {max_bytes}-byte curation limit",
        )


def _require_jsonl_size(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_bytes: int,
    label: str,
) -> None:
    size_bytes = 0
    for row in rows:
        try:
            size_bytes += len(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ) + 1
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"{label} is not valid JSON",
            ) from exc
        if size_bytes > max_bytes:
            raise ServiceError(
                ServiceErrorCode.ARTIFACT_TOO_LARGE,
                f"{label} exceeds the {max_bytes}-byte curation limit",
            )


def _change_summary(value: str) -> str:
    summary = value.strip()
    if not summary:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "change_summary must not be empty",
        )
    if len(summary) > 500:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "change_summary must be <= 500 characters",
        )
    return summary


def _file_etag(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _validation_issues(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "path": "/" + "/".join(str(part) for part in error["loc"]),
            "message": str(error["msg"]),
        }
        for error in exc.errors(include_url=False, include_input=False)
    ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
