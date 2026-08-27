# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Customer-safe serialization helpers for MCP responses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from assert_ai.core.security import redact_path_prefixes, sanitize_payload
from assert_ai.core.workspace import WorkspaceService


def sanitize_for_mcp(
    value: Any,
    *,
    workspace: WorkspaceService,
) -> Any:
    """Redact credentials and host paths from one JSON-compatible value."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    sanitized = sanitize_payload(value)
    return _sanitize_paths(sanitized, workspace=workspace)


def _sanitize_paths(value: Any, *, workspace: WorkspaceService) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_paths(item, workspace=workspace)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_paths(item, workspace=workspace)
            for item in value
        ]
    if not isinstance(value, str):
        return value

    replaced = redact_path_prefixes(value, (workspace.root,))
    if replaced != value:
        return replaced

    try:
        candidate = Path(value)
    except (OSError, ValueError):
        return value
    if not candidate.is_absolute():
        return value
    try:
        return workspace.reference(candidate)
    except ValueError:
        return "[EXTERNAL_PATH]"
