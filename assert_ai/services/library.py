# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Typed, bounded access to ASSERT's built-in preset library."""

from __future__ import annotations

import base64
import json
from enum import StrEnum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from assert_ai.library.loader import discover, load_preset
from assert_ai.services.errors import ServiceError, ServiceErrorCode

_CURSOR_VERSION = 1
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_MAX_PAGE_SIZE = 200


class PresetKind(StrEnum):
    """Stable built-in preset categories."""

    BEHAVIOR = "behavior"
    JUDGE_PRESET = "judge_preset"
    SCENARIO = "scenario"


class _LibraryModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class PresetCatalogEntry(_LibraryModel):
    """Lightweight metadata for one built-in preset."""

    kind: PresetKind
    name: str
    version: str | None = None
    tags: tuple[str, ...] = ()
    summary: str | None = None
    description: str | None = None


class PresetPage(_LibraryModel):
    """Bounded page of built-in preset metadata."""

    items: tuple[PresetCatalogEntry, ...]
    next_cursor: str | None = None


class PresetRecord(_LibraryModel):
    """One complete built-in preset definition."""

    kind: PresetKind
    name: str
    version: str | None = None
    tags: tuple[str, ...] = ()
    yaml: str
    document: dict[str, Any]


class LibraryService:
    """Read the packaged preset library without exposing package paths."""

    def __init__(
        self,
        *,
        default_page_size: int = _DEFAULT_PAGE_SIZE,
        max_page_size: int = _DEFAULT_MAX_PAGE_SIZE,
    ) -> None:
        if default_page_size < 1:
            raise ValueError("default_page_size must be positive")
        if max_page_size < default_page_size:
            raise ValueError("max_page_size must be >= default_page_size")
        self.default_page_size = default_page_size
        self.max_page_size = max_page_size

    def list_presets(
        self,
        *,
        kind: str | PresetKind | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> PresetPage:
        try:
            parsed_kind = PresetKind(kind) if kind is not None else None
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"Unknown preset kind: {kind!r}",
            ) from exc
        entries = [
            self._catalog_entry(item)
            for item in discover(parsed_kind.value if parsed_kind is not None else None)
        ]
        entries.sort(key=lambda item: (item.kind.value, item.name))

        limit = self._page_size(page_size)
        offset = 0
        if cursor is not None:
            payload = _decode_cursor(cursor)
            expected_kind = parsed_kind.value if parsed_kind is not None else None
            if payload.get("kind") != expected_kind:
                raise ServiceError(
                    ServiceErrorCode.STALE_CURSOR,
                    "Preset cursor does not match the requested filter",
                )
            offset = int(payload["offset"])
            if offset < 0 or offset > len(entries):
                raise ServiceError(
                    ServiceErrorCode.STALE_CURSOR,
                    "Preset cursor is no longer valid",
                )

        items = tuple(entries[offset : offset + limit])
        next_cursor = None
        next_offset = offset + len(items)
        if next_offset < len(entries):
            next_cursor = _encode_cursor(
                kind=parsed_kind.value if parsed_kind is not None else None,
                offset=next_offset,
            )
        return PresetPage(items=items, next_cursor=next_cursor)

    def get_preset(
        self,
        kind: str | PresetKind,
        name: str,
    ) -> PresetRecord:
        try:
            parsed_kind = PresetKind(kind)
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"Unknown preset kind: {kind!r}",
            ) from exc
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                "Preset name must be a simple library identifier",
            )
        try:
            document = load_preset(parsed_kind.value, name)
        except ValueError as exc:
            raise ServiceError(
                ServiceErrorCode.NOT_FOUND,
                f"Preset not found: {parsed_kind.value}/{name}",
            ) from exc

        normalized = yaml.safe_dump(
            document,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        return PresetRecord(
            kind=parsed_kind,
            name=str(document.get("name") or name),
            version=_optional_text(document.get("version")),
            tags=_tags(document.get("tags")),
            yaml=normalized if normalized.endswith("\n") else normalized + "\n",
            document=json.loads(json.dumps(document, ensure_ascii=False)),
        )

    def _page_size(self, requested: int | None) -> int:
        if requested is None:
            return self.default_page_size
        if requested < 1 or requested > self.max_page_size:
            raise ServiceError(
                ServiceErrorCode.INVALID_ARGUMENT,
                f"page_size must be between 1 and {self.max_page_size}",
            )
        return requested

    @staticmethod
    def _catalog_entry(item: dict[str, Any]) -> PresetCatalogEntry:
        return PresetCatalogEntry(
            kind=PresetKind(str(item["kind"])),
            name=str(item["name"]),
            version=_optional_text(item.get("version")),
            tags=_tags(item.get("tags")),
            summary=_optional_text(item.get("summary")),
            description=_bounded_text(item.get("description"), limit=512),
        )


def _tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _optional_text(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int, float)) else None


def _bounded_text(value: Any, *, limit: int) -> str | None:
    text = _optional_text(value)
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _encode_cursor(*, kind: str | None, offset: int) -> str:
    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "kind": kind,
            "offset": offset,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid preset cursor",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or payload.get("kind") not in {None, *(kind.value for kind in PresetKind)}
        or not isinstance(payload.get("offset"), int)
    ):
        raise ServiceError(
            ServiceErrorCode.INVALID_ARGUMENT,
            "Invalid preset cursor",
        )
    return payload
