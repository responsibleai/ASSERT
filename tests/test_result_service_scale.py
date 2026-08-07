# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.services.results import ResultRepository
from tests.result_catalog_fixture import create_result_catalog_fixture


def test_large_catalog_is_metadata_only_and_single_case_lookup_is_indexed() -> None:
    with TemporaryDirectory() as tmp:
        fixture = create_result_catalog_fixture(Path(tmp))
        repository = ResultRepository(
            fixture.results_root,
            default_page_size=100,
            max_page_size=200,
        )
        original_open = Path.open

        def reject_score_reads(
            path: Path,
            mode: str = "r",
            *args: object,
            **kwargs: object,
        ):
            if path.name == "scores.jsonl" and "r" in mode:
                raise AssertionError("catalog listing opened score rows")
            return original_open(path, mode, *args, **kwargs)

        started = time.perf_counter()
        with (
            patch.object(Path, "open", reject_score_reads),
            patch(
                "assert_ai.services.results.scan_jsonl",
                side_effect=AssertionError("catalog listing scanned JSONL"),
            ),
        ):
            suites = repository.list_suite_catalog_entries(page_size=100)
            run_count = 0
            for suite in suites.items:
                runs = repository.list_run_catalog_entries(
                    str(suite["suite_id"]),
                    page_size=20,
                )
                run_count += len(runs.items)
                assert runs.next_cursor is None
        elapsed = time.perf_counter() - started

        assert len(suites.items) == 100
        assert suites.next_cursor is None
        assert run_count == 1_000
        assert elapsed < 20.0

        with patch(
            "assert_ai.services.results.scan_jsonl",
            side_effect=AssertionError("indexed lookup scanned test_set.jsonl"),
        ):
            test_case = repository.get_test_case(
                fixture.large_suite_id,
                fixture.last_test_case_id,
                kind="prompt",
                run_id=fixture.large_run_id,
            )

        assert test_case["test_case_id"] == fixture.last_test_case_id
