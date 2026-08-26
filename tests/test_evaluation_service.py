# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from assert_ai.config import load_runtime_context
from assert_ai.core.artifact_cache import (
    activate_artifact_plan,
    finalize_artifact_plan,
    prepare_artifact_plan,
)
from assert_ai.core.io import write_json
from assert_ai.core.run_result import RunResult, RunState
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services._evaluation_worker import (
    _BoundedTextLog,
    main as worker_main,
)
from assert_ai.services.artifact_pins import load_artifact_pin
from assert_ai.services.configs import ConfigService
from assert_ai.services.curation import CurationService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.evaluations import (
    EvaluationJobManager,
    EvaluationService,
    _active_stage_from_events,
)
from assert_ai.services.job_models import JobState, NewJob
from assert_ai.services.job_store import JobStore
from assert_ai.services.results import ResultRepository
from assert_ai.services.run_planning import (
    EvaluationOverrides,
    PreflightPolicy,
    RunPlanningService,
)
from assert_ai.stages import STAGES


def _service(
    root: Path,
    *,
    cancellation_grace_seconds: float = 10.0,
    lease_seconds: float = 60.0,
    max_trace_input_bytes: int = 16 * 1024 * 1024,
    max_prompt_sample_size: int = 100_000,
    max_concurrency: int = 32,
    allowed_model_patterns: tuple[str, ...] = (),
) -> tuple[ConfigService, EvaluationService]:
    workspace = WorkspaceService.create(root)
    configs = ConfigService(workspace)
    planning = RunPlanningService(
        workspace,
        configs,
        policy=PreflightPolicy(
            max_prompt_sample_size=max_prompt_sample_size,
            max_concurrency=max_concurrency,
            allowed_model_patterns=allowed_model_patterns,
        ),
    )
    store = JobStore(workspace.artifacts_root / "mcp" / "jobs.sqlite3")
    manager = EvaluationJobManager(
        workspace,
        store,
        max_active_jobs=1,
        cancellation_grace_seconds=cancellation_grace_seconds,
        lease_seconds=lease_seconds,
    )
    return configs, EvaluationService(
        workspace,
        configs,
        planning,
        store,
        manager,
        default_page_size=10,
        max_page_size=20,
        max_queued_jobs=10,
        max_trace_input_bytes=max_trace_input_bytes,
    )


def _write_inference_fixture(root: Path) -> dict:
    fixture = root / "evals" / "fixture.jsonl"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(
        json.dumps(
            {
                "type": "prompt",
                "test_case_id": "case-1",
                "behavior": "local behavior",
                "seed": {"description": "hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "agent.py").write_text(
        "def run(message, *, history=None):\n"
        "    del history\n"
        "    return f'local: {message}'\n",
        encoding="utf-8",
    )
    return {
        "suite": "mcp-job-suite",
        "pipeline": {
            "inference": {
                "target": {
                    "callable": "agent:run",
                },
                "test_set_path": "fixture.jsonl",
                "concurrency": 1,
            }
        },
    }


def _write_trace_fixture(root: Path, *, with_events: bool = False) -> dict:
    evals_root = root / "evals"
    fixtures_root = root / "fixtures"
    evals_root.mkdir(parents=True, exist_ok=True)
    fixtures_root.mkdir(parents=True, exist_ok=True)
    write_json(
        evals_root / "trace_taxonomy.json",
        {
            "behavior": {
                "name": "safe_agent",
                "definition": "The agent follows safety requirements.",
            },
            "definition_of_terms": [],
            "behavior_categories": [
                {
                    "name": "safe",
                    "definition": "The agent follows the requirement.",
                    "examples": ["The agent refuses an unsafe action."],
                    "permissible": True,
                }
            ],
        },
    )
    attributes = [
        {
            "key": "session.id",
            "value": {"stringValue": "session-one"},
        }
    ]
    if with_events:
        attributes.extend(
            [
                {
                    "key": "openinference.span.kind",
                    "value": {"stringValue": "LLM"},
                },
                {
                    "key": "input.value",
                    "value": {"stringValue": "Help me."},
                },
                {
                    "key": "output.value",
                    "value": {"stringValue": "Here is a safe response."},
                },
                {
                    "key": "llm.model_name",
                    "value": {"stringValue": "fixture-model"},
                },
            ]
        )
    write_json(
        fixtures_root / "traces.json",
        {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "a" * 32,
                                    "spanId": "b" * 16,
                                    "name": "agent",
                                    "startTimeUnixNano": "1",
                                    "endTimeUnixNano": "2",
                                    "attributes": attributes,
                                }
                            ]
                        }
                    ]
                }
            ]
        },
    )
    return {
        "default_model": {"name": "fixture/judge"},
        "pipeline": {
            "judge": {
                "model": {"name": "fixture/judge"},
                "taxonomy_path": "trace_taxonomy.json",
            }
        },
    }


def _wait_terminal(
    service: EvaluationService,
    job_id: str,
    *,
    timeout_s: float = 30,
):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        detail = service.get(job_id)
        if detail.state in {
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.INTERRUPTED,
        }:
            return detail
        time.sleep(0.05)
    raise AssertionError("evaluation job did not finish")


def _wait_state(
    service: EvaluationService,
    job_id: str,
    state: JobState,
    *,
    timeout_s: float = 10,
):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        detail = service.get(job_id)
        if detail.state is state:
            return detail
        time.sleep(0.05)
    raise AssertionError(f"evaluation job did not reach {state.value}")


def _wait_stage_state(
    service: EvaluationService,
    job_id: str,
    stage: str,
    state: str,
    *,
    timeout_s: float = 15,
):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        detail = service.get(job_id)
        if detail.stages.get(stage) == state:
            return detail
        time.sleep(0.05)
    raise AssertionError(
        f"evaluation stage {stage} did not reach {state}"
    )


