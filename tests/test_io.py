# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import tracemalloc
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.core.io import (
    load_json,
    load_jsonl,
    write_bytes_atomic,
    write_json,
    write_jsonl,
    write_text_atomic,
)


class LoadJsonlTest(unittest.TestCase):
    def test_load_jsonl_skips_bad_lines_with_warning(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "data.jsonl"
            path.write_text(
                '{"a": 1}\nnot json\n{"b": 2}\n',
                encoding="utf-8",
            )
            with self.assertLogs("assert_ai.core.io", level="WARNING"):
                rows = load_jsonl(path)
            self.assertEqual(len(rows), 2)

    def test_load_jsonl_returns_empty_for_missing_file(self) -> None:
        result = load_jsonl(Path("/tmp/nonexistent.jsonl"))
        self.assertEqual(result, [])


class LoadJsonTest(unittest.TestCase):
    def test_load_json_returns_none_for_missing_file(self) -> None:
        result = load_json(Path("/tmp/nonexistent.json"))
        self.assertIsNone(result)

    def test_load_json_raises_on_corrupt_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bad.json"
            path.write_text("not json at all", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_json(path)


class AtomicWriteTest(unittest.TestCase):
    def test_streamed_json_preserves_serialization(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "data.json"
            payload = {"text": "caf\u00e9", "nested": [{"value": 1}, None, True]}

            write_json(path, payload)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

    def test_streamed_jsonl_preserves_serialization(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "data.jsonl"
            rows = [{"text": "caf\u00e9"}, {"nested": [None, True]}]

            write_jsonl(path, iter(rows))

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            )
            write_jsonl(path, iter(()))
            self.assertEqual(path.read_bytes(), b"")

    def test_jsonl_writer_does_not_materialize_all_rows(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "data.jsonl"
            text = "x" * (64 * 1024)
            tracemalloc.start()
            try:
                write_jsonl(path, ({"text": text} for _ in range(128)))
                _, peak_bytes = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            self.assertLess(peak_bytes, 2 * 1024 * 1024)
            self.assertGreater(path.stat().st_size, 8 * 1024 * 1024)

    def test_encoding_failure_preserves_destination_and_removes_temporary_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "data.json"
            path.write_bytes(b"original")

            with self.assertRaises(TypeError):
                write_json(path, {"unsupported": object()})

            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(root.iterdir()), [path])

    def test_iterator_failure_preserves_destination_and_removes_temporary_file(self) -> None:
        def failing_rows():
            yield {"first": True}
            raise RuntimeError("row generation failed")

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "data.jsonl"
            path.write_bytes(b"original")

            with self.assertRaisesRegex(RuntimeError, "row generation failed"):
                write_jsonl(path, failing_rows())

            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(root.iterdir()), [path])

    def test_flush_failure_cleans_up_all_atomic_writers(self) -> None:
        for writer, payload in (
            (write_json, {"value": True}),
            (write_jsonl, [{"value": True}]),
            (write_text_atomic, "text"),
            (write_bytes_atomic, b"bytes"),
        ):
            with self.subTest(writer=writer.__name__), TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                path = root / "data"
                path.write_bytes(b"original")

                with patch("assert_ai.core.io.os.fsync", side_effect=OSError("disk error")):
                    with self.assertRaisesRegex(OSError, "disk error"):
                        writer(path, payload)

                self.assertEqual(path.read_bytes(), b"original")
                self.assertEqual(list(root.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
