# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Canonical validation shared by test-case curation and inference."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from assert_ai.core.io import get_permissible_flag
from assert_ai.core.tools import normalize_tool_defs

_NESTED_TEST_CASE_FIELDS = {
    "prompt",
    "description",
    "system_prompt",
    "title",
    "tools",
    "state",
}


def prepare_test_cases(
    rows: Sequence[Mapping[str, Any]],
    *,
    per_test_case_tools: bool | None,
    fixed_system_prompt: str | None,
) -> list[dict[str, Any]]:
    """Validate canonical rows and normalize prompt/scenario payload fields.

    ``per_test_case_tools=None`` performs config-independent validation for
    curation. Inference passes a boolean so target-specific tool invariants
    are enforced before execution.
    """
    test_set: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"test case at index {index} must be an object")

        kind = row.get("type")
        if kind not in {"prompt", "scenario"}:
            raise ValueError(
                f"test case at index {index} must declare type 'prompt' or 'scenario'"
            )

        test_case_payload = row.get("seed")
        if not isinstance(test_case_payload, dict):
            raise ValueError(
                f"{kind} test case at index {index} requires a test case payload object"
            )
        test_case_row = dict(row)
        normalized_payload = dict(test_case_payload)
        system_prompt = (
            str(normalized_payload.get("system_prompt") or "").strip() or None
        )
        if system_prompt is None:
            normalized_payload.pop("system_prompt", None)
        else:
            normalized_payload["system_prompt"] = system_prompt
        if fixed_system_prompt and system_prompt is not None:
            raise ValueError(
                "target.system_prompt cannot be combined with non-empty "
                "test case system_prompt"
            )
        tools = normalized_payload.get("tools")
        if per_test_case_tools is True:
            if not isinstance(tools, list) or not tools:
                raise ValueError(
                    "test case tools are required when tool_source=per_test_case"
                )
            normalize_tool_defs(tools)
        elif per_test_case_tools is None and tools is not None:
            if not isinstance(tools, list) or not tools:
                raise ValueError(
                    "test case tools must be a non-empty list when present"
                )
            normalize_tool_defs(tools)
        elif per_test_case_tools is False and tools is not None:
            raise ValueError(
                "test case tools are only allowed when tool_source=per_test_case"
            )
        test_case_row["seed"] = normalized_payload
        if kind == "prompt":
            invalid_fields = sorted(
                field for field in _NESTED_TEST_CASE_FIELDS if field in row
            )
            if invalid_fields:
                raise ValueError(
                    f"prompt test case at index {index} must move "
                    f"{', '.join(invalid_fields)} under the test case payload"
                )
        if not str(normalized_payload.get("description") or "").strip():
            raise ValueError(
                f"{kind} test case at index {index} requires a non-empty "
                "test case description"
            )
        permissible = get_permissible_flag(test_case_row)
        if permissible is not None:
            test_case_row["permissible"] = permissible
        test_set.append(test_case_row)
    return test_set
