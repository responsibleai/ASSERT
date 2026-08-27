# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Portable validation and locking identities for managed suite/run paths."""

from __future__ import annotations

import re
from typing import Any

from assert_ai.services.errors import ServiceError, ServiceErrorCode

_OUTPUT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def validate_output_id(value: Any, *, field_name: str) -> str:
    """Return a portable path component or raise a stable service error."""
    if not isinstance(value, str) or not _OUTPUT_ID_RE.fullmatch(value):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"{field_name} must contain only letters, numbers, '.', '_', or '-'",
        )
    if value.endswith("."):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"{field_name} must not end with a period",
        )
    device_stem = value.split(".", 1)[0].upper()
    if device_stem in _WINDOWS_RESERVED_NAMES:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            f"{field_name} uses a reserved Windows device name",
        )
    return value


def suite_resource_key(suite_id: str) -> str:
    """Return a case-insensitive, cross-platform suite lock identity."""
    return f"suite:{suite_id.casefold()}"


def run_resource_key(suite_id: str, run_id: str) -> str:
    """Return a case-insensitive, cross-platform run lock identity."""
    return f"run:{suite_id.casefold()}/{run_id.casefold()}"
