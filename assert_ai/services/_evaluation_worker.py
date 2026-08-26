# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Internal subprocess entry point for one persisted evaluation job."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import re
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from assert_ai.core.config_document import PIPELINE_STAGE_ORDER
from assert_ai.core.io import write_bytes_atomic, write_json, write_jsonl
from assert_ai.core.otel import parse_otel_trace_document
from assert_ai.core.run_control import (
    PipelineFinished,
    PipelineStarted,
    RunCancelled,
    RunControl,
    StageFinished,
    StagePlanned,
    StageProgress,
    StageStarted,
)
from assert_ai.core.run_result import RunResult, RunState
from assert_ai.core.security import (
    redact_path_prefixes,
    sanitize_payload,
    sanitize_text,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.job_store import JobStore

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_MAX_TRACE_BYTES = 64 * 1024 * 1024
_MIN_LOG_BYTES = 4096
_MAX_LOG_BYTES = 16 * 1024 * 1024
_DEFAULT_LOG_BYTES = 1024 * 1024
_TRUNCATION_MARKER = b"[earlier worker output truncated]\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)

    workspace: WorkspaceService | None = None
    result_path: Path | None = None
    result_token: str | None = None
    exit_code = 1
    try:
        if not _JOB_ID_RE.fullmatch(args.job_id):
            raise ValueError("Invalid evaluation job id")
        workspace = WorkspaceService.create(args.workspace)
        jobs_root = _jobs_root(workspace)
        job_dir = workspace.path_policy.resolve_managed_output(
            jobs_root / args.job_id,
            field_name="evaluation job directory",
            expected_root=jobs_root,
            reject_links=True,
        )
        request_path = workspace.path_policy.resolve_managed_output(
            job_dir / "request.json",
            field_name="evaluation job request",
            expected_root=job_dir,
            reject_links=True,
        )
        snapshot_path = workspace.path_policy.resolve_managed_output(
            job_dir / "config.yaml",
            field_name="evaluation config snapshot",
            expected_root=job_dir,
            reject_links=True,
        )
        result_path = workspace.path_policy.resolve_managed_output(
            job_dir / "result.json",
            field_name="evaluation job result",
            expected_root=job_dir,
            reject_links=True,
        )
        request = _read_request(request_path)
        if request.get("job_id") != args.job_id:
            raise ValueError("Evaluation job request identity mismatch")
        kind = request.get("kind", "evaluation")
        if kind not in {"evaluation", "trace_judging"}:
            raise ValueError("Unsupported evaluation job kind")
        result_token = _required_string(request, "result_token")
        config_ref = _required_string(request, "config_ref")
        expected_snapshot_hash = _required_string(
            request,
            "config_sha256",
        )
        if not _SHA256_RE.fullmatch(expected_snapshot_hash):
            raise ValueError("config_sha256 must be a SHA-256 digest")
        snapshot_bytes = _read_bytes(
            snapshot_path,
            max_bytes=_MAX_SNAPSHOT_BYTES,
            label="Evaluation config snapshot",
        )
        actual_snapshot_hash = (
            "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest()
        )
        if actual_snapshot_hash != expected_snapshot_hash:
            raise ValueError("Evaluation config snapshot digest mismatch")

        document = yaml.safe_load(snapshot_bytes.decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("Evaluation config snapshot must be a mapping")
        config_path = workspace.path_policy.resolve_config_path(
            config_ref,
            reject_links=True,
        )
        force_stages = request.get("force_stages")
        if not isinstance(force_stages, list) or not all(
            isinstance(item, str) for item in force_stages
        ):
            raise ValueError("force_stages must be a string array")
        strict = request.get("strict")
        if not isinstance(strict, bool):
            raise ValueError("strict must be a boolean")
        max_log_bytes = _log_limit(request.get("max_log_bytes"))
        stdout_path = workspace.path_policy.resolve_managed_output(
            job_dir / "stdout.log",
            field_name="evaluation worker stdout",
            expected_root=job_dir,
            reject_links=True,
        )
        stderr_path = workspace.path_policy.resolve_managed_output(
            job_dir / "stderr.log",
            field_name="evaluation worker stderr",
            expected_root=job_dir,
            reject_links=True,
        )
        cancel_path = workspace.path_policy.resolve_managed_output(
            job_dir / "cancel.requested",
            field_name="evaluation cancellation marker",
            expected_root=job_dir,
            reject_links=True,
        )
        cancel_acknowledged_path = (
            workspace.path_policy.resolve_managed_output(
                job_dir / "cancel.acknowledged",
                field_name="evaluation cancellation acknowledgement",
                expected_root=job_dir,
                reject_links=True,
            )
        )
        store = JobStore(
            jobs_root.parent / "jobs.sqlite3",
            path_policy=workspace.path_policy,
            expected_root=workspace.artifacts_root,
        )
        observer = _JobRunObserver(
            store=store,
            job_id=args.job_id,
            workspace=workspace,
        )
        control = RunControl.from_marker(
            cancel_path,
            cancel_acknowledged=lambda stage: (
                _acknowledge_cancellation(
                    cancel_acknowledged_path,
                    store=store,
                    job_id=args.job_id,
                    stage=stage,
                )
            ),
        )
        with (
            _BoundedTextLog(stdout_path, max_bytes=max_log_bytes) as stdout,
            _BoundedTextLog(stderr_path, max_bytes=max_log_bytes) as stderr,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            _capture_worker_logs(stderr),
            observer,
        ):
            try:
                control.raise_if_cancelled(
                    stage=(
                        "trace_import"
                        if kind == "trace_judging"
                        else _first_enabled_stage(document)
                    )
                )
            except RunCancelled as cancelled:
                result = _cancelled_before_runner(
                    document,
                    workspace=workspace,
                    observer=observer,
                    failed_stage=cancelled.stage,
                )
            else:
                if kind == "trace_judging":
                    result = _run_trace_judging(
                        document,
                        request=request,
                        job_dir=job_dir,
                        config_path=config_path,
                        workspace=workspace,
                        control=control,
                        observer=observer,
                    )
                else:
                    from assert_ai.runner import run_pipeline_document_result

                    result = run_pipeline_document_result(
                        document=document,
                        config_path=str(config_path),
                        force_stages=force_stages,
                        strict=strict,
                        path_policy=workspace.path_policy,
                        control=control,
                        observer=observer,
                    )
        payload = {
            "schema_version": 1,
            "job_id": args.job_id,
            "result_token": result_token,
            "run_result": result.to_dict(),
        }
        exit_code = result.exit_code
    except Exception as exc:  # noqa: BLE001 - subprocess boundary
        message = sanitize_text(str(exc)) or "Evaluation worker failed"
        if workspace is not None:
            message = redact_path_prefixes(
                message,
                (
                    workspace.root,
                    workspace.configs_root,
                    workspace.artifacts_root,
                    workspace.results_root,
                ),
            )
        payload = {
            "schema_version": 1,
            "job_id": str(args.job_id),
            "worker_error": {
                "error_code": "INTERNAL",
                "error_message": message,
            },
        }
        if result_token is not None:
            payload["result_token"] = result_token

    if result_path is not None:
        write_json(result_path, payload)
    else:
        sys.stderr.write("Evaluation worker could not resolve its result path\n")
    return exit_code


def _run_trace_judging(
    document: dict[str, Any],
    *,
    request: dict[str, Any],
    job_dir: Path,
    config_path: Path,
    workspace: WorkspaceService,
    control: RunControl,
    observer: "_JobRunObserver",
) -> RunResult:
    suite_id = _required_string(document, "suite")
    run_id = _required_string(document, "run")
    group_by = _required_string(request, "group_by")
    trace_path = workspace.path_policy.resolve_managed_output(
        job_dir / "trace.json",
        field_name="immutable OTLP trace input",
        expected_root=job_dir,
        reject_links=True,
    )
    taxonomy_snapshot = workspace.path_policy.resolve_managed_output(
        job_dir / "taxonomy.json",
        field_name="immutable trace taxonomy",
        expected_root=job_dir,
        reject_links=True,
    )
    trace_bytes = _verified_snapshot(
        trace_path,
        expected_sha256=_required_string(request, "trace_sha256"),
        max_bytes=_MAX_TRACE_BYTES,
        label="OTLP trace input",
    )
    taxonomy_bytes = _verified_snapshot(
        taxonomy_snapshot,
        expected_sha256=_required_string(request, "taxonomy_sha256"),
        max_bytes=_MAX_SNAPSHOT_BYTES,
        label="Trace taxonomy",
    )
    suite_root = workspace.path_policy.resolve_managed_output(
        workspace.results_root / suite_id,
        field_name="trace judge suite root",
        expected_root=workspace.results_root,
        reject_links=True,
    )
    run_root = workspace.path_policy.resolve_managed_output(
        suite_root / run_id,
        field_name="trace judge run root",
        expected_root=suite_root,
        reject_links=True,
    )
    inference_path = workspace.path_policy.resolve_managed_output(
        run_root / "inference_set.jsonl",
        field_name="trace judge inference set",
        expected_root=run_root,
        reject_links=True,
    )
    run_taxonomy_path = workspace.path_policy.resolve_managed_output(
        run_root / "taxonomy.json",
        field_name="trace judge taxonomy",
        expected_root=run_root,
        reject_links=True,
    )

    observer.pipeline_started(
        PipelineStarted(
            suite_id=suite_id,
            run_id=run_id,
            stages=("trace_import", "judge"),
        )
    )
    observer.stage_planned(
        StagePlanned(
            name="trace_import",
            scope="run",
            action="run",
        )
    )
    observer.stage_started(StageStarted(name="trace_import", scope="run"))
    started = time.monotonic()
    try:
        control.raise_if_cancelled(stage="trace_import")
        raw_document = json.loads(trace_bytes.decode("utf-8"))
        if not isinstance(raw_document, dict):
            raise ValueError("OTLP trace input must contain a JSON object")
        rows = _normalize_trace_rows(
            parse_otel_trace_document(raw_document, group_by=group_by)
        )
        if not rows:
            raise ValueError("OTLP trace input contains no trace sessions")
        control.raise_if_cancelled(stage="trace_import")
        run_root.mkdir(parents=True, exist_ok=True)
        write_jsonl(inference_path, rows)
        write_bytes_atomic(run_taxonomy_path, taxonomy_bytes)
        control.raise_if_cancelled(stage="trace_import")
    except RunCancelled:
        duration = max(0.0, time.monotonic() - started)
        observer.stage_finished(
            StageFinished(
                name="trace_import",
                scope="run",
                state="cancelled",
                duration_seconds=duration,
            )
        )
        result = RunResult(
            state=RunState.CANCELLED,
            exit_code=130,
            suite_id=suite_id,
            run_id=run_id,
            suite_root=suite_root,
            run_root=run_root,
            failed_stage="trace_import",
            error_message="Trace import was cancelled",
        )
        observer.pipeline_finished(
            PipelineFinished(
                state=result.state.value,
                exit_code=result.exit_code,
                failed_stage=result.failed_stage,
                error_message=result.error_message,
            )
        )
        return result
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        duration = max(0.0, time.monotonic() - started)
        message = sanitize_text(str(exc)) or "OTLP trace import failed"
        message = redact_path_prefixes(
            message,
            (
                workspace.root,
                workspace.configs_root,
                workspace.artifacts_root,
                workspace.results_root,
            ),
        )
        observer.stage_finished(
            StageFinished(
                name="trace_import",
                scope="run",
                state="failed",
                duration_seconds=duration,
                summary={"error": message},
            )
        )
        result = RunResult(
            state=RunState.FAILED,
            exit_code=1,
            suite_id=suite_id,
            run_id=run_id,
            suite_root=suite_root,
            run_root=run_root,
            failed_stage="trace_import",
            error_code="RUN_FAILED",
            error_message=message,
        )
        observer.pipeline_finished(
            PipelineFinished(
                state=result.state.value,
                exit_code=result.exit_code,
                failed_stage=result.failed_stage,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        )
        return result

    duration = max(0.0, time.monotonic() - started)
    observer.stage_progress(
        StageProgress(
            name="trace_import",
            values={
                "completed": len(rows),
                "total": len(rows),
                "unit": "sessions",
            },
        )
    )
    observer.stage_finished(
        StageFinished(
            name="trace_import",
            scope="run",
            state="completed",
            duration_seconds=duration,
            summary={"session_count": len(rows)},
        )
    )

    from assert_ai.runner import run_pipeline_document_result

    return run_pipeline_document_result(
        document=document,
        config_path=str(config_path),
        force_stages=["judge"],
        strict=False,
        path_policy=workspace.path_policy,
        control=control,
        observer=_TraceContinuationObserver(observer),
    )


def _verified_snapshot(
    path: Path,
    *,
    expected_sha256: str,
    max_bytes: int,
    label: str,
) -> bytes:
    if not _SHA256_RE.fullmatch(expected_sha256):
        raise ValueError(f"{label} digest is invalid")
    value = _read_bytes(path, max_bytes=max_bytes, label=label)
    actual = "sha256:" + hashlib.sha256(value).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"{label} digest mismatch")
    return value


def _normalize_trace_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        metadata = row.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        session_id = str(metadata.get("session_id") or f"session-{index}")
        raw_refs = metadata.get("trace_refs")
        trace_refs = _normalize_trace_refs(raw_refs)
        identity_material = json.dumps(
            {
                "session_id": session_id,
                "trace_refs": trace_refs,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(identity_material).hexdigest()[:12]
        test_case_id = f"trace_{index:06d}_{digest}"
        if test_case_id in seen:
            raise ValueError("Imported trace identities are not unique")
        seen.add(test_case_id)
        metadata.update(
            {
                "type": "otel_import",
                "session_id": session_id,
                "runtime_mode": "otel_traced",
                "trace_refs": trace_refs,
            }
        )
        events = (
            row.get("events")
            if isinstance(row.get("events"), list)
            else []
        )
        normalized.append(
            {
                "type": "prompt",
                "test_case_id": test_case_id,
                "behavior": "",
                "target": "otel_import",
                "tester_model": "",
                "metadata": metadata,
                "trace_refs": trace_refs,
                "events": events,
                "stop_reason": "trace_empty" if not events else None,
                "raw": (
                    row.get("raw")
                    if isinstance(row.get("raw"), dict)
                    else {}
                ),
            }
        )
    return normalized


def _normalize_trace_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        trace_id = item.get("trace_id")
        span_ids = item.get("span_ids")
        if not isinstance(trace_id, str) or not trace_id:
            continue
        refs.append(
            {
                "trace_id": trace_id,
                "span_ids": [
                    span_id
                    for span_id in (
                        span_ids if isinstance(span_ids, list) else []
                    )
                    if isinstance(span_id, str) and span_id
                ],
            }
        )
    return refs


class _TraceContinuationObserver:
    """Forward runner events after the trace-import pipeline start."""

    def __init__(self, delegate: "_JobRunObserver") -> None:
        self._delegate = delegate

    def pipeline_started(self, event: PipelineStarted) -> None:
        del event

    def stage_planned(self, event: StagePlanned) -> None:
        self._delegate.stage_planned(event)

    def stage_started(self, event: StageStarted) -> None:
        self._delegate.stage_started(event)

    def stage_progress(self, event: StageProgress) -> None:
        self._delegate.stage_progress(event)

    def stage_finished(self, event: StageFinished) -> None:
        self._delegate.stage_finished(event)

    def pipeline_finished(self, event: PipelineFinished) -> None:
        self._delegate.pipeline_finished(event)


def _jobs_root(workspace: WorkspaceService) -> Path:
    root = workspace.artifacts_root / "mcp" / "jobs"
    return workspace.path_policy.resolve_managed_output(
        root,
        field_name="evaluation jobs root",
        expected_root=workspace.artifacts_root,
        reject_links=True,
    )


def _read_request(path: Path) -> dict[str, Any]:
    payload = json.loads(
        _read_bytes(
            path,
            max_bytes=_MAX_REQUEST_BYTES,
            label="Evaluation job request",
        ).decode("utf-8")
    )
    if not isinstance(payload, dict):
        raise ValueError("Evaluation job request must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported evaluation job request schema")
    return payload


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _acknowledge_cancellation(
    path: Path,
    *,
    store: JobStore,
    job_id: str,
    stage: str | None,
) -> None:
    acknowledged_at = datetime.now(timezone.utc).isoformat()
    write_json(
        path,
        {
            "schema_version": 1,
            "job_id": job_id,
            "acknowledged_at": acknowledged_at,
            "stage": stage,
        },
    )
    store.append_event(
        job_id,
        "cancel_observed",
        {
            "state": "cancelling",
            "stage": stage,
            "acknowledged_at": acknowledged_at,
        },
    )


def _first_enabled_stage(document: dict[str, Any]) -> str | None:
    pipeline = document.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    for stage_name in PIPELINE_STAGE_ORDER:
        stage = pipeline.get(stage_name)
        if isinstance(stage, dict) and stage.get("enabled", True):
            return stage_name
    return None


def _cancelled_before_runner(
    document: dict[str, Any],
    *,
    workspace: WorkspaceService,
    observer: "_JobRunObserver",
    failed_stage: str | None,
) -> RunResult:
    suite_id = document.get("suite")
    run_id = document.get("run")
    suite_id = suite_id if isinstance(suite_id, str) else None
    run_id = run_id if isinstance(run_id, str) else None
    suite_root = None
    if suite_id is not None:
        suite_root = workspace.path_policy.resolve_managed_output(
            workspace.results_root / suite_id,
            field_name="cancelled evaluation suite root",
            expected_root=workspace.results_root,
            reject_links=True,
        )
    run_root = (
        workspace.path_policy.resolve_managed_output(
            suite_root / run_id,
            field_name="cancelled evaluation run root",
            expected_root=suite_root,
            reject_links=True,
        )
        if suite_root is not None and run_id is not None
        else None
    )
    stages = tuple(
        stage_name
        for stage_name in PIPELINE_STAGE_ORDER
        if isinstance(document.get("pipeline"), dict)
        and isinstance(document["pipeline"].get(stage_name), dict)
        and document["pipeline"][stage_name].get("enabled", True)
    )
    observer.pipeline_started(
        PipelineStarted(
            suite_id=suite_id,
            run_id=run_id,
            stages=stages,
        )
    )
    if failed_stage is not None:
        scope = (
            "suite"
            if failed_stage in {"systematize", "test_set"}
            else "run"
        )
        observer.stage_planned(
            StagePlanned(
                name=failed_stage,
                scope=scope,
                action="pending",
            )
        )
        observer.stage_finished(
            StageFinished(
                name=failed_stage,
                scope=scope,
                state="cancelled",
                duration_seconds=0.0,
            )
        )
    result = RunResult(
        state=RunState.CANCELLED,
        exit_code=130,
        suite_id=suite_id,
        run_id=run_id,
        suite_root=suite_root,
        run_root=run_root,
        failed_stage=failed_stage,
    )
    observer.pipeline_finished(
        PipelineFinished(
            state=result.state.value,
            exit_code=result.exit_code,
            failed_stage=result.failed_stage,
        )
    )
    return result


def _read_bytes(path: Path, *, max_bytes: int, label: str) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(max_bytes + 1)
    if len(value) > max_bytes:
        raise ValueError(f"{label} exceeds the worker limit")
    return value


def _log_limit(value: Any) -> int:
    if value is None:
        return _DEFAULT_LOG_BYTES
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not _MIN_LOG_BYTES <= value <= _MAX_LOG_BYTES
    ):
        raise ValueError(
            f"max_log_bytes must be between {_MIN_LOG_BYTES} and "
            f"{_MAX_LOG_BYTES}"
        )
    return value


class _BoundedTextLog(io.TextIOBase):
    """UTF-8 log sink that retains a bounded tail without using pipes."""

    def __init__(self, path: Path, *, max_bytes: int) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._stream = path.open("w+b", buffering=0)
        self._buffer = _BoundedBinaryLog(self)

    @property
    def encoding(self) -> str:
        return "utf-8"

    @property
    def buffer(self) -> "_BoundedBinaryLog":
        return self._buffer

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._stream.fileno()

    def write(self, value: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed worker log")
        if not isinstance(value, str):
            value = str(value)
        value = sanitize_text(value)
        encoded = value.encode("utf-8", errors="replace")
        self._write_bytes(encoded)
        return len(value)

    def _write_bytes(self, value: bytes) -> None:
        with self._lock:
            self._stream.seek(0, 2)
            self._stream.write(value)
            if self._stream.tell() > self._max_bytes:
                self._truncate_to_tail()
            self._stream.flush()

    def flush(self) -> None:
        if self.closed:
            return
        with self._lock:
            self._stream.flush()

    def close(self) -> None:
        if self.closed:
            return
        with self._lock:
            self._stream.seek(0, 2)
            if self._stream.tell() > self._max_bytes:
                self._truncate_to_tail()
        super().close()
        with self._lock:
            self._stream.close()

    def _truncate_to_tail(self) -> None:
        keep_bytes = max(
            1,
            self._max_bytes // 2 - len(_TRUNCATION_MARKER),
        )
        self._stream.seek(-keep_bytes, 2)
        tail = self._stream.read()
        tail = tail.decode("utf-8", errors="replace").encode("utf-8")
        retained = (_TRUNCATION_MARKER + tail)[-self._max_bytes :]
        self._stream.seek(0)
        self._stream.write(retained)
        self._stream.truncate()


class _BoundedBinaryLog:
    """Binary facade used by code that writes to ``sys.stdout.buffer``."""

    def __init__(self, text_log: _BoundedTextLog) -> None:
        self._text_log = text_log

    def write(self, value: bytes | bytearray) -> int:
        encoded = bytes(value)
        sanitized = sanitize_text(
            encoded.decode("utf-8", errors="replace")
        ).encode("utf-8")
        self._text_log._write_bytes(sanitized)
        return len(encoded)

    def flush(self) -> None:
        self._text_log.flush()

    def fileno(self) -> int:
        return self._text_log.fileno()


class _JobRunObserver:
    """Persist runner lifecycle events and a suite-only heartbeat."""

    def __init__(
        self,
        *,
        store: JobStore,
        job_id: str,
        workspace: WorkspaceService,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        self._store = store
        self._job_id = job_id
        self._workspace = workspace
        self._heartbeat_seconds = heartbeat_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_JobRunObserver":
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="assert-mcp-job-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def pipeline_started(self, event: PipelineStarted) -> None:
        self._append("pipeline_started", event)

    def stage_planned(self, event: StagePlanned) -> None:
        self._append("stage_planned", event)

    def stage_started(self, event: StageStarted) -> None:
        self._append("stage_started", event)

    def stage_progress(self, event: StageProgress) -> None:
        self._append("stage_progress", event)

    def stage_finished(self, event: StageFinished) -> None:
        self._append("stage_finished", event)

    def pipeline_finished(self, event: PipelineFinished) -> None:
        self._append("pipeline_finished", event)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            self._append("heartbeat", {"state": "running"})

    def _append(self, event_type: str, event: object) -> None:
        try:
            raw = asdict(event) if hasattr(event, "__dataclass_fields__") else event
            payload = sanitize_payload(raw)
            if not isinstance(payload, dict):
                payload = {}
            payload = _redact_payload_paths(payload, self._workspace)
            self._store.append_event(
                self._job_id,
                event_type,
                payload,
            )
        except Exception:  # noqa: BLE001 - diagnostic boundary
            logging.getLogger(__name__).exception(
                "Could not persist evaluation job event %s",
                event_type,
            )


def _redact_payload_paths(
    payload: dict[str, Any],
    workspace: WorkspaceService,
) -> dict[str, Any]:
    serialized = json.dumps(payload, ensure_ascii=False)
    redacted = redact_path_prefixes(
        serialized,
        (
            workspace.root,
            workspace.configs_root,
            workspace.artifacts_root,
            workspace.results_root,
        ),
    )
    parsed = json.loads(redacted)
    return parsed if isinstance(parsed, dict) else {}


@contextlib.contextmanager
def _capture_worker_logs(
    stream: _BoundedTextLog,
) -> Iterator[None]:
    root = logging.getLogger()
    previous_level = root.level
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    if previous_level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
