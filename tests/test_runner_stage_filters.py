# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from assert_eval.runner import run_pipeline


class RunnerStageFilterTest(unittest.TestCase):
    def _module(self, name: str, seen: list[str]) -> SimpleNamespace:
        async def run(ctx: dict[str, object], raw_cfg: dict[str, object]) -> None:
            seen.append(name)

        return SimpleNamespace(SCOPE="suite", SUITE_OUTPUT=None, run=run)

    @unittest.skip("from_stage parameter removed from run_pipeline in merge")
    def test_from_stage_uses_configured_order(self) -> None:
        seen: list[str] = []
        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            ctx = {
                "stages": [("prepare", {}), ("judge", {}), ("report", {})],
                "suite_root": str(suite_root),
                "run_root": None,
            }
            stages = {name: self._module(name, seen) for name in ["prepare", "judge", "report"]}

            with (
                patch("assert_eval.runner._load_context", return_value=ctx),
                patch("assert_eval.runner._write_suite_metadata"),
                patch("assert_eval.runner.STAGES", stages),
            ):
                rc = run_pipeline(config="config.yaml", from_stage="judge")

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["judge", "report"])

    @unittest.skip("stage_filter parameter removed from run_pipeline in merge")
    def test_stage_filter_runs_only_selected_stages(self) -> None:
        seen: list[str] = []
        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            ctx = {
                "stages": [("prepare", {}), ("judge", {}), ("report", {})],
                "suite_root": str(suite_root),
                "run_root": None,
            }
            stages = {name: self._module(name, seen) for name in ["prepare", "judge", "report"]}

            with (
                patch("assert_eval.runner._load_context", return_value=ctx),
                patch("assert_eval.runner._write_suite_metadata"),
                patch("assert_eval.runner.STAGES", stages),
            ):
                rc = run_pipeline(config="config.yaml", stage_filter=["report", "prepare"])

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["prepare", "report"])

    @unittest.skip("stage_filter parameter removed from run_pipeline in merge")
    def test_stage_filter_rejects_stages_missing_from_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            ctx = {
                "stages": [("judge", {})],
                "suite_root": str(suite_root),
                "run_root": None,
            }
            stages = {"judge": self._module("judge", [])}

            with (
                patch("assert_eval.runner._load_context", return_value=ctx),
                patch("assert_eval.runner._write_suite_metadata"),
                patch("assert_eval.runner.STAGES", stages),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", stage_filter=["report"])

        self.assertEqual(rc, 1)
        self.assertIn("--stage stage(s) not present in config: report", fake_err.getvalue())

    # ─────────────────────────────────────────────────────────────
    # --force-stage cascade tests (added Apr 28 2026 alongside the
    # downstream cascade fix in assert_eval/runner.py).
    #
    # Without cascade, `--force-stage test_set` regenerates test_set.jsonl
    # but inference silently keeps the prior transcripts (its resume
    # cache keys on test_case_id, and test case ids are deterministic enough
    # to collide). Same hazard for judge against scores.jsonl. The
    # cascade extends the explicit forced set to every stage at or
    # downstream of the lowest forced index in PIPELINE_STAGE_ORDER.
    # ─────────────────────────────────────────────────────────────

    def _async_recorder(self, name: str, seen: list[str]):
        async def run(ctx: dict[str, object], raw_cfg: dict[str, object]) -> None:
            seen.append(name)
        return run

    def test_force_stage_cascade_only_includes_downstream(self) -> None:
        seen: list[str] = []
        suite_modules = {
            "taxonomy": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="taxonomy.json", run=self._async_recorder("taxonomy", seen)),
            "stratification": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="stratification.json", run=self._async_recorder("stratification", seen)),
            "test_set": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="test_set.jsonl", run=self._async_recorder("test_set", seen)),
            "inference": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=self._async_recorder("inference", seen)),
            "judge": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=self._async_recorder("judge", seen)),
        }

        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            suite_root.mkdir(parents=True)
            (suite_root / "taxonomy.json").write_text("{}", encoding="utf-8")
            (suite_root / "stratification.json").write_text("{}", encoding="utf-8")
            (suite_root / "test_set.jsonl").write_text("", encoding="utf-8")
            ctx = {
                "stages": [
                    ("taxonomy", {}),
                    ("stratification", {}),
                    ("test_set", {}),
                    ("inference", {}),
                    ("judge", {}),
                ],
                "suite_root": str(suite_root),
                "run_root": str(Path(tmp_dir) / "run"),
            }
            stub_manifest = SimpleNamespace(
                started_at="",
                status="running",
                ended_at=None,
                stages={},
                stage_timings={},
                to_dict=lambda: {},
            )

            with (
                patch("assert_eval.runner._load_context", return_value=ctx),
                patch("assert_eval.runner._write_suite_metadata"),
                patch("assert_eval.runner._build_manifest", return_value=stub_manifest),
                patch("assert_eval.runner._write_manifest"),
                patch("assert_eval.runner.STAGES", suite_modules),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", force_stages=["test_set"])

        self.assertEqual(rc, 0)
        # taxonomy + stratification are upstream of test_set in PIPELINE_STAGE_ORDER
        # and have cached outputs, so they stay skipped. test_set is the
        # explicit force; inference + judge get cascaded in.
        self.assertEqual(seen, ["test_set", "inference", "judge"])

    def test_force_stage_no_cascade_when_only_terminal_stage_forced(self) -> None:
        seen: list[str] = []
        suite_modules = {
            "taxonomy": SimpleNamespace(SCOPE="suite", SUITE_OUTPUT="taxonomy.json", run=self._async_recorder("taxonomy", seen)),
            "judge": SimpleNamespace(SCOPE="run", SUITE_OUTPUT=None, run=self._async_recorder("judge", seen)),
        }

        with TemporaryDirectory() as tmp_dir:
            suite_root = Path(tmp_dir) / "suite"
            suite_root.mkdir(parents=True)
            (suite_root / "taxonomy.json").write_text("{}", encoding="utf-8")
            ctx = {
                "stages": [("taxonomy", {}), ("judge", {})],
                "suite_root": str(suite_root),
                "run_root": str(Path(tmp_dir) / "run"),
            }
            stub_manifest = SimpleNamespace(
                started_at="",
                status="running",
                ended_at=None,
                stages={},
                stage_timings={},
                to_dict=lambda: {},
            )

            with (
                patch("assert_eval.runner._load_context", return_value=ctx),
                patch("assert_eval.runner._write_suite_metadata"),
                patch("assert_eval.runner._build_manifest", return_value=stub_manifest),
                patch("assert_eval.runner._write_manifest"),
                patch("assert_eval.runner.STAGES", suite_modules),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", force_stages=["judge"])

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["judge"])


if __name__ == "__main__":
    unittest.main()
