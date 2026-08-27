# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Cooperative pipeline cancellation and transport-neutral run events."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)


class RunCancelled(RuntimeError):
    """Raised at a safe checkpoint after cancellation is requested."""

    def __init__(self, *, stage: str | None = None) -> None:
        super().__init__("Evaluation cancellation requested")
        self.stage = stage


@dataclass(slots=True)
class RunControl:
    """Cancellation token checked between safe units of pipeline work."""

    cancel_requested: Callable[[], bool]
    cancel_acknowledged: Callable[[str | None], None] | None = None
    _acknowledged: bool = field(default=False, init=False, repr=False)
    _acknowledge_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    @classmethod
    def from_marker(
        cls,
        marker: str | Path,
        *,
        cancel_acknowledged: Callable[[str | None], None] | None = None,
    ) -> "RunControl":
        path = Path(marker)
        return cls(
            cancel_requested=path.is_file,
            cancel_acknowledged=cancel_acknowledged,
        )

    def raise_if_cancelled(self, *, stage: str | None = None) -> None:
        if self.cancel_requested():
            self._acknowledge(stage)
            raise RunCancelled(stage=stage)

    def _acknowledge(self, stage: str | None) -> None:
        callback = self.cancel_acknowledged
        if callback is None:
            return
        with self._acknowledge_lock:
            if self._acknowledged:
                return
            self._acknowledged = True
        try:
            callback(stage)
        except Exception:  # noqa: BLE001 - acknowledgement is diagnostic
            log.warning(
                "Could not acknowledge evaluation cancellation",
                exc_info=True,
            )


@dataclass(frozen=True, slots=True)
class PipelineStarted:
    suite_id: str | None
    run_id: str | None
    stages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StagePlanned:
    name: str
    scope: str
    action: str


@dataclass(frozen=True, slots=True)
class StageStarted:
    name: str
    scope: str


@dataclass(frozen=True, slots=True)
class StageProgress:
    name: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StageFinished:
    name: str
    scope: str
    state: str
    duration_seconds: float | None = None
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PipelineFinished:
    state: str
    exit_code: int
    failed_stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class RunObserver(Protocol):
    """Receives lifecycle events without depending on a transport."""

    def pipeline_started(self, event: PipelineStarted) -> None: ...

    def stage_planned(self, event: StagePlanned) -> None: ...

    def stage_started(self, event: StageStarted) -> None: ...

    def stage_progress(self, event: StageProgress) -> None: ...

    def stage_finished(self, event: StageFinished) -> None: ...

    def pipeline_finished(self, event: PipelineFinished) -> None: ...
