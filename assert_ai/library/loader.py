# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Discover and load preset YAML files from the library directory."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import yaml

LIBRARY_ROOT = Path(__file__).resolve().parent

VALID_KINDS = {"behavior", "judge_preset", "scenario"}

KIND_TO_SUBDIR = {
    "behavior": "behaviors",
    "judge_preset": "judges",
    # Application scenarios (role, domain objects, tools, procedures) rather
    # than atomic behaviors. Kept a distinct kind so a scenario cannot be
    # mistaken for something a single judge verdict can be attributed to.
    "scenario": "scenarios",
}


def resolve_preset(kind: str, name: str) -> Path:
    """Return the path to a preset YAML file, or raise ValueError."""
    if kind not in KIND_TO_SUBDIR:
        raise ValueError(f"Unknown preset kind: {kind!r}. Must be one of {sorted(VALID_KINDS)}")
    subdir = LIBRARY_ROOT / KIND_TO_SUBDIR[kind]
    path = subdir / f"{name}.yaml"
    if not path.is_file():
        # Compatibility shim: these three were reclassified from `behavior` to
        # `scenario` because they describe an application, not one atomic
        # mechanism. Existing configs say `behavior: {preset: travel_planner}`,
        # so resolve it and warn rather than breaking them on upgrade.
        if kind == "behavior":
            moved = LIBRARY_ROOT / KIND_TO_SUBDIR["scenario"] / f"{name}.yaml"
            if moved.is_file():
                warnings.warn(
                    f"{name!r} is an application scenario, not an atomic behavior, and moved to "
                    f"the 'scenario' kind. Use kind='scenario', and pair it with atomic behaviors "
                    f"via context:. Resolving as a behavior is deprecated.",
                    FutureWarning,
                    stacklevel=2,
                )
                return moved
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
    # A preset reached through the deprecation shim legitimately declares a
    # different kind than the one asked for; don't fail that path.
    if file_kind != kind and not (kind == "behavior" and file_kind == "scenario"):
        raise ValueError(
            f"Preset {name!r} has kind={file_kind!r}, expected {kind!r}"
        )
    if kind == "behavior" and file_kind == "scenario" and not data.get("description"):
        data = {**data, "description": _legacy_scenario_description(data)}
    return data


def _legacy_scenario_description(data: dict[str, Any]) -> str:
    """Build a deprecated behavior description for configs using behavior.preset."""
    title = str(data.get("summary") or data.get("name") or "Application scenario")
    context = str(data.get("context") or "").strip()
    behaviors = data.get("behaviors") or []
    lines = [
        f"# {data.get('name', 'scenario')}",
        "",
        title,
    ]
    if context:
        lines.extend(["", context])
    if behaviors:
        lines.extend(["", "Applicable atomic behavior presets:"])
        lines.extend(f"- {behavior}" for behavior in behaviors)
    return "\n".join(lines).strip() + "\n"


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
