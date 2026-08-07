# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Typed terminal outcomes for ASSERT pipeline execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class RunState(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunResult:
    """Transport-neutral terminal result for one pipeline invocation."""

    state: RunState
    exit_code: int
    suite_id: str | None = None
    run_id: str | None = None
    suite_root: Path | None = None
    run_root: Path | None = None
    failed_stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "exit_code": self.exit_code,
            "suite_id": self.suite_id,
            "run_id": self.run_id,
            "suite_root": str(self.suite_root) if self.suite_root is not None else None,
            "run_root": str(self.run_root) if self.run_root is not None else None,
            "failed_stage": self.failed_stage,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }
