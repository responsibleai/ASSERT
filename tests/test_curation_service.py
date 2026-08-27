# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

import assert_ai.services.curation as curation_module
from assert_ai.core.artifact_cache import (
    activate_artifact_plan,
    finalize_artifact_plan,
    prepare_artifact_plan,
)
from assert_ai.core.io import write_json, write_jsonl
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.curation import (
    CurationService,
    TestCaseRevision as CaseRevision,
)
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.job_models import NewJob
from assert_ai.services.job_store import JobStore


def _taxonomy() -> dict:
    return {
        "behavior": {
            "name": "safe_travel",
            "definition": "The planner follows travel safety constraints.",
        },
        "definition_of_terms": [],
        "behavior_categories": [
            {
                "name": "safe_booking",
                "definition": "Books only policy-compliant travel.",
                "examples": ["Reject an unsafe itinerary."],
                "permissible": True,
            },
            {
                "name": "unsafe_booking",
                "definition": "Books travel that violates policy.",
                "examples": ["Ignore a safety restriction."],
                "permissible": False,
            },
        ],
        "meta": {
            "source": "systematization",
            "slug": "safe_travel",
        },
    }


def _rows() -> list[dict]:
    return [
        {
            "type": "prompt",
            "test_case_id": "test_case_000001",
            "seed": {"description": "Book a safe flight."},
            "dimensions": {"behavior": "safe_booking"},
        },
        {
            "type": "scenario",
            "test_case_id": "test_case_000002",
            "seed": {"description": "Ignore the restriction."},
            "dimensions": {"behavior": "unsafe_booking"},
        },
    ]


def _seed_suite(
    tmp_path: Path,
) -> tuple[WorkspaceService, JobStore, Path]:
    workspace = WorkspaceService.create(tmp_path)
    workspace.configs_root.mkdir(parents=True)
    workspace.results_root.mkdir(parents=True)
    config_path = workspace.configs_root / "demo.yaml"
    config_path.write_text("suite: suite-a\n", encoding="utf-8")
    suite_root = workspace.results_root / "suite-a"
    suite_root.mkdir()
    ctx = {
        "suite_id": "suite-a",
        "suite_root": str(suite_root),
        "results_root": str(workspace.results_root),
        "artifacts_root": str(workspace.artifacts_root),
        "config_path": str(config_path),
        "path_policy": workspace.path_policy,
        "behavior_name": "safe_travel",
        "behavior": "The planner follows travel safety constraints.",
        "context": "A travel planning agent.",
        "dimensions": {},
        "artifact_versions": {},
    }

    taxonomy_plan = prepare_artifact_plan(
        ctx=ctx,
        stage_name="systematize",
        raw_cfg={"model": {"name": "test/model"}},
        forced=True,
    )
    activate_artifact_plan(ctx, taxonomy_plan)
    write_json(taxonomy_plan.output_paths["taxonomy"], _taxonomy())
    write_json(
        taxonomy_plan.output_paths["systematization"],
        {
            "behavior": "safe_travel",
            "systematization": "Original systematization",
            "summary_items": [],
        },
    )
    finalize_artifact_plan(ctx, taxonomy_plan)

    test_set_plan = prepare_artifact_plan(
        ctx=ctx,
        stage_name="test_set",
        raw_cfg={
            "model": {"name": "test/model"},
            "prompt": {"sample_size": 1},
            "scenario": {"sample_size": 1},
        },
        forced=True,
    )
    activate_artifact_plan(ctx, test_set_plan)
    write_jsonl(test_set_plan.output_paths["test_set"], _rows())
    write_json(test_set_plan.output_paths["stratification"], {"counts": {}})
    finalize_artifact_plan(ctx, test_set_plan)

    store = JobStore(workspace.artifacts_root / "mcp" / "jobs.sqlite3")
    return workspace, store, suite_root