def test_inference_only_job_completes_and_is_idempotent(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )

    started = service.start(
        "demo.yaml",
        request_id="request-one",
    )
    terminal = _wait_terminal(service, started.job.job_id)
    repeated = service.start(
        "demo.yaml",
        request_id="request-one",
    )
    _, restarted_service = _service(tmp_path)
    repeated_after_restart = restarted_service.start(
        "demo.yaml",
        request_id="request-one",
    )

    assert started.created is True
    assert terminal.state is JobState.COMPLETED
    assert terminal.terminal_result is not None
    assert terminal.terminal_result.exit_code == 0
    assert terminal.run_id is not None
    assert repeated.created is False
    assert repeated.job.job_id == started.job.job_id
    assert repeated_after_restart.created is False
    assert repeated_after_restart.job.job_id == started.job.job_id
    run_root = (
        tmp_path
        / "artifacts"
        / "results"
        / terminal.suite_id
        / terminal.run_id
    )
    inference_rows = [
        json.loads(line)
        for line in (run_root / "inference_set.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert inference_rows[0]["events"][-1]["edit"]["message"]["content"] == (
        "local: hello"
    )
    snapshot = (run_root / "config.yaml").read_text(encoding="utf-8")
    assert f"run: {terminal.run_id}" in snapshot
    assert service.list().items[0].job_id == started.job.job_id


def test_queued_job_rejects_a_different_curated_artifact_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    seed_document = {
        "suite": "pinned-suite",
        "behavior": {
            "name": "safe_help",
            "description": "The agent should provide safe help.",
        },
        "default_model": {"name": "openai/gpt-test"},
        "pipeline": {
            "systematize": {
                "model": {"name": "openai/gpt-test"},
            },
            "test_set": {
                "model": {"name": "openai/gpt-test"},
                "prompt": {"sample_size": 1},
            },
        },
    }
    configs.save_config("seed.yaml", document=seed_document)
    record = configs.get_config("seed.yaml")
    config_path = service.workspace.path_policy.resolve_config_path(
        record.config_ref,
        must_exist=True,
        reject_links=True,
    )
    context = load_runtime_context(
        deepcopy(record.document),
        config_path,
        stage_modules=STAGES,
        path_policy=service.workspace.path_policy,
    )
    raw_systematize = dict(
        next(raw for name, raw in context["stages"] if name == "systematize")
    )
    taxonomy_artifact = prepare_artifact_plan(
        ctx=context,
        stage_name="systematize",
        raw_cfg=raw_systematize,
        forced=False,
    )
    activate_artifact_plan(context, taxonomy_artifact)
    taxonomy = {
        "behavior": {
            "name": "safe_help",
            "definition": "The agent should provide safe help.",
        },
        "definition_of_terms": [],
        "behavior_categories": [
            {
                "name": "safe",
                "definition": "The response follows the requirement.",
                "examples": ["Provide safe help."],
                "permissible": True,
            }
        ],
    }
    write_json(taxonomy_artifact.output_paths["taxonomy"], taxonomy)
    write_json(
        taxonomy_artifact.output_paths["systematization"],
        {
            "behavior": "safe_help",
            "systematization": "Fixture",
            "summary_items": [],
        },
    )
    finalize_artifact_plan(context, taxonomy_artifact)
    raw_test_set = dict(
        next(raw for name, raw in context["stages"] if name == "test_set")
    )
    test_set_artifact = prepare_artifact_plan(
        ctx=context,
        stage_name="test_set",
        raw_cfg=raw_test_set,
        forced=False,
    )
    activate_artifact_plan(context, test_set_artifact)
    test_set_artifact.output_paths["test_set"].write_text(
        json.dumps(
            {
                "type": "prompt",
                "test_case_id": "case-1",
                "seed": {"description": "Provide safe help."},
                "dimensions": {"behavior": "safe"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(test_set_artifact.output_paths["stratification"], {"counts": {}})
    finalize_artifact_plan(context, test_set_artifact)
    (tmp_path / "agent.py").write_text(
        "def run(message, *, history=None):\n"
        "    del history\n"
        "    return message\n",
        encoding="utf-8",
    )
    configs.save_config(
        "pinned.yaml",
        document={
            "suite": "pinned-suite",
            "pipeline": {
                "inference": {
                    "target": {"callable": "agent:run"},
                    "concurrency": 1,
                },
            },
        },
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start(
        "pinned.yaml",
        request_id="pinned-artifact",
    )
    job_record = service.store.get(started.job.job_id)
    request = json.loads(
        Path(job_record.request_path).read_text(encoding="utf-8")
    )
    assert request["expected_artifacts"]["test_set"]["version"] == (
        test_set_artifact.version
    )
    test_set_etag = "sha256:" + hashlib.sha256(
        test_set_artifact.output_paths["test_set"].read_bytes()
    ).hexdigest()
    CurationService(
        service.workspace,
        job_store=service.store,
    ).revise_test_case(
        "pinned-suite",
        "case-1",
        {"seed": {"description": "Curated after preflight."}},
        expected_etag=test_set_etag,
        change_summary="Change the queued job's active test set.",
    )

    exit_code = worker_main(
        [
            "--workspace",
            str(tmp_path),
            "--job-id",
            started.job.job_id,
        ]
    )

    result = json.loads(
        (
            Path(job_record.request_path).parent / "result.json"
        ).read_text(encoding="utf-8")
    )["run_result"]
    assert exit_code == 1
    assert result["state"] == "failed"
    assert result["error_code"] == "PREFLIGHT_FAILED"
    assert "changed while the job was queued" in result["error_message"]


def test_large_reusable_artifact_can_be_pinned(tmp_path: Path) -> None:
    workspace = WorkspaceService.create(tmp_path)
    artifact_dir = (
        workspace.results_root
        / "large-suite"
        / "artifacts"
        / "test_set"
        / "v0001"
    )
    artifact_dir.mkdir(parents=True)
    test_set = artifact_dir / "test_set.jsonl"
    stratification = artifact_dir / "stratification.json"
    test_set.write_bytes(b"x" * ((16 * 1024 * 1024) + 1))
    stratification.write_text("{}", encoding="utf-8")
    test_set_hash = hashlib.sha256(test_set.read_bytes()).hexdigest()
    stratification_hash = hashlib.sha256(
        stratification.read_bytes()
    ).hexdigest()
    write_json(
        artifact_dir / "artifact.json",
        {
            "schema_version": 1,
            "artifact_type": "test_set",
            "version": "v0001",
            "files": {
                "test_set": "test_set.jsonl",
                "stratification": "stratification.json",
            },
            "file_hashes": {
                "test_set": test_set_hash,
                "stratification": stratification_hash,
            },
        },
    )

    pin = load_artifact_pin(
        workspace,
        suite_id="large-suite",
        stage_name="test_set",
        version="v0001",
    )

    assert pin.file_hashes["test_set"] == test_set_hash


def test_trace_job_preflight_and_no_credential_execution(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )

    preflight = service.preflight_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        suite_id="trace-suite",
        run_id="trace-run",
    )
    assert preflight.ready is True
    assert preflight.session_count == 1
    assert preflight.estimated_judge_calls == 1
    assert preflight.trace_ref == "fixtures/traces.json"
    assert preflight.taxonomy_ref == "evals/trace_taxonomy.json"
    assert preflight.judge_model == "fixture/judge"
    assert preflight.warnings
    assert not (tmp_path / "artifacts").exists()

    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-request",
        suite_id="trace-suite",
        run_id="trace-run",
    )
    terminal = _wait_terminal(service, started.job.job_id)
    repeated = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-request",
        suite_id="trace-suite",
        run_id="trace-run",
    )
    with pytest.raises(ServiceError) as conflict:
        service.start_trace_judging(
            "trace.yaml",
            "fixtures/traces.json",
            request_id="trace-request",
            group_by="conversation.id",
            suite_id="trace-suite",
            run_id="trace-run",
        )

    assert started.created is True
    assert repeated.created is False
    assert repeated.job.job_id == started.job.job_id
    assert conflict.value.code == ServiceErrorCode.CONFLICT
    assert terminal.kind == "trace_judging"
    assert terminal.state is JobState.COMPLETED
    assert terminal.stages["trace_import"] == "completed"
    assert terminal.stages["judge"] == "completed"

    run_root = (
        tmp_path / "artifacts" / "results" / "trace-suite" / "trace-run"
    )
    inference = json.loads(
        (run_root / "inference_set.jsonl").read_text(encoding="utf-8")
    )
    score = json.loads(
        (run_root / "scores.jsonl").read_text(encoding="utf-8")
    )
    assert inference["type"] == "prompt"
    assert inference["test_case_id"].startswith("trace_000001_")
    assert inference["metadata"]["session_id"] == "session-one"
    assert inference["trace_refs"] == [
        {"trace_id": "a" * 32, "span_ids": ["b" * 16]}
    ]
    assert score["judge_status"] == "scoring_skipped"
    assert score["trace_refs"] == inference["trace_refs"]

    repository = ResultRepository(
        service.workspace.results_root,
        path_policy=service.workspace.path_policy,
    )
    transcript = repository.get_transcript(
        "trace-suite",
        "trace-run",
        inference["test_case_id"],
        kind="prompt",
    )
    assert transcript["inference"]["trace_refs"] == inference["trace_refs"]
    assert transcript["score"]["trace_refs"] == inference["trace_refs"]


def test_trace_worker_runs_model_backed_judge_with_fake_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path, with_events=True),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-scored",
        suite_id="trace-suite",
        run_id="trace-scored",
    )

    async def fake_run_judge_attempts(
        *args: object,
        **kwargs: object,
    ) -> tuple[list[dict[str, object]], list[str], int]:
        return (
            [
                {
                    "dimensions": {
                        "policy_violation": False,
                        "overrefusal": False,
                    },
                    "justification": "The response is safe [1].",
                    "dimension_justifications": {
                        "policy_violation": "No violation [1].",
                        "overrefusal": "The request was answered [1].",
                    },
                    "node_judgments": [],
                    "highlights": (
                        '1. <cite id="1" description="Safe response">'
                        "Here is a safe response.</cite>"
                    ),
                }
            ],
            ['{"judge":"ok"}'],
            0,
        )

    with patch(
        "assert_ai.core.judge._run_judge_attempts",
        new=fake_run_judge_attempts,
    ):
        exit_code = worker_main(
            [
                "--workspace",
                str(tmp_path),
                "--job-id",
                started.job.job_id,
            ]
        )

    run_root = (
        tmp_path
        / "artifacts"
        / "results"
        / "trace-suite"
        / "trace-scored"
    )
    score = json.loads(
        (run_root / "scores.jsonl").read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert score["judge_status"] == "ok"
    assert score["verdict"]["dimensions"] == {
        "overrefusal": False,
        "policy_violation": False,
    }
    assert score["trace_refs"] == [
        {"trace_id": "a" * 32, "span_ids": ["b" * 16]}
    ]


def test_trace_job_snapshots_inputs_and_retries_immutably(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-original",
        suite_id="trace-suite",
        run_id="trace-original",
    )
    claimed = service.store.claim_next(
        lease_owner="fixture-manager",
        lease_seconds=60,
        max_active_jobs=1,
    )
    assert claimed is not None
    assert claimed.job_id == started.job.job_id
    original = service.store.mark_terminal(
        started.job.job_id,
        state=JobState.FAILED,
        exit_code=1,
        failed_stage="trace_import",
        error_code=ServiceErrorCode.RUN_FAILED.value,
        error_message="Fixture failure",
        result={
            "state": "failed",
            "exit_code": 1,
            "failed_stage": "trace_import",
            "error_code": ServiceErrorCode.RUN_FAILED.value,
            "error_message": "Fixture failure",
        },
        run_root=None,
        lease_owner="fixture-manager",
    )
    source_trace = tmp_path / "fixtures" / "traces.json"
    source_trace.write_text('{"changed": true}', encoding="utf-8")
    (tmp_path / "evals" / "trace_taxonomy.json").write_text(
        '{"changed": true}',
        encoding="utf-8",
    )

    retried = service.retry(
        original.job_id,
        request_id="trace-retry",
    )
    replayed = service.retry(
        original.job_id,
        request_id="trace-retry",
    )

    assert retried.created is True
    assert replayed.created is False
    assert retried.job.kind == "trace_judging"
    assert retried.job.retry_of == original.job_id
    retry_record = service.store.get(retried.job.job_id)
    retry_dir = service.manager._job_dir(retry_record.job_id)
    original_dir = service.manager._job_dir(original.job_id)
    assert (retry_dir / "trace.json").read_bytes() == (
        original_dir / "trace.json"
    ).read_bytes()
    assert (retry_dir / "taxonomy.json").read_bytes() == (
        original_dir / "taxonomy.json"
    ).read_bytes()


def test_trace_retry_reapplies_current_model_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-policy-original",
        suite_id="trace-suite",
        run_id="trace-policy-original",
    )
    claimed = service.store.claim_next(
        lease_owner="fixture-manager",
        lease_seconds=60,
        max_active_jobs=1,
    )
    assert claimed is not None
    service.store.mark_terminal(
        claimed.job_id,
        state=JobState.FAILED,
        exit_code=1,
        failed_stage="judge",
        error_code=ServiceErrorCode.RUN_FAILED.value,
        error_message="Fixture failure",
        result={"state": "failed", "exit_code": 1},
        run_root=None,
        lease_owner="fixture-manager",
    )
    service.planning = RunPlanningService(
        service.workspace,
        configs,
        policy=PreflightPolicy(
            allowed_model_patterns=("approved/*",),
        ),
    )

    with pytest.raises(ServiceError) as blocked:
        service.retry(
            started.job.job_id,
            request_id="trace-policy-retry",
        )

    assert blocked.value.code == ServiceErrorCode.PREFLIGHT_FAILED
    assert "current server policy" in str(blocked.value)


def test_trace_retry_reapplies_current_session_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    trace_path = tmp_path / "fixtures" / "traces.json"
    trace_document = json.loads(trace_path.read_text(encoding="utf-8"))
    spans = trace_document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    second = deepcopy(spans[0])
    second["spanId"] = "c" * 16
    second["attributes"][0]["value"]["stringValue"] = "session-two"
    spans.append(second)
    write_json(trace_path, trace_document)
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-size-original",
        suite_id="trace-suite",
        run_id="trace-size-original",
    )
    claimed = service.store.claim_next(
        lease_owner="fixture-manager",
        lease_seconds=60,
        max_active_jobs=1,
    )
    assert claimed is not None
    service.store.mark_terminal(
        claimed.job_id,
        state=JobState.FAILED,
        exit_code=1,
        failed_stage="judge",
        error_code=ServiceErrorCode.RUN_FAILED.value,
        error_message="Fixture failure",
        result={"state": "failed", "exit_code": 1},
        run_root=None,
        lease_owner="fixture-manager",
    )
    service.planning = RunPlanningService(
        service.workspace,
        configs,
        policy=PreflightPolicy(max_prompt_sample_size=1),
    )

    with pytest.raises(ServiceError) as blocked:
        service.retry(
            started.job.job_id,
            request_id="trace-size-retry",
        )

    assert blocked.value.code == ServiceErrorCode.PREFLIGHT_FAILED
    assert "current server limit" in str(blocked.value)


