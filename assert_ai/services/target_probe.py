# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Timeout-bounded target probing in a disposable subprocess."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assert_ai.core.security import (
    redact_path_prefixes,
    sanitize_payload,
    sanitize_text,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.configs import ConfigService
from assert_ai.services.errors import ServiceError, ServiceErrorCode

_RESULT_MARKER = "ASSERT_TARGET_PROBE_RESULT="
_MAX_WORKER_OUTPUT_BYTES = 1024 * 1024


class TargetProbeResult(BaseModel):
    """Sanitized result from an isolated import and shape check."""

    model_config = ConfigDict(frozen=True)

    config_ref: str
    target_kind: Literal["model", "callable", "connector", "endpoint"]
    ready: Literal[True] = True
    isolated: Literal[True] = True
    duration_ms: int = Field(ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class TargetProbeService:
    """Probe managed target code without importing it into the server process."""

    workspace: WorkspaceService
    configs: ConfigService
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        if self.timeout_s <= 0:
            raise ValueError("target probe timeout must be positive")

    def probe(self, config_ref: str) -> TargetProbeResult:
        record = self.configs.get_config(config_ref)
        if not record.validation.valid:
            raise ServiceError(
                ServiceErrorCode.CONFIG_INVALID,
                "Target probe requires a valid config",
                details={
                    "validation": record.validation.model_dump(mode="json"),
                },
            )

        request = {
            "workspace_root": str(self.workspace.root),
            "config_ref": record.config_ref,
            "max_config_bytes": self.configs.max_config_bytes,
        }
        started = time.perf_counter()
        payload = self._invoke_worker(request)
        duration_ms = max(
            0,
            round((time.perf_counter() - started) * 1000),
        )
        if payload.get("ok") is not True:
            message = self._sanitize_message(
                str(payload.get("message") or "Target probe failed")
            )
            details = self._sanitize_details(
                {
                    "target_kind": payload.get("target_kind"),
                    "error_type": payload.get("error_type"),
                }
            )
            raise ServiceError(
                ServiceErrorCode.TARGET_IMPORT_FAILED,
                message,
                details=details,
            )

        target_kind = payload.get("target_kind")
        if target_kind not in {"model", "callable", "connector", "endpoint"}:
            raise ServiceError(
                ServiceErrorCode.TARGET_IMPORT_FAILED,
                "Target probe worker returned an invalid target kind",
            )
        details = payload.get("details")
        if not isinstance(details, dict):
            raise ServiceError(
                ServiceErrorCode.TARGET_IMPORT_FAILED,
                "Target probe worker returned invalid details",
            )
        return TargetProbeResult(
            config_ref=record.config_ref,
            target_kind=target_kind,
            duration_ms=duration_ms,
            details=self._sanitize_details(details),
        )

    def _invoke_worker(self, request: dict[str, Any]) -> dict[str, Any]:
        result_token = secrets.token_hex(16)
        worker_request = {
            **request,
            "result_token": result_token,
        }
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        )
        with (
            tempfile.TemporaryFile(mode="w+b") as stdout_file,
            tempfile.TemporaryFile(mode="w+b") as stderr_file,
        ):
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "assert_ai.services._target_probe_worker",
                ],
                cwd=self.workspace.root,
                env=env,
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
            process_create_time = _process_create_time(process.pid)
            try:
                process.communicate(
                    json.dumps(worker_request, ensure_ascii=False),
                    timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                _terminate_process_tree(
                    process,
                    expected_create_time=process_create_time,
                )
                raise ServiceError(
                    ServiceErrorCode.TARGET_IMPORT_FAILED,
                    (
                        "Target probe exceeded the operator timeout "
                        f"of {self.timeout_s:g} seconds"
                    ),
                    details={"timed_out": True},
                ) from exc

            output = _read_tail(
                stdout_file,
                max_bytes=_MAX_WORKER_OUTPUT_BYTES,
            )
            payload = _parse_worker_result(
                output,
                result_token=result_token,
            )
            if payload is not None:
                return payload
            raise ServiceError(
                ServiceErrorCode.TARGET_IMPORT_FAILED,
                "Target probe worker did not return a valid result",
                details={"exit_code": process.returncode},
            )

    def _sanitize_message(self, message: str) -> str:
        return redact_path_prefixes(
            sanitize_text(message),
            (
                self.workspace.root,
                self.workspace.configs_root,
                self.workspace.artifacts_root,
                self.workspace.results_root,
            ),
        )

    def _sanitize_details(self, details: dict[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_payload(details)
        assert isinstance(sanitized, dict)
        result = _redact_paths(
            sanitized,
            workspace=self.workspace,
        )
        assert isinstance(result, dict)
        return result


def _read_tail(handle: Any, *, max_bytes: int) -> str:
    handle.flush()
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(max(0, size - max_bytes))
    return handle.read(max_bytes).decode("utf-8", errors="replace")


def _parse_worker_result(
    output: str,
    *,
    result_token: str,
) -> dict[str, Any] | None:
    marker = f"{_RESULT_MARKER}{result_token}="
    marker_index = output.rfind(marker)
    if marker_index < 0:
        return None
    encoded = output[marker_index + len(marker) :].splitlines()[0]
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _process_create_time(pid: int) -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except (OSError, psutil.Error):
        return None


def _terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    expected_create_time: float | None,
) -> None:
    try:
        import psutil
    except ImportError:
        process.kill()
        process.wait(timeout=5)
        return

    try:
        parent = psutil.Process(process.pid)
        if (
            expected_create_time is not None
            and abs(parent.create_time() - expected_create_time) > 0.001
        ):
            return
        descendants = parent.children(recursive=True)
        for child in descendants:
            child.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs(
            [*descendants, parent],
            timeout=2,
        )
        for item in alive:
            item.kill()
        psutil.wait_procs(alive, timeout=2)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
        pass
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _redact_paths(
    value: Any,
    *,
    workspace: WorkspaceService,
) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_paths(item, workspace=workspace)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_paths(item, workspace=workspace)
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            _redact_paths(item, workspace=workspace)
            for item in value
        ]
    if isinstance(value, str):
        return redact_path_prefixes(
            value,
            (
                workspace.root,
                workspace.configs_root,
                workspace.artifacts_root,
                workspace.results_root,
            ),
        )
    return value
