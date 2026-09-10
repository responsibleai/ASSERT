# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from assert_ai.core.config_document import (
    ConfigValidationCode,
    ConfigValidationIssue,
    ConfigValidationReport,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.presentation import dump_yaml, public_run, public_suite, run_resources
from assert_ai.mcp.sanitize import sanitize_for_mcp, sanitize_mapping, sanitize_mapping_list
from assert_ai.services.run_planning import EvaluationPreflight, PreflightIssue


def test_run_projection_preserves_public_fields_without_mutating_summary() -> None:
    summary = {
        "suite_id": "suite",
        "run_id": "run",
        "state": "completed",
        "future_metadata": {"keep": True},
        "artifact_versions": {"test_set": "v0001"},
        "sources": {"scores": {"path": "scores.jsonl"}},
        "indexes": {"scores": {"path": "scores.index.json"}},
    }
    original = deepcopy(summary)

    payload = public_run(summary)

    assert payload == {
        key: value
        for key, value in original.items()
        if key not in {"artifact_versions", "sources", "indexes"}
    }
    assert summary == original


def test_suite_projection_exposes_only_valid_curation_etags() -> None:
    summary = {
        "suite_id": "suite",
        "sources": {
            "taxonomy": {"sha256": "a" * 64},
            "test_set": {"sha256": "b" * 64},
            "scores": {"sha256": "c" * 64},
        },
        "artifact_versions": {},
        "run_set_identity": {"run_count": 1},
        "run_catalog_identity": {"run_count": 1},
    }
    original = deepcopy(summary)

    assert public_suite(summary) == {
        "suite_id": "suite",
        "active_artifact_etags": {
            "taxonomy": "sha256:" + "a" * 64,
            "test_set": "sha256:" + "b" * 64,
        },
    }
    assert summary == original


@pytest.mark.parametrize("sha256", [None, 64, "a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_suite_projection_rejects_invalid_source_hashes(sha256: object) -> None:
    summary = {"sources": {"test_set": {"sha256": sha256}}}

    assert public_suite(summary)["active_artifact_etags"] == {}


def test_yaml_renderer_preserves_unicode_and_one_trailing_newline() -> None:
    document = {"context": "caf\u00e9", "pipeline": {"inference": {"enabled": False}}}

    text = dump_yaml(document)

    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "caf\u00e9" in text
    assert yaml.safe_load(text) == document


def test_run_links_match_resource_templates() -> None:
    assert run_resources("suite-a", "run-a") == {
        "summary": "assert://run/suite-a/run-a/summary",
        "manifest": "assert://run/suite-a/run-a/manifest",
        "config": "assert://run/suite-a/run-a/config",
    }


def test_mapping_helpers_sanitize_without_mutating_input(tmp_path: Path) -> None:
    workspace = WorkspaceService.create(tmp_path)
    value = {"api_key": "not-a-real-secret", "path": str(tmp_path / "example.txt")}
    original = dict(value)

    sanitized = sanitize_mapping(value, workspace=workspace)

    assert sanitized["api_key"] == "[REDACTED]"
    assert str(tmp_path) not in sanitized["path"]
    assert sanitize_mapping_list([value], workspace=workspace) == [sanitized]
    assert value == original


def test_typed_diagnostic_pointers_are_not_filesystem_paths(
    tmp_path: Path,
) -> None:
    pointer = "/pipeline/inference/new~1field/~0option"
    issue = ConfigValidationIssue(
        code=ConfigValidationCode.UNKNOWN_FIELD,
        path=pointer,
        message=f"Unknown field in {tmp_path}",
    )
    plan = EvaluationPreflight(
        config_ref="draft.yaml",
        source_etag="sha256:" + "a" * 64,
        effective_document={"path": pointer},
        validation=ConfigValidationReport(valid=False, issues=(issue,), warnings=(issue,)),
        ready=False,
        blocking_issues=(PreflightIssue(code="INVALID_VALUE", path=pointer, message="Bad"),),
        warnings=(PreflightIssue(code="WARNING", path="", message="Warning"),),
    )
    original = plan.model_dump(mode="json")

    payload = sanitize_for_mcp(plan, workspace=WorkspaceService.create(tmp_path))

    assert payload["validation"]["issues"][0]["path"] == pointer
    assert payload["validation"]["warnings"][0]["path"] == pointer
    assert payload["blocking_issues"][0]["path"] == pointer
    assert payload["warnings"][0]["path"] == ""
    assert str(tmp_path) not in payload["validation"]["issues"][0]["message"]
    assert payload["effective_document"]["path"] == "[EXTERNAL_PATH]"
    assert plan.model_dump(mode="json") == original


def test_host_paths_are_redacted_independently_of_server_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_path_resolution(*args: object, **kwargs: object) -> str:
        raise AssertionError("Redaction must not resolve untrusted filesystem paths")

    workspace = WorkspaceService.create(tmp_path)
    monkeypatch.setattr(WorkspaceService, "reference", reject_path_resolution)
    value = {
        "posix": "/private/worker.log",
        "windows": r"C:\private\worker.log",
        "unc": r"\\redaction-test.invalid\share\worker.log",
        "workspace": str(tmp_path / "worker.log"),
        "relative": "artifacts/results",
        "url": "https://example.test/resource",
        "message": "A response with /slashes/ is not an absolute path.",
    }

    payload = sanitize_mapping(value, workspace=workspace)

    for name in ("posix", "windows", "unc"):
        assert payload[name] == "[EXTERNAL_PATH]"
    assert str(tmp_path) not in payload["workspace"]
    for name in ("relative", "url", "message"):
        assert payload[name] == value[name]


def test_tuple_mapping_values_receive_credential_redaction(tmp_path: Path) -> None:
    value = ({"api_key": "synthetic-value"}, {"nested": ({"password": "test"},)})

    payload = sanitize_mapping_list(value, workspace=WorkspaceService.create(tmp_path))

    assert payload == [
        {"api_key": "[REDACTED]"},
        {"nested": [{"password": "[REDACTED]"}]},
    ]
    assert value[0]["api_key"] == "synthetic-value"


@pytest.mark.parametrize("value", [None, 1, [], "text"])
def test_mapping_helper_rejects_other_shapes(tmp_path: Path, value: object) -> None:
    with pytest.raises(TypeError, match="Expected a mapping"):
        sanitize_mapping(value, workspace=WorkspaceService.create(tmp_path))


@pytest.mark.parametrize("value", [None, {}, ["text"], [{"valid": True}, 1]])
def test_mapping_list_helper_rejects_other_shapes(tmp_path: Path, value: object) -> None:
    with pytest.raises(TypeError, match="Expected a list of mappings"):
        sanitize_mapping_list(value, workspace=WorkspaceService.create(tmp_path))
