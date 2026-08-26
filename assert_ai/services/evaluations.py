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
import re
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import yaml

from assert_ai.config import parse_model_config
from assert_ai.core.config_model import DEFAULT_INFERENCE_CONCURRENCY
from assert_ai.core.io import write_bytes_atomic, write_json, write_text_atomic
from assert_ai.core.jsonl_index import JsonlIndexError, scan_jsonl
from assert_ai.core.config_document import PIPELINE_STAGE_ORDER
from assert_ai.core.otel import parse_otel_trace_document
from assert_ai.core.security import (
    redact_path_prefixes,
    sanitize_payload,
    sanitize_text,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.core.yaml_io import dump_yaml
from assert_ai.services.artifact_pins import load_artifact_pin
from assert_ai.services.configs import ConfigRecord, ConfigService
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
    TraceJudgingPreflight,
)
from assert_ai.services.job_store import JobStore
from assert_ai.services.output_identity import (
    run_resource_key,
    suite_resource_key,
    validate_output_id,
)
from assert_ai.services.run_planning import (
    EvaluationOverrides,
    RunPlanningService,
    StageAction,
)

_CURSOR_VERSION = 1
_JOB_RESULT_MAX_BYTES = 1024 * 1024
_JOB_SNAPSHOT_MAX_BYTES = 16 * 1024 * 1024
_JOB_ID_RETRIES = 5
_LEASE_SECONDS = 60.0
_LEASE_RENEW_SECONDS = 15.0
_DEFAULT_CANCELLATION_GRACE_SECONDS = 10.0
_PROCESS_EXIT_TIMEOUT_SECONDS = 5.0
_RECOVERY_POLL_SECONDS = 0.25
_MAX_RECOVERY_SLEEP_SECONDS = 30.0
_CANCELLATION_POLL_SECONDS = 0.1
_REQUEST_ID_MAX_LENGTH = 200
_MIN_LOG_BYTES = 4096
_MAX_LOG_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_TRACE_INPUT_BYTES = 64 * 1024 * 1024
_GROUP_BY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SUPPORTED_JOB_KINDS = frozenset({"evaluation", "trace_judging"})

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _TraceInputs:
    config: ConfigRecord
    trace_path: Path
    trace_ref: str
    trace_bytes: bytes
    trace_etag: str


@dataclass(frozen=True, slots=True)
class _TracePlan:
    inputs: _TraceInputs
    suite_id: str | None
    run_id: str | None
    group_by: str
    session_count: int
    estimated_judge_calls: int
    judge_model: str
    concurrency: int
    taxonomy_path: Path
    taxonomy_ref: str
    taxonomy_bytes: bytes
    taxonomy_etag: str
    warnings: tuple[str, ...]


