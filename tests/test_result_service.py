# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from assert_ai.cli import cli
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.results import ResultRepository, RunReference


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _score(
    test_case_id: str,
    *,
    kind: str = "prompt",
    violation: bool = False,
    severity: int = 1,
    judge_status: str = "ok",
) -> dict:
    verdict = (
        {
            "dimensions": {
                "policy_violation": violation,
                "severity": severity,
            },
            "node_judgments": [],
        }
        if judge_status == "ok"
        else {}
    )
    return {
        "type": kind,
        "test_case_id": test_case_id,
        "behavior": "unsafe-action",
        "target": "target-a",
        "tester_model": "tester-a" if kind == "scenario" else "",
        "judge_model": "judge-a",
        "judge_status": judge_status,
        "score_keys": ["policy_violation", "severity"],
        "not_applicable_score_keys": [],
        "dimension_scales": {
            "severity": {
                "type": "ordinal",
                "values": [
                    {"value": 1, "label": "low"},
                    {"value": 2, "label": "high"},
                ],
            }
        },
        "verdict": verdict,
    }


def _build_legacy_fixture(root: Path, *, second_run: bool = False) -> Path:
    results_root = root / "results"
    suite_root = results_root / "suite-a"
    taxonomy = suite_root / "artifacts" / "systematize" / "v0001" / "taxonomy.json"
    test_set = suite_root / "artifacts" / "test_set" / "v0002" / "test_set.jsonl"
    _write_json(
        suite_root / "suite.json",
        {"created_at": "2026-08-12T00:00:00+00:00"},
    )
    _write_json(
        taxonomy,
        {
            "behavior": {"name": "safe-agent"},
            "behavior_categories": [
                {"name": "unsafe-action", "permissible": False},
            ],
        },
    )
    _write_jsonl(
        test_set,
        [
            {
                "type": "prompt",
                "test_case_id": "p1",
                "dimensions": {"behavior": "unsafe-action", "region": "us"},
                "seed": {"prompt": "one"},
            },
            {
                "type": "prompt",
                "test_case_id": "p2",
                "dimensions": {"behavior": "unsafe-action", "region": "eu"},
                "seed": {"prompt": "two"},
            },
            {
                "type": "scenario",
                "test_case_id": "s1",
                "dimensions": {"behavior": "unsafe-action", "region": "us"},
                "seed": {"prompt": "three"},
            },
        ],
    )
    _write_json(
        suite_root / "latest.json",
        {
            "artifacts": {
                "systematize": {
                    "path": "artifacts/systematize/v0001/taxonomy.json",
                    "version": "v0001",
                },
                "test_set": {
                    "path": "artifacts/test_set/v0002/test_set.jsonl",
                    "version": "v0002",
                },
            }
        },
    )
    _write_jsonl(
        suite_root / "test_set.jsonl",
        [{"type": "prompt", "test_case_id": "stale"}],
    )

    run_ids = ["run-a", "run-b"] if second_run else ["run-a"]
    for index, run_id in enumerate(run_ids):
        run_root = suite_root / run_id
        _write_json(
            run_root / "manifest.json",
            {
                "status": "completed",
                "started_at": f"2026-08-12T00:0{index + 1}:00+00:00",
                "ended_at": f"2026-08-12T00:0{index + 2}:00+00:00",
                "stages": {
                    "inference": "completed",
                    "judge": "completed",
                },
            },
        )
        _write_json(
            run_root / "artifacts.json",
            {
                "schema_version": 1,
                "artifacts": {
                    "systematize": {
                        "path": "artifacts/systematize/v0001/taxonomy.json",
                        "version": "v0001",
                    },
                    "test_set": {
                        "path": "artifacts/test_set/v0002/test_set.jsonl",
                        "version": "v0002",
                    },
                },
            },
        )
        _write_jsonl(
            run_root / "inference_set.jsonl",
            [
                {
                    "type": "prompt",
                    "test_case_id": "p1",
                    "stop_reason": "completed",
                    "events": [
                        {"edit": {"type": "add_message"}},
                        {"edit": {"type": "tool_call"}},
                    ],
                },
                {
                    "type": "prompt",
                    "test_case_id": "p2",
                    "stop_reason": "target_error",
                    "events": [],
                },
                {
                    "type": "scenario",
                    "test_case_id": "s1",
                    "stop_reason": "completed",
                    "events": [{"edit": {"type": "add_message"}}],
                },
            ],
        )
        _write_jsonl(
            run_root / "scores.jsonl",
            [
                _score(
                    "p1",
                    violation=index == 0,
                    severity=1 if index == 0 else 2,
                ),
                _score("p2", judge_status="judge_failed"),
                _score("s1", kind="scenario", severity=2),
            ],
        )
    return results_root


