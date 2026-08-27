"""Headless validation bridge for config-design clients."""

from __future__ import annotations

import re
from typing import Any

import yaml

from assert_ai.core.config_document import (
    ConfigValidationCode,
    ConfigValidationIssue,
    format_config_validation_issues,
    validate_eval_config_document,
)

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def validate_proposed_yaml(yaml_str: str) -> tuple[bool, list[str]]:
    """Validate a proposed YAML string against the config schema.

    Returns ``(True, [])`` on success, ``(False, [error1, ...])`` on failure.
    """
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        issue = ConfigValidationIssue(
            code=ConfigValidationCode.INVALID_YAML,
            path="",
            message=str(exc),
        )
        return False, [
            format_config_validation_issues([issue])
        ]
    if not isinstance(data, dict):
        report = validate_eval_config_document(data)
        issues = report.issues or (
            ConfigValidationIssue(
                code=ConfigValidationCode.INVALID_TYPE,
                path="",
                message="Top-level YAML must be a mapping",
            ),
        )
        return False, [
            format_config_validation_issues([issue])
            for issue in issues
        ]
    return validate_raw_config(data)


def validate_raw_config(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate document shape plus inexpensive identifier semantics."""
    report = validate_eval_config_document(data)
    issues = list(report.issues)

    for field in ("suite", "run"):
        val = data.get(field)
        if val is not None:
            val = str(val)
            if not _SAFE_ID_RE.match(val):
                issues.append(
                    ConfigValidationIssue(
                        code=ConfigValidationCode.INVALID_VALUE,
                        path=f"/{field}",
                        message=(
                            "must start with an alphanumeric character and contain only "
                            f"alphanumerics, dots, hyphens, or underscores; got: {val!r}"
                        ),
                    )
                )
            if ".." in val:
                issues.append(
                    ConfigValidationIssue(
                        code=ConfigValidationCode.INVALID_VALUE,
                        path=f"/{field}",
                        message="must not contain '..'",
                    )
                )
            if len(val) > 255:
                issues.append(
                    ConfigValidationIssue(
                        code=ConfigValidationCode.INVALID_VALUE,
                        path=f"/{field}",
                        message="exceeds maximum length of 255 characters",
                    )
                )

    behavior = data.get("behavior")
    pipeline = data.get("pipeline")
    systematize = pipeline.get("systematize") if isinstance(pipeline, dict) else None
    requires_behavior = (
        isinstance(systematize, dict)
        and systematize.get("enabled", True)
    )
    if behavior is None and requires_behavior:
        issues.append(
            ConfigValidationIssue(
                code=ConfigValidationCode.REQUIRED_FIELD,
                path="/behavior",
                message="is required when systematize is enabled",
            )
        )
    if isinstance(behavior, dict):
        name = behavior.get("name")
        if name is not None:
            name = str(name)
            if not _SAFE_ID_RE.match(name):
                issues.append(
                    ConfigValidationIssue(
                        code=ConfigValidationCode.INVALID_VALUE,
                        path="/behavior/name",
                        message=f"must be a valid identifier; got: {name!r}",
                    )
                )
        if not name and not behavior.get("preset"):
            issues.append(
                ConfigValidationIssue(
                    code=ConfigValidationCode.REQUIRED_FIELD,
                    path="/behavior/name",
                    message="is required (or use behavior.preset)",
                )
            )

    if isinstance(pipeline, dict):
        test_set = pipeline.get("test_set")
        if isinstance(test_set, dict):
            stratify = test_set.get("stratify")
            if isinstance(stratify, dict):
                dims = stratify.get("dimensions")
                if isinstance(dims, list):
                    for index, dim in enumerate(dims):
                        if isinstance(dim, dict):
                            dim_name = dim.get("name", "")
                            if dim_name == "behavior":
                                issues.append(
                                    ConfigValidationIssue(
                                        code=ConfigValidationCode.INVALID_VALUE,
                                        path=(
                                            "/pipeline/test_set/stratify/"
                                            f"dimensions/{index}/name"
                                        ),
                                        message=(
                                            "Dimension name 'behavior' is reserved; "
                                            "choose a different name"
                                        ),
                                    )
                                )

    errors = [
        format_config_validation_issues([issue])
        for issue in issues
    ]
    return (not errors), errors