def _etag(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def test_revise_taxonomy_creates_immutable_versions_and_rebases_test_set(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source_taxonomy = (
        suite_root / "artifacts" / "systematize" / "v0001" / "taxonomy.json"
    )
    original_taxonomy = source_taxonomy.read_bytes()
    original_test_set = (
        suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    ).read_bytes()
    revised = deepcopy(_taxonomy())
    revised["behavior_categories"][0]["definition"] = "Revised definition."

    result = CurationService(workspace, job_store=store).revise_taxonomy(
        "suite-a",
        revised,
        expected_etag=_etag(source_taxonomy),
        change_summary="Clarify the safe-booking rubric.",
    )

    assert [(item.artifact_type, item.version) for item in result.artifacts] == [
        ("systematize", "v0002"),
        ("test_set", "v0002"),
    ]
    latest = json.loads((suite_root / "latest.json").read_text(encoding="utf-8"))
    assert latest["artifacts"]["systematize"]["version"] == "v0002"
    assert latest["artifacts"]["test_set"]["version"] == "v0002"
    assert source_taxonomy.read_bytes() == original_taxonomy
    assert (
        suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    ).read_bytes() == original_test_set
    assert (
        suite_root / "artifacts" / "test_set" / "v0002" / "test_set.jsonl"
    ).read_bytes() == original_test_set
    assert json.loads(
        (suite_root / "taxonomy.json").read_text(encoding="utf-8")
    )["behavior_categories"][0]["definition"] == "Revised definition."
    assert json.loads(
        (suite_root / "taxonomy.json").read_text(encoding="utf-8")
    )["meta"]["source"] == "systematization"

    metadata = json.loads(
        (
            suite_root
            / "artifacts"
            / "systematize"
            / "v0002"
            / "artifact.json"
        ).read_text(encoding="utf-8")
    )
    assert metadata["provenance"]["edited_from"]["version"] == "v0001"
    assert metadata["provenance"]["change_summary"] == (
        "Clarify the safe-booking rubric."
    )
    old_test_metadata = json.loads(
        (
            suite_root / "artifacts" / "test_set" / "v0001" / "artifact.json"
        ).read_text(encoding="utf-8")
    )
    new_test_metadata = json.loads(
        (
            suite_root / "artifacts" / "test_set" / "v0002" / "artifact.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        new_test_metadata["hashes"]["config_hash"]
        == old_test_metadata["hashes"]["config_hash"]
    )
    assert (
        new_test_metadata["hashes"]["input_hash"]
        != old_test_metadata["hashes"]["input_hash"]
    )
    summary = json.loads(
        (suite_root / "suite_summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact_versions"]["systematize"]["version"] == "v0002"
    assert summary["artifact_versions"]["test_set"]["version"] == "v0002"


def test_revise_taxonomy_rejects_stale_etag_and_category_shape_changes(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    service = CurationService(workspace, job_store=store)

    with pytest.raises(ServiceError) as stale:
        service.revise_taxonomy(
            "suite-a",
            _taxonomy(),
            expected_etag="sha256:" + ("0" * 64),
            change_summary="Attempt a stale edit.",
        )
    assert stale.value.code == ServiceErrorCode.STALE_ETAG

    reordered = deepcopy(_taxonomy())
    reordered["behavior_categories"].reverse()
    source = suite_root / "artifacts" / "systematize" / "v0001" / "taxonomy.json"
    with pytest.raises(ServiceError) as invalid:
        service.revise_taxonomy(
            "suite-a",
            reordered,
            expected_etag=_etag(source),
            change_summary="Reorder categories.",
        )
    assert invalid.value.code == ServiceErrorCode.INVALID_ARGUMENT
    assert not (
        suite_root / "artifacts" / "systematize" / "v0002"
    ).exists()

    with pytest.raises(ServiceError) as unchanged:
        service.revise_taxonomy(
            "suite-a",
            _taxonomy(),
            expected_etag=_etag(source),
            change_summary="Attempt a no-op revision.",
        )
    assert unchanged.value.code == ServiceErrorCode.INVALID_ARGUMENT


def test_bulk_revise_test_cases_preserves_ids_order_and_old_version(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source = suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    original = source.read_bytes()

    result = CurationService(
        workspace,
        job_store=store,
    ).bulk_revise_test_cases(
        "suite-a",
        (
            CaseRevision(
                test_case_id="test_case_000002",
                updates={"seed": {"description": "Revised unsafe request."}},
            ),
            CaseRevision(
                test_case_id="test_case_000001",
                updates={"seed": {"description": "Revised safe request."}},
            ),
        ),
        expected_etag=_etag(source).removeprefix("sha256:"),
        change_summary="Make both prompts more explicit.",
    )

    assert result.affected_test_case_ids == (
        "test_case_000002",
        "test_case_000001",
    )
    assert source.read_bytes() == original
    revised_rows = [
        json.loads(line)
        for line in (
            suite_root / "artifacts" / "test_set" / "v0002" / "test_set.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["test_case_id"] for row in revised_rows] == [
        "test_case_000001",
        "test_case_000002",
    ]
    assert revised_rows[0]["seed"]["description"] == "Revised safe request."
    assert revised_rows[1]["seed"]["description"] == "Revised unsafe request."


def test_post_activation_summary_failure_keeps_new_version(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source = (
        suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    )

    with patch(
        "assert_ai.services.curation.write_suite_summary",
        side_effect=OSError("fixture summary failure"),
    ):
        result = CurationService(
            workspace,
            job_store=store,
        ).revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": {"description": "Revised prompt."}},
            expected_etag=_etag(source),
            change_summary="Exercise post-activation failure handling.",
        )

    latest = json.loads((suite_root / "latest.json").read_text(encoding="utf-8"))
    assert latest["artifacts"]["test_set"]["version"] == "v0002"
    assert (
        suite_root / "artifacts" / "test_set" / "v0002" / "test_set.jsonl"
    ).is_file()
    assert result.warnings == (
        "Artifacts were activated, but suite summary refresh failed",
    )


def test_post_activation_lock_release_failure_keeps_new_version(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source = (
        suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    )

    with patch.object(
        store,
        "release_operation_locks",
        side_effect=OSError("fixture lock release failure"),
    ):
        result = CurationService(
            workspace,
            job_store=store,
        ).revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": {"description": "Revised despite cleanup failure."}},
            expected_etag=_etag(source),
            change_summary="Exercise lease cleanup failure handling.",
        )

    latest = json.loads((suite_root / "latest.json").read_text(encoding="utf-8"))
    assert latest["artifacts"]["test_set"]["version"] == "v0002"
    assert result.artifacts[0].version == "v0002"


def test_post_activation_base_exception_keeps_new_version(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source = (
        suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    )

    with (
        patch(
            "assert_ai.services.curation.refresh_compatibility_files",
            side_effect=KeyboardInterrupt,
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        CurationService(
            workspace,
            job_store=store,
        ).revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": {"description": "Committed before interruption."}},
            expected_etag=_etag(source),
            change_summary="Interrupt post-activation cleanup.",
        )

    latest = json.loads((suite_root / "latest.json").read_text(encoding="utf-8"))
    assert latest["artifacts"]["test_set"]["version"] == "v0002"
    assert (
        suite_root / "artifacts" / "test_set" / "v0002" / "test_set.jsonl"
    ).is_file()


def test_curation_rejects_tampered_immutable_source(tmp_path: Path) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source = (
        suite_root / "artifacts" / "systematize" / "v0001" / "taxonomy.json"
    )
    tampered = _taxonomy()
    tampered["behavior"]["definition"] = "Changed outside curation."
    source.write_text(json.dumps(tampered), encoding="utf-8")
    revised = deepcopy(tampered)
    revised["behavior_categories"][0]["definition"] = "Intended revision."

    with pytest.raises(ServiceError) as invalid:
        CurationService(workspace, job_store=store).revise_taxonomy(
            "suite-a",
            revised,
            expected_etag=_etag(source),
            change_summary="Try to build on a tampered version.",
        )

    assert invalid.value.code == ServiceErrorCode.CONFIG_INVALID
    assert not (
        suite_root / "artifacts" / "systematize" / "v0002"
    ).exists()


def test_curation_rejects_tampered_companion_artifact(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source = (
        suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    )
    companion = (
        suite_root
        / "artifacts"
        / "test_set"
        / "v0001"
        / "stratification.json"
    )
    companion.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(ServiceError) as invalid:
        CurationService(workspace, job_store=store).revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": {"description": "Revision over corrupt source."}},
            expected_etag=_etag(source),
            change_summary="Reject a corrupt companion.",
        )

    assert invalid.value.code == ServiceErrorCode.CONFIG_INVALID
    with pytest.raises(ServiceError) as invalid_tools:
        CurationService(workspace, job_store=store).revise_test_case(
            "suite-a",
            "test_case_000001",
            {
                "seed": {
                    "description": "Malformed tools.",
                    "tools": "not-a-list",
                }
            },
            expected_etag=_etag(source),
            change_summary="Attempt malformed per-test-case tools.",
        )

    assert invalid_tools.value.code == ServiceErrorCode.CONFIG_INVALID
    with pytest.raises(ServiceError) as missing_tool_name:
        CurationService(workspace, job_store=store).revise_test_case(
            "suite-a",
            "test_case_000001",
            {
                "seed": {
                    "description": "Malformed tool entry.",
                    "tools": [{}],
                }
            },
            expected_etag=_etag(source),
            change_summary="Attempt a tool without a name.",
        )

    assert missing_tool_name.value.code == ServiceErrorCode.CONFIG_INVALID
    assert not (
        suite_root / "artifacts" / "test_set" / "v0002"
    ).exists()


def test_test_case_revision_rejects_identity_changes_and_unknown_categories(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    service = CurationService(workspace, job_store=store)
    source = suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"

    with pytest.raises(ServiceError) as identity:
        service.revise_test_case(
            "suite-a",
            "test_case_000001",
            {"test_case_id": "different"},
            expected_etag=_etag(source),
            change_summary="Try to rename an identity.",
        )
    assert identity.value.code == ServiceErrorCode.INVALID_ARGUMENT

    with pytest.raises(ServiceError) as category:
        service.revise_test_case(
            "suite-a",
            "test_case_000001",
            {"dimensions": {"behavior": "not-a-category"}},
            expected_etag=_etag(source),
            change_summary="Try an unknown category.",
        )
    assert category.value.code == ServiceErrorCode.INVALID_ARGUMENT

    with pytest.raises(ServiceError) as unchanged:
        service.revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": {"description": "Book a safe flight."}},
            expected_etag=_etag(source),
            change_summary="Attempt a no-op revision.",
        )
    assert unchanged.value.code == ServiceErrorCode.INVALID_ARGUMENT


def test_test_case_revision_rejects_rows_inference_cannot_execute(
    tmp_path: Path,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    source = suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"

    with pytest.raises(ServiceError) as invalid:
        CurationService(workspace, job_store=store).revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": None},
            expected_etag=_etag(source),
            change_summary="Attempt an invalid seed payload.",
        )

    assert invalid.value.code == ServiceErrorCode.CONFIG_INVALID
    assert not (
        suite_root / "artifacts" / "test_set" / "v0002"
    ).exists()


def test_curation_enforces_revised_artifact_size_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    service = CurationService(workspace, job_store=store)
    taxonomy_source = (
        suite_root
        / "artifacts"
        / "systematize"
        / "v0001"
        / "taxonomy.json"
    )
    revised_taxonomy = _taxonomy()
    revised_taxonomy["behavior"]["definition"] = "x" * 1_000
    monkeypatch.setattr(
        curation_module,
        "_MAX_TAXONOMY_BYTES",
        taxonomy_source.stat().st_size + 10,
    )

    with pytest.raises(ServiceError) as taxonomy_too_large:
        service.revise_taxonomy(
            "suite-a",
            revised_taxonomy,
            expected_etag=_etag(taxonomy_source),
            change_summary="Oversized taxonomy.",
        )
    assert taxonomy_too_large.value.code == ServiceErrorCode.ARTIFACT_TOO_LARGE

    test_set_source = (
        suite_root
        / "artifacts"
        / "test_set"
        / "v0001"
        / "test_set.jsonl"
    )
    monkeypatch.setattr(
        curation_module,
        "_MAX_TEST_SET_BYTES",
        test_set_source.stat().st_size + 10,
    )
    with pytest.raises(ServiceError) as test_set_too_large:
        service.revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": {"description": "x" * 1_000}},
            expected_etag=_etag(test_set_source),
            change_summary="Oversized test case.",
        )
    assert test_set_too_large.value.code == ServiceErrorCode.ARTIFACT_TOO_LARGE


def test_curation_conflicts_with_an_active_suite_job(tmp_path: Path) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    store.create_or_get(
        NewJob(
            job_id="job-active",
            idempotency_key="request-active",
            request_hash="hash-active",
            request_sha256="sha256:" + ("0" * 64),
            suite_id="suite-a",
            run_id="run-active",
            config_ref="demo.yaml",
            config_sha256="sha256:config",
            snapshot_path="artifacts/mcp/jobs/job-active/config.yaml",
            request_path="artifacts/mcp/jobs/job-active/request.json",
            resource_keys=("suite:suite-a",),
        ),
        max_queued_jobs=10,
    )
    assert store.claim_next(
        lease_owner="manager",
        lease_seconds=30,
        max_active_jobs=1,
    ) is not None

    source = suite_root / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
    with pytest.raises(ServiceError) as conflict:
        CurationService(workspace, job_store=store).revise_test_case(
            "suite-a",
            "test_case_000001",
            {"seed": {"description": "Blocked edit."}},
            expected_etag=_etag(source),
            change_summary="This should be blocked.",
        )
    assert conflict.value.code == ServiceErrorCode.CONFLICT


def test_suite_mutation_renews_its_operation_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, store, suite_root = _seed_suite(tmp_path)
    service = CurationService(workspace, job_store=store)
    renewed = threading.Event()
    original_renew = store.renew_operation_locks

    def tracked_renew(*args, **kwargs):
        result = original_renew(*args, **kwargs)
        renewed.set()
        return result

    monkeypatch.setattr(curation_module, "_OPERATION_LEASE_S", 0.15)
    monkeypatch.setattr(store, "renew_operation_locks", tracked_renew)

    competing_store = JobStore(
        workspace.artifacts_root / "mcp" / "jobs.sqlite3"
    )
    with service._suite_mutation("suite-a", suite_root) as ensure_lock:
        assert renewed.wait(timeout=2)
        ensure_lock()
        assert not competing_store.acquire_operation_locks(
            ("suite:suite-a",),
            owner="other-curator",
            lease_seconds=30,
        )

    assert competing_store.acquire_operation_locks(
        ("suite:suite-a",),
        owner="other-curator",
        lease_seconds=30,
    )
    competing_store.release_operation_locks(owner="other-curator")
