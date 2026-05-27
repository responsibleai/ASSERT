"""Atomic file writer for generated eval configs (stub — wired in later commits)."""

from __future__ import annotations

from pathlib import Path


def emit_config(yaml_content: str, output: Path, *, force: bool = False) -> None:
    """Write *yaml_content* to *output* atomically.

    Writes to a sibling ``.tmp`` file first, then renames.
    Raises ``SystemExit(1)`` if *output* exists and *force* is False.
    """
    raise NotImplementedError("Emit not yet implemented (commit 6)")
