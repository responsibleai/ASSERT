# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from assert_ai.core.io import write_json
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services._evaluation_worker import (
    _BoundedTextLog,
    main as worker_main,
)
from assert_ai.services.configs import ConfigService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.evaluations import (
    EvaluationJobManager,
    EvaluationService,
    _active_stage_from_events,
)
from assert_ai.services.job_models import JobState
from assert_ai.services.job_store import JobStore
from assert_ai.services.run_planning import (
    EvaluationOverrides,
    RunPlanningService,
)


def _service(
    root: Path,
    *,
    cancellation_grace_seconds: float = 10.0,
    lease_seconds: float = 60.0,
) -> tuple[ConfigService, EvaluationService]:
    workspace = WorkspaceService.create(root)
    configs = ConfigService(workspace)
    planning = RunPlanningService(workspace, configs)
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
    snapshot = (
        run_root / "config.yaml"
    ).read_text(encoding="utf-8")
    assert f"run: {terminal.run_id}" in snapshot
    assert service.list().items[0].job_id == started.job.job_id


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
            "assert_ai.services.evaluations.write_json",
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
