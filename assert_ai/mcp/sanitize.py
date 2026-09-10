# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Customer-safe serialization helpers for MCP responses."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel

from assert_ai.core.config_document import JSON_POINTER_METADATA
from assert_ai.core.security import redact_path_prefixes, sanitize_payload
from assert_ai.core.workspace import WorkspaceService

_Location = tuple[str | int, ...]


def sanitize_for_mcp(
    value: Any,
    *,
    workspace: WorkspaceService,
    diagnostics: BaseModel | None = None,
) -> Any:
    """Redact credentials and host paths, preserving typed diagnostic pointers.

    ``diagnostics`` supplies field meaning for already-serialized service errors
    without replacing their original payload or discarding extra details.
    """
    pointers = _json_pointer_locations(diagnostics if diagnostics is not None else value)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    sanitized = sanitize_payload(value)
    return _sanitize_paths(sanitized, workspace=workspace, pointers=pointers)


def sanitize_mapping(value: Any, *, workspace: WorkspaceService) -> dict[str, Any]:
    sanitized = sanitize_for_mcp(value, workspace=workspace)
    if not isinstance(sanitized, dict):
        raise TypeError("Expected a mapping from the application service")
    return sanitized


def sanitize_mapping_list(
    value: Any,
    *,
    workspace: WorkspaceService,
) -> list[dict[str, Any]]:
    sanitized = sanitize_for_mcp(value, workspace=workspace)
    if not isinstance(sanitized, list) or not all(
        isinstance(item, dict) for item in sanitized
    ):
        raise TypeError("Expected a list of mappings from the application service")
    return sanitized


def _json_pointer_locations(
    value: Any, location: _Location = ()
) -> set[_Location]:
    locations: set[_Location] = set()
    if isinstance(value, BaseModel):
        for name, field in type(value).model_fields.items():
            key = (
                field.serialization_alias or name
                if value.model_config.get("serialize_by_alias")
                else name
            )
            child_location = (*location, key)
            if JSON_POINTER_METADATA in field.metadata:
                locations.add(child_location)
            else:
                locations.update(
                    _json_pointer_locations(getattr(value, name), child_location)
                )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            locations.update(_json_pointer_locations(item, (*location, index)))
    # Untyped dictionaries cannot declare that their path strings are diagnostics.
    return locations


def _sanitize_paths(
    value: Any,
    *,
    workspace: WorkspaceService,
    pointers: set[_Location],
    location: _Location = (),
) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_paths(
                item,
                workspace=workspace,
                pointers=pointers,
                location=(*location, str(key)) if pointers else (),
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_paths(
                item,
                workspace=workspace,
                pointers=pointers,
                location=(*location, index) if pointers else (),
            )
            for index, item in enumerate(value)
        ]
    if not isinstance(value, str) or location in pointers:
        return value

    replaced = redact_path_prefixes(value, (workspace.root,))
    if replaced != value:
        return replaced

    if not (
        value.startswith(("/", "\\"))
        or (len(value) >= 3 and value[1] == ":" and value[2] in "/\\")
    ):
        return value
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return "[EXTERNAL_PATH]"
    return value
