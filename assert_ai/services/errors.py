# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Stable application-service error taxonomy."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ServiceErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    STALE_ETAG = "STALE_ETAG"
    STALE_CURSOR = "STALE_CURSOR"
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    WORKSPACE_VIOLATION = "WORKSPACE_VIOLATION"
    CONFIG_INVALID = "CONFIG_INVALID"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    TARGET_IMPORT_FAILED = "TARGET_IMPORT_FAILED"
    JOB_NOT_CANCELLABLE = "JOB_NOT_CANCELLABLE"
    JOB_INTERRUPTED = "JOB_INTERRUPTED"
    RUN_FAILED = "RUN_FAILED"
    ARTIFACT_TOO_LARGE = "ARTIFACT_TOO_LARGE"
    INTERNAL = "INTERNAL"


class ServiceError(Exception):
    """Expected application failure suitable for CLI or MCP adaptation."""

    def __init__(
        self,
        code: ServiceErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
