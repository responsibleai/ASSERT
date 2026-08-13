# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for assert_ai.core.yaml_io.dump_yaml.

These check the two properties the emitter used to lose:

- multi-line strings are emitted as literal block scalars (``|``),
  not as double-quoted flow scalars with escaped ``\\n``.
- non-ASCII characters (em-dashes, curly quotes, CJK) are emitted
  verbatim, not as ``\\uXXXX`` escapes.
"""

from __future__ import annotations

import yaml

from assert_ai.core.yaml_io import dump_yaml


def test_multiline_string_uses_block_scalar() -> None:
    data = {"description": "line one\nline two\nline three"}
    out = dump_yaml(data)
    assert "description: |" in out
    assert "\\n" not in out
    # Roundtrip preserves the string exactly.
    assert yaml.safe_load(out)["description"] == data["description"]


def test_single_line_string_stays_plain() -> None:
    data = {"name": "single_line_value"}
    out = dump_yaml(data)
    # No block indicator for single-line values.
    assert "name: single_line_value" in out
    assert "name: |" not in out


def test_non_ascii_emitted_verbatim() -> None:
    data = {"context": "em—dash and “curly quotes” and 日本語"}
    out = dump_yaml(data)
    assert "—" in out
    assert "“" in out
    assert "日本語" in out
    assert "\\u" not in out
    assert yaml.safe_load(out)["context"] == data["context"]


def test_multiline_with_unicode_uses_block_scalar_and_verbatim() -> None:
    data = {"rubric": "step 1 — describe\nstep 2 — evaluate"}
    out = dump_yaml(data)
    assert "rubric: |" in out
    assert "—" in out
    assert "\\n" not in out
    assert "\\u" not in out


def test_key_order_is_preserved() -> None:
    data = {"z": 1, "m": 2, "a": 3}
    out = dump_yaml(data)
    lines = [line for line in out.splitlines() if line and not line.startswith(" ")]
    assert lines == ["z: 1", "m: 2", "a: 3"]


def test_nested_mapping_uses_block_style() -> None:
    data = {
        "behavior": {
            "name": "grounded",
            "description": "line one\nline two",
        }
    }
    out = dump_yaml(data)
    # Nested mapping is emitted in block style, not inline flow style.
    assert "behavior:" in out
    assert "  description: |" in out
    assert "{" not in out


def test_line_break_control_chars_round_trip() -> None:
    """U+0085, U+2028, U+2029 fold to a space under plain/single-quoted YAML
    with ``allow_unicode=True``; forcing double-quoted style preserves them."""
    for ch in ("\u0085", "\u2028", "\u2029"):
        # Single-line: string is only the control char surrounded by ASCII.
        single = {"x": f"a{ch}b"}
        assert yaml.safe_load(dump_yaml(single)) == single, (
            f"single-line round trip failed for U+{ord(ch):04X}"
        )
        # Multi-line: control char sits inside an already-multi-line scalar.
        multi = {"x": f"line one\nl{ch}ne two"}
        assert yaml.safe_load(dump_yaml(multi)) == multi, (
            f"multi-line round trip failed for U+{ord(ch):04X}"
        )


def test_line_break_control_chars_use_double_quoted_style() -> None:
    """Sanity check: the guard picks double-quoted, not block scalar."""
    out = dump_yaml({"x": "a\u0085b"})
    assert 'x: "a\\Nb"' in out
