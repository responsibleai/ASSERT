# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from assert_ai.core.config_document import ConfigValidationCode
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.configs import (
    ConfigDesignRequest,
    ConfigService,
)
from assert_ai.services.errors import ServiceError, ServiceErrorCode


def _service(root: Path, *, max_config_bytes: int = 1_048_576) -> ConfigService:
    return ConfigService(
        workspace=WorkspaceService.create(root),
        max_config_bytes=max_config_bytes,
    )


def _valid_document(*, suite: str = "demo") -> dict:
    return {
        "suite": suite,
        "behavior": {"name": "safe_help"},
        "pipeline": {
            "inference": {
                "target": {"callable": "agent:run"},
                "test_set_path": "fixtures/test_set.jsonl",
            }
        },
    }


def test_save_get_list_and_replace_with_etag() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = _service(root)

        saved = service.save_config("nested/demo.yaml", document=_valid_document())
        record = service.get_config("nested/demo.yaml")
        page = service.list_configs(limit=1)

        assert saved.created is True
        assert saved.etag == record.etag
        assert record.config_ref == "nested/demo.yaml"
        assert record.document["suite"] == "demo"
        assert record.validation.valid is True
        assert [entry.config_ref for entry in page.items] == ["nested/demo.yaml"]
        assert page.next_cursor is None

        with pytest.raises(ServiceError) as missing_etag:
            service.save_config(
                "nested/demo.yaml",
                document=_valid_document(suite="changed"),
            )
        assert missing_etag.value.code == ServiceErrorCode.CONFLICT

        with pytest.raises(ServiceError) as stale:
            service.save_config(
                "nested/demo.yaml",
                document=_valid_document(suite="changed"),
                expected_etag="sha256:stale",
            )
        assert stale.value.code == ServiceErrorCode.STALE_ETAG

        replaced = service.save_config(
            "nested/demo.yaml",
            document=_valid_document(suite="changed"),
            expected_etag=record.etag,
        )
        assert replaced.created is False
        assert service.get_config("nested/demo.yaml").document["suite"] == "changed"


def test_concurrent_replacements_allow_only_one_etag_winner() -> None:
    with TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        original = service.save_config("demo.yaml", document=_valid_document())

        def replace(suite: str) -> str:
            try:
                service.save_config(
                    "demo.yaml",
                    document=_valid_document(suite=suite),
                    expected_etag=original.etag,
                )
            except ServiceError as exc:
                return exc.code.value
            return "saved"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(replace, ("first", "second")))

        assert sorted(outcomes) == ["STALE_ETAG", "saved"]


def test_list_configs_is_paginated_and_cursor_is_opaque() -> None:
    with TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        for name in ("a.yaml", "b.yaml", "c.yaml"):
            service.save_config(name, document=_valid_document(suite=name[0]))

        first = service.list_configs(limit=2)
        second = service.list_configs(limit=2, cursor=first.next_cursor)

        assert [item.config_ref for item in first.items] == ["a.yaml", "b.yaml"]
        assert first.next_cursor is not None
        assert "b.yaml" not in first.next_cursor
        assert [item.config_ref for item in second.items] == ["c.yaml"]
        assert second.next_cursor is None

        with pytest.raises(ServiceError) as invalid:
            service.list_configs(cursor="not-a-cursor")
        assert invalid.value.code == ServiceErrorCode.INVALID_ARGUMENT


def test_save_rejects_invalid_config_without_writing() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = _service(root)

        with pytest.raises(ServiceError) as invalid:
            service.save_config(
                "bad.yaml",
                yaml_text=(
                    "pipeline:\n"
                    "  inference:\n"
                    "    target:\n"
                    "      model:\n"
                    "        name: azure/gpt-5.4\n"
                    "      legacy: true\n"
                ),
            )

        assert invalid.value.code == ServiceErrorCode.CONFIG_INVALID
        validation = invalid.value.details["validation"]
        assert validation["issues"][0]["path"] == "/pipeline/inference/target/legacy"
        assert not (root / "evals" / "bad.yaml").exists()


