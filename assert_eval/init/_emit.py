"""Atomic file writer for generated eval configs.

Writes to a sibling ``.tmp`` file first, normalizes YAML formatting,
then atomically replaces the final path.  On failure the ``.tmp`` file
is preserved so the user doesn't lose work.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


def emit_config(yaml_content: str, output: Path, *, force: bool = False) -> None:
    """Write *yaml_content* to *output* atomically.

    1. Normalize YAML formatting via a safe_load/dump roundtrip.
    2. Write to ``{output}.tmp``.
    3. Replace *output* atomically (works cross-platform).

    Raises ``FileExistsError`` if *output* exists and *force* is False.
    """
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists. Use --force to overwrite.")

    # Normalize formatting.
    data = yaml.safe_load(yaml_content)
    normalized = yaml.dump(data, default_flow_style=False, sort_keys=False)

    # Ensure trailing newline.
    if not normalized.endswith("\n"):
        normalized += "\n"

    tmp_path = output.with_suffix(output.suffix + ".tmp")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(normalized, encoding="utf-8")
        tmp_path.replace(output)
    except Exception:
        log.warning("Atomic write failed. Draft preserved at %s", tmp_path)
        raise
