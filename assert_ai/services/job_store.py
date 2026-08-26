# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Transactional SQLite persistence for evaluation jobs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from assert_ai.core.runtime_path_policy import RuntimePathPolicy
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.job_models import (
    CreateJobResult,
    JobRecord,
    JobState,
    NewJob,
    TERMINAL_JOB_STATES,
)

_BUSY_TIMEOUT_MS = 5_000
_JOB_STORE_SCHEMA_VERSION = 3
_ACTIVE_STATES = (
    JobState.STARTING.value,
    JobState.RUNNING.value,
    JobState.CANCELLING.value,
)
_MAX_EVENTS_PER_JOB = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    retry_of TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    suite_id TEXT NOT NULL,
    run_id TEXT,
    config_ref TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    request_path TEXT NOT NULL,
    run_root TEXT,
    pid INTEGER,
    process_create_time REAL,
    exit_code INTEGER,
    failed_stage TEXT,
    error_code TEXT,
    error_message TEXT,
    cancel_requested_at TEXT,
    result_json TEXT,
    resource_keys_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_suite_run_unique
    ON jobs(suite_id, run_id)
    WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS jobs_state_created
    ON jobs(state, created_at, job_id);
CREATE TABLE IF NOT EXISTS job_events(
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(job_id, sequence),
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS resource_locks(
    resource_key TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS operation_locks(
    resource_key TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL
);
"""


class JobStore:
    """Concurrency-safe persisted job registry with expiring leases."""

    def __init__(
        self,
        path: str | Path,
        *,
        path_policy: RuntimePathPolicy | None = None,
        expected_root: str | Path | None = None,
    ) -> None:
        if (path_policy is None) != (expected_root is None):
            raise ValueError(
                "path_policy and expected_root must be provided together"
            )
        self.path = Path(path)
        self._path_policy = path_policy
        self._expected_root = (
            Path(expected_root) if expected_root is not None else None
        )
        self._initialize_lock = threading.Lock()
        self._initialized = False

    @property
    def exists(self) -> bool:
        return self._database_path().is_file()

    def create_or_get(
        self,
        new_job: NewJob,
        *,
        max_queued_jobs: int,
    ) -> CreateJobResult:
        if max_queued_jobs < 1:
            raise ValueError("max_queued_jobs must be positive")
        self.initialize()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (new_job.idempotency_key,),
            ).fetchone()
            if existing is not None:
                record = _record(existing)
                if record.request_hash != new_job.request_hash:
                    raise ServiceError(
                        ServiceErrorCode.CONFLICT,
                        "request_id is already bound to a different evaluation request",
                        details={"job_id": record.job_id},
                    )
                return CreateJobResult(record=record, created=False)

            queued_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state = ?",
                    (JobState.QUEUED.value,),
                ).fetchone()[0]
            )
            if queued_count >= max_queued_jobs:
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "The evaluation queue has reached its operator limit",
                    details={"max_queued_jobs": max_queued_jobs},
                )

            created_at = _now()
            try:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, idempotency_key, request_hash, kind, retry_of,
                        state,
                        created_at, suite_id, run_id, config_ref,
                        config_sha256, snapshot_path, request_path,
                        resource_keys_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_job.job_id,
                        new_job.idempotency_key,
                        new_job.request_hash,
                        new_job.kind,
                        new_job.retry_of,
                        JobState.QUEUED.value,
                        created_at,
                        new_job.suite_id,
                        new_job.run_id,
                        new_job.config_ref,
                        new_job.config_sha256,
                        new_job.snapshot_path,
                        new_job.request_path,
                        _json(new_job.resource_keys),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                collision = connection.execute(
                    "SELECT job_id FROM jobs WHERE suite_id = ? AND run_id = ?",
                    (new_job.suite_id, new_job.run_id),
                ).fetchone()
                if collision is not None:
                    raise ServiceError(
                        ServiceErrorCode.CONFLICT,
                        "The requested suite/run output is already assigned",
                        details={"job_id": str(collision["job_id"])},
                    ) from exc
                raise
            self._append_event(
                connection,
                new_job.job_id,
                "queued",
                {"state": JobState.QUEUED.value},
                timestamp=created_at,
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (new_job.job_id,),
            ).fetchone()
            assert row is not None
            return CreateJobResult(record=_record(row), created=True)

    def get(self, job_id: str) -> JobRecord:
        if not self.exists:
            raise ServiceError(ServiceErrorCode.NOT_FOUND, "Job not found")
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ServiceError(ServiceErrorCode.NOT_FOUND, "Job not found")
        return _record(row)

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> JobRecord | None:
        if not self.exists:
            return None
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _record(row) if row is not None else None

    def list_records(
        self,
        *,
        limit: int,
        states: Sequence[JobState] = (),
        before: tuple[str, str] | None = None,
    ) -> tuple[JobRecord, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if not self.exists:
            return ()
        self.initialize()
        conditions: list[str] = []
        values: list[Any] = []
        if states:
            placeholders = ", ".join("?" for _ in states)
            conditions.append(f"state IN ({placeholders})")
            values.extend(state.value for state in states)
        if before is not None:
            conditions.append(
                "(created_at < ? OR (created_at = ? AND job_id < ?))"
            )
            values.extend((before[0], before[0], before[1]))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM jobs
                {where}
                ORDER BY created_at DESC, job_id DESC
                LIMIT ?
                """,
                tuple(values),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def claim_next(
        self,
        *,
        lease_owner: str,
        lease_seconds: float,
        max_active_jobs: int,
        job_kinds: Sequence[str] = (),
    ) -> JobRecord | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if max_active_jobs < 1:
            raise ValueError("max_active_jobs must be positive")
        if not self.exists:
            return None
        self.initialize()
        now = _now()
        expires_at = _after(lease_seconds)
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM operation_locks WHERE lease_expires_at <= ?",
                (now,),
            )
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state IN (?, ?, ?)",
                    _ACTIVE_STATES,
                ).fetchone()[0]
            )
            if active >= max_active_jobs:
                return None
            kinds = tuple(dict.fromkeys(job_kinds))
            if kinds:
                kind_placeholders = ", ".join("?" for _ in kinds)
                candidates = connection.execute(
                    f"""
                    SELECT * FROM jobs
                    WHERE state = ? AND kind IN ({kind_placeholders})
                    ORDER BY created_at, job_id
                    """,
                    (JobState.QUEUED.value, *kinds),
                ).fetchall()
            else:
                candidates = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE state = ?
                    ORDER BY created_at, job_id
                    """,
                    (JobState.QUEUED.value,),
                ).fetchall()
            for row in candidates:
                record = _record(row)
                if not self._resources_available(
                    connection,
                    record.resource_keys,
                ):
                    continue
                changed = connection.execute(
                    """
                    UPDATE jobs
                    SET state = ?, lease_owner = ?, lease_expires_at = ?,
                        revision = revision + 1
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        JobState.STARTING.value,
                        lease_owner,
                        expires_at,
                        record.job_id,
                        JobState.QUEUED.value,
                    ),
                ).rowcount
                if changed != 1:
                    continue
                for resource_key in record.resource_keys:
                    connection.execute(
                        """
                        INSERT INTO resource_locks(
                            resource_key, job_id, acquired_at,
                            lease_expires_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            resource_key,
                            record.job_id,
                            now,
                            expires_at,
                        ),
                    )
                self._append_event(
                    connection,
                    record.job_id,
                    "starting",
                    {"state": JobState.STARTING.value},
                    timestamp=now,
                )
                claimed = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (record.job_id,),
                ).fetchone()
                assert claimed is not None
                return _record(claimed)
        return None

    def acquire_operation_locks(
        self,
        resource_keys: Sequence[str],
        *,
        owner: str,
        lease_seconds: float,
    ) -> bool:
        """Reserve resources against job claims for one short operation."""
        keys = tuple(dict.fromkeys(resource_keys))
        if not keys:
            raise ValueError("at least one resource key is required")
        if not owner:
            raise ValueError("owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.initialize()
        now = _now()
        expires_at = _after(lease_seconds)
        placeholders = ", ".join("?" for _ in keys)
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM operation_locks WHERE lease_expires_at <= ?",
                (now,),
            )
            active_job = any(
                self._operation_conflicts_with_active_job(
                    connection,
                    resource_key,
                )
                for resource_key in keys
            )
            active_operation = connection.execute(
                f"""
                SELECT 1 FROM operation_locks
                WHERE resource_key IN ({placeholders})
                LIMIT 1
                """,
                keys,
            ).fetchone()
            if active_job or active_operation is not None:
                return False
            connection.executemany(
                """
                INSERT INTO operation_locks(
                    resource_key, owner, acquired_at, lease_expires_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (resource_key, owner, now, expires_at)
                    for resource_key in keys
                ),
            )
        return True

    def release_operation_locks(
        self,
        *,
        owner: str,
        resource_keys: Sequence[str] = (),
    ) -> None:
        """Release operation locks owned by one caller."""
        if not owner:
            raise ValueError("owner is required")
        self.initialize()
        keys = tuple(dict.fromkeys(resource_keys))
        with self._transaction() as connection:
            if keys:
                placeholders = ", ".join("?" for _ in keys)
                connection.execute(
                    f"""
                    DELETE FROM operation_locks
                    WHERE owner = ? AND resource_key IN ({placeholders})
                    """,
                    (owner, *keys),
                )
            else:
                connection.execute(
                    "DELETE FROM operation_locks WHERE owner = ?",
                    (owner,),
                )

    def renew_operation_locks(
        self,
        resource_keys: Sequence[str],
        *,
        owner: str,
        lease_seconds: float,
    ) -> bool:
        """Extend unexpired operation locks when every key is still owned."""
        keys = tuple(dict.fromkeys(resource_keys))
        if not keys:
            raise ValueError("at least one resource key is required")
        if not owner:
            raise ValueError("owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.initialize()
        now = _now()
        expires_at = _after(lease_seconds)
        placeholders = ", ".join("?" for _ in keys)
        with self._transaction() as connection:
            owned = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM operation_locks
                WHERE owner = ?
                  AND lease_expires_at > ?
                  AND resource_key IN ({placeholders})
                """,
                (owner, now, *keys),
            ).fetchone()
            if owned is None or int(owned["count"]) != len(keys):
                return False
            changed = connection.execute(
                f"""
                UPDATE operation_locks
                SET lease_expires_at = ?
                WHERE owner = ?
                  AND lease_expires_at > ?
                  AND resource_key IN ({placeholders})
                """,
                (expires_at, owner, now, *keys),
            ).rowcount
            return changed == len(keys)

    def mark_running(
        self,
        job_id: str,
        *,
        lease_owner: str,
        pid: int,
        process_create_time: float,
        lease_seconds: float,
    ) -> JobRecord:
        self.initialize()
        now = _now()
        expires_at = _after(lease_seconds)
        with self._transaction() as connection:
            current = self._get_in_transaction(connection, job_id)
            if (
                current.lease_owner != lease_owner
                or current.state
                not in {JobState.STARTING, JobState.CANCELLING}
            ):
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "Job can no longer attach its evaluation worker",
                )
            next_state = (
                JobState.CANCELLING
                if current.state is JobState.CANCELLING
                else JobState.RUNNING
            )
            changed = connection.execute(
                """
                UPDATE jobs
                SET state = ?, started_at = COALESCE(started_at, ?),
                    pid = ?, process_create_time = ?,
                    lease_expires_at = ?, revision = revision + 1
                WHERE job_id = ? AND state = ? AND lease_owner = ?
                """,
                (
                    next_state.value,
                    now,
                    pid,
                    process_create_time,
                    expires_at,
                    job_id,
                    current.state.value,
                    lease_owner,
                ),
            ).rowcount
            if changed != 1:
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "Job can no longer transition to running",
                )
            connection.execute(
                """
                UPDATE resource_locks
                SET lease_expires_at = ?
                WHERE job_id = ?
                """,
                (expires_at, job_id),
            )
            self._append_event(
                connection,
                job_id,
                (
                    "running"
                    if next_state is JobState.RUNNING
                    else "cancelling_worker_started"
                ),
                {"state": next_state.value, "pid": pid},
                timestamp=now,
            )
            return self._get_in_transaction(connection, job_id)

    def renew_lease(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: float,
    ) -> bool:
        self.initialize()
        expires_at = _after(lease_seconds)
        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?
                WHERE job_id = ? AND lease_owner = ?
                    AND state IN (?, ?, ?)
                """,
                (
                    expires_at,
                    job_id,
                    lease_owner,
                    JobState.STARTING.value,
                    JobState.RUNNING.value,
                    JobState.CANCELLING.value,
                ),
            ).rowcount
            if changed != 1:
                return False
            connection.execute(
                """
                UPDATE resource_locks
                SET lease_expires_at = ?
                WHERE job_id = ?
                """,
                (expires_at, job_id),
            )
            return True

    def request_cancel(self, job_id: str) -> JobRecord:
        """Persist an idempotent cancellation request."""
        self.initialize()
        now = _now()
        with self._transaction() as connection:
            current = self._get_in_transaction(connection, job_id)
            if current.state is JobState.CANCELLED:
                return current
            if current.state in TERMINAL_JOB_STATES:
                raise ServiceError(
                    ServiceErrorCode.JOB_NOT_CANCELLABLE,
                    f"Job is already {current.state.value}",
                )
            if current.state is JobState.CANCELLING:
                return current
            if current.state is JobState.QUEUED:
                result = {
                    "state": JobState.CANCELLED.value,
                    "exit_code": 130,
                    "failed_stage": None,
                    "error_code": None,
                    "error_message": None,
                }
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = ?, cancel_requested_at = ?, ended_at = ?,
                        exit_code = ?, result_json = ?,
                        lease_owner = NULL, lease_expires_at = NULL,
                        revision = revision + 1
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        JobState.CANCELLED.value,
                        now,
                        now,
                        130,
                        _json(result),
                        job_id,
                        JobState.QUEUED.value,
                    ),
                )
                self._append_event(
                    connection,
                    job_id,
                    "cancelled",
                    {
                        "state": JobState.CANCELLED.value,
                        "exit_code": 130,
                    },
                    timestamp=now,
                )
            else:
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = ?, cancel_requested_at = ?,
                        revision = revision + 1
                    WHERE job_id = ? AND state IN (?, ?)
                    """,
                    (
                        JobState.CANCELLING.value,
                        now,
                        job_id,
                        JobState.STARTING.value,
                        JobState.RUNNING.value,
                    ),
                )
                self._append_event(
                    connection,
                    job_id,
                    "cancel_requested",
                    {"state": JobState.CANCELLING.value},
                    timestamp=now,
                )
            return self._get_in_transaction(connection, job_id)

    def adopt_lease(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: float,
    ) -> JobRecord | None:
        """Claim an expired active-job lease during startup recovery."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.initialize()
        now = _now()
        expires_at = _after(lease_seconds)
        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE jobs
                SET lease_owner = ?, lease_expires_at = ?,
                    revision = revision + 1
                WHERE job_id = ?
                    AND state IN (?, ?, ?)
                    AND (
                        lease_expires_at IS NULL
                        OR lease_expires_at <= ?
                        OR lease_owner = ?
                    )
                """,
                (
                    lease_owner,
                    expires_at,
                    job_id,
                    JobState.STARTING.value,
                    JobState.RUNNING.value,
                    JobState.CANCELLING.value,
                    now,
                    lease_owner,
                ),
            ).rowcount
            if changed != 1:
                return None
            connection.execute(
                """
                UPDATE resource_locks
                SET lease_expires_at = ?
                WHERE job_id = ?
                """,
                (expires_at, job_id),
            )
            self._append_event(
                connection,
                job_id,
                "manager_recovered",
                {"state": self._get_in_transaction(connection, job_id).state.value},
                timestamp=now,
            )
            return self._get_in_transaction(connection, job_id)

    def list_nonterminal_records(
        self,
        *,
        job_kinds: Sequence[str] = (),
    ) -> tuple[JobRecord, ...]:
        """Return every queued or active job for deterministic recovery."""
        if not self.exists:
            return ()
        self.initialize()
        placeholders = ", ".join("?" for _ in TERMINAL_JOB_STATES)
        values: list[str] = [
            state.value for state in TERMINAL_JOB_STATES
        ]
        kinds = tuple(dict.fromkeys(job_kinds))
        kind_clause = ""
        if kinds:
            kind_placeholders = ", ".join("?" for _ in kinds)
            kind_clause = f" AND kind IN ({kind_placeholders})"
            values.extend(kinds)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM jobs
                WHERE state NOT IN ({placeholders}){kind_clause}
                ORDER BY created_at, job_id
                """,
                tuple(values),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def mark_terminal(
        self,
        job_id: str,
        *,
        state: JobState,
        exit_code: int | None,
        failed_stage: str | None,
        error_code: str | None,
        error_message: str | None,
        result: dict[str, Any] | None,
        run_root: str | None,
        lease_owner: str | None = None,
    ) -> JobRecord:
        if state not in TERMINAL_JOB_STATES:
            raise ValueError("terminal state required")
        self.initialize()
        now = _now()
        with self._transaction() as connection:
            current = self._get_in_transaction(connection, job_id)
            if current.state in TERMINAL_JOB_STATES:
                return current
            if current.state not in {
                JobState.STARTING,
                JobState.RUNNING,
                JobState.CANCELLING,
            }:
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    f"Job cannot transition from {current.state.value} to {state.value}",
                )
            if lease_owner is not None and current.lease_owner != lease_owner:
                raise ServiceError(
                    ServiceErrorCode.CONFLICT,
                    "Job lease is owned by another manager",
                )
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, ended_at = ?, exit_code = ?,
                    failed_stage = ?, error_code = ?, error_message = ?,
                    result_json = ?, run_root = ?, lease_owner = NULL,
                    lease_expires_at = NULL, revision = revision + 1
                WHERE job_id = ?
                """,
                (
                    state.value,
                    now,
                    exit_code,
                    failed_stage,
                    error_code,
                    error_message,
                    _json(result) if result is not None else None,
                    run_root,
                    job_id,
                ),
            )
            connection.execute(
                "DELETE FROM resource_locks WHERE job_id = ?",
                (job_id,),
            )
            self._append_event(
                connection,
                job_id,
                state.value,
                {
                    "state": state.value,
                    "exit_code": exit_code,
                    "failed_stage": failed_stage,
                    "error_code": error_code,
                },
                timestamp=now,
            )
            return self._get_in_transaction(connection, job_id)

    def append_event(
        self,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        self.initialize()
        with self._transaction() as connection:
            self._get_in_transaction(connection, job_id)
            return self._append_event(
                connection,
                job_id,
                event_type,
                payload,
                timestamp=_now(),
            )

    def list_events(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if limit > _MAX_EVENTS_PER_JOB:
            raise ValueError(
                f"limit must be <= {_MAX_EVENTS_PER_JOB}"
            )
        self.get(job_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence, timestamp, event_type, payload_json
                FROM job_events
                WHERE job_id = ? AND sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (job_id, after_sequence, limit),
            ).fetchall()
        return tuple(
            {
                "sequence": int(row["sequence"]),
                "timestamp": str(row["timestamp"]),
                "event_type": str(row["event_type"]),
                "payload": _json_object(
                    row["payload_json"],
                    field_name="job event payload",
                    default={},
                ),
            }
            for row in rows
        )

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            path = self._database_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._database_path()
            deadline = time.monotonic() + (_BUSY_TIMEOUT_MS / 1000)
            while True:
                try:
                    with self._connection() as connection:
                        connection.execute("PRAGMA journal_mode = WAL")
                        connection.executescript(_SCHEMA)
                        version = int(
                            connection.execute(
                                "PRAGMA user_version"
                            ).fetchone()[0]
                        )
                        if version not in {
                            0,
                            1,
                            2,
                            _JOB_STORE_SCHEMA_VERSION,
                        }:
                            raise ServiceError(
                                ServiceErrorCode.INTERNAL,
                                "Unsupported job store schema version",
                            )
                        columns = {
                            str(row["name"])
                            for row in connection.execute(
                                "PRAGMA table_info(jobs)"
                            ).fetchall()
                        }
                        if "retry_of" not in columns:
                            try:
                                connection.execute(
                                    "ALTER TABLE jobs "
                                    "ADD COLUMN retry_of TEXT"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                        connection.execute(
                            "PRAGMA user_version = "
                            f"{_JOB_STORE_SCHEMA_VERSION}"
                        )
                    self._initialized = True
                    return
                except sqlite3.OperationalError as exc:
                    if (
                        "locked" not in str(exc).lower()
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.05)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self._database_path(),
            timeout=_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _database_path(self) -> Path:
        if self._path_policy is None:
            return self.path
        assert self._expected_root is not None
        return self._path_policy.resolve_managed_output(
            self.path,
            field_name="evaluation job store",
            expected_root=self._expected_root,
            reject_links=True,
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _resources_available(
        connection: sqlite3.Connection,
        resource_keys: tuple[str, ...],
    ) -> bool:
        if not resource_keys:
            return True
        placeholders = ", ".join("?" for _ in resource_keys)
        row = connection.execute(
            f"""
            SELECT 1 FROM resource_locks
            WHERE resource_key IN ({placeholders})
            LIMIT 1
            """,
            resource_keys,
        ).fetchone()
        if row is not None:
            return False
        operation_keys = tuple(
            dict.fromkeys(
                (
                    *resource_keys,
                    *(
                        f"suite:{suite_id}"
                        for suite_id in (
                            _suite_id_from_resource_key(key)
                            for key in resource_keys
                        )
                        if suite_id is not None
                    ),
                )
            )
        )
        operation_placeholders = ", ".join("?" for _ in operation_keys)
        operation = connection.execute(
            f"""
            SELECT 1 FROM operation_locks
            WHERE resource_key IN ({operation_placeholders})
            LIMIT 1
            """,
            operation_keys,
        ).fetchone()
        return operation is None

    @staticmethod
    def _operation_conflicts_with_active_job(
        connection: sqlite3.Connection,
        resource_key: str,
    ) -> bool:
        if resource_key.startswith("suite:"):
            suite_id = resource_key.removeprefix("suite:")
            row = connection.execute(
                """
                SELECT 1 FROM resource_locks
                WHERE resource_key = ? OR resource_key LIKE ?
                LIMIT 1
                """,
                (
                    resource_key,
                    f"run:{suite_id}/%",
                ),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT 1 FROM resource_locks
                WHERE resource_key = ?
                LIMIT 1
                """,
                (resource_key,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _get_in_transaction(
        connection: sqlite3.Connection,
        job_id: str,
    ) -> JobRecord:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ServiceError(ServiceErrorCode.NOT_FOUND, "Job not found")
        return _record(row)

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        timestamp: str,
    ) -> int:
        sequence = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM job_events
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO job_events(
                job_id, sequence, timestamp, event_type, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                sequence,
                timestamp,
                event_type,
                _json(payload),
            ),
        )
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM job_events WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        excess = count - _MAX_EVENTS_PER_JOB
        if excess > 0:
            connection.execute(
                """
                DELETE FROM job_events
                WHERE job_id = ? AND sequence IN (
                    SELECT sequence FROM job_events
                    WHERE job_id = ?
                        AND event_type IN ('heartbeat', 'stage_progress')
                    ORDER BY sequence
                    LIMIT ?
                )
                """,
                (job_id, job_id, excess),
            )
            remaining = int(
                connection.execute(
                    "SELECT COUNT(*) FROM job_events WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
            overflow = remaining - _MAX_EVENTS_PER_JOB
            if overflow > 0:
                connection.execute(
                    """
                    DELETE FROM job_events
                    WHERE job_id = ? AND sequence IN (
                        SELECT sequence FROM job_events
                        WHERE job_id = ?
                        ORDER BY sequence
                        LIMIT ?
                    )
                    """,
                    (job_id, job_id, overflow),
                )
        return sequence


def _suite_id_from_resource_key(resource_key: str) -> str | None:
    if resource_key.startswith("suite:"):
        return resource_key.removeprefix("suite:")
    if resource_key.startswith("run:"):
        value = resource_key.removeprefix("run:")
        suite_id, separator, _ = value.partition("/")
        return suite_id if separator and suite_id else None
    return None


def _record(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=str(row["job_id"]),
        idempotency_key=str(row["idempotency_key"]),
        request_hash=str(row["request_hash"]),
        kind=str(row["kind"]),
        retry_of=_optional_str(row["retry_of"]),
        state=JobState(str(row["state"])),
        created_at=str(row["created_at"]),
        started_at=_optional_str(row["started_at"]),
        ended_at=_optional_str(row["ended_at"]),
        suite_id=str(row["suite_id"]),
        run_id=_optional_str(row["run_id"]),
        config_ref=str(row["config_ref"]),
        config_sha256=str(row["config_sha256"]),
        snapshot_path=str(row["snapshot_path"]),
        request_path=str(row["request_path"]),
        run_root=_optional_str(row["run_root"]),
        pid=int(row["pid"]) if row["pid"] is not None else None,
        process_create_time=(
            float(row["process_create_time"])
            if row["process_create_time"] is not None
            else None
        ),
        exit_code=(
            int(row["exit_code"])
            if row["exit_code"] is not None
            else None
        ),
        failed_stage=_optional_str(row["failed_stage"]),
        error_code=_optional_str(row["error_code"]),
        error_message=_optional_str(row["error_message"]),
        cancel_requested_at=_optional_str(row["cancel_requested_at"]),
        result=_json_object(
            row["result_json"],
            field_name="job result",
            default=None,
        ),
        resource_keys=_json_string_tuple(
            row["resource_keys_json"],
            field_name="job resource keys",
        ),
        revision=int(row["revision"]),
        lease_owner=_optional_str(row["lease_owner"]),
        lease_expires_at=_optional_str(row["lease_expires_at"]),
    )


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_json(value: Any, default: Any, *, field_name: str) -> Any:
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            ServiceErrorCode.INTERNAL,
            f"Job store contains invalid {field_name}",
        ) from exc


def _json_object(
    value: Any,
    *,
    field_name: str,
    default: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = _load_json(
        value,
        default,
        field_name=field_name,
    )
    if payload is not None and not isinstance(payload, dict):
        raise ServiceError(
            ServiceErrorCode.INTERNAL,
            f"Job store contains invalid {field_name}",
        )
    return payload


def _json_string_tuple(
    value: Any,
    *,
    field_name: str,
) -> tuple[str, ...]:
    payload = _load_json(value, [], field_name=field_name)
    if not isinstance(payload, list) or not all(
        isinstance(item, str) for item in payload
    ):
        raise ServiceError(
            ServiceErrorCode.INTERNAL,
            f"Job store contains invalid {field_name}",
        )
    return tuple(payload)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _after(seconds: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat()
