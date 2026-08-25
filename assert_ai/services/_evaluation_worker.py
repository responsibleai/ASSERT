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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from assert_ai.core.io import write_json
from assert_ai.core.security import redact_path_prefixes, sanitize_text
from assert_ai.core.workspace import WorkspaceService

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
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
        with (
            _BoundedTextLog(stdout_path, max_bytes=max_log_bytes) as stdout,
            _BoundedTextLog(stderr_path, max_bytes=max_log_bytes) as stderr,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            _capture_worker_logs(stderr),
        ):
            from assert_ai.runner import run_pipeline_document_result

            result = run_pipeline_document_result(
                document=document,
                config_path=str(config_path),
                force_stages=force_stages,
                strict=strict,
                path_policy=workspace.path_policy,
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
