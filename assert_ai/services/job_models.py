# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Transport-neutral models for persisted evaluation jobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Public job responses remain API-v1 compatible. Persisted SQLite migrations
# use their own independent schema version in job_store.py.
JOB_SCHEMA_VERSION = 1


class JobState(StrEnum):
    """Persisted orchestration states for evaluation workers."""

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_JOB_STATES = frozenset(
    {
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.INTERRUPTED,
    }
)


@dataclass(frozen=True, slots=True)
class JobRecord:
    """Complete internal representation of one persisted job row."""

    job_id: str
    idempotency_key: str
    request_hash: str
    kind: str
    retry_of: str | None
    state: JobState
    created_at: str
    started_at: str | None
    ended_at: str | None
    suite_id: str
    run_id: str | None
    config_ref: str
    config_sha256: str
    snapshot_path: str
    request_path: str
    run_root: str | None
    pid: int | None
    process_create_time: float | None
    exit_code: int | None
    failed_stage: str | None
    error_code: str | None
    error_message: str | None
    cancel_requested_at: str | None
    result: dict[str, Any] | None
    resource_keys: tuple[str, ...]
    revision: int
    lease_owner: str | None
    lease_expires_at: str | None


@dataclass(frozen=True, slots=True)
class NewJob:
    """Values fixed before a queued job becomes visible."""

    job_id: str
    idempotency_key: str
    request_hash: str
    suite_id: str
    run_id: str | None
    config_ref: str
    config_sha256: str
    snapshot_path: str
    request_path: str
    resource_keys: tuple[str, ...]
    retry_of: str | None = None
    kind: str = "evaluation"


@dataclass(frozen=True, slots=True)
class CreateJobResult:
    record: JobRecord
    created: bool


class _ServiceModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class JobTerminalResult(_ServiceModel):
    """Sanitized terminal outcome retained with a job."""

    state: Literal["completed", "failed", "cancelled"]
    exit_code: int
    failed_stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class JobCatalogEntry(_ServiceModel):
    """Lightweight persisted-job metadata."""

    schema_version: Literal[1] = JOB_SCHEMA_VERSION
    job_id: str
    state: JobState
    revision: int = Field(ge=0)
    kind: Literal["evaluation"] = "evaluation"
    retry_of: str | None = None
    config_ref: str
    suite_id: str
    run_id: str | None = None
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None


class JobPage(_ServiceModel):
    """Bounded page of persisted jobs."""

    items: tuple[JobCatalogEntry, ...]
    next_cursor: str | None = None


class JobDetail(JobCatalogEntry):
    """Detailed status for one evaluation job."""

    request_id: str
    config_sha256: str
    cancel_requested_at: str | None = None
    heartbeat_at: str | None = None
    heartbeat_age_seconds: float | None = Field(default=None, ge=0)
    stages: dict[str, Any] = Field(default_factory=dict)
    stage_timings: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    terminal_result: JobTerminalResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    resources: dict[str, str] = Field(default_factory=dict)


class JobStartResult(_ServiceModel):
    """Idempotent response returned after an evaluation is accepted."""

    job: JobDetail
    created: bool