@dataclass(slots=True)
class EvaluationJobManager:
    """Launch queued jobs and reconcile their terminal worker results."""

    workspace: WorkspaceService
    store: JobStore
    max_active_jobs: int = 1
    max_log_bytes: int = 1024 * 1024
    launch_enabled: bool = True
    job_kinds: tuple[str, ...] = ("evaluation", "trace_judging")
    lease_seconds: float = _LEASE_SECONDS
    cancellation_grace_seconds: float = (
        _DEFAULT_CANCELLATION_GRACE_SECONDS
    )
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
    _monitored_jobs: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
    )
    _cancellation_jobs: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
    )
    _recovery: threading.Thread | None = field(
        default=None,
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
        if self.cancellation_grace_seconds <= 0:
            raise ValueError(
                "cancellation_grace_seconds must be positive"
            )
        self.job_kinds = tuple(dict.fromkeys(self.job_kinds))
        if (
            (self.launch_enabled and not self.job_kinds)
            or any(kind not in _SUPPORTED_JOB_KINDS for kind in self.job_kinds)
        ):
            raise ValueError("job_kinds must contain supported job kinds")

    def start(self) -> None:
        """Recover persisted work and then schedule queued jobs."""
        if not self.launch_enabled:
            return
        with self._lock:
            if self._recovery is not None and self._recovery.is_alive():
                return
            self._recovery = threading.Thread(
                target=self._recover_startup,
                name="assert-mcp-job-recovery",
                daemon=True,
            )
            self._recovery.start()

    def cancel(self, job_id: str) -> JobRecord:
        """Persist cancellation and enforce it outside the request thread."""
        if not self.launch_enabled:
            raise ServiceError(
                ServiceErrorCode.CAPABILITY_DISABLED,
                "Evaluation execution is disabled for this service",
            )
        current = self.store.get(job_id)
        if current.kind not in self.job_kinds:
            raise ServiceError(
                ServiceErrorCode.CAPABILITY_DISABLED,
                f"The {current.kind} job kind is not controllable by this server",
            )
        record = self.store.request_cancel(job_id)
        if record.state is JobState.CANCELLING:
            self._write_cancel_marker(record)
            self._ensure_cancellation(record)
        return record

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
        if not self.launch_enabled or record.kind not in _SUPPORTED_JOB_KINDS:
            return record
        if record.state is JobState.QUEUED:
            if record.kind in self.job_kinds:
                self.enqueue()
            return record
        if (
            record.state is JobState.STARTING
            and record.pid is None
            and record.lease_owner == self._owner
            and not _lease_expired(record.lease_expires_at)
        ):
            return record
        adopted_worker = False
        if record.lease_owner != self._owner:
            if not _lease_expired(record.lease_expires_at):
                return record
            adopted = self.store.adopt_lease(
                record.job_id,
                lease_owner=self._owner,
                lease_seconds=self.lease_seconds,
            )
            if adopted is None:
                return self.store.get(record.job_id)
            record = adopted
            adopted_worker = True
        elif _lease_expired(record.lease_expires_at):
            adopted = self.store.adopt_lease(
                record.job_id,
                lease_owner=self._owner,
                lease_seconds=self.lease_seconds,
            )
            if adopted is not None:
                record = adopted
                adopted_worker = True
        process_alive = (
            record.pid is not None
            and record.process_create_time is not None
            and _process_matches(
                record.pid,
                record.process_create_time,
            )
        )
        result = self._read_result(record)
        if result is not None and not process_alive:
            try:
                return self._adopt_result(
                    record,
                    result,
                    lease_owner=self._owner,
                )
            except Exception as exc:  # noqa: BLE001 - persisted boundary
                log.exception(
                    "Could not reconcile evaluation job %s",
                    record.job_id,
                )
                return self._mark_internal_failure(
                    record,
                    exc,
                    lease_owner=self._owner,
                )
        if process_alive:
            if adopted_worker:
                self._ensure_recovered_monitor(record)
            if record.state is JobState.CANCELLING:
                try:
                    self._write_cancel_marker(record)
                    self._ensure_cancellation(record)
                except Exception:
                    log.exception(
                        "Could not enforce cancellation for evaluation job %s",
                        record.job_id,
                    )
            return record
        if record.state is JobState.CANCELLING:
            return self._mark_cancelled_without_result(
                record,
                lease_owner=self._owner,
            )
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
            lease_owner=self._owner,
        )

    def _schedule(self) -> None:
        try:
            while True:
                with self._lock:
                    self._schedule_requested = False
                try:
                    self._sweep_cancelling_jobs()
                except Exception:  # noqa: BLE001 - daemon boundary
                    log.exception(
                        "Evaluation scheduler could not sweep cancelling jobs"
                    )
                    return
                try:
                    claimed = self.store.claim_next(
                        lease_owner=self._owner,
                        lease_seconds=self.lease_seconds,
                        max_active_jobs=self.max_active_jobs,
                        job_kinds=self.job_kinds,
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
                    self._monitored_jobs.add(claimed.job_id)
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

    def _sweep_cancelling_jobs(self) -> None:
        for record in self.store.list_nonterminal_records():
            if record.state is not JobState.CANCELLING:
                continue
            try:
                self.reconcile(record)
            except Exception:  # noqa: BLE001 - scheduler boundary
                log.exception(
                    "Could not reconcile cancelling evaluation job %s",
                    record.job_id,
                )

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
            if record.state in TERMINAL_JOB_STATES:
                return
            payload = self._read_result(record)
            if payload is None:
                if record.state is JobState.CANCELLING:
                    self._mark_cancelled_without_result(
                        record,
                        lease_owner=self._owner,
                    )
                else:
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
                self._monitored_jobs.discard(job_id)
            self.enqueue()

    def _recover_startup(self) -> None:
        try:
            while self.launch_enabled:
                next_lease_check: float | None = None
                has_queued_job = False
                try:
                    records = self.store.list_nonterminal_records()
                except Exception:  # noqa: BLE001 - daemon boundary
                    log.exception("Could not scan evaluation jobs for recovery")
                    return
                for record in records:
                    if record.state is JobState.QUEUED:
                        has_queued_job = (
                            has_queued_job or record.kind in self.job_kinds
                        )
                        continue
                    try:
                        current = self.reconcile(record)
                    except Exception:  # noqa: BLE001 - persisted boundary
                        log.exception(
                            "Could not recover evaluation job %s",
                            record.job_id,
                        )
                        continue
                    if (
                        current.state not in TERMINAL_JOB_STATES
                        and current.lease_owner != self._owner
                    ):
                        lease_wait = _lease_seconds_remaining(
                            current.lease_expires_at
                        )
                        next_lease_check = (
                            lease_wait
                            if next_lease_check is None
                            else min(next_lease_check, lease_wait)
                        )
                if has_queued_job:
                    self.enqueue()
                if next_lease_check is None:
                    return
                time.sleep(
                    min(
                        _MAX_RECOVERY_SLEEP_SECONDS,
                        max(_RECOVERY_POLL_SECONDS, next_lease_check),
                    )
                )
        finally:
            with self._lock:
                if self._recovery is threading.current_thread():
                    self._recovery = None

    def _ensure_recovered_monitor(self, record: JobRecord) -> None:
        if record.pid is None or record.process_create_time is None:
            return
        with self._lock:
            if record.job_id in self._monitored_jobs:
                return
            self._monitored_jobs.add(record.job_id)
        monitor = threading.Thread(
            target=self._monitor_recovered,
            args=(
                record.job_id,
                record.pid,
                record.process_create_time,
            ),
            name=f"assert-mcp-recovered-{record.job_id[:8]}",
            daemon=True,
        )
        monitor.start()

    def _monitor_recovered(
        self,
        job_id: str,
        pid: int,
        process_create_time: float,
    ) -> None:
        poll_seconds = min(
            _LEASE_RENEW_SECONDS,
            max(0.05, self.lease_seconds / 3),
        )
        try:
            while _process_matches(pid, process_create_time):
                time.sleep(poll_seconds)
                if not self.store.renew_lease(
                    job_id,
                    lease_owner=self._owner,
                    lease_seconds=self.lease_seconds,
                ):
                    return
            record = self.store.get(job_id)
            if record.state in TERMINAL_JOB_STATES:
                return
            payload = self._read_result(record)
            if payload is not None:
                self._adopt_result(
                    record,
                    payload,
                    lease_owner=self._owner,
                )
            elif record.state is JobState.CANCELLING:
                self._mark_cancelled_without_result(
                    record,
                    lease_owner=self._owner,
                )
            else:
                self.store.mark_terminal(
                    job_id,
                    state=JobState.INTERRUPTED,
                    exit_code=record.exit_code,
                    failed_stage=record.failed_stage,
                    error_code=ServiceErrorCode.JOB_INTERRUPTED.value,
                    error_message=(
                        "Evaluation worker exited without a terminal result"
                    ),
                    result=None,
                    run_root=record.run_root,
                    lease_owner=self._owner,
                )
        except Exception:  # noqa: BLE001 - daemon boundary
            log.exception(
                "Could not monitor recovered evaluation job %s",
                job_id,
            )
        finally:
            with self._lock:
                self._monitored_jobs.discard(job_id)
            self.enqueue()

    def _ensure_cancellation(self, record: JobRecord) -> None:
        with self._lock:
            if record.job_id in self._cancellation_jobs:
                return
            self._cancellation_jobs.add(record.job_id)
        thread = threading.Thread(
            target=self._enforce_cancellation,
            args=(record.job_id,),
            name=f"assert-mcp-cancel-{record.job_id[:8]}",
            daemon=True,
        )
        thread.start()

    def _enforce_cancellation(self, job_id: str) -> None:
        observation_deadline = (
            time.monotonic() + self.cancellation_grace_seconds
        )
        teardown_deadline: float | None = None
        try:
            while True:
                record = self.store.get(job_id)
                if record.state in TERMINAL_JOB_STATES:
                    return
                process_alive = (
                    record.pid is not None
                    and record.process_create_time is not None
                    and _process_matches(
                        record.pid,
                        record.process_create_time,
                    )
                )
                if not process_alive:
                    current = self.reconcile(record)
                    if current.state in TERMINAL_JOB_STATES:
                        return
                    time.sleep(
                        (
                            _CANCELLATION_POLL_SECONDS
                            if current.lease_owner == self._owner
                            else _cancellation_wait_seconds(current)
                        )
                    )
                    continue

                now = time.monotonic()
                if (
                    teardown_deadline is None
                    and self._cancellation_acknowledged(record)
                ):
                    teardown_deadline = (
                        now + _PROCESS_EXIT_TIMEOUT_SECONDS
                    )
                deadline = (
                    teardown_deadline
                    if teardown_deadline is not None
                    else observation_deadline
                )
                if now < deadline:
                    time.sleep(
                        min(_CANCELLATION_POLL_SECONDS, deadline - now)
                    )
                    continue

                if record.lease_owner != self._owner:
                    if not _lease_expired(record.lease_expires_at):
                        time.sleep(_cancellation_wait_seconds(record))
                        continue
                    adopted = self.store.adopt_lease(
                        record.job_id,
                        lease_owner=self._owner,
                        lease_seconds=self.lease_seconds,
                    )
                    if adopted is None:
                        time.sleep(_CANCELLATION_POLL_SECONDS)
                        continue
                    record = adopted
                if (
                    record.pid is None
                    or record.process_create_time is None
                    or not _process_matches(
                        record.pid,
                        record.process_create_time,
                    )
                ):
                    continue
                self.store.append_event(
                    job_id,
                    "termination_escalated",
                    {"state": JobState.CANCELLING.value},
                )
                _terminate_process_tree(
                    record.pid,
                    record.process_create_time,
                    timeout_seconds=_PROCESS_EXIT_TIMEOUT_SECONDS,
                )
                settle_deadline = (
                    time.monotonic() + _PROCESS_EXIT_TIMEOUT_SECONDS
                )
                while time.monotonic() < settle_deadline:
                    current = self.store.get(job_id)
                    if current.state in TERMINAL_JOB_STATES:
                        return
                    if (
                        current.pid is None
                        or current.process_create_time is None
                        or not _process_matches(
                            current.pid,
                            current.process_create_time,
                        )
                    ):
                        payload = self._read_result(current)
                        if payload is not None:
                            self._adopt_result(
                                current,
                                payload,
                                lease_owner=self._owner,
                            )
                        else:
                            self._mark_cancelled_without_result(
                                current,
                                lease_owner=self._owner,
                            )
                        return
                    time.sleep(0.05)
                current = self.store.get(job_id)
                if (
                    current.state not in TERMINAL_JOB_STATES
                    and current.pid is not None
                    and current.process_create_time is not None
                    and _process_matches(
                        current.pid,
                        current.process_create_time,
                    )
                ):
                    raise RuntimeError(
                        "Evaluation worker remained alive after termination"
                    )
                self.reconcile(current)
                return
        except Exception:  # noqa: BLE001 - daemon boundary
            log.exception(
                "Could not enforce cancellation for evaluation job %s",
                job_id,
            )
        finally:
            with self._lock:
                self._cancellation_jobs.discard(job_id)
            self.enqueue()

    def _cancellation_acknowledged(self, record: JobRecord) -> bool:
        marker = self._job_file(
            self._job_dir(record.job_id),
            "cancel.acknowledged",
        )
        return marker.is_file()

    def _write_cancel_marker(self, record: JobRecord) -> None:
        job_dir = self._job_dir(record.job_id)
        marker = self._job_file(job_dir, "cancel.requested")
        write_text_atomic(
            marker,
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": record.job_id,
                    "requested_at": record.cancel_requested_at,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )

    def _mark_cancelled_without_result(
        self,
        record: JobRecord,
        *,
        lease_owner: str | None = None,
    ) -> JobRecord:
        failed_stage = record.failed_stage or _active_stage_from_events(
            self.store.list_events(record.job_id, limit=1000)
        )
        if failed_stage is None:
            acknowledgement = _read_json_file(
                self._job_file(
                    self._job_dir(record.job_id),
                    "cancel.acknowledged",
                ),
                max_bytes=_JOB_RESULT_MAX_BYTES,
            )
            if isinstance(acknowledgement, dict):
                acknowledged_stage = acknowledgement.get("stage")
                if isinstance(acknowledged_stage, str):
                    failed_stage = acknowledged_stage
        try:
            run_root = self._write_cancelled_manifest(
                record,
                failed_stage=failed_stage,
            )
        except Exception:  # noqa: BLE001 - terminal persistence wins
            log.exception(
                "Could not write cancelled manifest for evaluation job %s",
                record.job_id,
            )
            run_root = record.run_root
        result = {
            "state": JobState.CANCELLED.value,
            "exit_code": 130,
            "failed_stage": failed_stage,
            "error_code": None,
            "error_message": (
                "Evaluation stopped after cancellation was requested"
            ),
        }
        return self.store.mark_terminal(
            record.job_id,
            state=JobState.CANCELLED,
            exit_code=130,
            failed_stage=failed_stage,
            error_code=None,
            error_message=result["error_message"],
            result=result,
            run_root=run_root,
            lease_owner=lease_owner,
        )

    def _write_cancelled_manifest(
        self,
        record: JobRecord,
        *,
        failed_stage: str | None,
    ) -> str | None:
        if record.run_id is None:
            return record.run_root
        suite_root = self.workspace.path_policy.resolve_managed_output(
            self.workspace.results_root / record.suite_id,
            field_name="cancelled job suite root",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        run_root = self.workspace.path_policy.resolve_managed_output(
            suite_root / record.run_id,
            field_name="cancelled job run root",
            expected_root=suite_root,
            reject_links=True,
        )
        manifest_path = self.workspace.path_policy.resolve_managed_output(
            run_root / "manifest.json",
            field_name="cancelled job manifest",
            expected_root=run_root,
            reject_links=True,
        )
        manifest = _read_json_file(
            manifest_path,
            max_bytes=_JOB_RESULT_MAX_BYTES,
        )
        if not isinstance(manifest, dict):
            return str(run_root) if run_root.is_dir() else record.run_root
        manifest["status"] = "cancelled"
        manifest["ended_at"] = datetime.now(timezone.utc).isoformat()
        manifest["heartbeat_at"] = manifest["ended_at"]
        stages = manifest.get("stages")
        if isinstance(stages, dict) and failed_stage is not None:
            if stages.get(failed_stage) == "running":
                stages[failed_stage] = "cancelled"
        write_json(manifest_path, manifest)
        return str(run_root)

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
        request = _read_bound_request(
            record,
            self._job_file(job_dir, "request.json"),
        )
        if (
            payload.get("result_token") != request.get("result_token")
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
        identity_omitted = (
            result_suite_id is None and result_run_id is None
        )
        may_omit_identity = identity_omitted and (
            state is JobState.CANCELLED
            or (state is JobState.FAILED and failed_stage is None)
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
        if result_suite_id is not None:
            run_root = self._validated_run_root(record, raw_result)
        elif state is JobState.CANCELLED:
            run_root_value = self._write_cancelled_manifest(
                record,
                failed_stage=failed_stage,
            )
            run_root = (
                Path(run_root_value)
                if run_root_value is not None
                else None
            )
        else:
            run_root = None
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
    max_trace_input_bytes: int = _DEFAULT_MAX_TRACE_INPUT_BYTES

    def start(
        self,
        config_ref: str,
        *,
        request_id: str,
        overrides: EvaluationOverrides | None = None,
    ) -> JobStartResult:
        if (
            not self.manager.launch_enabled
            or "evaluation" not in self.manager.job_kinds
        ):
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
        suite_id = validate_output_id(
            applied.suite
            or config.document.get("suite")
            or _new_identity("mcp-suite"),
            field_name="suite_id",
        )
        has_run_stage = any(
            stage.scope == "run"
            and stage.action is not StageAction.DISABLED
            for stage in initial.stages
        )
        run_id = _optional_output_id(
            applied.run
            or config.document.get("run")
            or (_new_identity("run") if has_run_stage else None),
            field_name="run_id",
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

    def preflight_trace_judging(
        self,
        config_ref: str,
        trace_ref: str,
        *,
        group_by: str = "session.id",
        suite_id: str | None = None,
        run_id: str | None = None,
    ) -> TraceJudgingPreflight:
        """Validate a trace import and report its exact judge workload."""
        inputs = self._trace_inputs(config_ref, trace_ref)
        resolved_suite = _optional_output_id(
            suite_id or inputs.config.document.get("suite"),
            field_name="suite_id",
        )
        resolved_run = _optional_output_id(
            run_id or inputs.config.document.get("run"),
            field_name="run_id",
        )
        plan = self._trace_plan(
            inputs,
            group_by=group_by,
            suite_id=resolved_suite,
            run_id=resolved_run,
        )
        return TraceJudgingPreflight(
            ready=True,
            config_ref=inputs.config.config_ref,
            config_etag=inputs.config.etag,
            trace_ref=inputs.trace_ref,
            trace_etag=inputs.trace_etag,
            trace_size_bytes=len(inputs.trace_bytes),
            group_by=plan.group_by,
            session_count=plan.session_count,
            estimated_judge_calls=plan.estimated_judge_calls,
            suite_id=plan.suite_id,
            run_id=plan.run_id,
            judge_model=plan.judge_model,
            taxonomy_ref=plan.taxonomy_ref,
            warnings=plan.warnings,
        )

    def start_trace_judging(
        self,
        config_ref: str,
        trace_ref: str,
        *,
        request_id: str,
        group_by: str = "session.id",
        suite_id: str | None = None,
        run_id: str | None = None,
    ) -> JobStartResult:
        """Snapshot and enqueue one persisted OTLP trace-judging job."""
        if (
            not self.manager.launch_enabled
            or "trace_judging" not in self.manager.job_kinds
        ):
            raise ServiceError(
                ServiceErrorCode.CAPABILITY_DISABLED,
                "Trace judging is disabled for this service",
            )
        request_id = _validate_request_id(request_id)
        inputs = self._trace_inputs(config_ref, trace_ref)
        requested_suite = _optional_output_id(
            suite_id,
            field_name="suite_id",
        )
        requested_run = _optional_output_id(
            run_id,
            field_name="run_id",
        )
        group_by = _validate_group_by(group_by)
        request_hash = _trace_request_hash(
            config_ref=inputs.config.config_ref,
            config_etag=inputs.config.etag,
            trace_ref=inputs.trace_ref,
            trace_etag=inputs.trace_etag,
            group_by=group_by,
            suite_id=requested_suite,
            run_id=requested_run,
        )
        existing = self.store.get_by_idempotency_key(request_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "request_id is already bound to a different trace-judging request",
                    details={"job_id": existing.job_id},
                )
            self.manager.enqueue()
            return JobStartResult(
                job=self.get(existing.job_id),
                created=False,
            )

        allocated_suite = _optional_output_id(
            requested_suite
            or inputs.config.document.get("suite")
            or _new_identity("trace-suite"),
            field_name="suite_id",
        )
        allocated_run = _optional_output_id(
            requested_run
            or inputs.config.document.get("run")
            or _new_identity("trace-run"),
            field_name="run_id",
        )
        assert allocated_suite is not None
        assert allocated_run is not None
        plan = self._trace_plan(
            inputs,
            group_by=group_by,
            suite_id=allocated_suite,
            run_id=allocated_run,
        )
        self._reject_existing_run(allocated_suite, allocated_run)
        new_job, job_dir = self._prepare_trace_job(
            plan,
            request_id=request_id,
            request_hash=request_hash,
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

    def cancel(self, job_id: str) -> JobDetail:
        """Request idempotent cooperative cancellation for one job."""
        record = self.manager.cancel(_validate_job_id(job_id))
        return self._detail(record)

    def retry(
        self,
        job_id: str,
        *,
        request_id: str,
    ) -> JobStartResult:
        """Create an idempotent retry from an immutable terminal snapshot."""
        if not self.manager.launch_enabled:
            raise ServiceError(
                ServiceErrorCode.CAPABILITY_DISABLED,
                "Evaluation execution is disabled for this service",
            )
        job_id = _validate_job_id(job_id)
        request_id = _validate_request_id(request_id)
        original = self.store.get(job_id)
        if original.kind not in self.manager.job_kinds:
            raise ServiceError(
                ServiceErrorCode.CAPABILITY_DISABLED,
                f"The {original.kind} job kind is not controllable by this server",
            )
        original = self.manager.reconcile(original)
        request_hash = _retry_request_hash(
            retry_of=original.job_id,
            config_sha256=original.config_sha256,
            kind=original.kind,
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
        if original.state not in {
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.INTERRUPTED,
        }:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Only failed, cancelled, or interrupted jobs can be retried",
            )
        if original.kind == "trace_judging":
            return self._retry_trace_judging(
                original,
                request_id=request_id,
                request_hash=request_hash,
            )
        if original.kind != "evaluation":
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The persisted job kind is not supported",
            )
        document, request = self._retry_snapshot(original)
        retry_stage = self._retry_stage(original, document)

        run_id = _new_identity("run") if original.run_id is not None else None
        overrides = EvaluationOverrides.model_validate(
            {
                "suite": original.suite_id,
                "run": run_id,
                "force_stages": [retry_stage],
                "strict": bool(request.get("strict", False)),
            }
        )
        plan = self.planning.preflight_document(
            original.config_ref,
            document,
            source_etag=original.config_sha256,
            overrides=overrides,
        )
        _require_ready(plan)
        if plan.suite_id != original.suite_id or plan.run_id != run_id:
            raise ServiceError(
                ServiceErrorCode.INTERNAL,
                "Retry preflight did not preserve allocated evaluation identity",
            )
        if run_id is not None:
            self._reject_existing_run(original.suite_id, run_id)

        yaml_text = dump_yaml(plan.effective_document)
        config_sha256 = (
            "sha256:"
            + hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
        )
        new_job, job_dir = self._prepare_job(
            config_ref=original.config_ref,
            request_id=request_id,
            request_hash=request_hash,
            config_sha256=config_sha256,
            suite_id=original.suite_id,
            run_id=run_id,
            plan=plan,
            yaml_text=yaml_text,
            retry_of=original.job_id,
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

    def _trace_inputs(
        self,
        config_ref: str,
        trace_ref: str,
    ) -> _TraceInputs:
        config = self.configs.get_config(config_ref)
        if not config.validation.valid:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                "Trace judge config validation failed",
                details={
                    "validation": config.validation.model_dump(mode="json"),
                },
            )
        trace_path = self.workspace.resolve_file(
            trace_ref,
            field_name="OTLP trace input",
        )
        _reject_environment_file(trace_path)
        if trace_path.suffix.lower() != ".json":
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "OTLP trace input must be a JSON file",
            )
        trace_bytes = _read_stable_bytes(
            trace_path,
            max_bytes=self.max_trace_input_bytes,
            label="OTLP trace input",
        )
        return _TraceInputs(
            config=config,
            trace_path=trace_path,
            trace_ref=self.workspace.reference(trace_path),
            trace_bytes=trace_bytes,
            trace_etag=_sha256_etag(trace_bytes),
        )

    def _trace_plan(
        self,
        inputs: _TraceInputs,
        *,
        group_by: str,
        suite_id: str | None,
        run_id: str | None,
    ) -> _TracePlan:
        group_by = _validate_group_by(group_by)
        document = inputs.config.document
        pipeline = document.get("pipeline")
        judge = pipeline.get("judge") if isinstance(pipeline, dict) else None
        if not isinstance(judge, dict) or judge.get("enabled", True) is False:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                "Trace judging requires an enabled pipeline.judge stage",
            )
        judge_model = _trace_judge_model(document)
        allowed_patterns = self.planning.policy.allowed_model_patterns
        if allowed_patterns and not any(
            fnmatchcase(judge_model, pattern)
            for pattern in allowed_patterns
        ):
            raise ServiceError(
                ServiceErrorCode.PREFLIGHT_FAILED,
                "The judge model is not allowed by server policy",
                details={"model": judge_model},
            )
        concurrency = _trace_concurrency(
            document,
            maximum=self.planning.policy.max_concurrency,
        )

        taxonomy_path = self._trace_taxonomy_path(
            inputs.config,
            suite_id=suite_id,
        )
        _reject_environment_file(taxonomy_path)
        taxonomy_bytes = _read_stable_bytes(
            taxonomy_path,
            max_bytes=_JOB_SNAPSHOT_MAX_BYTES,
            label="Trace judge taxonomy",
        )
        _validate_taxonomy_bytes(taxonomy_bytes)
        try:
            trace_document = json.loads(inputs.trace_bytes.decode("utf-8"))
            if not isinstance(trace_document, dict):
                raise ValueError("OTLP payload must be an object")
            parsed_rows = parse_otel_trace_document(
                trace_document,
                group_by=group_by,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "OTLP trace input could not be parsed",
            ) from exc
        if not parsed_rows:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "OTLP trace input contains no trace sessions",
            )
        max_sessions = self.planning.policy.max_prompt_sample_size
        if len(parsed_rows) > max_sessions:
            raise ServiceError(
                ServiceErrorCode.PREFLIGHT_FAILED,
                f"Trace input contains {len(parsed_rows)} sessions, exceeding "
                f"the server limit of {max_sessions}",
            )
        empty_sessions = sum(
            1
            for row in parsed_rows
            if not isinstance(row.get("events"), list) or not row["events"]
        )
        warnings = (
            (
                f"{empty_sessions} imported session(s) contain no "
                "judgeable transcript events",
            )
            if empty_sessions
            else ()
        )
        judge_n = judge.get("n", 1)
        if (
            isinstance(judge_n, bool)
            or not isinstance(judge_n, int)
            or judge_n < 1
        ):
            judge_n = 1
        return _TracePlan(
            inputs=inputs,
            suite_id=suite_id,
            run_id=run_id,
            group_by=group_by,
            session_count=len(parsed_rows),
            estimated_judge_calls=len(parsed_rows) * judge_n,
            judge_model=judge_model,
            concurrency=concurrency,
            taxonomy_path=taxonomy_path,
            taxonomy_ref=self.workspace.reference(taxonomy_path),
            taxonomy_bytes=taxonomy_bytes,
            taxonomy_etag=_sha256_etag(taxonomy_bytes),
            warnings=warnings,
        )

    def _trace_taxonomy_path(
        self,
        config: ConfigRecord,
        *,
        suite_id: str | None,
    ) -> Path:
        pipeline = config.document.get("pipeline")
        judge = pipeline.get("judge") if isinstance(pipeline, dict) else None
        raw_path = judge.get("taxonomy_path") if isinstance(judge, dict) else None
        config_path = self.workspace.path_policy.resolve_config_path(
            config.config_ref,
            reject_links=True,
        )
        if isinstance(raw_path, str) and raw_path.strip():
            return self.workspace.path_policy.resolve_input(
                raw_path,
                base_dir=config_path.parent,
                field_name="trace judge taxonomy",
                must_exist=True,
                file_only=True,
            )
        if suite_id is None:
            raise ServiceError(
                ServiceErrorCode.PREFLIGHT_FAILED,
                "Provide suite_id or configure pipeline.judge.taxonomy_path",
            )
        suite_root = self.workspace.path_policy.resolve_managed_output(
            self.workspace.results_root / suite_id,
            field_name="trace judge suite",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        latest_path = self.workspace.path_policy.resolve_managed_output(
            suite_root / "latest.json",
            field_name="trace judge active artifacts",
            expected_root=suite_root,
            reject_links=True,
        )
        latest = _read_json_file(
            latest_path,
            max_bytes=_JOB_RESULT_MAX_BYTES,
        )
        artifacts = latest.get("artifacts") if isinstance(latest, dict) else None
        systematize = (
            artifacts.get("systematize")
            if isinstance(artifacts, dict)
            else None
        )
        version = (
            systematize.get("version")
            if isinstance(systematize, dict)
            else None
        )
        if isinstance(version, str) and re.fullmatch(r"v[0-9]{4,}", version):
            taxonomy_path = suite_root / "artifacts" / "systematize" / version / "taxonomy.json"
        else:
            taxonomy_path = suite_root / "taxonomy.json"
        resolved = self.workspace.path_policy.resolve_managed_output(
            taxonomy_path,
            field_name="trace judge taxonomy",
            expected_root=suite_root,
            reject_links=True,
        )
        if not resolved.is_file():
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                "Trace judge taxonomy was not found",
            )
        return resolved

    def _prepare_trace_job(
        self,
        plan: _TracePlan,
        *,
        request_id: str,
        request_hash: str,
        retry_of: str | None = None,
        base_document: dict[str, Any] | None = None,
    ) -> tuple[NewJob, Path]:
        assert plan.suite_id is not None
        assert plan.run_id is not None
        return self._prepare_trace_job_values(
            config_ref=plan.inputs.config.config_ref,
            base_document=base_document or plan.inputs.config.document,
            trace_ref=plan.inputs.trace_ref,
            trace_bytes=plan.inputs.trace_bytes,
            trace_etag=plan.inputs.trace_etag,
            taxonomy_ref=plan.taxonomy_ref,
            taxonomy_bytes=plan.taxonomy_bytes,
            taxonomy_etag=plan.taxonomy_etag,
            group_by=plan.group_by,
            session_count=plan.session_count,
            concurrency=plan.concurrency,
            suite_id=plan.suite_id,
            run_id=plan.run_id,
            request_id=request_id,
            request_hash=request_hash,
            retry_of=retry_of,
        )

    def _prepare_trace_job_values(
        self,
        *,
        config_ref: str,
        base_document: dict[str, Any],
        trace_ref: str,
        trace_bytes: bytes,
        trace_etag: str,
        taxonomy_ref: str,
        taxonomy_bytes: bytes,
        taxonomy_etag: str,
        group_by: str,
        session_count: int,
        concurrency: int,
        suite_id: str,
        run_id: str,
        request_id: str,
        request_hash: str,
        retry_of: str | None,
    ) -> tuple[NewJob, Path]:
        job_id, job_dir = _allocate_job_dir(self.workspace)
        try:
            run_root = self.workspace.path_policy.resolve_managed_output(
                self.workspace.results_root / suite_id / run_id,
                field_name="trace judge run",
                expected_root=self.workspace.results_root,
                reject_links=True,
            )
            run_taxonomy = self.workspace.path_policy.resolve_managed_output(
                run_root / "taxonomy.json",
                field_name="trace judge taxonomy snapshot",
                expected_root=run_root,
                reject_links=True,
            )
            taxonomy_relative = self.workspace.reference(run_taxonomy)
            effective = deepcopy(base_document)
            pipeline = effective.get("pipeline")
            judge = (
                deepcopy(pipeline.get("judge"))
                if isinstance(pipeline, dict)
                and isinstance(pipeline.get("judge"), dict)
                else None
            )
            if judge is None:
                raise ServiceError(
                    ServiceErrorCode.CONFIG_INVALID,
                    "Trace judge snapshot has no judge stage",
                )
            judge["enabled"] = True
            judge["taxonomy_path"] = taxonomy_relative
            judge.pop("inference_set_path", None)
            judge.pop("save_dir", None)
            effective["pipeline"] = {"judge": judge}
            effective["suite"] = suite_id
            effective["run"] = run_id
            effective.pop("artifacts_root", None)
            effective.pop("results_dir", None)
            yaml_text = dump_yaml(effective)
            config_sha256 = _sha256_etag(yaml_text.encode("utf-8"))

            snapshot = job_dir / "config.yaml"
            request_path = job_dir / "request.json"
            trace_snapshot = job_dir / "trace.json"
            taxonomy_snapshot = job_dir / "taxonomy.json"
            write_text_atomic(snapshot, yaml_text)
            write_bytes_atomic(trace_snapshot, trace_bytes)
            write_bytes_atomic(taxonomy_snapshot, taxonomy_bytes)
            request_sha256 = _write_request_snapshot(
                request_path,
                {
                    "schema_version": 1,
                    "kind": "trace_judging",
                    "job_id": job_id,
                    "result_token": secrets.token_hex(32),
                    "config_ref": config_ref,
                    "config_sha256": config_sha256,
                    "strict": False,
                    "force_stages": ["judge"],
                    "max_log_bytes": self.manager.max_log_bytes,
                    "trace_ref": trace_ref,
                    "trace_sha256": trace_etag,
                    "trace_size_bytes": len(trace_bytes),
                    "taxonomy_ref": taxonomy_ref,
                    "taxonomy_sha256": taxonomy_etag,
                    "group_by": group_by,
                    "session_count": session_count,
                    "concurrency": concurrency,
                    "retry_of": retry_of,
                },
            )
            return (
                NewJob(
                    job_id=job_id,
                    idempotency_key=request_id,
                    request_hash=request_hash,
                    request_sha256=request_sha256,
                    suite_id=suite_id,
                    run_id=run_id,
                    config_ref=config_ref,
                    config_sha256=config_sha256,
                    snapshot_path=str(snapshot),
                    request_path=str(request_path),
                    resource_keys=(run_resource_key(suite_id, run_id),),
                    retry_of=retry_of,
                    kind="trace_judging",
                ),
                job_dir,
            )
        except BaseException:
            _remove_job_dir(job_dir)
            raise

    def _retry_trace_judging(
        self,
        original: JobRecord,
        *,
        request_id: str,
        request_hash: str,
    ) -> JobStartResult:
        document, request = self._retry_snapshot(original)
        if request.get("kind") != "trace_judging":
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable trace job request has an invalid kind",
            )
        job_dir = self.manager._job_dir(original.job_id)
        trace_bytes = _read_integrity_snapshot(
            self.manager._job_file(job_dir, "trace.json"),
            expected_etag=request.get("trace_sha256"),
            max_bytes=self.max_trace_input_bytes,
            label="immutable OTLP trace input",
        )
        taxonomy_bytes = _read_integrity_snapshot(
            self.manager._job_file(job_dir, "taxonomy.json"),
            expected_etag=request.get("taxonomy_sha256"),
            max_bytes=_JOB_SNAPSHOT_MAX_BYTES,
            label="immutable trace taxonomy",
        )
        _validate_taxonomy_bytes(taxonomy_bytes)
        group_by = _validate_group_by(request.get("group_by"))
        try:
            trace_document = json.loads(trace_bytes.decode("utf-8"))
            if not isinstance(trace_document, dict):
                raise ValueError("OTLP payload must be an object")
            parsed_rows = parse_otel_trace_document(
                trace_document,
                group_by=group_by,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable OTLP trace input is invalid",
            ) from exc
        session_count = request.get("session_count")
        if (
            isinstance(session_count, bool)
            or not isinstance(session_count, int)
            or session_count < 1
            or len(parsed_rows) != session_count
        ):
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable trace job session count is invalid",
            )
        maximum_sessions = self.planning.policy.max_prompt_sample_size
        if session_count > maximum_sessions:
            raise ServiceError(
                ServiceErrorCode.PREFLIGHT_FAILED,
                f"Trace input contains {session_count} sessions, exceeding "
                f"the current server limit of {maximum_sessions}",
            )
        judge_model = _trace_judge_model(document)
        allowed_patterns = self.planning.policy.allowed_model_patterns
        if allowed_patterns and not any(
            fnmatchcase(judge_model, pattern)
            for pattern in allowed_patterns
        ):
            raise ServiceError(
                ServiceErrorCode.PREFLIGHT_FAILED,
                "The judge model is not allowed by current server policy",
                details={"model": judge_model},
            )
        stored_concurrency = request.get("concurrency")
        if (
            isinstance(stored_concurrency, bool)
            or not isinstance(stored_concurrency, int)
            or stored_concurrency < 1
        ):
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable trace job concurrency is invalid",
            )
        concurrency = min(
            stored_concurrency,
            self.planning.policy.max_concurrency,
        )
        trace_ref = request.get("trace_ref")
        taxonomy_ref = request.get("taxonomy_ref")
        if not isinstance(trace_ref, str) or not isinstance(taxonomy_ref, str):
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable trace job references are invalid",
            )
        run_id = _new_identity("trace-run")
        self._reject_existing_run(original.suite_id, run_id)
        new_job, new_job_dir = self._prepare_trace_job_values(
            config_ref=original.config_ref,
            base_document=document,
            trace_ref=trace_ref,
            trace_bytes=trace_bytes,
            trace_etag=str(request["trace_sha256"]),
            taxonomy_ref=taxonomy_ref,
            taxonomy_bytes=taxonomy_bytes,
            taxonomy_etag=str(request["taxonomy_sha256"]),
            group_by=group_by,
            session_count=session_count,
            concurrency=concurrency,
            suite_id=original.suite_id,
            run_id=run_id,
            request_id=request_id,
            request_hash=request_hash,
            retry_of=original.job_id,
        )
        try:
            created = self.store.create_or_get(
                new_job,
                max_queued_jobs=self.max_queued_jobs,
            )
        except BaseException:
            _remove_job_dir(new_job_dir)
            raise
        if not created.created:
            _remove_job_dir(new_job_dir)
        self.manager.enqueue()
        return JobStartResult(
            job=self.get(created.record.job_id),
            created=created.created,
        )

    def _retry_snapshot(
        self,
        record: JobRecord,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        job_dir = self.manager._job_dir(record.job_id)
        snapshot_path = self.manager._job_file(job_dir, "config.yaml")
        request_path = self.manager._job_file(job_dir, "request.json")
        try:
            if snapshot_path.stat().st_size > _JOB_SNAPSHOT_MAX_BYTES:
                raise ServiceError(
                    ServiceErrorCode.JOB_INTERRUPTED,
                    "The immutable evaluation snapshot exceeds its size limit",
                )
            snapshot_bytes = snapshot_path.read_bytes()
        except OSError as exc:
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable evaluation snapshot is unavailable",
            ) from exc
        actual_hash = (
            "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest()
        )
        if actual_hash != record.config_sha256:
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable evaluation snapshot failed its integrity check",
            )
        try:
            document = yaml.safe_load(snapshot_bytes.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable evaluation snapshot is invalid",
            ) from exc
        request = _read_bound_request(record, request_path)
        if not isinstance(document, dict) or not isinstance(request, dict):
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable evaluation job inputs are invalid",
            )
        if (
            request.get("job_id") != record.job_id
            or request.get("kind", "evaluation") != record.kind
            or request.get("config_ref") != record.config_ref
            or request.get("config_sha256") != record.config_sha256
            or not isinstance(request.get("strict"), bool)
        ):
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The immutable evaluation job inputs failed their integrity check",
            )
        return document, request

    def _retry_stage(
        self,
        record: JobRecord,
        document: dict[str, Any],
    ) -> str:
        pipeline = document.get("pipeline")
        configured = [
            stage
            for stage in PIPELINE_STAGE_ORDER
            if isinstance(pipeline, dict)
            and isinstance(pipeline.get(stage), dict)
            and pipeline[stage].get("enabled", True)
        ]
        if not configured:
            raise ServiceError(
                ServiceErrorCode.JOB_INTERRUPTED,
                "The evaluation snapshot has no enabled stage to retry",
            )
        candidate = (
            record.failed_stage
            if record.failed_stage in configured
            else _active_stage_from_events(
                self.store.list_events(record.job_id, limit=1000)
            )
        )
        if candidate not in configured:
            candidate = configured[0]

        candidates = [candidate]
        suite_root = self.workspace.path_policy.resolve_managed_output(
            self.workspace.results_root / record.suite_id,
            field_name="retry suite root",
            expected_root=self.workspace.results_root,
            reject_links=True,
        )
        retry_index = PIPELINE_STAGE_ORDER.index(candidate)
        if (
            "test_set" in configured
            and PIPELINE_STAGE_ORDER.index("test_set") < retry_index
        ):
            test_set = suite_root / "test_set.jsonl"
            if not _valid_jsonl(test_set):
                candidates.append("test_set")
        if (
            record.run_id is not None
            and "inference" in configured
            and PIPELINE_STAGE_ORDER.index("inference") < retry_index
        ):
            run_root = self.workspace.path_policy.resolve_managed_output(
                suite_root / record.run_id,
                field_name="retry source run",
                expected_root=suite_root,
                reject_links=True,
            )
            if not _valid_jsonl(run_root / "inference_set.jsonl"):
                candidates.append("inference")
        return min(
            candidates,
            key=PIPELINE_STAGE_ORDER.index,
        )

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
        retry_of: str | None = None,
    ) -> tuple[NewJob, Path]:
        job_id, job_dir = _allocate_job_dir(self.workspace)
        try:
            snapshot = job_dir / "config.yaml"
            request_path = job_dir / "request.json"
            result_token = secrets.token_hex(32)
            write_text_atomic(snapshot, yaml_text)
            force_stages = [
                stage.name for stage in plan.stages if stage.forced
            ]
            expected_artifacts = _artifact_pins(
                self.workspace,
                suite_id=suite_id,
                plan=plan,
            )
            request_sha256 = _write_request_snapshot(
                request_path,
                {
                    "schema_version": 1,
                    "job_id": job_id,
                    "result_token": result_token,
                    "config_ref": config_ref,
                    "config_sha256": config_sha256,
                    "strict": bool(plan.strict),
                    "force_stages": force_stages,
                    "expected_artifacts": expected_artifacts,
                    "max_log_bytes": self.manager.max_log_bytes,
                    "retry_of": retry_of,
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
                resource_keys.append(suite_resource_key(suite_id))
            if expected_artifacts and suite_resource_key(suite_id) not in resource_keys:
                resource_keys.append(suite_resource_key(suite_id))
            if run_id is not None:
                resource_keys.append(run_resource_key(suite_id, run_id))
            return (
                NewJob(
                    job_id=job_id,
                    idempotency_key=request_id,
                    request_hash=request_hash,
                    request_sha256=request_sha256,
                    suite_id=suite_id,
                    run_id=run_id,
                    config_ref=config_ref,
                    config_sha256=config_sha256,
                    snapshot_path=str(snapshot),
                    request_path=str(request_path),
                    resource_keys=tuple(resource_keys),
                    retry_of=retry_of,
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
        event_projection = _event_projection(
            self.store.list_events(record.job_id, limit=1000)
        )
        heartbeat_at = (
            event_projection["heartbeat_at"]
            or _optional_text(manifest.get("heartbeat_at"))
        )
        stages = (
            dict(manifest.get("stages") or {})
            if isinstance(manifest.get("stages"), dict)
            else {}
        )
        stages.update(event_projection["stages"])
        stage_timings = (
            dict(manifest.get("stage_timings") or {})
            if isinstance(manifest.get("stage_timings"), dict)
            else {}
        )
        for stage_name, timing in event_projection["stage_timings"].items():
            existing = dict(stage_timings.get(stage_name) or {})
            existing.update(timing)
            stage_timings[stage_name] = existing
        manifest_progress = (
            dict(manifest.get("progress") or {})
            if isinstance(manifest.get("progress"), dict)
            else {}
        )
        terminal_result = (
            JobTerminalResult.model_validate(record.result)
            if record.result is not None
            else None
        )
        if (
            terminal_result is not None
            and terminal_result.failed_stage is not None
        ):
            stages.setdefault(
                terminal_result.failed_stage,
                (
                    "cancelled"
                    if terminal_result.state == "cancelled"
                    else "failed"
                ),
            )
        return JobDetail(
            **_catalog_entry(record).model_dump(),
            request_id=record.idempotency_key,
            config_sha256=record.config_sha256,
            cancel_requested_at=record.cancel_requested_at,
            heartbeat_at=heartbeat_at,
            heartbeat_age_seconds=_heartbeat_age(heartbeat_at),
            stages=stages,
            stage_timings=stage_timings,
            progress=event_projection["progress"] or manifest_progress,
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
        kind=record.kind,
        retry_of=record.retry_of,
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


def _retry_request_hash(
    *,
    retry_of: str,
    config_sha256: str,
    kind: str,
) -> str:
    payload = json.dumps(
        {
            "operation": f"retry_{kind}",
            "retry_of": retry_of,
            "config_sha256": config_sha256,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _active_stage_from_events(
    events: Sequence[dict[str, Any]],
) -> str | None:
    active: str | None = None
    terminal_stage: str | None = None
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        event_type = event.get("event_type")
        if event_type == "pipeline_finished":
            failed_stage = payload.get("failed_stage")
            if isinstance(failed_stage, str) and failed_stage:
                terminal_stage = failed_stage
            continue
        name = payload.get("name")
        if not isinstance(name, str):
            continue
        if event_type == "stage_started":
            active = name
        elif event_type == "stage_finished":
            if payload.get("state") in {"cancelled", "failed"}:
                terminal_stage = name
            if active == name:
                active = None
    return terminal_stage or active


def _valid_jsonl(path: Path) -> bool:
    try:
        scan = scan_jsonl(path, allow_trailing_partial=False)
    except (JsonlIndexError, OSError):
        return False
    if not scan.records:
        return False
    identities: set[tuple[str, str]] = set()
    for record in scan.records:
        kind = record.row.get("type")
        test_case_id = record.row.get("test_case_id")
        if (
            not isinstance(kind, str)
            or not kind
            or not isinstance(test_case_id, str)
            or not test_case_id
        ):
            return False
        identity = (kind, test_case_id)
        if identity in identities:
            return False
        identities.add(identity)
    return True


def _event_projection(
    events: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    stages: dict[str, str] = {}
    stage_timings: dict[str, dict[str, Any]] = {}
    progress: dict[str, Any] = {}
    heartbeat_at: str | None = None
    for event in events:
        event_type = event.get("event_type")
        timestamp = event.get("timestamp")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if isinstance(timestamp, str):
            heartbeat_at = timestamp
        name = payload.get("name")
        if event_type == "stage_planned" and isinstance(name, str):
            action = payload.get("action")
            if isinstance(action, str):
                stages[name] = action
        elif event_type == "stage_started" and isinstance(name, str):
            stages[name] = "running"
            stage_timings.setdefault(name, {})["started_at"] = timestamp
        elif event_type == "stage_progress" and isinstance(name, str):
            values = payload.get("values")
            if isinstance(values, dict):
                progress = dict(values)
        elif event_type == "stage_finished" and isinstance(name, str):
            state = payload.get("state")
            if isinstance(state, str):
                stages[name] = state
            timing = stage_timings.setdefault(name, {})
            timing["ended_at"] = timestamp
            duration = payload.get("duration_seconds")
            if isinstance(duration, (int, float)) and not isinstance(
                duration, bool
            ):
                timing["duration_secs"] = duration
            progress = {}
    return {
        "stages": stages,
        "stage_timings": stage_timings,
        "progress": progress,
        "heartbeat_at": heartbeat_at,
    }


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


def _optional_output_id(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return validate_output_id(value, field_name=field_name)


def _trace_judge_model(document: dict[str, Any]) -> str:
    pipeline = document.get("pipeline")
    judge = pipeline.get("judge") if isinstance(pipeline, dict) else None
    raw_model = (
        judge.get("model") if isinstance(judge, dict) else None
    ) or document.get("default_model")
    try:
        return parse_model_config(
            raw_model,
            field_name="pipeline.judge.model",
        ).name
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            ServiceErrorCode.CONFIG_INVALID,
            "pipeline.judge.model or default_model is required",
        ) from exc


def _trace_concurrency(
    document: dict[str, Any],
    *,
    maximum: int,
) -> int:
    pipeline = document.get("pipeline")
    inference = (
        pipeline.get("inference")
        if isinstance(pipeline, dict)
        else None
    )
    configured = (
        inference.get("concurrency", DEFAULT_INFERENCE_CONCURRENCY)
        if isinstance(inference, dict)
        else DEFAULT_INFERENCE_CONCURRENCY
    )
    if (
        isinstance(configured, bool)
        or not isinstance(configured, int)
        or configured < 1
    ):
        raise ServiceError(
            ServiceErrorCode.CONFIG_INVALID,
            "pipeline.inference.concurrency must be a positive integer",
        )
    return min(configured, maximum)


def _validate_group_by(value: Any) -> str:
    if not isinstance(value, str) or not _GROUP_BY_RE.fullmatch(value):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "group_by must be a 1-128 character OpenTelemetry attribute name",
        )
    return value


def _trace_request_hash(
    *,
    config_ref: str,
    config_etag: str,
    trace_ref: str,
    trace_etag: str,
    group_by: str,
    suite_id: str | None,
    run_id: str | None,
) -> str:
    payload = json.dumps(
        {
            "operation": "trace_judging",
            "config_ref": config_ref,
            "config_etag": config_etag,
            "trace_ref": trace_ref,
            "trace_etag": trace_etag,
            "group_by": group_by,
            "suite_id": suite_id,
            "run_id": run_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256_etag(payload)


def _sha256_etag(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _read_stable_bytes(
    path: Path,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    try:
        before = path.stat()
        with path.open("rb") as handle:
            value = handle.read(max_bytes + 1)
        after = path.stat()
    except OSError as exc:
        raise ServiceError(
            ServiceErrorCode.NOT_FOUND,
            f"{label} is unavailable",
        ) from exc
    if len(value) > max_bytes:
        raise ServiceError(
            ServiceErrorCode.ARTIFACT_TOO_LARGE,
            f"{label} exceeds the {max_bytes}-byte limit",
        )
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise ServiceError(
            ServiceErrorCode.CONFLICT,
            f"{label} changed while it was being read",
        )
    return value


def _read_integrity_snapshot(
    path: Path,
    *,
    expected_etag: Any,
    max_bytes: int,
    label: str,
) -> bytes:
    if (
        not isinstance(expected_etag, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_etag)
    ):
        raise ServiceError(
            ServiceErrorCode.JOB_INTERRUPTED,
            f"The {label} digest is invalid",
        )
    value = _read_stable_bytes(path, max_bytes=max_bytes, label=label)
    if _sha256_etag(value) != expected_etag:
        raise ServiceError(
            ServiceErrorCode.JOB_INTERRUPTED,
            f"The {label} failed its integrity check",
        )
    return value


def _read_bound_request(
    record: JobRecord,
    path: Path,
) -> dict[str, Any]:
    value = _read_integrity_snapshot(
        path,
        expected_etag=record.request_sha256,
        max_bytes=_JOB_RESULT_MAX_BYTES,
        label="immutable evaluation job request",
    )
    try:
        request = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            ServiceErrorCode.JOB_INTERRUPTED,
            "The immutable evaluation job request is invalid",
        ) from exc
    if not isinstance(request, dict):
        raise ServiceError(
            ServiceErrorCode.JOB_INTERRUPTED,
            "The immutable evaluation job request must contain an object",
        )
    return request


def _artifact_pins(
    workspace: WorkspaceService,
    *,
    suite_id: str,
    plan: Any,
) -> dict[str, dict[str, Any]]:
    pins: dict[str, dict[str, Any]] = {}
    for stage_name, expected in plan.consumed_artifacts.items():
        current = load_artifact_pin(
            workspace,
            suite_id=suite_id,
            stage_name=stage_name,
            version=expected.version,
        )
        if current != expected:
            raise ServiceError(
                ServiceErrorCode.PREFLIGHT_FAILED,
                f"The preflight-selected {stage_name} artifact changed "
                "before the job was registered",
            )
        pins[stage_name] = expected.model_dump(mode="json")
    return pins


def _write_request_snapshot(
    path: Path,
    payload: dict[str, Any],
) -> str:
    value = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    write_bytes_atomic(path, value)
    return _sha256_etag(value)


def _validate_taxonomy_bytes(value: bytes) -> None:
    try:
        taxonomy = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            ServiceErrorCode.CONFIG_INVALID,
            "Trace judge taxonomy is not valid JSON",
        ) from exc
    if (
        not isinstance(taxonomy, dict)
        or not isinstance(taxonomy.get("behavior"), dict)
        or not isinstance(taxonomy.get("behavior_categories"), list)
    ):
        raise ServiceError(
            ServiceErrorCode.CONFIG_INVALID,
            "Trace judge taxonomy has an invalid structure",
        )


def _reject_environment_file(path: Path) -> None:
    for part in path.parts:
        name = part.lower()
        if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            raise ServiceError(
                ServiceErrorCode.WORKSPACE_VIOLATION,
                "Environment files cannot be used as trace-judging inputs",
            )


def _allocate_job_dir(
    workspace: WorkspaceService,
) -> tuple[str, Path]:
    jobs_root = _jobs_root(workspace)
    jobs_root.mkdir(parents=True, exist_ok=True)
    jobs_root = _jobs_root(workspace)
    for _ in range(_JOB_ID_RETRIES):
        job_id = uuid.uuid4().hex
        job_dir = workspace.path_policy.resolve_managed_output(
            jobs_root / job_id,
            field_name="evaluation job directory",
            expected_root=jobs_root,
            reject_links=True,
        )
        try:
            job_dir.mkdir()
        except FileExistsError:
            continue
        return job_id, job_dir
    raise ServiceError(
        ServiceErrorCode.CONFLICT,
        "Could not allocate a unique evaluation job id",
    )


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
            _terminate_process_tree(
                process.pid,
                _process_create_time(process.pid),
                timeout_seconds=_PROCESS_EXIT_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        log.exception(
            "Could not terminate partially launched evaluation worker %s",
            process.pid,
        )


def _terminate_process_tree(
    pid: int,
    create_time: float,
    *,
    timeout_seconds: float,
) -> None:
    """Terminate one identity-verified worker and all descendants."""
    import psutil

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    try:
        process = psutil.Process(pid)
        if (
            not process.is_running()
            or abs(process.create_time() - create_time) >= 0.01
        ):
            return
        descendants = process.children(recursive=True)
    except psutil.Error:
        return

    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            log.warning(
                "Could not terminate evaluation process group %s",
                pid,
                exc_info=True,
            )
            for child in descendants:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            try:
                process.terminate()
            except psutil.Error:
                pass
    else:
        for child in descendants:
            try:
                child.terminate()
            except psutil.Error:
                pass
        try:
            process.terminate()
        except psutil.Error:
            pass

    targets = [*descendants, process]
    _, alive = psutil.wait_procs(targets, timeout=timeout_seconds)
    if not alive:
        return

    if os.name != "nt":
        if process in alive and _process_matches(pid, create_time):
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    for remaining in alive:
        try:
            remaining.kill()
        except psutil.Error:
            pass
    _, still_alive = psutil.wait_procs(alive, timeout=timeout_seconds)
    if still_alive:
        raise RuntimeError(
            "Evaluation process tree did not exit after forced termination"
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


def _lease_seconds_remaining(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return 0.0
    except (TypeError, ValueError):
        return 0.0
    return max(
        0.0,
        (parsed - datetime.now(timezone.utc)).total_seconds(),
    )


def _cancellation_wait_seconds(record: JobRecord) -> float:
    lease_wait = _lease_seconds_remaining(record.lease_expires_at)
    if lease_wait <= 0:
        return _CANCELLATION_POLL_SECONDS
    return min(
        _MAX_RECOVERY_SLEEP_SECONDS,
        max(_CANCELLATION_POLL_SECONDS, lease_wait),
    )


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
