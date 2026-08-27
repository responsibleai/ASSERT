# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Stable application-error adaptation for MCP tools and resources."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from functools import wraps
from typing import TypeVar
from uuid import uuid4

from mcp.server.mcpserver.exceptions import (
    ResourceError,
    ResourceNotFoundError,
    ToolError,
)
from pydantic import BaseModel

from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.services.errors import ServiceError, ServiceErrorCode

log = logging.getLogger(__name__)

_T = TypeVar("_T")


class _McpToolError(ToolError):
    """Expected sanitized error that should survive nested adaptation."""


def invoke_tool(
    operation: Callable[[], _T],
    *,
    workspace: WorkspaceService,
) -> _T:
    """Invoke one service operation with stable, sanitized tool errors."""
    try:
        return operation()
    except _McpToolError:
        raise
    except ServiceError as exc:
        raise _McpToolError(
            _service_error_payload(exc, workspace=workspace)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        correlation_id = uuid4().hex
        log.exception("Unhandled MCP tool failure (%s)", correlation_id)
        internal = ServiceError(
            ServiceErrorCode.INTERNAL,
            "An internal error occurred",
            details={"correlation_id": correlation_id},
        )
        raise _McpToolError(
            _service_error_payload(internal, workspace=workspace)
        ) from exc


def adapt_tool_errors(
    workspace: WorkspaceService,
    *,
    max_response_bytes: int | None = None,
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorate a complete tool body so adapter-side failures are sanitized."""

    def decorator(operation: Callable[..., _T]) -> Callable[..., _T]:
        @wraps(operation)
        def wrapper(*args: object, **kwargs: object) -> _T:
            return invoke_tool(
                lambda: _bounded_result(
                    operation(*args, **kwargs),
                    max_response_bytes=max_response_bytes,
                ),
                workspace=workspace,
            )

        return wrapper

    return decorator


def _bounded_result(
    result: _T,
    *,
    max_response_bytes: int | None,
) -> _T:
    if max_response_bytes is None:
        return result
    payload = (
        result.model_dump(mode="json")
        if isinstance(result, BaseModel)
        else result
    )
    payload_size_bytes = len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )
    estimated_wire_bytes = payload_size_bytes * 2 + 2048
    if estimated_wire_bytes > max_response_bytes:
        raise ServiceError(
            ServiceErrorCode.ARTIFACT_TOO_LARGE,
            (
                "Tool response exceeds the configured response limit; "
                "use a narrower query or a smaller page"
            ),
            details={
                "payload_size_bytes": payload_size_bytes,
                "estimated_wire_bytes": estimated_wire_bytes,
                "max_response_bytes": max_response_bytes,
            },
        )
    return result


def invoke_resource(
    operation: Callable[[], _T],
    *,
    workspace: WorkspaceService,
) -> _T:
    """Invoke one service operation with resource-appropriate errors."""
    try:
        return operation()
    except ServiceError as exc:
        error_type = (
            ResourceNotFoundError
            if exc.code is ServiceErrorCode.NOT_FOUND
            else ResourceError
        )
        raise error_type(_service_error_payload(exc, workspace=workspace)) from exc
    except Exception as exc:  # noqa: BLE001
        correlation_id = uuid4().hex
        log.exception("Unhandled MCP resource failure (%s)", correlation_id)
        internal = ServiceError(
            ServiceErrorCode.INTERNAL,
            "An internal error occurred",
            details={"correlation_id": correlation_id},
        )
        raise ResourceError(
            _service_error_payload(internal, workspace=workspace)
        ) from exc


def _service_error_payload(
    error: ServiceError,
    *,
    workspace: WorkspaceService,
) -> str:
    payload = {
        "code": error.code.value,
        "message": str(error),
        "details": error.details,
    }
    return json.dumps(
        sanitize_for_mcp(payload, workspace=workspace),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
