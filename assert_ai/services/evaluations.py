# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Persisted, idempotent evaluation execution services."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

from assert_ai.core.io import write_json, write_text_atomic
from assert_ai.core.security import (
    redact_path_prefixes,
    sanitize_payload,
    sanitize_text,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.core.yaml_io import dump_yaml
from assert_ai.services.configs import ConfigService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.job_models import (
    JobCatalogEntry,
    JobDetail,
    JobPage,
    JobRecord,
    JobStartResult,
    JobState,
    JobTerminalResult,
    NewJob,
    TERMINAL_JOB_STATES,
)
from assert_ai.services.job_store import JobStore
from assert_ai.services.run_planning import (
    EvaluationOverrides,
    RunPlanningService,
    StageAction,
)

_CURSOR_VERSION = 1
_JOB_RESULT_MAX_BYTES = 1024 * 1024
_JOB_ID_RETRIES = 5
_LEASE_SECONDS = 60.0
_LEASE_RENEW_SECONDS = 15.0
_REQUEST_ID_MAX_LENGTH = 200
_MIN_LOG_BYTES = 4096
_MAX_LOG_BYTES = 16 * 1024 * 1024

log = logging.getLogger(__name__)


@dataclass(slots=True)
class EvaluationJobManager:
    """Launch queued jobs and reconcile their terminal worker results."""

    workspace: WorkspaceService
    store: JobStore
    max_active_jobs: int = 1
    max_log_bytes: int = 1024 * 1024
    launch_enabled: bool = True
    lease_seconds: float = _LEASE_SECONDS
    _owner: str = field(
        default_factory=lambda: uuid.uuid4().hex,
        init=False,
    )
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _scheduler: threading.Thread | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _schedule_requested: bool = field(
        default=False,
        init=False,
        repr=False,
    )
    _processes: dict[str, subprocess.Popen[bytes]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.max_active_jobs < 1:
            raise ValueError("max_active_jobs must be positive")
        if not _MIN_LOG_BYTES <= self.max_log_bytes <= _MAX_LOG_BYTES:
            raise ValueError(
                f"max_log_bytes must be between {_MIN_LOG_BYTES} and "
                f"{_MAX_LOG_BYTES}"
            )
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

    def enqueue(self) -> None:
        """Wake a short-lived scheduler without holding an MCP request open."""
        if not self.launch_enabled:
            return
        with self._lock:
            self._schedule_requested = True
            if self._scheduler is not None and self._scheduler.is_alive():
                return
            self._scheduler = threading.Thread(
                target=self._schedule,
                name="assert-mcp-job-scheduler",
                daemon=True,
            )
            self._scheduler.start()

    def reconcile(self, record: JobRecord) -> JobRecord:
        """Adopt a worker result or mark a dead worker interrupted."""
        if record.state in TERMINAL_JOB_STATES:
            return record
        if record.state is JobState.QUEUED:
            self.enqueue()
            return record
        result = self._read_result(record)
        if result is not None:
            try:
                return self._adopt_result(
                    record,
                    result,
                    lease_owner=None,
                )
            except Exception as exc:  # noqa: BLE001 - persisted boundary
                log.exception(
                    "Could not reconcile evaluation job %s",
                    record.job_id,
                )
                return self._mark_internal_failure(
                    record,
                    exc,
                    lease_owner=None,
                )
        if record.state is JobState.STARTING and record.pid is None:
            if not _lease_expired(record.lease_expires_at):
                return record
        if (
            record.pid is not None
            and record.process_create_time is not None
            and _process_matches(
                record.pid,
                record.process_create_time,
            )
        ):
            return record
        return self.store.mark_terminal(
            record.job_id,
            state=JobState.INTERRUPTED,
            exit_code=record.exit_code,
            failed_stage=record.failed_stage,
            error_code=ServiceErrorCode.JOB_INTERRUPTED.value,
            error_message=(
                "Evaluation worker exited without a terminal result"
            ),
            result=None,
            run_root=record.run_root,
        )

    def _schedule(self) -> None:
        try:
            while True:
                with self._lock:
                    self._schedule_requested = False
                try:
                    claimed = self.store.claim_next(
                        lease_owner=self._owner,
                        lease_seconds=self.lease_seconds,
                        max_active_jobs=self.max_active_jobs,
                    )
                except Exception:  # noqa: BLE001 - daemon boundary
                    log.exception(
                        "Evaluation scheduler could not claim a queued job"
                    )
                    return
                if claimed is None:
                    with self._lock:
                        if self._schedule_requested:
                            continue
                        if self._scheduler is threading.current_thread():
                            self._scheduler = None
                    return
                try:
                    process = self._launch(claimed)
                except Exception as exc:  # noqa: BLE001 - process boundary
                    log.exception(
                        "Could not launch evaluation job %s",
                        claimed.job_id,
                    )
                    self._mark_internal_failure(
                        claimed,
                        exc,
                        lease_owner=self._owner,
                        fallback="Evaluation worker could not be started",
                    )
                    continue
                with self._lock:
                    self._processes[claimed.job_id] = process
                monitor = threading.Thread(
                    target=self._monitor,
                    args=(claimed.job_id, process),
                    name=f"assert-mcp-job-{claimed.job_id[:8]}",
                    daemon=True,
                )
                monitor.start()
        finally:
            restart = False
            with self._lock:
                if self._scheduler is threading.current_thread():
                    restart = (
                        self.launch_enabled and self._schedule_requested
                    )
                    self._scheduler = None
            if restart:
                self.enqueue()

    def _launch(self, record: JobRecord) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            sys.executable,
            "-m",
            "assert_ai.services._evaluation_worker",
            "--workspace",
            str(self.workspace.root),
            "--job-id",
            record.job_id,
        ]
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        )
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.workspace.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
            create_time = _process_create_time(process.pid)
            self.store.mark_running(
                record.job_id,
                lease_owner=self._owner,
                pid=process.pid,
                process_create_time=create_time,
                lease_seconds=self.lease_seconds,
            )
        except BaseException:
            if process is not None:
                _terminate_failed_launch(process)
            raise
        return process

    def _monitor(
        self,
        job_id: str,
        process: subprocess.Popen[bytes],
    ) -> None:
        lost_lease = False
        while True:
            try:
                process.wait(timeout=_LEASE_RENEW_SECONDS)
                break
            except subprocess.TimeoutExpired:
                try:
                    renewed = self.store.renew_lease(
                        job_id,
                        lease_owner=self._owner,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception:  # noqa: BLE001 - daemon boundary
                    log.exception(
                        "Could not renew lease for evaluation job %s",
                        job_id,
                    )
                    continue
                if not renewed:
                    lost_lease = True
                    break
        try:
            if lost_lease:
                log.warning(
                    "Stopped monitoring evaluation job %s after losing its lease",
                    job_id,
                )
                return
            record = self.store.get(job_id)
            payload = self._read_result(record)
            if payload is None:
                self.store.mark_terminal(
                    job_id,
                    state=JobState.FAILED,
                    exit_code=process.returncode,
                    failed_stage=None,
                    error_code=ServiceErrorCode.RUN_FAILED.value,
                    error_message=(
                        "Evaluation worker exited without a valid result"
                    ),
                    result=None,
                    run_root=None,
                    lease_owner=self._owner,
                )
            else:
                self._adopt_result(
                    record,
                    payload,
                    lease_owner=self._owner,
                )
        except Exception as exc:  # noqa: BLE001 - daemon boundary
            log.exception(
                "Could not adopt terminal result for evaluation job %s",
                job_id,
            )
            try:
                record = self.store.get(job_id)
                self._mark_internal_failure(
                    record,
                    exc,
                    lease_owner=self._owner,
                )
            except Exception:
                log.exception(
                    "Could not persist failure for evaluation job %s",
                    job_id,
                )
        finally:
            with self._lock:
                self._processes.pop(job_id, None)
            self.enqueue()

    def _mark_internal_failure(
        self,
        record: JobRecord,
        error: Exception,
        *,
        lease_owner: str | None,
        fallback: str = "Evaluation result reconciliation failed",
    ) -> JobRecord:
        return self.store.mark_terminal(
            record.job_id,
            state=JobState.FAILED,
            exit_code=1,
            failed_stage=None,
            error_code=ServiceErrorCode.INTERNAL.value,
            error_message=_safe_error(
                error,
                workspace=self.workspace,
                fallback=fallback,
            ),
            result=None,
            run_root=record.run_root,
            lease_owner=lease_owner,
        )

    def _read_result(self, record: JobRecord) -> dict[str, Any] | None:
        result_path = self._job_file(
            self._job_dir(record.job_id),
            "result.json",
        )
        if not result_path.is_file():
            return None
        try:
            if result_path.stat().st_size > _JOB_RESULT_MAX_BYTES:
                return None
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != 1:
            return None
        if payload.get("job_id") != record.job_id:
            return None
        return payload

    def _adopt_result(
        self,
        record: JobRecord,
        payload: dict[str, Any],
        *,
        lease_owner: str | None,
    ) -> JobRecord:
        job_dir = self._job_dir(record.job_id)
        request = _read_json_file(
            self._job_file(job_dir, "request.json"),
            max_bytes=_JOB_RESULT_MAX_BYTES,
        )
        if (
            not isinstance(request, dict)
            or payload.get("result_token") != request.get("result_token")
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker result identity mismatch",
            )
        raw_result = payload.get("run_result")
        if not isinstance(raw_result, dict):
            worker_error = payload.get("worker_error")
            error_code = ServiceErrorCode.INTERNAL.value
            message = None
            if isinstance(worker_error, dict):
                message = worker_error.get("error_message")
                if worker_error.get("error_code") == "INTERNAL":
                    error_code = ServiceErrorCode.INTERNAL.value
            return self.store.mark_terminal(
                record.job_id,
                state=JobState.FAILED,
                exit_code=1,
                failed_stage=None,
                error_code=error_code,
                error_message=_safe_text(
                    message,
                    workspace=self.workspace,
                    fallback="Evaluation worker failed",
                ),
                result=None,
                run_root=None,
                lease_owner=lease_owner,
            )
        state_value = raw_result.get("state")
        state_map = {
            "completed": JobState.COMPLETED,
            "failed": JobState.FAILED,
            "cancelled": JobState.CANCELLED,
        }
        state = state_map.get(state_value)
        if state is None:
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned an invalid state",
            )
        result_suite_id = raw_result.get("suite_id")
        result_run_id = raw_result.get("run_id")
        failed_stage = _validated_optional_text(
            raw_result.get("failed_stage"),
            field_name="failed_stage",
        )
        if (
            result_suite_id is not None
            and not isinstance(result_suite_id, str)
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned an invalid suite id",
            )
        if (
            result_run_id is not None
            and not isinstance(result_run_id, str)
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned an invalid run id",
            )
        if (
            result_suite_id is not None
            and result_suite_id != record.suite_id
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned a mismatched suite id",
            )
        if (
            result_run_id is not None
            and result_run_id != record.run_id
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned a mismatched run id",
            )
        may_omit_identity = (
            state is JobState.FAILED
            and failed_stage is None
            and result_suite_id is None
            and result_run_id is None
        )
        if not may_omit_identity and (
            result_suite_id != record.suite_id
            or result_run_id != record.run_id
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker omitted its allocated run identity",
            )
        exit_code = raw_result.get("exit_code")
        if (
            not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or exit_code < 0
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned an invalid exit code",
            )
        if (
            state is JobState.COMPLETED
            and exit_code != 0
        ) or (
            state is not JobState.COMPLETED
            and exit_code == 0
        ):
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned an inconsistent exit code",
            )
        run_root = (
            self._validated_run_root(record, raw_result)
            if result_suite_id is not None
            else None
        )
        public_result = {
            "state": state_value,
            "exit_code": exit_code,
            "failed_stage": failed_stage,
            "error_code": _validated_optional_text(
                raw_result.get("error_code"),
                field_name="error_code",
            ),
            "error_message": _safe_text(
                _validated_optional_text(
                    raw_result.get("error_message"),
                    field_name="error_message",
                ),
                workspace=self.workspace,
                fallback=None,
            ),
        }
        public_result = sanitize_payload(public_result)
        return self.store.mark_terminal(
            record.job_id,
            state=state,
            exit_code=public_result["exit_code"],
            failed_stage=public_result["failed_stage"],
            error_code=public_result["error_code"],
            error_message=public_result["error_message"],
            result=public_result,
            run_root=str(run_root) if run_root is not None else None,
            lease_owner=lease_owner,
        )

    def _validated_run_root(
        self,
        record: JobRecord,
        result: dict[str, Any],
    ) -> Path | None:
        if record.run_id is None:
            if result.get("run_root") is not None:
                raise ServiceError(
                    ServiceErrorCode.RUN_FAILED,
                    "Suite-only worker returned an unexpected run root",
                )
            return None
        expected_suite = self.workspace.path_policy.resolve_managed_output(
            self.workspace.results_root / record.suite_id,
            field_name="job suite root",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        expected_run = self.workspace.path_policy.resolve_managed_output(
            expected_suite / record.run_id,
            field_name="job run root",
            expected_root=expected_suite,
            reject_links=True,
        )
        actual = result.get("run_root")
        if actual is None or Path(str(actual)).resolve() != expected_run:
            raise ServiceError(
                ServiceErrorCode.RUN_FAILED,
                "Evaluation worker returned a mismatched run root",
            )
        return expected_run

    def _job_dir(self, job_id: str) -> Path:
        jobs_root = _jobs_root(self.workspace)
        return self.workspace.path_policy.resolve_managed_output(
            jobs_root / job_id,
            field_name="evaluation job directory",
            expected_root=jobs_root,
            reject_links=True,
        )

    def _job_file(self, job_dir: Path, name: str) -> Path:
        return self.workspace.path_policy.resolve_managed_output(
            job_dir / name,
            field_name=f"evaluation job {name}",
            expected_root=job_dir,
            reject_links=True,
        )


@dataclass(slots=True)
class EvaluationService:
    """Author, persist, execute, and inspect evaluation jobs."""

    workspace: WorkspaceService
    configs: ConfigService
    planning: RunPlanningService
    store: JobStore
    manager: EvaluationJobManager
    default_page_size: int = 50
    max_page_size: int = 200
    max_queued_jobs: int = 100

    def start(
        self,
        config_ref: str,
        *,
        request_id: str,
        overrides: EvaluationOverrides | None = None,
    ) -> JobStartResult:
        if not self.manager.launch_enabled:
            raise ServiceError(
                ServiceErrorCode.CAPABILITY_DISABLED,
                "Evaluation execution is disabled for this service",
            )
        request_id = _validate_request_id(request_id)
        applied = overrides or EvaluationOverrides()
        config = self.configs.get_config(config_ref)
        request_hash = _request_hash(
            config_ref=config.config_ref,
            config_etag=config.etag,
            overrides=applied,
        )
        existing = self.store.get_by_idempotency_key(request_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "request_id is already bound to a different evaluation request",
                    details={"job_id": existing.job_id},
                )
            self.manager.enqueue()
            return JobStartResult(
                job=self.get(existing.job_id),
                created=False,
            )

        initial = self.planning.preflight(
            config.config_ref,
            overrides=applied,
        )
        _require_source_etag(initial.source_etag, config.etag)
        _require_ready(initial)
        suite_id = (
            applied.suite
            or config.document.get("suite")
            or _new_identity("mcp-suite")
        )
        has_run_stage = any(
            stage.scope == "run"
            and stage.action is not StageAction.DISABLED
            for stage in initial.stages
        )
        run_id = (
            applied.run
            or config.document.get("run")
            or (_new_identity("run") if has_run_stage else None)
        )
        effective_overrides = applied.model_copy(
            update={"suite": suite_id, "run": run_id},
        )
        plan = self.planning.preflight(
            config.config_ref,
            overrides=effective_overrides,
        )
        _require_source_etag(plan.source_etag, config.etag)
        _require_ready(plan)
        if plan.suite_id != suite_id or plan.run_id != run_id:
            raise ServiceError(
                ServiceErrorCode.INTERNAL,
                "Preflight did not preserve allocated evaluation identity",
            )
        if run_id is not None:
            self._reject_existing_run(suite_id, run_id)

        yaml_text = dump_yaml(plan.effective_document)
        config_sha256 = (
            "sha256:"
            + hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
        )
        new_job, job_dir = self._prepare_job(
            config_ref=config.config_ref,
            request_id=request_id,
            request_hash=request_hash,
            config_sha256=config_sha256,
            suite_id=suite_id,
            run_id=run_id,
            plan=plan,
            yaml_text=yaml_text,
        )
        try:
            created = self.store.create_or_get(
                new_job,
                max_queued_jobs=self.max_queued_jobs,
            )
        except BaseException:
            _remove_job_dir(job_dir)
            raise
        if not created.created:
            _remove_job_dir(job_dir)
        self.manager.enqueue()
        return JobStartResult(
            job=self.get(created.record.job_id),
            created=created.created,
        )

    def get(self, job_id: str) -> JobDetail:
        record = self.manager.reconcile(self.store.get(_validate_job_id(job_id)))
        return self._detail(record)

    def list(
        self,
        *,
        states: Sequence[JobState] = (),
        cursor: str | None = None,
        limit: int | None = None,
    ) -> JobPage:
        page_size = self.default_page_size if limit is None else limit
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size < 1
        ):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "limit must be a positive integer",
            )
        if page_size > self.max_page_size:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"limit must be <= {self.max_page_size}",
            )
        before = _decode_cursor(cursor) if cursor else None
        records = self.store.list_records(
            limit=page_size + 1,
            states=states,
            before=before,
        )
        visible = tuple(
            (
                self.manager.reconcile(record)
                if record.state not in TERMINAL_JOB_STATES
                else record
            )
            for record in records[:page_size]
        )
        next_cursor = (
            _encode_cursor(
                visible[-1].created_at,
                visible[-1].job_id,
            )
            if len(records) > page_size and visible
            else None
        )
        return JobPage(
            items=tuple(_catalog_entry(record) for record in visible),
            next_cursor=next_cursor,
        )

    def read_log(self, job_id: str, *, max_bytes: int) -> str:
        """Return bounded, credential-filtered tails from one worker."""
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes < 4
        ):
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "max_bytes must be an integer of at least 4",
            )
        record = self.store.get(_validate_job_id(job_id))
        job_dir = self.manager._job_dir(record.job_id)
        per_stream = max(128, (max_bytes - 128) // 2)
        sections = []
        for label, name in (
            ("stdout", "stdout.log"),
            ("stderr", "stderr.log"),
        ):
            path = self.manager._job_file(job_dir, name)
            text = _read_text_tail(path, max_bytes=per_stream)
            if text:
                sections.append(f"--- {label} (filtered tail) ---\n{text}")
        combined = (
            "\n".join(sections)
            if sections
            else "No worker output is available."
        )
        combined = sanitize_text(combined)
        combined = redact_path_prefixes(
            combined,
            (
                self.workspace.root,
                self.workspace.configs_root,
                self.workspace.artifacts_root,
                self.workspace.results_root,
            ),
        )
        encoded = combined.encode("utf-8")
        if len(encoded) > max_bytes:
            combined = encoded[-max_bytes:].decode(
                "utf-8",
                errors="ignore",
            )
        return combined

    def _prepare_job(
        self,
        *,
        config_ref: str,
        request_id: str,
        request_hash: str,
        config_sha256: str,
        suite_id: str,
        run_id: str | None,
        plan: Any,
        yaml_text: str,
    ) -> tuple[NewJob, Path]:
        jobs_root = _jobs_root(self.workspace)
        jobs_root.mkdir(parents=True, exist_ok=True)
        jobs_root = _jobs_root(self.workspace)
        for _ in range(_JOB_ID_RETRIES):
            job_id = uuid.uuid4().hex
            job_dir = self.workspace.path_policy.resolve_managed_output(
                jobs_root / job_id,
                field_name="evaluation job directory",
                expected_root=jobs_root,
                reject_links=True,
            )
            try:
                job_dir.mkdir()
            except FileExistsError:
                continue
            break
        else:
            raise ServiceError(
                ServiceErrorCode.CONFLICT,
                "Could not allocate a unique evaluation job id",
            )
        try:
            snapshot = job_dir / "config.yaml"
            request_path = job_dir / "request.json"
            result_token = secrets.token_hex(32)
            write_text_atomic(snapshot, yaml_text)
            force_stages = [
                stage.name for stage in plan.stages if stage.forced
            ]
            write_json(
                request_path,
                {
                    "schema_version": 1,
                    "job_id": job_id,
                    "result_token": result_token,
                    "config_ref": config_ref,
                    "config_sha256": config_sha256,
                    "strict": bool(plan.strict),
                    "force_stages": force_stages,
                    "max_log_bytes": self.manager.max_log_bytes,
                },
            )
            resource_keys = []
            # Reuse must not race a concurrent job that replaces active suite
            # artifacts after this job's preflight selected them.
            if any(
                stage.scope == "suite"
                and stage.action is not StageAction.DISABLED
                for stage in plan.stages
            ):
                resource_keys.append(f"suite:{suite_id}")
            if run_id is not None:
                resource_keys.append(f"run:{suite_id}/{run_id}")
            return (
                NewJob(
                    job_id=job_id,
                    idempotency_key=request_id,
                    request_hash=request_hash,
                    suite_id=suite_id,
                    run_id=run_id,
                    config_ref=config_ref,
                    config_sha256=config_sha256,
                    snapshot_path=str(snapshot),
                    request_path=str(request_path),
                    resource_keys=tuple(resource_keys),
                ),
                job_dir,
            )
        except BaseException:
            _remove_job_dir(job_dir)
            raise

    def _reject_existing_run(
        self,
        suite_id: str,
        run_id: str,
    ) -> None:
        suite_root = self.workspace.path_policy.resolve_managed_output(
            self.workspace.results_root / suite_id,
            field_name="job suite root",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        run_root = self.workspace.path_policy.resolve_managed_output(
            suite_root / run_id,
            field_name="job run root",
            expected_root=suite_root,
            reject_links=True,
        )
        if run_root.exists():
            raise ServiceError(
                ServiceErrorCode.CONFLICT,
                "The requested suite/run output already exists",
            )

    def _detail(self, record: JobRecord) -> JobDetail:
        manifest = self._manifest(record)
        heartbeat_at = _optional_text(manifest.get("heartbeat_at"))
        terminal_result = (
            JobTerminalResult.model_validate(record.result)
            if record.result is not None
            else None
        )
        return JobDetail(
            **_catalog_entry(record).model_dump(),
            request_id=record.idempotency_key,
            config_sha256=record.config_sha256,
            heartbeat_at=heartbeat_at,
            heartbeat_age_seconds=_heartbeat_age(heartbeat_at),
            stages=(
                dict(manifest.get("stages") or {})
                if isinstance(manifest.get("stages"), dict)
                else {}
            ),
            stage_timings=(
                dict(manifest.get("stage_timings") or {})
                if isinstance(manifest.get("stage_timings"), dict)
                else {}
            ),
            progress=(
                dict(manifest.get("progress") or {})
                if isinstance(manifest.get("progress"), dict)
                else {}
            ),
            terminal_result=terminal_result,
            error_code=record.error_code,
            error_message=record.error_message,
            resources=_job_resources(record),
        )

    def _manifest(self, record: JobRecord) -> dict[str, Any]:
        if record.run_id is None:
            return {}
        suite_root = self.workspace.path_policy.resolve_managed_output(
            self.workspace.results_root / record.suite_id,
            field_name="job suite root",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        run_root = self.workspace.path_policy.resolve_managed_output(
            suite_root / record.run_id,
            field_name="job run root",
            expected_root=suite_root,
            reject_links=True,
        )
        manifest_path = self.workspace.path_policy.resolve_managed_output(
            run_root / "manifest.json",
            field_name="job run manifest",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        payload = _read_json_file(
            manifest_path,
            max_bytes=_JOB_RESULT_MAX_BYTES,
        )
        return payload if isinstance(payload, dict) else {}


def _catalog_entry(record: JobRecord) -> JobCatalogEntry:
    return JobCatalogEntry(
        job_id=record.job_id,
        state=record.state,
        revision=record.revision,
        kind="evaluation",
        config_ref=record.config_ref,
        suite_id=record.suite_id,
        run_id=record.run_id,
        created_at=record.created_at,
        started_at=record.started_at,
        ended_at=record.ended_at,
    )


def _job_resources(record: JobRecord) -> dict[str, str]:
    resources = {
        "config": f"assert://config/{quote(record.config_ref, safe='')}",
        "worker_log": f"assert://job/{quote(record.job_id, safe='')}/log",
    }
    if record.run_id is not None:
        suite_id = quote(record.suite_id, safe="")
        run_id = quote(record.run_id, safe="")
        resources.update(
            {
                "run_summary": (
                    f"assert://run/{suite_id}/{run_id}/summary"
                ),
                "run_manifest": (
                    f"assert://run/{suite_id}/{run_id}/manifest"
                ),
                "run_config": (
                    f"assert://run/{suite_id}/{run_id}/config"
                ),
            }
        )
    return resources


def _jobs_root(workspace: WorkspaceService) -> Path:
    return workspace.path_policy.resolve_managed_output(
        workspace.artifacts_root / "mcp" / "jobs",
        field_name="evaluation jobs root",
        expected_root=workspace.artifacts_root,
        reject_links=True,
    )


def _request_hash(
    *,
    config_ref: str,
    config_etag: str,
    overrides: EvaluationOverrides,
) -> str:
    payload = json.dumps(
        {
            "config_ref": config_ref,
            "config_etag": config_etag,
            "overrides": overrides.model_dump(mode="json"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _require_ready(plan: Any) -> None:
    if plan.ready:
        return
    raise ServiceError(
        ServiceErrorCode.PREFLIGHT_FAILED,
        "Evaluation preflight has blocking issues",
        details={
            "validation": plan.validation.model_dump(mode="json"),
            "blocking_issues": [
                issue.model_dump(mode="json")
                for issue in plan.blocking_issues
            ],
        },
    )


def _require_source_etag(actual: str, expected: str) -> None:
    if actual == expected:
        return
    raise ServiceError(
        ServiceErrorCode.STALE_ETAG,
        "Config changed while the evaluation request was being prepared; retry",
        details={
            "expected_etag": expected,
            "current_etag": actual,
        },
    )


def _validate_request_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "request_id must be a non-empty string",
        )
    normalized = value.strip()
    if len(normalized) > _REQUEST_ID_MAX_LENGTH:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"request_id must be <= {_REQUEST_ID_MAX_LENGTH} characters",
        )
    return normalized


def _validate_job_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "job_id must be a valid ASSERT job id",
        ) from exc
    normalized = parsed.hex
    if normalized != value:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "job_id must use the canonical ASSERT job-id format",
        )
    return normalized


def _new_identity(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{timestamp}-{secrets.token_hex(8)}"


def _encode_cursor(created_at: str, job_id: str) -> str:
    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "created_at": created_at,
            "job_id": job_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 4096:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid job cursor",
        )
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(value + padding).decode("utf-8")
        )
        if (
            not isinstance(payload, dict)
            or payload.get("v") != _CURSOR_VERSION
            or not isinstance(payload.get("created_at"), str)
            or not isinstance(payload.get("job_id"), str)
        ):
            raise ValueError
        parsed_at = datetime.fromisoformat(payload["created_at"])
        if parsed_at.tzinfo is None:
            raise ValueError
        _validate_job_id(payload["job_id"])
    except (
        binascii.Error,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid job cursor",
        ) from exc
    return payload["created_at"], payload["job_id"]


def _read_json_file(path: Path, *, max_bytes: int) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text_tail(path: Path, *, max_bytes: int) -> str:
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - max_bytes))
            value = stream.read(max_bytes)
    except OSError as exc:
        raise ServiceError(
            ServiceErrorCode.INTERNAL,
            "Could not read the evaluation worker log",
        ) from exc
    return value.decode("utf-8", errors="replace")


