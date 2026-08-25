# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

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
