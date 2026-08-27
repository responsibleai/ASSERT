# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from assert_ai.core.io import write_json
from assert_ai.core.jsonl_index import build_jsonl_index
from assert_ai.services.result_metadata import (
    RUN_SUMMARY_SCHEMA_VERSION,
    SUITE_SUMMARY_SCHEMA_VERSION,
    suite_run_catalog_identity,
    suite_run_set_identity,
    write_run_catalog,
)


@dataclass(frozen=True, slots=True)
class ResultCatalogFixture:
    results_root: Path
    large_suite_id: str
    large_run_id: str
    last_test_case_id: str


def create_result_catalog_fixture(
    root: Path,
    *,
    suite_count: int = 100,
    runs_per_suite: int = 10,
    large_test_case_count: int = 10_000,
) -> ResultCatalogFixture:
    results_root = root / "results"
    large_suite_id = "suite-000"
    large_run_id = "run-000"
    last_test_case_id = f"case-{large_test_case_count - 1:05d}"

    for suite_index in range(suite_count):
        suite_id = f"suite-{suite_index:03d}"
        suite_root = results_root / suite_id
        suite_root.mkdir(parents=True)
        run_summaries = []

        for run_index in range(runs_per_suite):
            run_id = f"run-{run_index:03d}"
            run_root = suite_root / run_id
            run_root.mkdir()
            score_row = {
                "type": "prompt",
                "test_case_id": f"{suite_id}-{run_id}",
                "target": "fixture-target",
                "judge_model": "fixture-judge",
                "judge_status": "ok",
                "score_keys": ["policy_violation"],
                "not_applicable_score_keys": [],
                "verdict": {
                    "dimensions": {"policy_violation": False},
                    "node_judgments": [],
                },
            }
            (run_root / "scores.jsonl").write_text(
                json.dumps(score_row) + "\n",
                encoding="utf-8",
            )
            timestamp = (
                f"2026-08-{(suite_index % 28) + 1:02d}"
                f"T00:{run_index:02d}:00+00:00"
            )
            run_summary = {
                "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
                "suite_id": suite_id,
                "run_id": run_id,
                "state": "completed",
                "current_stage": "judge",
                "started_at": timestamp,
                "ended_at": timestamp,
                "updated_at": timestamp,
                "stages": {
                    "inference": "completed",
                    "judge": "completed",
                },
                "stage_timings": {},
                "stage_summaries": {},
                "models": {
                    "target": {
                        "kind": "model",
                        "identifier": "fixture-target",
                    },
                    "tester": None,
                    "judge": "fixture-judge",
                },
                "counts": {
                    "scores": {
                        "total": 1,
                        "prompt": 1,
                        "scenario": 0,
                        "other": 0,
                    }
                },
                "quality": {
                    "prompt": {
                        "total": 1,
                        "scored_total": 1,
                        "judge_failures": 0,
                        "judge_failure_rate": 0.0,
                        "policy_violation_rate": 0.0,
                        "overrefusal_rate": None,
                        "dimensions": {
                            "policy_violation": {
                                "rate": 0.0,
                                "counts": {"0": 1, "1": 0},
                                "count": 1,
                                "applicable_count": 1,
                                "not_applicable_count": 0,
                                "flagged_count": 0,
                                "clear_count": 1,
                            }
                        },
                        "target": "fixture-target",
                        "judge_model": "fixture-judge",
                    },
                    "scenario": None,
                },
                "metrics": {
                    "schema_version": 1,
                    "elapsed_s": 1.0,
                    "totals": {"calls": 1},
                },
                "artifact_versions": {},
                "sources": {},
                "indexes": {},
            }
            write_json(run_root / "run_summary.json", run_summary)
            run_summaries.append(run_summary)

        run_catalog_identity = suite_run_catalog_identity(suite_root)
        if (
            write_run_catalog(
                suite_root,
                run_summaries,
                catalog_identity=run_catalog_identity,
            )
            is None
        ):
            raise RuntimeError("Result catalog fixture changed while being built")

        sources: dict[str, object] = {}
        test_case_counts = {
            "total": 0,
            "prompt": 0,
            "scenario": 0,
            "other": 0,
        }
        if suite_id == large_suite_id:
            test_set_path = suite_root / "test_set.jsonl"
            with test_set_path.open("w", encoding="utf-8") as handle:
                for case_index in range(large_test_case_count):
                    handle.write(
                        json.dumps(
                            {
                                "type": "prompt",
                                "test_case_id": f"case-{case_index:05d}",
                                "dimensions": {
                                    "behavior": "fixture-behavior",
                                    "partition": case_index % 10,
                                },
                                "seed": {
                                    "prompt": f"Fixture prompt {case_index}",
                                },
                            }
                        )
                        + "\n"
                    )
            index = build_jsonl_index(test_set_path)
            sources["test_set"] = {
                "scope": "suite",
                "path": "test_set.jsonl",
                **index["source"],
                "index_schema_version": index["schema_version"],
                "index": {
                    "scope": "suite",
                    "path": "test_set.index.json",
                },
            }
            test_case_counts = {
                "total": large_test_case_count,
                "prompt": large_test_case_count,
                "scenario": 0,
                "other": 0,
            }

        write_json(
            suite_root / "suite_summary.json",
            {
                "schema_version": SUITE_SUMMARY_SCHEMA_VERSION,
                "suite_id": suite_id,
                "status": "has_results",
                "behavior": {
                    "name": "fixture-behavior",
                    "description": "",
                },
                "behavior_category_count": 1,
                "test_case_counts": test_case_counts,
                "created_at": (
                    f"2026-08-{(suite_index % 28) + 1:02d}T00:00:00+00:00"
                ),
                "updated_at": (
                    f"2026-08-{(suite_index % 28) + 1:02d}T01:00:00+00:00"
                ),
                "run_count": runs_per_suite,
                "run_set_identity": suite_run_set_identity(suite_root),
                "run_catalog_identity": run_catalog_identity,
                "latest_run": {
                    "run_id": f"run-{runs_per_suite - 1:03d}",
                    "state": "completed",
                },
                "artifact_versions": {},
                "sources": sources,
            },
        )

    return ResultCatalogFixture(
        results_root=results_root,
        large_suite_id=large_suite_id,
        large_run_id=large_run_id,
        last_test_case_id=last_test_case_id,
    )
