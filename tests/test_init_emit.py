"""Tests for the emit (file writer) module in ``assert-eval init``."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assert_eval.init._emit import emit_config


_VALID_YAML = "suite: test\nbehavior:\n  name: b\n  description: d\n"


class EmitConfigTest(unittest.TestCase):
    def test_writes_file(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "eval.yaml"
            emit_config(_VALID_YAML, out)
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("suite:", content)

    def test_no_force_raises_on_existing(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "eval.yaml"
            out.write_text("existing", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                emit_config(_VALID_YAML, out, force=False)

    def test_force_overwrites_existing(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "eval.yaml"
            out.write_text("existing", encoding="utf-8")
            emit_config(_VALID_YAML, out, force=True)
            content = out.read_text(encoding="utf-8")
            self.assertIn("suite:", content)

    def test_yaml_roundtrip_normalization(self) -> None:
        messy = "suite:   test\nbehavior:  \n  name: b\n  description:    d\n"
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "eval.yaml"
            emit_config(messy, out)
            content = out.read_text(encoding="utf-8")
            # Roundtripped YAML should be cleaner
            self.assertNotIn("   ", content)


if __name__ == "__main__":
    unittest.main()
