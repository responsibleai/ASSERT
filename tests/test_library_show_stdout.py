# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for ``assert-ai library show`` stdout encoding.

The command emits YAML with ``allow_unicode=True`` so em-dashes and CJK
render verbatim. When stdout is a text stream whose encoding cannot
represent those characters (Windows ANSI code pages, ``PYTHONIOENCODING``
set to ``ascii`` or ``cp1252``), a naive ``click.echo`` re-encodes the
payload and raises ``UnicodeEncodeError``. ``_write_stdout_utf8`` writes
UTF-8 bytes through ``sys.stdout.buffer`` to bypass that re-encoding, so
the on-disk YAML produced by a redirected ``library show`` matches what
``assert-ai init`` writes.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys

import click
import pytest
import yaml

from assert_ai.cli import _write_stdout_utf8


class _StdoutWithBuffer:
    """Minimal stdout stub exposing a ``buffer`` attribute for byte writes."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, _text: str) -> int:  # pragma: no cover - guards the fast path
        raise AssertionError("text write path used instead of buffer")


class _StdoutWithoutBuffer:
    """Minimal stdout stub with no binary layer, mirroring CliRunner streams."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def test_write_stdout_utf8_uses_buffer_when_available(monkeypatch) -> None:
    """Bytes go through ``sys.stdout.buffer`` verbatim, no re-encoding."""
    stub = _StdoutWithBuffer()
    monkeypatch.setattr(sys, "stdout", stub)
    _write_stdout_utf8("preset: 日本語\n")
    assert stub.buffer.getvalue() == "preset: 日本語\n".encode("utf-8")


def test_write_stdout_utf8_falls_back_when_no_buffer(monkeypatch) -> None:
    """Text streams without ``.buffer`` (Click's CliRunner) still work."""
    stub = _StdoutWithoutBuffer()
    monkeypatch.setattr(sys, "stdout", stub)
    # click.echo resolves its target via _default_text_stdout(); redirect that
    # too so the fallback exercised here matches what CliRunner would see.
    monkeypatch.setattr(click.utils, "_default_text_stdout", lambda: stub)
    _write_stdout_utf8("preset: hello\n")
    assert "".join(stub.written) == "preset: hello\n"


@pytest.mark.skipif(
    shutil.which("assert-ai") is None,
    reason="assert-ai console script not installed in this environment",
)
def test_library_show_survives_ascii_stdout_encoding() -> None:
    """End-to-end regression: a preset with an em-dash prints as valid UTF-8
    even when ``PYTHONIOENCODING=ascii`` would make ``click.echo`` raise."""
    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    # ``stereotyping`` is a bundled behavior preset that contains em-dashes.
    # Passing ``--kind behavior`` explicitly keeps the regression stable across
    # library reshuffles that move presets between kinds.
    result = subprocess.run(
        ["assert-ai", "library", "show", "stereotyping", "--kind", "behavior"],
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    doc = yaml.safe_load(result.stdout.decode("utf-8"))
    assert doc["name"] == "stereotyping"
    # Confirm the em-dash actually survived to the output, i.e. the guard is
    # doing something (a broken guard could still exit 0 by silently stripping).
    assert "—" in result.stdout.decode("utf-8")
