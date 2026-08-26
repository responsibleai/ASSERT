# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.job_models import JobState, NewJob
from assert_ai.services.job_store import JobStore


def _new_job(
    suffix: str,
    *,
    request_id: str | None = None,
    request_hash: str | None = None,
    suite_id: str | None = None,
    run_id: str | None = None,
    resource_keys: tuple[str, ...] = (),
    retry_of: str | None = None,
) -> NewJob:
    return NewJob(
        job_id=f"job-{suffix}",
        idempotency_key=request_id or f"request-{suffix}",
        request_hash=request_hash or f"hash-{suffix}",
        suite_id=suite_id or f"suite-{suffix}",
        run_id=run_id if run_id is not None else f"run-{suffix}",
        config_ref="demo.yaml",
        config_sha256=f"sha256:{suffix}",
        snapshot_path=f"artifacts/mcp/jobs/job-{suffix}/config.yaml",
        request_path=f"artifacts/mcp/jobs/job-{suffix}/request.json",
        resource_keys=resource_keys,
        retry_of=retry_of,
    )


def test_create_is_idempotent_and_detects_request_conflicts(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    first = store.create_or_get(
        _new_job("one"),
        max_queued_jobs=10,
    )
    repeated = store.create_or_get(
        _new_job(
            "other-id",
            request_id="request-one",
            request_hash="hash-one",
        ),
        max_queued_jobs=10,
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.record.job_id == "job-one"
    assert store.list_events("job-one")[0]["event_type"] == "queued"

    with pytest.raises(ServiceError) as conflict:
        store.create_or_get(
            _new_job(
                "conflict",
                request_id="request-one",
                request_hash="different",
            ),
            max_queued_jobs=10,
        )
    assert conflict.value.code == ServiceErrorCode.CONFLICT


def test_suite_run_assignment_is_unique(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create_or_get(_new_job("one"), max_queued_jobs=10)

    with pytest.raises(ServiceError) as conflict:
        store.create_or_get(
            _new_job(
                "two",
                suite_id="suite-one",
                run_id="run-one",
            ),
            max_queued_jobs=10,
        )

    assert conflict.value.code == ServiceErrorCode.CONFLICT
    assert conflict.value.details == {"job_id": "job-one"}


def test_create_respects_queued_job_limit(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create_or_get(_new_job("one"), max_queued_jobs=1)

    with pytest.raises(ServiceError) as conflict:
        store.create_or_get(_new_job("two"), max_queued_jobs=1)

    assert conflict.value.code == ServiceErrorCode.CONFLICT
    assert conflict.value.details == {"max_queued_jobs": 1}


def test_claim_transition_and_terminal_result_persist(
    tmp_path: Path,
) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    store.create_or_get(
        _new_job(
            "one",
            resource_keys=("suite:suite-one", "run:suite-one/run-one"),
        ),
        max_queued_jobs=10,
    )

    claimed = store.claim_next(
        lease_owner="manager-a",
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert claimed is not None
    assert claimed.state is JobState.STARTING
    running = store.mark_running(
        claimed.job_id,
        lease_owner="manager-a",
        pid=123,
        process_create_time=456.0,
        lease_seconds=30,
    )
    assert running.state is JobState.RUNNING
    assert running.revision == 2
    completed = store.mark_terminal(
        claimed.job_id,
        state=JobState.COMPLETED,
        exit_code=0,
        failed_stage=None,
        error_code=None,
        error_message=None,
        result={"state": "completed", "exit_code": 0},
        run_root="artifacts/results/suite-one/run-one",
        lease_owner="manager-a",
    )

    assert completed.state is JobState.COMPLETED
    assert completed.result == {"state": "completed", "exit_code": 0}
    reopened = JobStore(path).get(claimed.job_id)
    assert reopened == completed
    assert [event["event_type"] for event in store.list_events(claimed.job_id)] == [
        "queued",
        "starting",
        "running",
        "completed",
    ]


def test_claim_respects_global_active_limit_and_resource_locks(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create_or_get(
        _new_job("one", resource_keys=("suite:shared",)),
        max_queued_jobs=10,
    )
    store.create_or_get(
        _new_job("two", resource_keys=("suite:shared",)),
        max_queued_jobs=10,
    )
    first = store.claim_next(
        lease_owner="manager-a",
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert first is not None
    assert (
        store.claim_next(
            lease_owner="manager-b",
            lease_seconds=30,
            max_active_jobs=1,
        )
        is None
    )
    store.mark_terminal(
        first.job_id,
        state=JobState.FAILED,
        exit_code=1,
        failed_stage=None,
        error_code="RUN_FAILED",
        error_message="failed",
        result=None,
        run_root=None,
        lease_owner="manager-a",
    )
    second = store.claim_next(
        lease_owner="manager-b",
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert second is not None
    assert second.job_id == "job-two"


def test_concurrent_idempotent_create_has_one_winner(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")

    def create(index: int) -> tuple[str, bool]:
        result = store.create_or_get(
            _new_job(
                str(index),
                request_id="same-request",
                request_hash="same-hash",
            ),
            max_queued_jobs=10,
        )
        return result.record.job_id, result.created

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(create, range(4)))

    assert sum(created for _, created in outcomes) == 1
    assert len({job_id for job_id, _ in outcomes}) == 1


def test_cancel_queued_job_is_terminal_and_idempotent(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create_or_get(_new_job("one"), max_queued_jobs=10)

    cancelled = store.request_cancel("job-one")
    repeated = store.request_cancel("job-one")

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.cancel_requested_at is not None
    assert cancelled.ended_at is not None
    assert cancelled.result == {
        "state": "cancelled",
        "exit_code": 130,
        "failed_stage": None,
        "error_code": None,
        "error_message": None,
    }
    assert repeated == cancelled


def test_cancel_active_job_preserves_lock_until_terminal(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create_or_get(
        _new_job("one", resource_keys=("suite:shared",)),
        max_queued_jobs=10,
    )
    claimed = store.claim_next(
        lease_owner="manager-a",
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert claimed is not None

    cancelling = store.request_cancel(claimed.job_id)

    assert cancelling.state is JobState.CANCELLING
    assert (
        store.claim_next(
            lease_owner="manager-b",
            lease_seconds=30,
            max_active_jobs=1,
        )
        is None
    )
    terminal = store.mark_terminal(
        claimed.job_id,
        state=JobState.CANCELLED,
        exit_code=130,
        failed_stage=None,
        error_code=None,
        error_message=None,
        result={"state": "cancelled", "exit_code": 130},
        run_root=None,
    )
    assert terminal.state is JobState.CANCELLED


def test_expired_active_lease_can_be_adopted(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create_or_get(_new_job("one"), max_queued_jobs=10)
    claimed = store.claim_next(
        lease_owner="manager-a",
        lease_seconds=0.01,
        max_active_jobs=1,
    )
    assert claimed is not None
    running = store.mark_running(
        claimed.job_id,
        lease_owner="manager-a",
        pid=123,
        process_create_time=456,
        lease_seconds=0.01,
    )
    assert running.state is JobState.RUNNING

    import time

    time.sleep(0.02)
    adopted = store.adopt_lease(
        running.job_id,
        lease_owner="manager-b",
        lease_seconds=30,
    )

    assert adopted is not None
    assert adopted.lease_owner == "manager-b"
    assert adopted.revision == running.revision + 1


def test_retry_provenance_is_persisted(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_or_get(
        _new_job("retry", retry_of="job-original"),
        max_queued_jobs=10,
    )

    assert created.record.retry_of == "job-original"
    assert JobStore(tmp_path / "jobs.sqlite3").get(
        created.record.job_id
    ).retry_of == "job-original"


def test_v1_store_is_migrated_before_a_mutating_operation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs(
                job_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_hash TEXT NOT NULL,
                kind TEXT NOT NULL,
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
            CREATE TABLE job_events(
                job_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(job_id, sequence)
            );
            CREATE TABLE resource_locks(
                resource_key TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                lease_expires_at TEXT NOT NULL
            );
            INSERT INTO jobs(
                job_id, idempotency_key, request_hash, kind, state,
                created_at, suite_id, run_id, config_ref, config_sha256,
                snapshot_path, request_path, resource_keys_json
            ) VALUES (
                'job-v1', 'request-v1', 'hash-v1', 'evaluation', 'queued',
                '2026-01-01T00:00:00+00:00', 'suite-v1', 'run-v1',
                'demo.yaml', 'sha256:v1', 'config.yaml', 'request.json',
                '[]'
            );
            PRAGMA user_version = 1;
            """
        )

    cancelled = JobStore(path).request_cancel("job-v1")

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.retry_of is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(jobs)")
        }
    assert "retry_of" in columns


def test_event_retention_prefers_lifecycle_events(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create_or_get(_new_job("one"), max_queued_jobs=10)

    for completed in range(1_005):
        store.append_event(
            "job-one",
            "stage_progress",
            {"name": "inference", "completed": completed},
        )

    events = store.list_events("job-one", limit=1000)
    assert len(events) == 1000
    assert events[0]["event_type"] == "queued"
    assert events[-1]["payload"]["completed"] == 1004
