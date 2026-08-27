# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Versioned byte-offset indexes for canonical ASSERT JSONL artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from assert_ai.core.io import load_json, write_json

JSONL_INDEX_SCHEMA_VERSION = 1
DEFAULT_MAX_INDEXED_ROW_BYTES = 16 * 1024 * 1024


class JsonlIndexErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    INVALID_JSON = "invalid_json"
    INVALID_ROW = "invalid_row"
    INVALID_KEY = "invalid_key"
    DUPLICATE_KEY = "duplicate_key"
    INVALID_INDEX = "invalid_index"
    STALE_INDEX = "stale_index"
    SOURCE_CHANGED = "source_changed"
    ROW_TOO_LARGE = "row_too_large"


class JsonlIndexError(ValueError):
    """Typed JSONL scan, index, and lookup failure."""

    def __init__(
        self,
        code: JsonlIndexErrorCode,
        message: str,
        *,
        path: Path,
        line_number: int | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.line_number = line_number
        self.key = key


@dataclass(frozen=True, slots=True)
class JsonlRecord:
    offset: int
    length: int
    line_number: int
    row: dict[str, Any]


@dataclass(frozen=True, slots=True)
class JsonlScan:
    path: Path
    records: tuple[JsonlRecord, ...]
    size_bytes: int
    mtime_ns: int
    sha256: str


def jsonl_index_path(source_path: Path) -> Path:
    """Return the canonical sibling index path for one JSONL source."""
    return source_path.with_name(f"{source_path.stem}.index.json")


def jsonl_row_key(kind: str, test_case_id: str) -> str:
    return f"{kind}:{test_case_id}"


def scan_jsonl(
    path: Path,
    *,
    allow_trailing_partial: bool = False,
    max_row_bytes: int | None = None,
) -> JsonlScan:
    """Scan one JSONL file in binary mode and preserve exact byte ranges."""
    try:
        before = path.stat()
    except FileNotFoundError as exc:
        raise JsonlIndexError(
            JsonlIndexErrorCode.NOT_FOUND,
            f"Missing JSONL artifact: {path}",
            path=path,
        ) from exc

    records: list[JsonlRecord] = []
    digest = hashlib.sha256()
    offset = 0
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            length = len(raw_line)
            digest.update(raw_line)
            if max_row_bytes is not None and length > max_row_bytes:
                raise JsonlIndexError(
                    JsonlIndexErrorCode.ROW_TOO_LARGE,
                    f"JSONL row exceeds {max_row_bytes} bytes in {path} on line {line_number}",
                    path=path,
                    line_number=line_number,
                )
            stripped = raw_line.strip()
            if not stripped:
                offset += length
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                is_trailing_partial = (
                    allow_trailing_partial
                    and not raw_line.endswith((b"\n", b"\r"))
                    and offset + length == before.st_size
                )
                if is_trailing_partial:
                    offset += length
                    continue
                raise JsonlIndexError(
                    JsonlIndexErrorCode.INVALID_JSON,
                    f"Invalid JSONL in {path} on line {line_number}: {exc}",
                    path=path,
                    line_number=line_number,
                ) from exc
            if not isinstance(row, dict):
                raise JsonlIndexError(
                    JsonlIndexErrorCode.INVALID_ROW,
                    f"Expected JSON object in {path} on line {line_number}",
                    path=path,
                    line_number=line_number,
                )
            records.append(
                JsonlRecord(
                    offset=offset,
                    length=length,
                    line_number=line_number,
                    row=row,
                )
            )
            offset += length

    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise JsonlIndexError(
            JsonlIndexErrorCode.SOURCE_CHANGED,
            f"JSONL source changed while it was being indexed: {path}",
            path=path,
        )
    return JsonlScan(
        path=path,
        records=tuple(records),
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


def build_jsonl_index(
    source_path: Path,
    *,
    index_path: Path | None = None,
    scan: JsonlScan | None = None,
) -> dict[str, Any]:
    """Build and atomically persist a unique type/test-case lookup index."""
    source_path = source_path.resolve()
    current_scan = scan or scan_jsonl(source_path)
    if current_scan.path.resolve() != source_path:
        raise ValueError("scan path does not match source_path")

    items: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in current_scan.records:
        kind, test_case_id = _row_identity(record.row, path=source_path)
        key = jsonl_row_key(kind, test_case_id)
        if key in items:
            raise JsonlIndexError(
                JsonlIndexErrorCode.DUPLICATE_KEY,
                f"Duplicate {key} row in {source_path}",
                path=source_path,
                line_number=record.line_number,
                key=key,
            )
        items[key] = {
            "type": kind,
            "test_case_id": test_case_id,
            "offset": record.offset,
            "length": record.length,
            "line_number": record.line_number,
        }
        order.append(key)

    payload = {
        "schema_version": JSONL_INDEX_SCHEMA_VERSION,
        "source": {
            "name": source_path.name,
            "size_bytes": current_scan.size_bytes,
            "mtime_ns": current_scan.mtime_ns,
            "sha256": current_scan.sha256,
        },
        "key_fields": ["type", "test_case_id"],
        "row_count": len(order),
        "order": order,
        "items": items,
    }
    write_json(index_path or jsonl_index_path(source_path), payload)
    return payload


def load_jsonl_index(
    source_path: Path,
    *,
    index_path: Path | None = None,
    verify_hash: bool = False,
) -> dict[str, Any]:
    """Load an index and reject incompatible or stale source metadata."""
    source_path = source_path.resolve()
    resolved_index_path = index_path or jsonl_index_path(source_path)
    try:
        payload = load_json(resolved_index_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise JsonlIndexError(
            JsonlIndexErrorCode.INVALID_INDEX,
            f"Invalid or unreadable JSONL index: {resolved_index_path}",
            path=resolved_index_path,
        ) from exc
    if not _valid_index_payload(payload):
        raise JsonlIndexError(
            JsonlIndexErrorCode.INVALID_INDEX,
            f"Invalid or missing JSONL index: {resolved_index_path}",
            path=resolved_index_path,
        )
    assert payload is not None
    source = payload["source"]
    try:
        stat_result = source_path.stat()
    except FileNotFoundError as exc:
        raise JsonlIndexError(
            JsonlIndexErrorCode.NOT_FOUND,
            f"Missing JSONL artifact: {source_path}",
            path=source_path,
        ) from exc
    is_current = (
        source.get("name") == source_path.name
        and source.get("size_bytes") == stat_result.st_size
        and source.get("mtime_ns") == stat_result.st_mtime_ns
    )
    if is_current and verify_hash:
        is_current = source.get("sha256") == _file_sha256(source_path)
    if not is_current:
        raise JsonlIndexError(
            JsonlIndexErrorCode.STALE_INDEX,
            f"JSONL index is stale for {source_path}",
            path=resolved_index_path,
        )
    return payload


def read_indexed_jsonl_row(
    source_path: Path,
    *,
    kind: str,
    test_case_id: str,
    index_path: Path | None = None,
    max_row_bytes: int = DEFAULT_MAX_INDEXED_ROW_BYTES,
) -> dict[str, Any]:
    """Seek directly to one indexed row and verify its stable identity."""
    source_path = source_path.resolve()
    payload = load_jsonl_index(source_path, index_path=index_path)
    key = jsonl_row_key(kind, test_case_id)
    item = payload["items"].get(key)
    if not isinstance(item, dict):
        raise JsonlIndexError(
            JsonlIndexErrorCode.NOT_FOUND,
            f"JSONL row not found: {key}",
            path=source_path,
            key=key,
        )
    offset = item.get("offset")
    length = item.get("length")
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or not isinstance(length, int)
        or isinstance(length, bool)
        or length < 1
    ):
        raise JsonlIndexError(
            JsonlIndexErrorCode.INVALID_INDEX,
            f"Invalid byte range for {key}",
            path=index_path or jsonl_index_path(source_path),
            key=key,
        )
    if length > max_row_bytes:
        raise JsonlIndexError(
            JsonlIndexErrorCode.ROW_TOO_LARGE,
            f"Indexed JSONL row exceeds {max_row_bytes} bytes: {key}",
            path=source_path,
            key=key,
        )
    with source_path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read(length)
    try:
        row = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise JsonlIndexError(
            JsonlIndexErrorCode.STALE_INDEX,
            f"Indexed JSONL row is no longer readable: {key}",
            path=source_path,
            key=key,
        ) from exc
    if not isinstance(row, dict) or _row_identity(row, path=source_path) != (
        kind,
        test_case_id,
    ):
        raise JsonlIndexError(
            JsonlIndexErrorCode.STALE_INDEX,
            f"Indexed JSONL row identity changed: {key}",
            path=source_path,
            key=key,
        )
    stat_result = source_path.stat()
    source = payload["source"]
    if (
        stat_result.st_size != source["size_bytes"]
        or stat_result.st_mtime_ns != source["mtime_ns"]
    ):
        raise JsonlIndexError(
            JsonlIndexErrorCode.STALE_INDEX,
            f"JSONL source changed during indexed lookup: {source_path}",
            path=source_path,
            key=key,
        )
    return row


def _row_identity(row: dict[str, Any], *, path: Path) -> tuple[str, str]:
    kind = row.get("type")
    test_case_id = row.get("test_case_id")
    if not isinstance(kind, str) or not kind:
        raise JsonlIndexError(
            JsonlIndexErrorCode.INVALID_KEY,
            f'{path.name}: expected field "type" (a non-empty string)',
            path=path,
        )
    if not isinstance(test_case_id, str) or not test_case_id:
        raise JsonlIndexError(
            JsonlIndexErrorCode.INVALID_KEY,
            f'{path.name}: expected field "test_case_id" (a non-empty string)',
            path=path,
        )
    return kind, test_case_id


def _valid_index_payload(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("schema_version") != JSONL_INDEX_SCHEMA_VERSION:
        return False
    source = payload.get("source")
    items = payload.get("items")
    order = payload.get("order")
    row_count = payload.get("row_count")
    return (
        isinstance(source, dict)
        and isinstance(source.get("name"), str)
        and isinstance(source.get("size_bytes"), int)
        and isinstance(source.get("mtime_ns"), int)
        and isinstance(source.get("sha256"), str)
        and len(source["sha256"]) == 64
        and all(char in "0123456789abcdef" for char in source["sha256"])
        and isinstance(items, dict)
        and isinstance(order, list)
        and all(isinstance(key, str) for key in order)
        and isinstance(row_count, int)
        and row_count == len(order)
        and row_count == len(items)
        and len(set(order)) == len(order)
        and set(order) == set(items)
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
