# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from artifacts import PreparedDataset, rows_to_jsonl_bytes, sha256_hex
from evaluators import EvaluatorSpec
from foundry import (
    FoundryAdapterError,
    create_evaluation,
    dataset_lineage_metadata,
    dataset_schema,
    dataset_version,
    ensure_dataset,
    ensure_evaluators,
    poll_run,
    prepare_local_asset,
    run_agent_evaluation,
    run_dataset_evaluation,
    run_scenario_simulation,
    run_trace_evaluation,
    turn_criteria,
)
from run_native_target import _parser as native_target_parser
from run_traces import _parser, build_trace_source


def _dataset() -> PreparedDataset:
    rows = ({"assert_test_case_id": "a", "query": "hello"},)
    payload = rows_to_jsonl_bytes(rows)
    return PreparedDataset(rows=rows, payload=payload, content_sha256=sha256_hex(payload))


class NotFound(Exception):
    status_code = 404


class FakeDatasets:
    def __init__(self) -> None:
        self.existing = None
        self.uploads: list[dict] = []

    def get(self, *, name: str, version: str):
        if self.existing is None:
            raise NotFound()
        return self.existing

    def upload_file(self, **kwargs):
        self.uploads.append(kwargs)
        return SimpleNamespace(
            name=kwargs["name"],
            version=kwargs["version"],
            id=f"asset:{kwargs['name']}:{kwargs['version']}",
        )


class FakeEvaluators:
    def __init__(self, existing=None) -> None:
        self.existing = list(existing or [])
        self.creates: list[dict] = []

    def list_versions(self, **kwargs):
        return list(self.existing)

    def create_version(self, *, name, evaluator_version):
        self.creates.append({"name": name, "evaluator_version": evaluator_version})
        return SimpleNamespace(
            name=name,
            version="authoritative-7",
            metadata=evaluator_version["metadata"],
        )


def _project(datasets=None, evaluators=None):
    return SimpleNamespace(
        datasets=datasets or FakeDatasets(),
        beta=SimpleNamespace(evaluators=evaluators or FakeEvaluators()),
    )


def _spec(level: str = "turn") -> EvaluatorSpec:
    return EvaluatorSpec(
        dimension_name="bad_event",
        level=level,
        name=f"assert-pass-{level}-bad-event",
        fingerprint=f"hash-{level}",
        payload={
            "metadata": {"assert_contract_sha256": f"hash-{level}"},
            "definition": {"type": "prompt"},
        },
    )


def test_dataset_reuses_exact_content_version(tmp_path: Path) -> None:
    data = _dataset()
    datasets = FakeDatasets()
    datasets.existing = SimpleNamespace(
        name="demo",
        version=dataset_version(data.content_sha256),
        id="existing-id",
        tags={"assert_content_sha256": data.content_sha256},
    )
    ref = ensure_dataset(
        _project(datasets=datasets),
        name="demo",
        dataset=data,
        local_path=tmp_path / "data.jsonl",
    )
    assert ref.reused is True
    assert not datasets.uploads


def test_dataset_uploads_new_hash_without_delete(tmp_path: Path) -> None:
    data = _dataset()
    datasets = FakeDatasets()
    ref = ensure_dataset(
        _project(datasets=datasets),
        name="demo",
        dataset=data,
        local_path=tmp_path / "data.jsonl",
    )
    assert ref.reused is False
    assert datasets.uploads[0]["version"] == dataset_version(data.content_sha256)
    assert not hasattr(datasets, "delete_calls")


def test_dataset_collision_fails(tmp_path: Path) -> None:
    data = _dataset()
    datasets = FakeDatasets()
    datasets.existing = SimpleNamespace(
        name="other",
        version=dataset_version(data.content_sha256),
        id="id",
    )
    with pytest.raises(FoundryAdapterError, match="collision"):
        ensure_dataset(
            _project(datasets=datasets),
            name="demo",
            dataset=data,
            local_path=tmp_path / "data.jsonl",
        )


def test_local_asset_name_cannot_escape_output_directory(tmp_path: Path) -> None:
    dataset_path, lineage_path = prepare_local_asset(
        _dataset(),
        output_dir=tmp_path,
        dataset_name="../../outside",
        lineage={"route": "test"},
    )
    assert dataset_path.parent == tmp_path
    assert lineage_path.parent == tmp_path
    assert ".." not in dataset_path.name


def test_dataset_version_is_deterministic_and_service_compatible() -> None:
    version = dataset_version("a" * 64)
    assert version == "sha256-" + "a" * 43
    assert len(version) == 50

    with pytest.raises(FoundryAdapterError, match="SHA-256"):
        dataset_version("not-a-hash")


def test_evaluator_reuses_matching_fingerprint() -> None:
    spec = _spec()
    existing = SimpleNamespace(
        name=spec.name,
        version="3",
        metadata={"assert_contract_sha256": spec.fingerprint},
    )
    evaluators = FakeEvaluators([existing])
    versions = ensure_evaluators(_project(evaluators=evaluators), [spec])
    assert versions == [existing]
    assert not evaluators.creates