def _remove_job_dir(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
    except OSError:
        log.warning(
            "Could not remove unused evaluation job directory %s",
            path,
            exc_info=True,
        )


def _process_create_time(pid: int) -> float:
    import psutil

    return float(psutil.Process(pid).create_time())


def _terminate_failed_launch(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        log.exception(
            "Could not terminate partially launched evaluation worker %s",
            process.pid,
        )


def _process_matches(pid: int, create_time: float) -> bool:
    try:
        import psutil

        process = psutil.Process(pid)
        return (
            process.is_running()
            and abs(process.create_time() - create_time) < 0.01
        )
    except psutil.Error:
        return False


def _lease_expired(value: str | None) -> bool:
    if value is None:
        return True
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return True
        return parsed <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _heartbeat_age(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        heartbeat = datetime.fromisoformat(value)
        if heartbeat.tzinfo is None:
            return None
    except (TypeError, ValueError):
        return None
    return max(
        0.0,
        (datetime.now(timezone.utc) - heartbeat).total_seconds(),
    )


def _safe_error(
    error: Exception,
    *,
    workspace: WorkspaceService,
    fallback: str,
) -> str:
    return _safe_text(
        str(error),
        workspace=workspace,
        fallback=fallback,
    ) or fallback


def _safe_text(
    value: Any,
    *,
    workspace: WorkspaceService,
    fallback: str | None,
) -> str | None:
    if value is None:
        return fallback
    sanitized = sanitize_text(str(value))
    sanitized = redact_path_prefixes(
        sanitized,
        (
            workspace.root,
            workspace.configs_root,
            workspace.artifacts_root,
            workspace.results_root,
        ),
    )
    return sanitized or fallback


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _validated_optional_text(
    value: Any,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ServiceError(
            ServiceErrorCode.RUN_FAILED,
            f"Evaluation worker returned an invalid {field_name}",
        )
    return value
