"""Atomic file writer for generated eval configs.

Writes to a sibling ``.tmp`` file first, normalizes YAML formatting,
then atomically replaces the final path.  On failure the ``.tmp`` file
is preserved so the user doesn't lose work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from assert_ai.core.config_model import GENERATED_TESTER_MAX_TURNS
from assert_ai.core.yaml_io import dump_yaml

log = logging.getLogger(__name__)


def apply_generated_defaults(data: Any) -> Any:
    """State the values a generated config should not inherit by omission.

    ``DEFAULT_TESTER_MAX_TURNS`` is deliberately pinned to the legacy value so
    configs written before the harm-eval templates keep the turn budget they
    already ran with. That makes an omitted ``max_turns`` mean *the old
    default*, which is the wrong answer for a config being written now. The
    design agent is told to write the key, but an instruction to a model is not
    a guarantee, so the value is applied here as well.

    Only fills what is absent, so an explicit choice by the user or the model
    always wins, and re-applying is a no-op.
    """
    if not isinstance(data, dict):
        return data
    pipeline = data.get("pipeline")
    if not isinstance(pipeline, dict):
        return data
    inference = pipeline.get("inference")
    if not isinstance(inference, dict):
        return data
    inference.setdefault("max_turns", GENERATED_TESTER_MAX_TURNS)
    return data


def emit_config(yaml_content: str, output: Path, *, force: bool = False) -> None:
    """Write *yaml_content* to *output* atomically.

    1. Normalize YAML formatting via a safe_load/dump roundtrip.
    2. Fill values a generated config must state rather than inherit.
    3. Write to ``{output}.tmp``.
    4. Replace *output* atomically (works cross-platform).

    Raises ``FileExistsError`` if *output* exists and *force* is False.
    """
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists. Use --force to overwrite.")

    # Normalize formatting.
    data = apply_generated_defaults(yaml.safe_load(yaml_content))
    normalized = dump_yaml(data)

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
