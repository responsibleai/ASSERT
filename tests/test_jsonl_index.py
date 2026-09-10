# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from assert_ai.core.jsonl_index import (
    JsonlIndexError,
    JsonlIndexErrorCode,
    build_jsonl_index,
    jsonl_index_path,
    load_jsonl_index,
    read_indexed_jsonl_row,
    scan_jsonl,
)


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_build_and_seek_preserve_utf8_byte_offsets() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "scores.jsonl"
        rows = [
            {"type": "prompt", "test_case_id": "one", "text": "café"},
            {"type": "scenario", "test_case_id": "two", "text": "東京"},
        ]
        _write_rows(source, rows)

        payload = build_jsonl_index(source)

        assert payload["row_count"] == 2
        assert payload["source"]["sha256"]
        assert payload["order"] == ["prompt:one", "scenario:two"]
        assert jsonl_index_path(source).exists()
        assert read_indexed_jsonl_row(
            source,
            kind="scenario",
            test_case_id="two",
        ) == rows[1]


def test_scan_tracks_blank_lines_and_crlf_lengths() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "test_set.jsonl"
        first = b'{"type":"prompt","test_case_id":"one"}\r\n'
        blank = b"\r\n"
        second = b'{"type":"scenario","test_case_id":"two"}\r\n'
        source.write_bytes(first + blank + second)

        scan = scan_jsonl(source)

        assert scan.records[0].offset == 0
        assert scan.records[0].length == len(first)
        assert scan.records[1].offset == len(first) + len(blank)
        assert scan.records[1].length == len(second)


def test_duplicate_keys_and_invalid_rows_fail_index_build() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "inference_set.jsonl"
        row = {"type": "prompt", "test_case_id": "duplicate"}
        _write_rows(source, [row, row])

        with pytest.raises(JsonlIndexError) as duplicate:
            build_jsonl_index(source)
        assert duplicate.value.code == JsonlIndexErrorCode.DUPLICATE_KEY

        _write_rows(source, [{"type": "prompt"}])
        with pytest.raises(JsonlIndexError) as missing_id:
            build_jsonl_index(source)
        assert missing_id.value.code == JsonlIndexErrorCode.INVALID_KEY


def test_invalid_json_and_trailing_partial_handling() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "scores.jsonl"
        source.write_bytes(
            b'{"type":"prompt","test_case_id":"one"}\n{"type":"prompt"'
        )

        with pytest.raises(JsonlIndexError) as invalid:
            scan_jsonl(source)
        assert invalid.value.code == JsonlIndexErrorCode.INVALID_JSON
        assert invalid.value.line_number == 2

        scan = scan_jsonl(source, allow_trailing_partial=True)
        assert len(scan.records) == 1


def test_stale_index_is_rejected_after_source_change() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "scores.jsonl"
        _write_rows(source, [{"type": "prompt", "test_case_id": "one"}])
        build_jsonl_index(source)

        source.write_text(
            '{"type":"prompt","test_case_id":"changed"}\n',
            encoding="utf-8",
        )

        with pytest.raises(JsonlIndexError) as stale:
            load_jsonl_index(source)
        assert stale.value.code == JsonlIndexErrorCode.STALE_INDEX


def test_invalid_index_and_missing_row_are_typed() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "scores.jsonl"
        _write_rows(source, [{"type": "prompt", "test_case_id": "one"}])

        with pytest.raises(JsonlIndexError) as missing_index:
            load_jsonl_index(source)
        assert missing_index.value.code == JsonlIndexErrorCode.INVALID_INDEX

        build_jsonl_index(source)
        with pytest.raises(JsonlIndexError) as missing_row:
            read_indexed_jsonl_row(
                source,
                kind="prompt",
                test_case_id="missing",
            )
        assert missing_row.value.code == JsonlIndexErrorCode.NOT_FOUND


def test_streamed_index_matches_materialized_scan_with_partial_tail(tmp_path: Path) -> None:
    source = tmp_path / "scores.jsonl"
    source.write_bytes(
        b'\r\n{"type":"prompt","test_case_id":"one","text":"caf\\u00e9"}\r\n'
        b'{"type":"scenario","test_case_id":"two"}\n'
        b'{"type":"prompt"'
    )
    scan = scan_jsonl(source, allow_trailing_partial=True)
    expected = build_jsonl_index(source, scan=scan)

    actual = build_jsonl_index(source, allow_trailing_partial=True)

    assert actual == expected
    assert read_indexed_jsonl_row(
        source, kind="prompt", test_case_id="one"
    )["text"] == "caf\u00e9"
    with pytest.raises(JsonlIndexError) as incomplete:
        build_jsonl_index(source)
    assert incomplete.value.code == JsonlIndexErrorCode.INVALID_JSON
    assert load_jsonl_index(source) == expected


def test_index_build_does_not_retain_source_payloads(tmp_path: Path) -> None:
    source = tmp_path / "inference_set.jsonl"
    row_count = 256
    text = "x" * (64 * 1024)
    with source.open("w", encoding="utf-8") as handle:
        for number in range(row_count):
            handle.write(
                json.dumps(
                    {"type": "prompt", "test_case_id": str(number), "text": text}
                )
                + "\n"
            )

    tracemalloc.start()
    try:
        index = build_jsonl_index(source)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert index["row_count"] == row_count
    assert peak_bytes < row_count * len(text) // 2


def test_index_build_rejects_source_change_before_publishing(tmp_path: Path) -> None:
    source = tmp_path / "scores.jsonl"
    _write_rows(source, [{"type": "prompt", "test_case_id": "one"}])
    build_jsonl_index(source)
    original_index = jsonl_index_path(source).read_bytes()

    def change_source(row: dict, *, path: Path) -> tuple[str, str]:
        _write_rows(path, [{"type": "prompt", "test_case_id": "x"}])
        return row["type"], row["test_case_id"]

    with patch("assert_ai.core.jsonl_index._row_identity", side_effect=change_source):
        with pytest.raises(JsonlIndexError) as changed:
            build_jsonl_index(source)

    assert changed.value.code == JsonlIndexErrorCode.SOURCE_CHANGED
    assert jsonl_index_path(source).read_bytes() == original_index


def test_duplicate_index_order_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "scores.jsonl"
    _write_rows(
        source,
        [
            {"type": "prompt", "test_case_id": "one"},
            {"type": "prompt", "test_case_id": "two"},
        ],
    )
    payload = build_jsonl_index(source)
    payload["order"] = ["prompt:one", "prompt:one"]
    jsonl_index_path(source).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(JsonlIndexError) as duplicate:
        load_jsonl_index(source)
    assert duplicate.value.code == JsonlIndexErrorCode.INVALID_INDEX
