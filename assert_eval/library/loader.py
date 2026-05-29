# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Discover and load preset YAML files from the library directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

LIBRARY_ROOT = Path(__file__).resolve().parent

VALID_KINDS = {"behavior", "judge_preset"}

KIND_TO_SUBDIR = {
    "behavior": "behaviors",
    "judge_preset": "judges",
}


def resolve_preset(kind: str, name: str) -> Path:
    """Return the path to a preset YAML file, or raise ValueError."""
    if kind not in KIND_TO_SUBDIR:
        raise ValueError(f"Unknown preset kind: {kind!r}. Must be one of {sorted(VALID_KINDS)}")
    subdir = LIBRARY_ROOT / KIND_TO_SUBDIR[kind]
    path = subdir / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in subdir.glob("*.yaml"))
        raise ValueError(
            f"{kind} preset {name!r} not found. Available: {', '.join(available) or '(none)'}"
        )
    return path


def load_preset(kind: str, name: str) -> dict[str, Any]:
    """Load a preset YAML file and validate its kind field."""
    path = resolve_preset(kind, name)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Preset file {path} must contain a YAML mapping")
    file_kind = data.get("kind")
    if file_kind != kind:
        raise ValueError(
            f"Preset {name!r} has kind={file_kind!r}, expected {kind!r}"
        )
    return data


def discover(kind: str | None = None) -> list[dict[str, Any]]:
    """Discover all presets, optionally filtered by kind.

    Returns a list of dicts with keys: kind, name, path, and any
    top-level metadata (version, tags, description/summary).
    """
    kinds = [kind] if kind else sorted(VALID_KINDS)
    results: list[dict[str, Any]] = []
    for k in kinds:
        if k not in KIND_TO_SUBDIR:
            raise ValueError(f"Unknown preset kind: {k!r}")
        subdir = LIBRARY_ROOT / KIND_TO_SUBDIR[k]
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.glob("*.yaml")):
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict) or data.get("kind") != k:
                continue
            entry: dict[str, Any] = {
                "kind": k,
                "name": data.get("name", path.stem),
                "path": str(path),
            }
            for key in ("version", "tags", "description", "summary"):
                if key in data:
                    entry[key] = data[key]
            results.append(entry)
    return results
