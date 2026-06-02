"""Validation bridge — check a proposed YAML against config rules.

Uses the same structural checks as ``assert_ai.config`` without requiring
stage modules or filesystem context so the design agent can validate
proposals mid-conversation.
"""

from __future__ import annotations

from typing import Any

import yaml


def validate_proposed_yaml(yaml_str: str) -> tuple[bool, list[str]]:
    """Validate a proposed YAML string against the config schema.

    Returns ``(True, [])`` on success, ``(False, [error1, ...])`` on failure.
    """
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        return False, [f"Invalid YAML syntax: {exc}"]

    if not isinstance(data, dict):
        return False, ["Top-level YAML must be a mapping"]

    return validate_raw_config(data)


def validate_raw_config(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Structurally validate a raw config dict.

    Checks top-level keys, required fields, identifiers, behavior/context
    shape, and pipeline stage names. Does **not** resolve file paths or
    load stage modules — those are runtime concerns.
    """
    from assert_ai.config import (
        ConfigError,
        _SAFE_ID_RE,
        PIPELINE_STAGE_ORDER,
        reject_unknown_keys,
    )

    errors: list[str] = []

    # -- top-level keys ------------------------------------------------------
    allowed_top = {
        "suite", "run", "behavior", "context", "default_model",
        "artifacts_root", "results_dir", "pipeline",
    }
    unknown = sorted(set(data) - allowed_top)
    if unknown:
        errors.append(f"Unknown top-level key(s): {', '.join(unknown)}")

    # -- identifiers ---------------------------------------------------------
    for field in ("suite", "run"):
        val = data.get(field)
        if val is not None:
            val = str(val)
            if not _SAFE_ID_RE.match(val):
                errors.append(
                    f"{field} must start with an alphanumeric character and "
                    f"contain only alphanumerics, dots, hyphens, or underscores; "
                    f"got: {val!r}"
                )
            if ".." in val:
                errors.append(f"{field} must not contain '..'")
            if len(val) > 255:
                errors.append(f"{field} exceeds maximum length of 255 characters")

    # -- behavior ------------------------------------------------------------
    behavior = data.get("behavior")
    if behavior is None:
        errors.append("'behavior' is required")
    elif not isinstance(behavior, dict):
        errors.append("behavior must be a mapping")
    else:
        beh_allowed = {"name", "description", "preset"}
        beh_unknown = sorted(set(behavior) - beh_allowed)
        if beh_unknown:
            errors.append(f"behavior has unsupported field(s): {', '.join(beh_unknown)}")
        name = behavior.get("name")
        if name is not None:
            name = str(name)
            if not _SAFE_ID_RE.match(name):
                errors.append(
                    f"behavior.name must be a valid identifier; got: {name!r}"
                )
        if not name and not behavior.get("preset"):
            errors.append("behavior.name is required (or use behavior.preset)")

    # -- context -------------------------------------------------------------
    context = data.get("context")
    if context is not None and not isinstance(context, str):
        errors.append("context must be a string")

    # -- pipeline ------------------------------------------------------------
    pipeline = data.get("pipeline")
    if pipeline is None:
        errors.append("'pipeline' is required")
    elif not isinstance(pipeline, dict):
        errors.append("'pipeline' must be a mapping")
    else:
        valid_stages = set(PIPELINE_STAGE_ORDER)
        unknown_stages = sorted(set(pipeline) - valid_stages)
        if unknown_stages:
            errors.append(f"Unknown pipeline stage(s): {', '.join(unknown_stages)}")
        if not any(s in pipeline for s in valid_stages):
            errors.append("'pipeline' must define at least one stage")

    # -- default_model -------------------------------------------------------
    dm = data.get("default_model")
    if dm is not None:
        if isinstance(dm, str):
            pass  # shorthand form — valid
        elif isinstance(dm, dict):
            if "name" not in dm:
                errors.append("default_model.name is required")
        else:
            errors.append("default_model must be a string or mapping")

    # -- dimensions: reserved name check ------------------------------------
    if isinstance(pipeline, dict):
        test_set = pipeline.get("test_set")
        if isinstance(test_set, dict):
            stratify = test_set.get("stratify")
            if isinstance(stratify, dict):
                dims = stratify.get("dimensions")
                if isinstance(dims, list):
                    for dim in dims:
                        if isinstance(dim, dict):
                            dim_name = dim.get("name", "")
                            if dim_name == "behavior":
                                errors.append(
                                    "Dimension name 'behavior' is reserved; "
                                    "choose a different name"
                                )

    return (not errors), errors