def test_trace_worker_cancels_during_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-cancel",
        suite_id="trace-suite",
        run_id="trace-cancel",
    )
    job_dir = service.manager._job_dir(started.job.job_id)
    cancel_path = job_dir / "cancel.requested"

    from assert_ai.core.otel import parse_otel_trace_document

    def cancel_while_parsing(
        document: dict,
        *,
        group_by: str,
    ) -> list[dict]:
        rows = parse_otel_trace_document(document, group_by=group_by)
        cancel_path.touch()
        return rows

    with patch(
        "assert_ai.services._evaluation_worker.parse_otel_trace_document",
        side_effect=cancel_while_parsing,
    ):
        exit_code = worker_main(
            [
                "--workspace",
                str(tmp_path),
                "--job-id",
                started.job.job_id,
            ]
        )

    result = json.loads(
        (job_dir / "result.json").read_text(encoding="utf-8")
    )["run_result"]
    assert exit_code == 130
    assert result["state"] == "cancelled"
    assert result["failed_stage"] == "trace_import"
    assert (job_dir / "cancel.acknowledged").exists()


def test_trace_worker_rejects_tampered_input_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-tamper",
        suite_id="trace-suite",
        run_id="trace-tamper",
    )
    job_dir = service.manager._job_dir(started.job.job_id)
    (job_dir / "trace.json").write_text('{"tampered": true}', encoding="utf-8")

    exit_code = worker_main(
        [
            "--workspace",
            str(tmp_path),
            "--job-id",
            started.job.job_id,
        ]
    )

    result = json.loads(
        (job_dir / "result.json").read_text(encoding="utf-8")
    )
    assert exit_code == 1
    assert result["worker_error"]["error_code"] == "INTERNAL"
    assert "OTLP trace input digest mismatch" in (
        result["worker_error"]["error_message"]
    )