def test_evaluator_drift_creates_new_and_trusts_returned_version() -> None:
    spec = _spec()
    stale = SimpleNamespace(
        name=spec.name,
        version="2",
        metadata={"assert_contract_sha256": "old"},
    )
    evaluators = FakeEvaluators([stale])
    versions = ensure_evaluators(_project(evaluators=evaluators), [spec])
    assert versions[0].version == "authoritative-7"
    assert len(evaluators.creates) == 1


class FakeRuns:
    def __init__(self, statuses=None) -> None:
        self.statuses = list(statuses or ["completed"])
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="run-1")

    def retrieve(self, *, run_id: str, eval_id: str):
        status = self.statuses.pop(0) if self.statuses else "completed"
        return SimpleNamespace(
            id=run_id,
            status=status,
            report_url="https://portal.example/report" if status == "completed" else None,
            error={"message": "safe failure"} if status == "failed" else None,
        )


class FakeEvals:
    def __init__(self, statuses=None) -> None:
        self.created: list[dict] = []
        self.runs = FakeRuns(statuses)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=f"eval-{len(self.created)}")


def _openai(statuses=None):
    return SimpleNamespace(evals=FakeEvals(statuses))


def test_create_evaluation_is_always_fresh() -> None:
    client = _openai()
    first = create_evaluation(
        client,
        name="same",
        data_source_config=dataset_schema(),
        criteria=[],
        metadata={},
    )
    second = create_evaluation(
        client,
        name="same",
        data_source_config=dataset_schema(),
        criteria=[],
        metadata={},
    )
    assert first.id != second.id
    assert len(client.evals.created) == 2


def test_agent_dataset_trace_and_scenario_mappings() -> None:
    dataset_ref = SimpleNamespace(id="dataset-id")
    client = _openai()
    eval_object = SimpleNamespace(id="eval-1")

    run_agent_evaluation(
        client,
        eval_object=eval_object,
        dataset=dataset_ref,
        agent_name="agent",
        agent_version="1",
        protocol="responses",
        name="agent",
        metadata={},
        timeout_seconds=1,
        poll_seconds=0,
    )
    assert client.evals.runs.created[-1]["data_source"]["target"]["type"] == "azure_ai_agent"

    run_dataset_evaluation(
        client,
        eval_object=eval_object,
        dataset=dataset_ref,
        name="dataset",
        metadata={},
        timeout_seconds=1,
        poll_seconds=0,
    )
    assert client.evals.runs.created[-1]["data_source"]["type"] == "jsonl"

    run_trace_evaluation(
        client,
        eval_object=eval_object,
        level="conversation",
        trace_source={"type": "conversation_id_source", "conversation_ids": ["c"]},
        name="trace",
        metadata={},
        timeout_seconds=1,
        poll_seconds=0,
    )
    assert (
        client.evals.runs.created[-1]["data_source"]["type"]
        == "azure_ai_trace_data_source_preview"
    )
    assert client.evals.runs.created[-1]["extra_body"]["evaluation_level"] == "conversation"

    run_trace_evaluation(
        client,
        eval_object=eval_object,
        level="turn",
        trace_source={
            "type": "turn_trace_ids",
            "trace_ids": ["trace"],
            "lookback_hours": 24,
        },
        name="trace-turn",
        metadata={},
        timeout_seconds=1,
        poll_seconds=0,
    )
    assert client.evals.runs.created[-1]["data_source"]["type"] == "azure_ai_traces"

    run_scenario_simulation(
        client,
        eval_object=eval_object,
        dataset=dataset_ref,
        agent_name="agent",
        agent_version=None,
        simulator_model="judge",
        max_turns=4,
        name="scenario",
        metadata={},
        timeout_seconds=1,
        poll_seconds=0,
    )
    params = client.evals.runs.created[-1]["data_source"]["item_generation_params"]
    assert params["type"] == "conversation_gen_preview"
    assert params["max_turns"] == 4


def test_turn_mapping_uses_sample_for_agent_and_item_for_trace() -> None:
    spec = _spec()
    version = SimpleNamespace(name=spec.name, version="1")
    agent = turn_criteria([version], [spec], model_deployment="judge", target_generated=True)
    trace = turn_criteria([version], [spec], model_deployment="judge", trace=True)
    assert agent[0]["data_mapping"]["response"] == "{{sample.output_text}}"
    assert agent[0]["data_mapping"]["tool_evidence"] == "{{sample.output_items}}"
    assert trace[0]["data_mapping"]["response"] == "{{item.response}}"
    assert trace[0]["data_mapping"]["tool_evidence"] == "{{item.tool_calls}}"