def test_catalogs_rebuild_legacy_once_and_then_remain_metadata_only() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        results_root = _build_legacy_fixture(root, second_run=True)
        repository = ResultRepository(results_root, default_page_size=1)

        first = repository.list_suite_catalog_entries()
        assert first.items[0]["suite_id"] == "suite-a"
        assert first.items[0]["prompt_test_case_count"] == 2
        assert (results_root / "suite-a" / "suite_summary.json").exists()
        assert (
            results_root / "suite-a" / "run-a" / "run_summary.json"
        ).exists()

        with patch(
            "assert_ai.services.results.scan_jsonl",
            side_effect=AssertionError("catalog listing must not scan JSONL"),
        ):
            suites = repository.list_suite_catalog_entries()
            runs = repository.list_run_catalog_entries("suite-a")

        assert len(suites.items) == 1
        assert len(runs.items) == 1
        assert runs.next_cursor is not None
        second_page = repository.list_run_catalog_entries(
            "suite-a",
            cursor=runs.next_cursor,
        )
        assert len(second_page.items) == 1


def test_test_case_pagination_filters_and_stale_cursor() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp))
        repository = ResultRepository(results_root, default_page_size=1)
        repository.get_suite("suite-a")

        first = repository.list_test_cases(
            "suite-a",
            kind="prompt",
            factors={"region": "us"},
        )
        assert [item["test_case_id"] for item in first.items] == ["p1"]
        assert first.next_cursor is None

        page = repository.list_test_cases("suite-a")
        assert page.next_cursor is not None
        source = (
            results_root
            / "suite-a"
            / "artifacts"
            / "test_set"
            / "v0002"
            / "test_set.jsonl"
        )
        with source.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"type": "prompt", "test_case_id": "p3"}) + "\n"
            )

        with pytest.raises(ServiceError) as stale:
            repository.list_test_cases(
                "suite-a",
                cursor=page.next_cursor,
            )
        assert stale.value.code == ServiceErrorCode.STALE_CURSOR


def test_score_queries_failures_and_transcript_use_indexes() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp))
        repository = ResultRepository(results_root)
        repository.get_suite("suite-a")

        filtered = repository.list_scores(
            "suite-a",
            "run-a",
            stop_reason="completed",
            has_tool_use=True,
        )
        assert [row["test_case_id"] for row in filtered.items] == ["p1"]

        failures = repository.list_failures("suite-a", "run-a")
        assert {row["test_case_id"] for row in failures.items} == {"p1", "p2"}

        with patch(
            "assert_ai.services.results.scan_jsonl",
            side_effect=AssertionError("single transcript lookup must use indexes"),
        ):
            transcript = repository.get_transcript(
                "suite-a",
                "run-a",
                "p1",
                kind="prompt",
            )
        assert transcript["test_case"]["seed"]["prompt"] == "one"
        assert transcript["inference"]["events"][1]["edit"]["type"] == "tool_call"
        assert transcript["score"]["verdict"]["dimensions"]["policy_violation"] is True


def test_compare_runs_reports_binary_ordinal_structural_and_sample_warnings() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp), second_run=True)
        repository = ResultRepository(results_root)
        repository.get_suite("suite-a")

        comparison = repository.compare_runs(
            [
                RunReference("suite-a", "run-a"),
                RunReference("suite-a", "run-b"),
            ]
        )

        policy = comparison["dimension_deltas"]["policy_violation"]
        assert policy["kind"] == "binary"
        assert policy["runs"][1]["prompt_rate_delta"] == -1.0
        severity = comparison["dimension_deltas"]["severity"]
        assert severity["kind"] == "ordinal"
        assert (
            severity["runs"][1]["prompt_distribution_delta"]["2"]
            == 1.0
        )
        assert comparison["runs"][0]["structural"]["tool_events"] == 1
        assert comparison["behavior_category_deltas"][0]["delta"] == -1.0


def test_repository_rejects_path_traversal_and_invalid_page_sizes() -> None:
    with TemporaryDirectory() as tmp:
        repository = ResultRepository(Path(tmp) / "results")

        with pytest.raises(ServiceError) as traversal:
            repository.get_suite("../escape")
        assert traversal.value.code == ServiceErrorCode.INVALID_ARGUMENT

        with pytest.raises(ServiceError) as page_size:
            repository.list_suite_catalog_entries(page_size=1000)
        assert page_size.value.code == ServiceErrorCode.INVALID_ARGUMENT


