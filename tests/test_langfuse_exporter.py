# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from assert_ai.integrations.langfuse import (
    LangfuseContractError,
    LangfuseExporter,
    LangfuseHTTPClient,
)
from tests.langfuse_fake_server import fake_langfuse_server


def _write_run(run_dir: Path) -> None:
    inference_rows = [
        {
            "type": "prompt",
            "test_case_id": test_case_id,
            "behavior": "protect account data",
            "events": [],
            "llm_calls": [],
            "stop_reason": "completed",
            "target": "sample.target",
            "tester_model": "",
        }
        for test_case_id in ("case-001", "case-002")
    ]
    score_rows = [
        {
            "type": "prompt",
            "test_case_id": test_case_id,
            "judge_status": "ok",
            "judge_model": "sample-judge",
            "score_keys": ["policy_violation", "overrefusal"],
            "not_applicable_score_keys": [],
            "verdict": {
                "dimensions": {
                    "policy_violation": test_case_id == "case-001",
                    "overrefusal": False,
                },
                "dimension_justifications": {
                    "policy_violation": "Synthetic policy judgment [1].",
                    "overrefusal": "Synthetic refusal judgment [1].",
                },
                "node_judgments": [],
            },
        }
        for test_case_id in ("case-001", "case-002")
    ]
    run_dir.mkdir()
    for name, rows in (
        ("inference_set.jsonl", inference_rows),
        ("scores.jsonl", score_rows),
    ):
        (run_dir / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )


def test_exporter_posts_deterministic_trace_then_scores(tmp_path: Path) -> None:
    run_dir = tmp_path / "sample-run"
    _write_run(run_dir)

    with fake_langfuse_server() as server:
        client = LangfuseHTTPClient(
            base_url=server.base_url,
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
        summary = LangfuseExporter(client).export_run(
            run_dir,
            timestamp_ns=1_700_000_000_000_000_000,
        )

    assert summary.run_id == "sample-run"
    assert summary.traces_exported == 2
    assert summary.scores_exported == 4
    assert summary.not_applicable_scores == 0
    assert [request.path for request in server.requests] == [
        "/api/public/otel/v1/traces",
        "/api/public/scores",
        "/api/public/scores",
        "/api/public/otel/v1/traces",
        "/api/public/scores",
        "/api/public/scores",
    ]
    assert [
        request.body["name"]
        for request in server.requests
        if request.path == "/api/public/scores"
    ] == [
        "policy_violation",
        "overrefusal",
        "policy_violation",
        "overrefusal",
    ]
    first_span = server.requests[0].body["resourceSpans"][0]["scopeSpans"][0][
        "spans"
    ][0]
    second_span = server.requests[3].body["resourceSpans"][0]["scopeSpans"][0][
        "spans"
    ][0]
    assert first_span["startTimeUnixNano"] == "1700000000000000000"
    assert second_span["startTimeUnixNano"] == "1700000000000000002"


def test_exporter_validates_all_local_rows_before_network(tmp_path: Path) -> None:
    run_dir = tmp_path / "sample-run"
    _write_run(run_dir)
    score_path = run_dir / "scores.jsonl"
    rows = [
        json.loads(line)
        for line in score_path.read_text(encoding="utf-8").splitlines()
    ]
    rows.pop()
    score_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    with fake_langfuse_server() as server:
        client = LangfuseHTTPClient(
            base_url=server.base_url,
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
        with pytest.raises(LangfuseContractError, match="no matching score"):
            LangfuseExporter(client).export_run(run_dir)

    assert server.requests == []


def test_exporter_rejects_status_ok_with_malformed_verdict_before_network(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "sample-run"
    _write_run(run_dir)
    score_path = run_dir / "scores.jsonl"
    rows = [
        json.loads(line)
        for line in score_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["verdict"].pop("node_judgments")
    score_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    with fake_langfuse_server() as server:
        client = LangfuseHTTPClient(
            base_url=server.base_url,
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
        with pytest.raises(LangfuseContractError, match="judge contract"):
            LangfuseExporter(client).export_run(run_dir)

    assert server.requests == []


def test_checked_in_synthetic_example_exports_offline() -> None:
    run_dir = Path(__file__).parents[1] / "examples" / "langfuse_bridge" / "sample_run"

    with fake_langfuse_server() as server:
        client = LangfuseHTTPClient(
            base_url=server.base_url,
            public_key="public-placeholder",
            secret_key="secret-placeholder",
        )
        summary = LangfuseExporter(client).export_run(
            run_dir,
            timestamp_ns=1_700_000_000_000_000_000,
        )

    assert summary.traces_exported == 2
    assert summary.scores_exported == 4