def test_trace_worker_rejects_changed_parsed_session_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-parser-change",
        suite_id="trace-suite",
        run_id="trace-parser-change",
    )
    job_dir = service.manager._job_dir(started.job.job_id)

    from assert_ai.core.otel import parse_otel_trace_document

    def duplicate_sessions(
        document: dict,
        *,
        group_by: str,
    ) -> list[dict]:
        rows = parse_otel_trace_document(document, group_by=group_by)
        return [*rows, dict(rows[0])]

    with patch(
        "assert_ai.services._evaluation_worker.parse_otel_trace_document",
        side_effect=duplicate_sessions,
    ):
        exit_code = worker_main(
            [
                "--workspace",
                str(tmp_path),
                "--job-id",
                started.job.job_id,
            ]
        )

    result = json.loads(
        (job_dir / "result.json").read_text(encoding="utf-8")
    )
    assert exit_code == 1
    assert result["worker_error"]["error_code"] == "JOB_INTERRUPTED"
    assert "different session count" in result["worker_error"]["error_message"]


def test_trace_worker_rejects_preexisting_run_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-output-race",
        suite_id="trace-suite",
        run_id="trace-output-race",
    )
    run_root = (
        tmp_path
        / "artifacts"
        / "results"
        / "trace-suite"
        / "trace-output-race"
    )
    run_root.mkdir(parents=True)
    sentinel = run_root / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    exit_code = worker_main(
        [
            "--workspace",
            str(tmp_path),
            "--job-id",
            started.job.job_id,
        ]
    )

    assert exit_code == 1
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (run_root / "inference_set.jsonl").exists()