def test_validate_reports_semantics_deprecations_and_workspace_paths() -> None:
    with TemporaryDirectory() as tmp:
        service = _service(Path(tmp))

        deprecated = _valid_document()
        deprecated["default_model"] = {"name": "azure/gpt-5.4"}
        deprecated["pipeline"]["test_set"] = {
            "tool_source": "per_seed",
            "prompt": {},
        }
        report = service.validate_document(deprecated)
        assert report.valid is False
        assert report.warnings[0].code == ConfigValidationCode.DEPRECATED_FIELD
        assert report.warnings[0].path == "/pipeline/test_set/tool_source"
        assert any(
            issue.path == "/pipeline/test_set"
            and "prompt and/or scenario" in issue.message
            for issue in report.issues
        )

        escaped = _valid_document()
        escaped["artifacts_root"] = "../outside"
        report = service.validate_document(escaped)
        assert report.valid is False
        assert report.issues[0].code == ConfigValidationCode.WORKSPACE_VIOLATION
        assert report.issues[0].path == "/artifacts_root"


def test_validation_does_not_import_callable_target() -> None:
    with TemporaryDirectory() as tmp:
        service = _service(Path(tmp))
        raw = _valid_document()
        raw["pipeline"]["inference"]["target"]["callable"] = (
            "does_not_exist.anywhere:run"
        )

        report = service.validate_document(raw)

        assert report.valid is True


def test_sandbox_validation_contains_setup_references() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = _service(root)
        service.workspace.configs_root.mkdir(parents=True)
        outside_policy = root / "outside-policy.yaml"
        outside_policy.write_text(
            "interactions: []\ndefault: {mode: block}\n",
            encoding="utf-8",
        )
        (service.workspace.configs_root / "setup.yaml").write_text(
            "version: 1\n"
            "target: {kind: endpoint, url: 'http://localhost/chat'}\n"
            "policy: ../outside-policy.yaml\n",
            encoding="utf-8",
        )
        document = _valid_document()
        document["pipeline"]["inference"]["target"] = {
            "sandbox": "./setup.yaml",
        }

        report = service.validate_document(document)

        assert report.valid is False
        assert report.issues[0].code == (
            ConfigValidationCode.WORKSPACE_VIOLATION
        )
        assert report.issues[0].path == (
            "/pipeline/inference/target/sandbox/policy"
        )


def test_config_refs_are_contained_and_payloads_are_bounded() -> None:
    with TemporaryDirectory() as tmp:
        service = _service(Path(tmp), max_config_bytes=100)

        with pytest.raises(ServiceError) as escaped:
            service.save_config("../escape.yaml", document=_valid_document())
        assert escaped.value.code == ServiceErrorCode.WORKSPACE_VIOLATION

        with pytest.raises(ServiceError) as absolute:
            service.get_config(str((Path(tmp) / "evals" / "config.yaml").resolve()))
        assert absolute.value.code == ServiceErrorCode.INVALID_ARGUMENT

        with pytest.raises(ServiceError) as too_large:
            service.validate_yaml("x" * 101)
        assert too_large.value.code == ServiceErrorCode.ARTIFACT_TOO_LARGE


def test_list_rejects_linked_config_entries() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = _service(root)
        service.save_config("safe.yaml", document=_valid_document())
        outside = root / "outside.yaml"
        outside.write_text("pipeline:\n  inference: {}\n", encoding="utf-8")
        link = root / "evals" / "linked.yaml"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symbolic links are unavailable")

        with pytest.raises(ServiceError) as linked:
            service.list_configs()

        assert linked.value.code == ServiceErrorCode.WORKSPACE_VIOLATION


def test_design_config_is_headless_and_never_writes_a_draft() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = _service(root)
        yaml_result = (
            "suite: designed\n"
            "behavior:\n"
            "  name: safe_help\n"
            "pipeline:\n"
            "  inference:\n"
            "    target:\n"
            "      callable: agent:run\n"
            "    test_set_path: fixtures/test_set.jsonl\n"
        )

        with patch(
            "assert_ai.init._design_agent.run_design_loop",
            return_value=yaml_result,
        ) as design:
            draft = service.design_config(
                ConfigDesignRequest(description="Evaluate my agent")
            )

        assert draft.document["suite"] == "designed"
        assert draft.validation.valid is True
        assert not (root / "eval.draft.yaml").exists()
        assert design.call_args.kwargs["non_interactive"] is True
        assert design.call_args.kwargs["save_draft_on_failure"] is False


def test_schema_service_returns_versioned_document_schema() -> None:
    with TemporaryDirectory() as tmp:
        schema = _service(Path(tmp)).get_schema()

    assert schema["x-assert-schema-version"] == 1
    assert json.loads(json.dumps(schema))["required"] == ["pipeline"]