def test_poll_handles_completed_failed_and_timeout() -> None:
    completed = _openai(["running", "completed"])
    times = iter([0.0, 0.1, 0.2])
    result = poll_run(
        completed,
        eval_id="eval",
        run_id="run",
        timeout_seconds=1,
        poll_seconds=0,
        clock=lambda: next(times),
        sleep=lambda _: None,
    )
    assert result.status == "completed"
    assert result.report_url

    failed = _openai(["failed"])
    with pytest.raises(FoundryAdapterError, match="safe failure"):
        poll_run(failed, eval_id="eval", run_id="run", timeout_seconds=1, poll_seconds=0)

    canceled = _openai(["canceled"])
    with pytest.raises(FoundryAdapterError, match="canceled"):
        poll_run(canceled, eval_id="eval", run_id="run", timeout_seconds=1, poll_seconds=0)

    running = _openai(["running", "running"])
    times = iter([0.0, 2.0])
    with pytest.raises(FoundryAdapterError, match="Timed out"):
        poll_run(
            running,
            eval_id="eval",
            run_id="run",
            timeout_seconds=1,
            poll_seconds=0,
            clock=lambda: next(times),
            sleep=lambda _: None,
        )


def test_module_import_does_not_require_optional_sdk() -> None:
    import foundry

    assert callable(foundry.create_clients)


def test_dataset_lineage_metadata_fingerprints_ids_and_dimensions() -> None:
    dataset = _dataset()
    enriched = PreparedDataset(
        rows=(
            {
                **dataset.rows[0],
                "assert_dimensions": {"behavior": "budget", "traveler_type": "family"},
            },
        ),
        payload=dataset.payload,
        content_sha256=dataset.content_sha256,
    )
    result = dataset_lineage_metadata(enriched)
    assert result["assert_row_count"] == "1"
    assert len(result["assert_test_case_ids_sha256"]) == 64
    assert result["assert_dimension_keys"] == "behavior,traveler_type"


def test_trace_source_modes_are_explicit() -> None:
    turn = SimpleNamespace(
        trace_id=["trace-1"],
        conversation_id=[],
        agent_name=None,
        agent_version=None,
        level="turn",
        lookback_hours=12,
        conversation_end_time=None,
        agent_start_time=None,
        agent_end_time=None,
        max_traces=10,
        filter_strategy="random_sampling",
    )
    assert build_trace_source(turn) == {
        "type": "turn_trace_ids",
        "trace_ids": ["trace-1"],
        "lookback_hours": 12,
    }

    conversation = SimpleNamespace(
        trace_id=[],
        conversation_id=["conversation-1"],
        agent_name=None,
        agent_version=None,
        level="conversation",
        lookback_hours=24,
        conversation_end_time="2026-09-18T12:30:00Z",
        agent_start_time=None,
        agent_end_time=None,
        max_traces=10,
        filter_strategy="random_sampling",
    )
    assert build_trace_source(conversation) == {
        "type": "conversation_id_source",
        "conversation_ids": ["conversation-1"],
        "lookback_hours": 24,
        "end_time": "2026-09-18T12:30:00Z",
    }

    agent = SimpleNamespace(
        trace_id=[],
        conversation_id=[],
        agent_name="agent",
        agent_version="2",
        level="conversation",
        lookback_hours=24,
        conversation_end_time=None,
        agent_start_time=1_700_000_000,
        agent_end_time=1_700_003_600,
        max_traces=10,
        filter_strategy="smart_filtering",
    )
    assert build_trace_source(agent) == {
        "type": "agent_filter",
        "agent_name": "agent",
        "agent_version": "2",
        "start_time": 1_700_000_000,
        "end_time": 1_700_003_600,
        "max_traces": 10,
        "filter_strategy": "smart_filtering",
    }


def test_trace_cli_uses_source_specific_time_types() -> None:
    conversation = _parser().parse_args(
        [
            "--level",
            "conversation",
            "--conversation-id",
            "conversation-1",
            "--conversation-end-time",
            "2026-09-18T12:30:00Z",
        ]
    )
    assert conversation.conversation_end_time == "2026-09-18T12:30:00Z"

    agent = _parser().parse_args(
        [
            "--level",
            "conversation",
            "--agent-name",
            "agent",
            "--agent-start-time",
            "1700000000",
            "--agent-end-time",
            "1700003600",
        ]
    )
    assert agent.agent_start_time == 1_700_000_000
    assert agent.agent_end_time == 1_700_003_600

    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "--level",
                "conversation",
                "--conversation-id",
                "conversation-1",
                "--conversation-end-time",
                "1700003600",
            ]
        )


def test_trace_source_rejects_cross_source_time_arguments() -> None:
    args = SimpleNamespace(
        trace_id=[],
        conversation_id=["conversation-1"],
        agent_name=None,
        agent_version=None,
        level="conversation",
        lookback_hours=24,
        conversation_end_time=None,
        agent_start_time=1_700_000_000,
        agent_end_time=None,
        max_traces=10,
        filter_strategy="random_sampling",
    )
    with pytest.raises(FoundryAdapterError, match="require --agent-name"):
        build_trace_source(args)


def test_native_target_accepts_separate_simulator_model() -> None:
    args = native_target_parser().parse_args(
        [
            "--model-deployment",
            "judge-model",
            "--simulator-model",
            "simulator-model",
        ]
    )
    assert args.model_deployment == "judge-model"
    assert args.simulator_model == "simulator-model"