def test_changed_score_source_rebuilds_stale_run_summary() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp))
        repository = ResultRepository(results_root)
        original = repository.load_run_detail("suite-a", "run-a")
        assert (
            original["quality"]["prompt"]["dimensions"]["policy_violation"]["rate"]
            == 1.0
        )

        scores_path = results_root / "suite-a" / "run-a" / "scores.jsonl"
        _write_jsonl(
            scores_path,
            [
                _score("p1", violation=False),
                _score("p2", violation=False),
                _score("s1", kind="scenario", severity=2),
            ],
        )

        rebuilt = repository.load_run_detail("suite-a", "run-a")
        assert (
            rebuilt["quality"]["prompt"]["dimensions"]["policy_violation"]["rate"]
            == 0.0
        )


def test_partial_trailing_run_row_does_not_break_catalog_or_complete_row_queries() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp))
        repository = ResultRepository(results_root)
        repository.get_suite("suite-a")
        scores_path = results_root / "suite-a" / "run-a" / "scores.jsonl"
        with scores_path.open("ab") as handle:
            handle.write(b'{"type":"prompt","test_case_id":"partial"')

        runs = repository.list_run_catalog_entries("suite-a")
        scores = repository.list_scores("suite-a", "run-a")
        detail = repository.load_run_detail(
            "suite-a",
            "run-a",
            include_rows=True,
        )

        assert len(runs.items) == 1
        assert {row["test_case_id"] for row in scores.items} == {
            "p1",
            "p2",
            "s1",
        }
        assert {
            row["test_case_id"]
            for row in detail["prompt_rows"] + detail["scenario_rows"]
        } == {"p1", "p2", "s1"}


def test_suite_summary_detects_out_of_band_run_addition() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp))
        repository = ResultRepository(results_root)
        assert repository.get_suite("suite-a")["run_count"] == 1

        suite_root = results_root / "suite-a"
        shutil.copytree(suite_root / "run-a", suite_root / "run-b")
        (suite_root / "run-b" / "run_summary.json").unlink()

        assert repository.get_suite("suite-a")["run_count"] == 2


def test_oversized_page_row_returns_bounded_stub_and_remains_pageable() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp))
        repository = ResultRepository(
            results_root,
            default_page_size=1,
            max_page_bytes=250,
            max_item_bytes=10_000,
        )
        repository.get_suite("suite-a")

        first = repository.list_scores("suite-a", "run-a")

        assert first.items[0]["content_omitted"] is True
        assert first.items[0]["test_case_id"] == "p1"
        assert first.next_cursor is not None
        second = repository.list_scores(
            "suite-a",
            "run-a",
            cursor=first.next_cursor,
        )
        assert second.items[0]["test_case_id"] == "p2"


def test_compare_negative_behavior_limit_preserves_unlimited_compatibility() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp), second_run=True)
        repository = ResultRepository(results_root)
        repository.get_suite("suite-a")

        comparison = repository.compare_runs(
            [
                RunReference("suite-a", "run-a"),
                RunReference("suite-a", "run-b"),
            ],
            behavior_limit=-1,
        )

        assert len(comparison["behavior_category_deltas"]) == 1


def test_corrupt_derived_summaries_are_rebuilt_from_canonical_artifacts() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp))
        suite_root = results_root / "suite-a"
        run_root = suite_root / "run-a"
        (suite_root / "suite_summary.json").write_text("{", encoding="utf-8")
        (run_root / "run_summary.json").write_text("{", encoding="utf-8")

        repository = ResultRepository(results_root)
        suite = repository.get_suite("suite-a")
        run = repository.load_run_detail("suite-a", "run-a")

        assert suite["run_count"] == 1
        assert run["state"] == "completed"


def test_cli_results_list_uses_metadata_and_compare_supports_ordinal() -> None:
    with TemporaryDirectory() as tmp:
        results_root = _build_legacy_fixture(Path(tmp), second_run=True)
        repository = ResultRepository(results_root)
        repository.get_suite("suite-a")
        runner = CliRunner()

        with patch(
            "assert_ai.services.results.scan_jsonl",
            side_effect=AssertionError("CLI list must not scan JSONL"),
        ):
            listed = runner.invoke(
                cli,
                [
                    "results",
                    "list",
                    "--results-dir",
                    str(results_root),
                    "--json",
                ],
            )
        assert listed.exit_code == 0, listed.output
        list_payload = json.loads(listed.output)
        assert list_payload["suites"][0]["suite_id"] == "suite-a"
        assert list_payload["suites"][0]["runs"] == []

        compared = runner.invoke(
            cli,
            [
                "results",
                "compare",
                "suite-a",
                "run-a",
                "run-b",
                "--results-dir",
                str(results_root),
                "--metric",
                "severity",
                "--json",
            ],
        )
        assert compared.exit_code == 0, compared.output
        comparison = json.loads(compared.output)
        assert comparison["dimension_deltas"]["severity"]["kind"] == "ordinal"
