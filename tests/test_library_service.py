# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import pytest

from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.library import LibraryService, PresetKind


def test_library_service_lists_bounded_path_free_presets() -> None:
    service = LibraryService(default_page_size=2, max_page_size=3)

    first = service.list_presets(page_size=2)
    second = service.list_presets(cursor=first.next_cursor, page_size=2)

    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert second.items
    assert {item.kind for item in first.items} <= set(PresetKind)
    assert all(not hasattr(item, "path") for item in first.items)


def test_library_service_cursor_is_bound_to_kind() -> None:
    service = LibraryService(default_page_size=1, max_page_size=2)
    first = service.list_presets(kind=PresetKind.BEHAVIOR, page_size=1)
    assert first.next_cursor is not None

    with pytest.raises(ServiceError) as exc_info:
        service.list_presets(
            kind=PresetKind.JUDGE_PRESET,
            cursor=first.next_cursor,
            page_size=1,
        )

    assert exc_info.value.code is ServiceErrorCode.STALE_CURSOR


def test_library_service_gets_complete_preset() -> None:
    service = LibraryService()

    preset = service.get_preset(PresetKind.BEHAVIOR, "prompt_injection")

    assert preset.kind is PresetKind.BEHAVIOR
    assert preset.name == "prompt_injection"
    assert preset.document["kind"] == "behavior"
    assert "description:" in preset.yaml


def test_library_service_exposes_application_scenarios() -> None:
    service = LibraryService()

    page = service.list_presets(kind=PresetKind.SCENARIO)
    preset = service.get_preset(
        PresetKind.SCENARIO,
        "travel_planner",
    )

    assert page.items
    assert all(item.kind is PresetKind.SCENARIO for item in page.items)
    assert preset.kind is PresetKind.SCENARIO
    assert preset.document["kind"] == "scenario"


def test_library_service_rejects_unknown_or_path_like_names() -> None:
    service = LibraryService()

    with pytest.raises(ServiceError) as exc_info:
        service.get_preset("behavior", "../prompt_injection")
    assert exc_info.value.code is ServiceErrorCode.INVALID_ARGUMENT

    with pytest.raises(ServiceError) as exc_info:
        service.get_preset("behavior", "missing-preset")
    assert exc_info.value.code is ServiceErrorCode.NOT_FOUND
