"""Regression tests for ``scripts/benchmark.py``'s outcome-CSV reader.

Background: PR #45 added per-stage token-usage telemetry to
``metrics.json`` and replaced its previous schema. ``scripts/benchmark.py``
was still reading ``scenario_metrics`` / ``policy_violation`` /
``overrefusal`` from that file, so the outcome columns in the benchmark
CSV silently went blank on every run. Jake's review caught it.

Fix (verified by these tests): ``_load_metrics_summary`` now sources all
four outcome columns from the run's ``scores.jsonl`` (via
:func:`assert_eval.results.load_run_summary`). The runner's ``metrics.json``
is no longer consulted for outcome data, and the suite-root
compatibility ``test_set.jsonl`` is not consulted either -- it only
refreshes on full-success runs and would produce stale or blank
readings for partial-test_set runs that exit 0 but skip cache
finalization.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _import_benchmark_module() -> object:
    """Import scripts/benchmark.py as a module without polluting sys.modules.

    The script lives outside the ``assert_eval`` package and depends on
    ``REPO_ROOT`` for path resolution, so we load it via importlib with
    the scripts dir on ``sys.path`` only for the duration of import.
    """
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "scripts_benchmark_under_test", SCRIPTS_DIR / "benchmark.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario_score_row(
    test_case_id: str,
    *,
    policy_violation: bool,
    overrefusal: bool,
) -> dict[str, object]:
    """Build a minimal score row that ``load_run_summary`` will count.

    Requirements for the row to be classified as a *successful scenario*:

    * ``tester_model`` non-empty (so ``load_run_summary`` puts it in
      ``scenario_rows`` rather than ``prompt_rows``).
    * ``judge_status='ok'`` and ``verdict.node_judgments`` is a list, so
      :func:`infer_judge_status` returns ``'ok'``.
    * ``verdict.dimensions.policy_violation`` and
      ``verdict.dimensions.overrefusal`` are booleans, so
      ``compute_dimension_summary`` counts them.
    """
    return {
        "test_case_id": test_case_id,
        "type": "scenario",
        "behavior": "behavior",
        "target": "target-model",
        "judge_model": "judge-model",
        "tester_model": "tester-model",
        "judge_status": "ok",
        "verdict": {
            "node_judgments": [{"node": "behavior-1"}],
            "dimensions": {
                "policy_violation": policy_violation,
                "overrefusal": overrefusal,
            },
        },
    }


class LoadMetricsSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.benchmark = _import_benchmark_module()

    def _write_artifacts(
        self,
        repo_root: Path,
        suite_id: str,
        run_id: str,
        *,
        test_case_rows: list[dict[str, object]],
        score_rows: list[dict[str, object]],
        write_manifest: bool = True,
    ) -> Path:
        """Lay out the on-disk shape ``_load_metrics_summary`` expects."""
        suite_dir = repo_root / "artifacts" / "results" / suite_id
        run_dir = suite_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        test_set_path = suite_dir / "test_set.jsonl"
        test_set_path.write_text(
            "\n".join(json.dumps(row) for row in test_case_rows) + ("\n" if test_case_rows else ""),
            encoding="utf-8",
        )
        scores_path = run_dir / "scores.jsonl"
        scores_path.write_text(
            "\n".join(json.dumps(row) for row in score_rows) + ("\n" if score_rows else ""),
            encoding="utf-8",
        )
        if write_manifest:
            (run_dir / "manifest.json").write_text(
                json.dumps({
                    "status": "completed",
                    "stages": {"judge": "completed"},
                }),
                encoding="utf-8",
            )
        return run_dir

    def test_reads_scenarios_scored_and_dimension_rates_from_scores_jsonl(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            self._write_artifacts(
                repo_root,
                "suite-bench",
                "run-001",
                test_case_rows=[
                    {"type": "prompt", "test_case_id": "p-1"},
                    *(
                        {"type": "scenario", "test_case_id": f"s-{i}"}
                        for i in range(4)
                    ),
                ],
                score_rows=[
                    _scenario_score_row("s-0", policy_violation=True, overrefusal=False),
                    _scenario_score_row("s-1", policy_violation=False, overrefusal=False),
                    _scenario_score_row("s-2", policy_violation=False, overrefusal=True),
                    _scenario_score_row("s-3", policy_violation=False, overrefusal=False),
                ],
            )

            with patch.object(self.benchmark, "REPO_ROOT", repo_root):
                summary = self.benchmark._load_metrics_summary("suite-bench", "run-001")

            # 4 scenario score rows feed scenario_metrics.total, which
            # is what scenario_seeds_generated now sources from.
            self.assertEqual(summary["scenario_seeds_generated"], 4)
            # All 4 scenario score rows are scored ('ok').
            self.assertEqual(summary["scenarios_scored"], 4)
            # 1/4 taxonomy violations, 1/4 overrefusals.
            self.assertAlmostEqual(summary["policy_violation_true_rate"], 0.25)
            self.assertAlmostEqual(summary["overrefusal_true_rate"], 0.25)

    def test_returns_blank_strings_when_run_dir_missing(self) -> None:
        """Pre-pipeline-start invocation must not crash the CSV writer."""
        with TemporaryDirectory() as tmp_dir:
            with patch.object(self.benchmark, "REPO_ROOT", Path(tmp_dir)):
                summary = self.benchmark._load_metrics_summary(
                    "missing-suite", "missing-run"
                )

            self.assertEqual(summary["scenario_seeds_generated"], "")
            self.assertEqual(summary["scenarios_scored"], "")
            self.assertEqual(summary["policy_violation_true_rate"], "")
            self.assertEqual(summary["overrefusal_true_rate"], "")

    def test_does_not_read_metrics_json(self) -> None:
        """Even if a (legacy or token-only) metrics.json is present, the
        outcome columns must come from scores.jsonl, not from metrics.json.

        Regression for Jake's review: the runner now writes metrics.json
        as token-usage telemetry only, and this scanner must not look at
        it for outcome data.
        """
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            run_dir = self._write_artifacts(
                repo_root,
                "suite-bench",
                "run-002",
                test_case_rows=[{"type": "scenario", "test_case_id": "s-0"}],
                score_rows=[
                    _scenario_score_row("s-0", policy_violation=True, overrefusal=False),
                ],
            )
            # Plant a deliberately-misleading metrics.json with the OLD
            # schema. If the scanner read it, the asserts below would
            # pick up these wrong values instead of the score-derived
            # ones.
            (run_dir / "metrics.json").write_text(
                json.dumps({
                    "scenario_metrics": {
                        "count": 999,
                        "dimensions": {
                            "policy_violation": {"true_rate": 0.99},
                            "overrefusal": {"true_rate": 0.88},
                        },
                    },
                    "test_set_metrics": {"scenario_count": 999},
                }),
                encoding="utf-8",
            )

            with patch.object(self.benchmark, "REPO_ROOT", repo_root):
                summary = self.benchmark._load_metrics_summary("suite-bench", "run-002")

            self.assertEqual(summary["scenario_seeds_generated"], 1)
            self.assertEqual(summary["scenarios_scored"], 1)
            self.assertAlmostEqual(summary["policy_violation_true_rate"], 1.0)
            self.assertAlmostEqual(summary["overrefusal_true_rate"], 0.0)

    def test_ignores_stale_suite_root_test_set_jsonl(self) -> None:
        """The suite-root ``test_set.jsonl`` compatibility file refreshes
        only when the test_set stage cleanly finalizes its cacheable
        artifact. For partial-test_set runs that exit 0 but skip
        finalization (the runner gate added in this PR), that file is
        either missing (first-ever run) or stale (prior run's count).

        This test plants a deliberately-misleading suite-root
        ``test_set.jsonl`` with a wrong scenario count and asserts the
        scanner still reports the count from THIS run's
        ``scores.jsonl`` -- not the suite-root file.
        """
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            self._write_artifacts(
                repo_root,
                "suite-bench",
                "run-003",
                # Suite-root test_set.jsonl claims 99 scenarios (the
                # "stale prior run" shape).
                test_case_rows=[
                    {"type": "scenario", "test_case_id": f"stale-{i}"}
                    for i in range(99)
                ],
                # But this run actually only scored 2 scenarios.
                score_rows=[
                    _scenario_score_row("s-0", policy_violation=False, overrefusal=False),
                    _scenario_score_row("s-1", policy_violation=True, overrefusal=False),
                ],
            )

            with patch.object(self.benchmark, "REPO_ROOT", repo_root):
                summary = self.benchmark._load_metrics_summary("suite-bench", "run-003")

            self.assertEqual(summary["scenario_seeds_generated"], 2)
            self.assertEqual(summary["scenarios_scored"], 2)


if __name__ == "__main__":
    unittest.main()