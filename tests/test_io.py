# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from p2m.core.io import load_json, load_jsonl


class LoadJsonlTest(unittest.TestCase):
    def test_load_jsonl_skips_bad_lines_with_warning(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "data.jsonl"
            path.write_text(
                '{"a": 1}\nnot json\n{"b": 2}\n',
                encoding="utf-8",
            )
            with self.assertLogs("p2m.core.io", level="WARNING"):
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


if __name__ == "__main__":
    unittest.main()