def test_trace_worker_uses_policy_capped_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path, max_concurrency=1)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start_trace_judging(
        "trace.yaml",
        "fixtures/traces.json",
        request_id="trace-concurrency",
        suite_id="trace-suite",
        run_id="trace-concurrency",
    )
    record = service.store.get(started.job.job_id)
    request = json.loads(Path(record.request_path).read_text(encoding="utf-8"))
    captured: dict[str, object] = {}

    def fake_runner(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        run_root = (
            tmp_path
            / "artifacts"
            / "results"
            / "trace-suite"
            / "trace-concurrency"
        )
        return RunResult(
            state=RunState.COMPLETED,
            exit_code=0,
            suite_id="trace-suite",
            run_id="trace-concurrency",
            suite_root=run_root.parent,
            run_root=run_root,
        )

    with patch(
        "assert_ai.runner.run_pipeline_document_result",
        side_effect=fake_runner,
    ):
        exit_code = worker_main(
            [
                "--workspace",
                str(tmp_path),
                "--job-id",
                started.job.job_id,
            ]
        )

    assert request["concurrency"] == 1
    assert exit_code == 0
    assert captured["concurrency"] == 1


def test_trace_preflight_rejects_environment_files(tmp_path: Path) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    (tmp_path / ".env").write_text("{}", encoding="utf-8")

    with pytest.raises(ServiceError) as blocked:
        service.preflight_trace_judging(
            "trace.yaml",
            ".env",
        )

    assert blocked.value.code == ServiceErrorCode.WORKSPACE_VIOLATION


def test_trace_preflight_rejects_malformed_and_oversized_inputs(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    trace_path = tmp_path / "fixtures" / "traces.json"
    trace_path.write_text("{", encoding="utf-8")

    with pytest.raises(ServiceError) as malformed:
        service.preflight_trace_judging(
            "trace.yaml",
            "fixtures/traces.json",
        )

    trace_path.write_text(
        '{"resourceSpans":[null]}',
        encoding="utf-8",
    )
    with pytest.raises(ServiceError) as malformed_shape:
        service.preflight_trace_judging(
            "trace.yaml",
            "fixtures/traces.json",
        )

    _write_trace_fixture(tmp_path)
    parsed_trace = json.loads(trace_path.read_text(encoding="utf-8"))
    parsed_trace["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
        "traceId"
    ] = 42
    write_json(trace_path, parsed_trace)
    with pytest.raises(ServiceError) as malformed_span:
        service.preflight_trace_judging(
            "trace.yaml",
            "fixtures/traces.json",
        )

    trace_path.write_text("{  ", encoding="utf-8")
    _, bounded_service = _service(tmp_path, max_trace_input_bytes=2)
    with pytest.raises(ServiceError) as oversized:
        bounded_service.preflight_trace_judging(
            "trace.yaml",
            "fixtures/traces.json",
        )

    assert malformed.value.code == ServiceErrorCode.INVALID_ARGUMENT
    assert malformed_shape.value.code == ServiceErrorCode.INVALID_ARGUMENT
    assert malformed_span.value.code == ServiceErrorCode.INVALID_ARGUMENT
    assert oversized.value.code == ServiceErrorCode.ARTIFACT_TOO_LARGE


def test_trace_preflight_enforces_the_server_session_limit(
    tmp_path: Path,
) -> None:
    configs, _ = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    trace_path = tmp_path / "fixtures" / "traces.json"
    document = json.loads(trace_path.read_text(encoding="utf-8"))
    second_span = deepcopy(
        document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    )
    second_span["spanId"] = "c" * 16
    second_span["attributes"][0]["value"]["stringValue"] = "session-two"
    document["resourceSpans"][0]["scopeSpans"][0]["spans"].append(second_span)
    write_json(trace_path, document)
    _, bounded_service = _service(tmp_path, max_prompt_sample_size=1)

    with pytest.raises(ServiceError) as blocked:
        bounded_service.preflight_trace_judging(
            "trace.yaml",
            "fixtures/traces.json",
        )

    assert blocked.value.code == ServiceErrorCode.PREFLIGHT_FAILED


def test_suite_only_job_reports_observer_state_without_a_run_manifest(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "suite-only.yaml",
        document={
            "suite": "suite-only",
            "pipeline": {"inference": {"enabled": False}},
        },
    )

    started = service.start(
        "suite-only.yaml",
        request_id="suite-only",
    )
    terminal = _wait_terminal(service, started.job.job_id)

    assert terminal.state is JobState.COMPLETED
    assert terminal.run_id is None
    assert terminal.stages == {"inference": "disabled"}
    assert terminal.heartbeat_at is not None
    assert terminal.resources == {
        "config": "assert://config/suite-only.yaml",
        "worker_log": f"assert://job/{terminal.job_id}/log",
    }


def test_request_id_conflict_does_not_launch_duplicate(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    first = service.start("demo.yaml", request_id="same")

    with pytest.raises(ServiceError) as conflict:
        service.start(
            "demo.yaml",
            request_id="same",
            overrides=EvaluationOverrides(run="different"),
        )

    assert conflict.value.code is ServiceErrorCode.CONFLICT
    assert conflict.value.details == {"job_id": first.job.job_id}
    assert _wait_terminal(service, first.job.job_id).state is JobState.COMPLETED


def test_config_change_during_start_requires_a_retry(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    document = _write_inference_fixture(tmp_path)
    saved = configs.save_config("demo.yaml", document=document)
    original_preflight = service.planning.preflight
    calls = 0

    def racing_preflight(
        _planning: RunPlanningService,
        config_ref: str,
        *,
        overrides: EvaluationOverrides | None = None,
    ):
        nonlocal calls
        calls += 1
        plan = original_preflight(config_ref, overrides=overrides)
        if calls == 1:
            configs.save_config(
                "demo.yaml",
                document={
                    **document,
                    "context": "changed during start",
                },
                expected_etag=saved.etag,
            )
        return plan

    with (
        patch.object(
            RunPlanningService,
            "preflight",
            new=racing_preflight,
        ),
        pytest.raises(ServiceError) as stale,
    ):
        service.start("demo.yaml", request_id="request")

    assert stale.value.code is ServiceErrorCode.STALE_ETAG
    assert not service.store.exists


def test_failed_snapshot_write_removes_unregistered_job_directory(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )

    with (
        patch(
            "assert_ai.services.evaluations._write_request_snapshot",
            side_effect=OSError("disk full"),
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        service.start("demo.yaml", request_id="request")

    jobs_root = tmp_path / "artifacts" / "mcp" / "jobs"
    assert list(jobs_root.iterdir()) == []
    assert not service.store.exists


def test_missing_runtime_input_is_persisted_as_run_failure(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    document = _write_inference_fixture(tmp_path)
    document["pipeline"]["inference"]["test_set_path"] = (
        "missing.jsonl"
    )
    configs.save_config("demo.yaml", document=document)
    started = service.start("demo.yaml", request_id="request")
    terminal = _wait_terminal(service, started.job.job_id)

    assert terminal.state is JobState.FAILED
    assert terminal.error_code == "RUN_FAILED"


def test_worker_rejects_a_tampered_config_snapshot(
    tmp_path: Path,
) -> None:
    job_id = "a" * 32
    job_dir = tmp_path / "artifacts" / "mcp" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "config.yaml").write_text(
        "pipeline: {}\n",
        encoding="utf-8",
    )
    write_json(
        job_dir / "request.json",
        {
            "schema_version": 1,
            "job_id": job_id,
            "result_token": "token",
            "config_ref": "demo.yaml",
            "config_sha256": "sha256:" + ("0" * 64),
            "strict": False,
            "force_stages": [],
            "max_log_bytes": 4096,
        },
    )
    request_sha256 = "sha256:" + hashlib.sha256(
        (job_dir / "request.json").read_bytes()
    ).hexdigest()
    JobStore(
        tmp_path / "artifacts" / "mcp" / "jobs.sqlite3"
    ).create_or_get(
        NewJob(
            job_id=job_id,
            idempotency_key="tampered-config",
            request_hash="sha256:" + ("1" * 64),
            request_sha256=request_sha256,
            suite_id="suite",
            run_id="run",
            config_ref="demo.yaml",
            config_sha256="sha256:" + ("0" * 64),
            snapshot_path=str(job_dir / "config.yaml"),
            request_path=str(job_dir / "request.json"),
            resource_keys=("run:suite/run",),
        ),
        max_queued_jobs=1,
    )

    exit_code = worker_main(
        ["--workspace", str(tmp_path), "--job-id", job_id]
    )

    assert exit_code == 1
    result = json.loads(
        (job_dir / "result.json").read_text(encoding="utf-8")
    )
    assert result["result_token"] == "token"
    assert result["worker_error"]["error_code"] == "INTERNAL"
    assert "digest mismatch" in result["worker_error"]["error_message"]


def test_worker_rejects_coordinated_request_and_config_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    monkeypatch.setattr(EvaluationJobManager, "enqueue", lambda self: None)
    started = service.start("demo.yaml", request_id="bound-request")
    record = service.store.get(started.job.job_id)
    snapshot_path = Path(record.snapshot_path)
    request_path = Path(record.request_path)
    tampered_snapshot = b"pipeline: {}\n"
    snapshot_path.write_bytes(tampered_snapshot)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["config_sha256"] = "sha256:" + hashlib.sha256(
        tampered_snapshot
    ).hexdigest()
    write_json(request_path, request)

    exit_code = worker_main(
        [
            "--workspace",
            str(tmp_path),
            "--job-id",
            started.job.job_id,
        ]
    )

    result = json.loads(
        (
            request_path.parent / "result.json"
        ).read_text(encoding="utf-8")
    )
    assert exit_code == 1
    assert "job request digest mismatch" in (
        result["worker_error"]["error_message"]
    )


def test_malformed_worker_result_becomes_a_persisted_failure(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        started = service.start("demo.yaml", request_id="request")
    record = service.store.claim_next(
        lease_owner=service.manager._owner,
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert record is not None
    record = service.store.mark_running(
        record.job_id,
        lease_owner=service.manager._owner,
        pid=123,
        process_create_time=456,
        lease_seconds=30,
    )
    request = json.loads(Path(record.request_path).read_text(encoding="utf-8"))
    write_json(
        Path(record.request_path).parent / "result.json",
        {
            "schema_version": 1,
            "job_id": record.job_id,
            "result_token": request["result_token"],
            "run_result": {
                "state": "unknown",
                "exit_code": 0,
            },
        },
    )

    detail = service.get(record.job_id)
    listed = service.list().items[0]

    assert detail.state is JobState.FAILED
    assert detail.error_code == "INTERNAL"
    assert "invalid state" in detail.error_message
    assert listed.state is JobState.FAILED


def test_worker_log_retains_a_bounded_tail(tmp_path: Path) -> None:
    log_path = tmp_path / "worker.log"

    with _BoundedTextLog(log_path, max_bytes=4096) as worker_log:
        worker_log.write("first-line\n")
        worker_log.write("x" * 10_000)

    contents = log_path.read_text(encoding="utf-8")
    assert log_path.stat().st_size <= 4096
    assert contents.startswith("[earlier worker output truncated]")


def test_running_job_cancels_cooperatively_and_persists_progress(
    tmp_path: Path,
) -> None:
    configs, service = _service(
        tmp_path,
        cancellation_grace_seconds=3,
    )
    document = _write_inference_fixture(tmp_path)
    fixture = tmp_path / "evals" / "fixture.jsonl"
    fixture.write_text(
        "".join(
            json.dumps(
                {
                    "type": "prompt",
                    "test_case_id": f"case-{index}",
                    "behavior": "local behavior",
                    "seed": {"description": f"hello {index}"},
                }
            )
            + "\n"
            for index in range(8)
        ),
        encoding="utf-8",
    )
    (tmp_path / "agent.py").write_text(
        "import time\n"
        "def run(message, *, history=None):\n"
        "    del history\n"
        "    time.sleep(0.15)\n"
        "    return message\n",
        encoding="utf-8",
    )
    configs.save_config("demo.yaml", document=document)
    started = service.start("demo.yaml", request_id="cancel-cooperative")
    _wait_stage_state(
        service,
        started.job.job_id,
        "inference",
        "running",
    )

    cancelling = service.cancel(started.job.job_id)
    terminal = _wait_terminal(service, started.job.job_id)

    assert cancelling.state is JobState.CANCELLING
    assert cancelling.cancel_requested_at is not None
    assert terminal.state is JobState.CANCELLED
    assert terminal.terminal_result is not None
    assert terminal.terminal_result.exit_code == 130
    assert terminal.terminal_result.failed_stage == "inference"
    assert terminal.stages["inference"] == "cancelled"
    assert terminal.heartbeat_at is not None
    events = service.store.list_events(started.job.job_id, limit=1000)
    event_types = {event["event_type"] for event in events}
    assert {
        "cancel_observed",
        "pipeline_started",
        "stage_planned",
        "stage_started",
        "stage_progress",
        "stage_finished",
        "pipeline_finished",
        "cancel_requested",
        "cancelled",
    }.issubset(event_types)
    assert "termination_escalated" not in event_types
    assert (
        tmp_path
        / "artifacts"
        / "mcp"
        / "jobs"
        / started.job.job_id
        / "result.json"
    ).is_file()


def test_cancelled_stage_projection_prefers_terminal_events() -> None:
    assert _active_stage_from_events(
        (
            {
                "event_type": "stage_started",
                "payload": {"name": "inference"},
            },
            {
                "event_type": "stage_finished",
                "payload": {
                    "name": "inference",
                    "state": "cancelled",
                },
            },
            {
                "event_type": "pipeline_finished",
                "payload": {
                    "state": "cancelled",
                    "failed_stage": "inference",
                },
            },
        )
    ) == "inference"


def test_inspect_manager_does_not_reconcile_or_cancel_active_job(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        started = service.start("demo.yaml", request_id="inspect-only")
    claimed = service.store.claim_next(
        lease_owner="execute-manager",
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert claimed is not None
    running = service.store.mark_running(
        claimed.job_id,
        lease_owner="execute-manager",
        pid=2_147_483_647,
        process_create_time=1,
        lease_seconds=30,
    )
    cancelling = service.store.request_cancel(running.job_id)
    inspect_manager = EvaluationJobManager(
        service.workspace,
        service.store,
        launch_enabled=False,
    )

    observed = inspect_manager.reconcile(cancelling)

    assert observed == cancelling
    assert service.store.get(started.job.job_id).state is JobState.CANCELLING
    assert not (
        tmp_path
        / "artifacts"
        / "mcp"
        / "jobs"
        / started.job.job_id
        / "cancel.requested"
    ).exists()


def test_cancellation_terminates_worker_descendants(
    tmp_path: Path,
) -> None:
    psutil = pytest.importorskip("psutil")
    configs, service = _service(
        tmp_path,
        cancellation_grace_seconds=0.2,
    )
    document = _write_inference_fixture(tmp_path)
    (tmp_path / "agent.py").write_text(
        "import pathlib, subprocess, sys, time\n"
        "def run(message, *, history=None):\n"
        "    del message, history\n"
        "    child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        "    pathlib.Path('child.pid').write_text(str(child.pid), "
        "encoding='utf-8')\n"
        "    time.sleep(60)\n"
        "    return 'late'\n",
        encoding="utf-8",
    )
    configs.save_config("demo.yaml", document=document)
    started = service.start("demo.yaml", request_id="cancel-tree")
    child_pid_path = tmp_path / "child.pid"
    deadline = time.monotonic() + 15
    while not child_pid_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert child_pid_path.is_file()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))

    try:
        service.cancel(started.job.job_id)
        terminal = _wait_terminal(
            service,
            started.job.job_id,
            timeout_s=15,
        )
        assert terminal.state is JobState.CANCELLED
        deadline = time.monotonic() + 5
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(child_pid)
    finally:
        if psutil.pid_exists(child_pid):
            process = psutil.Process(child_pid)
            process.kill()
            process.wait(timeout=5)


def test_startup_recovery_marks_dead_worker_interrupted(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path, lease_seconds=0.05)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        started = service.start("demo.yaml", request_id="recover-dead")
    claimed = service.store.claim_next(
        lease_owner="old-manager",
        lease_seconds=0.05,
        max_active_jobs=1,
    )
    assert claimed is not None
    service.store.mark_running(
        claimed.job_id,
        lease_owner="old-manager",
        pid=2_147_483_647,
        process_create_time=1,
        lease_seconds=0.05,
    )
    time.sleep(0.06)
    recovered = EvaluationJobManager(
        service.workspace,
        service.store,
        lease_seconds=0.1,
    )

    recovered.start()
    service.manager = recovered
    terminal = _wait_terminal(service, started.job.job_id)

    assert terminal.state is JobState.INTERRUPTED
    assert terminal.error_code == ServiceErrorCode.JOB_INTERRUPTED.value


def test_startup_recovery_cleans_up_disabled_job_kinds(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "trace.yaml",
        document=_write_trace_fixture(tmp_path),
    )
    configs.save_config(
        "evaluation.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        trace = service.start_trace_judging(
            "trace.yaml",
            "fixtures/traces.json",
            request_id="disabled-trace",
            suite_id="trace-suite",
            run_id="disabled-trace",
        )
        evaluation = service.start(
            "evaluation.yaml",
            request_id="enabled-evaluation",
        )
    claimed = service.store.claim_next(
        lease_owner="old-trace-manager",
        lease_seconds=0.01,
        max_active_jobs=1,
        job_kinds=("trace_judging",),
    )
    assert claimed is not None
    service.store.mark_running(
        claimed.job_id,
        lease_owner="old-trace-manager",
        pid=2_147_483_647,
        process_create_time=1,
        lease_seconds=0.01,
    )
    time.sleep(0.02)
    execute_only = EvaluationJobManager(
        service.workspace,
        service.store,
        job_kinds=("evaluation",),
        lease_seconds=0.1,
    )

    with patch.object(EvaluationJobManager, "enqueue"):
        execute_only._recover_startup()

    assert service.store.get(trace.job.job_id).state is JobState.INTERRUPTED
    next_job = service.store.claim_next(
        lease_owner="execute-manager",
        lease_seconds=30,
        max_active_jobs=1,
        job_kinds=("evaluation",),
    )
    assert next_job is not None
    assert next_job.job_id == evaluation.job.job_id


def test_startup_recovery_adopts_and_monitors_a_live_worker(
    tmp_path: Path,
) -> None:
    psutil = pytest.importorskip("psutil")
    configs, service = _service(tmp_path, lease_seconds=0.05)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        started = service.start("demo.yaml", request_id="recover-live")
    claimed = service.store.claim_next(
        lease_owner="old-manager",
        lease_seconds=0.05,
        max_active_jobs=1,
    )
    assert claimed is not None
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        ),
        start_new_session=os.name != "nt",
    )
    try:
        running = service.store.mark_running(
            claimed.job_id,
            lease_owner="old-manager",
            pid=process.pid,
            process_create_time=psutil.Process(process.pid).create_time(),
            lease_seconds=0.05,
        )
        time.sleep(0.06)
        recovered = EvaluationJobManager(
            service.workspace,
            service.store,
            lease_seconds=0.15,
        )
        recovered.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = service.store.get(running.job_id)
            if current.lease_owner == recovered._owner:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("live worker lease was not recovered")

        process.terminate()
        process.wait(timeout=5)
        service.manager = recovered
        terminal = _wait_terminal(service, started.job.job_id)

        assert terminal.state is JobState.INTERRUPTED
        assert "manager_recovered" in {
            event["event_type"]
            for event in service.store.list_events(
                started.job.job_id,
                limit=1000,
            )
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_startup_recovery_waits_for_the_observed_peer_lease(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        started = service.start("demo.yaml", request_id="peer-lease")
    claimed = service.store.claim_next(
        lease_owner="peer-manager",
        lease_seconds=10,
        max_active_jobs=1,
    )
    assert claimed is not None
    recovering = EvaluationJobManager(
        service.workspace,
        service.store,
        lease_seconds=1,
    )

    with (
        patch.object(
            service.store,
            "list_nonterminal_records",
            side_effect=[(claimed,), ()],
        ),
        patch.object(EvaluationJobManager, "enqueue"),
        patch(
            "assert_ai.services.evaluations.time.sleep"
        ) as sleep,
    ):
        recovering._recover_startup()

    sleep.assert_called_once()
    assert sleep.call_args.args[0] > 1


def test_scheduler_sweep_releases_a_dead_cancelling_job(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        first = service.start("demo.yaml", request_id="first")
        second = service.start("demo.yaml", request_id="second")
    claimed = service.store.claim_next(
        lease_owner=service.manager._owner,
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert claimed is not None
    running = service.store.mark_running(
        claimed.job_id,
        lease_owner=service.manager._owner,
        pid=2_147_483_647,
        process_create_time=1,
        lease_seconds=30,
    )
    service.store.request_cancel(running.job_id)

    service.manager._sweep_cancelling_jobs()

    assert service.store.get(first.job.job_id).state is JobState.CANCELLED
    next_job = service.store.claim_next(
        lease_owner="next-manager",
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert next_job is not None
    assert next_job.job_id == second.job.job_id


def test_failed_job_retry_is_idempotent_and_uses_immutable_snapshot(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    document = _write_inference_fixture(tmp_path)
    document["pipeline"]["inference"]["test_set_path"] = "missing.jsonl"
    saved = configs.save_config("demo.yaml", document=document)
    started = service.start("demo.yaml", request_id="original")
    failed = _wait_terminal(service, started.job.job_id)
    assert failed.state is JobState.FAILED
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
        expected_etag=saved.etag,
    )

    retried = service.retry(
        failed.job_id,
        request_id="retry-request",
    )
    Path(service.store.get(failed.job_id).snapshot_path).unlink()
    repeated = service.retry(
        failed.job_id,
        request_id="retry-request",
    )
    retry_terminal = _wait_terminal(service, retried.job.job_id)

    assert retried.created is True
    assert repeated.created is False
    assert repeated.job.job_id == retried.job.job_id
    assert retried.job.retry_of == failed.job_id
    assert retried.job.run_id != failed.run_id
    assert retry_terminal.state is JobState.FAILED
    retry_record = service.store.get(retried.job.job_id)
    request = json.loads(
        Path(retry_record.request_path).read_text(encoding="utf-8")
    )
    snapshot = Path(retry_record.snapshot_path).read_text(encoding="utf-8")
    assert request["retry_of"] == failed.job_id
    assert request["force_stages"] == ["inference"]
    assert "missing.jsonl" in snapshot


def test_retry_moves_back_to_corrupt_upstream_jsonl(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    started = service.start("demo.yaml", request_id="corrupt-source")
    completed = _wait_terminal(service, started.job.job_id)
    record = replace(
        service.store.get(completed.job_id),
        state=JobState.FAILED,
        failed_stage="judge",
    )
    assert record.run_id is not None
    run_root = (
        tmp_path
        / "artifacts"
        / "results"
        / record.suite_id
        / record.run_id
    )
    (run_root / "inference_set.jsonl").write_text(
        '{"type":"prompt","test_case_id":"case-1"}\n{"type":',
        encoding="utf-8",
    )
    document = {
        "pipeline": {
            "inference": {"enabled": True},
            "judge": {"enabled": True},
        }
    }

    assert service._retry_stage(record, document) == "inference"


def test_cancelled_worker_result_may_omit_identity_before_setup(
    tmp_path: Path,
) -> None:
    configs, service = _service(tmp_path)
    configs.save_config(
        "demo.yaml",
        document=_write_inference_fixture(tmp_path),
    )
    with patch.object(EvaluationJobManager, "enqueue"):
        started = service.start("demo.yaml", request_id="cancel-before-setup")
    claimed = service.store.claim_next(
        lease_owner=service.manager._owner,
        lease_seconds=30,
        max_active_jobs=1,
    )
    assert claimed is not None
    running = service.store.mark_running(
        claimed.job_id,
        lease_owner=service.manager._owner,
        pid=2_147_483_647,
        process_create_time=1,
        lease_seconds=30,
    )
    request = json.loads(Path(running.request_path).read_text(encoding="utf-8"))
    write_json(
        Path(running.request_path).parent / "result.json",
        {
            "schema_version": 1,
            "job_id": running.job_id,
            "result_token": request["result_token"],
            "run_result": {
                "state": "cancelled",
                "exit_code": 130,
                "failed_stage": "inference",
            },
        },
    )

    terminal = service.get(running.job_id)

    assert terminal.state is JobState.CANCELLED
    assert terminal.terminal_result is not None
    assert terminal.terminal_result.failed_stage == "inference"
