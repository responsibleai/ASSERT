# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from assert_ai.core.config_model import EvaluationConfig, InferenceConfig, JudgeConfig, TargetConfig
from assert_ai.core.model_client import UsageStats, _record_usage
from assert_ai.runner import run_pipeline
from assert_ai.stages.inference import run_inference
from assert_ai.stages import judge as judge_stage


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
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch("assert_ai.runner.STAGES", stages),
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
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch("assert_ai.runner.STAGES", stages),
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
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch("assert_ai.runner.STAGES", stages),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", stage_filter=["report"])

        self.assertEqual(rc, 1)
        self.assertIn("--stage stage(s) not present in config: report", fake_err.getvalue())

    # ─────────────────────────────────────────────────────────────
    # --force-stage cascade tests (added Apr 28 2026 alongside the
    # downstream cascade fix in assert_ai/runner.py).
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

    def _usage_stage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        merge_mode: str,
        cached_count: int = 0,
    ):
        async def run(
            ctx: dict[str, object],
            raw_cfg: dict[str, object],
        ) -> dict[str, object]:
            _record_usage(
                UsageStats(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
                model="test/model",
            )
            return {
                "_usage_merge": merge_mode,
                "_summary": {
                    "count": cached_count + 1,
                    "new_count": 1,
                    "cached_count": cached_count,
                },
            }

        return run

    @staticmethod
    def _manifest() -> SimpleNamespace:
        return SimpleNamespace(
            started_at="",
            status="running",
            ended_at=None,
            stages={},
            stage_timings={},
            to_dict=lambda: {},
        )

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
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch("assert_ai.runner._build_manifest", return_value=stub_manifest),
                patch("assert_ai.runner._write_manifest"),
                patch("assert_ai.runner.STAGES", suite_modules),
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
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch("assert_ai.runner._build_manifest", return_value=stub_manifest),
                patch("assert_ai.runner._write_manifest"),
                patch("assert_ai.runner.STAGES", suite_modules),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config="config.yaml", force_stages=["judge"])

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["judge"])

    def test_forced_upstream_failure_preserves_unstarted_downstream_usage(self) -> None:
        async def fail_before_invalidation(
            ctx: dict[str, object],
            raw_cfg: dict[str, object],
        ) -> None:
            raise ValueError("setup failed")

        seen: list[str] = []
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_root = root / "run"
            run_root.mkdir()
            metrics_path = run_root / "metrics.json"
            original = {
                "stages": {
                    "inference": {"calls": 10, "total_tokens": 10_000},
                    "judge": {"calls": 5, "total_tokens": 2_000},
                },
                "totals": {"calls": 15, "total_tokens": 12_000},
            }
            metrics_path.write_text(json.dumps(original), encoding="utf-8")
            ctx = {
                "stages": [("inference", {}), ("judge", {})],
                "suite_root": str(root / "suite"),
                "run_root": str(run_root),
            }
            stages = {
                "inference": SimpleNamespace(
                    SCOPE="run",
                    SUITE_OUTPUT=None,
                    run=fail_before_invalidation,
                ),
                "judge": SimpleNamespace(
                    SCOPE="run",
                    SUITE_OUTPUT=None,
                    run=self._async_recorder("judge", seen),
                ),
            }
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch("assert_ai.runner.STAGES", stages),
            ):
                rc = run_pipeline(
                    config="config.yaml",
                    force_stages=["inference"],
                )

            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(seen, [])
        self.assertEqual(metrics["stages"]["inference"]["total_tokens"], 10_000)
        self.assertEqual(metrics["stages"]["judge"]["total_tokens"], 2_000)
        self.assertEqual(metrics["totals"]["total_tokens"], 12_000)
        self.assertEqual(metrics["invocation"]["totals"]["total_tokens"], 0)

    def test_forced_judge_setup_failure_preserves_prior_usage(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            suite_root = root / "suite"
            run_root = root / "run"
            suite_root.mkdir()
            run_root.mkdir()
            (run_root / "inference_set.jsonl").write_text(
                json.dumps({"test_case_id": "case-1", "events": []}) + "\n",
                encoding="utf-8",
            )
            (run_root / "scores.jsonl").write_text(
                json.dumps({"test_case_id": "case-1"}) + "\n",
                encoding="utf-8",
            )
            metrics_path = run_root / "metrics.json"
            original = {
                "stages": {
                    "inference": {"calls": 10, "total_tokens": 10_000},
                    "judge": {"calls": 5, "total_tokens": 2_000},
                },
                "totals": {"calls": 15, "total_tokens": 12_000},
            }
            metrics_path.write_text(json.dumps(original), encoding="utf-8")
            ctx = {
                "stages": [("judge", {})],
                "suite_root": str(suite_root),
                "run_root": str(run_root),
                "config_path": root / "config.yaml",
                "artifacts_root": str(root),
                "evaluation": EvaluationConfig(
                    judge=JudgeConfig(model="judge"),
                    inference=InferenceConfig(concurrency=1),
                ),
            }
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch("assert_ai.runner.STAGES", {"judge": judge_stage}),
            ):
                rc = run_pipeline(
                    config="config.yaml",
                    force_stages=["judge"],
                )

            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(metrics["stages"]["inference"]["total_tokens"], 10_000)
        self.assertEqual(metrics["stages"]["judge"]["total_tokens"], 2_000)
        self.assertEqual(metrics["totals"]["total_tokens"], 12_000)
        self.assertEqual(metrics["invocation"]["totals"]["total_tokens"], 0)

    def test_partial_stale_judge_cleanup_removes_deleted_artifact_usage(self) -> None:
        async def forced_inference(
            ctx: dict[str, object],
            raw_cfg: dict[str, object],
        ) -> None:
            await run_inference(
                test_set_path=str(ctx["test_set_path"]),
                save_dir=str(ctx["run_root"]),
                run_id="run",
                target=TargetConfig(model="azure/gpt-5.4"),
                forced=True,
                usage_merge_state=ctx,
            )

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            suite_root = root / "suite"
            run_root = root / "run"
            suite_root.mkdir()
            run_root.mkdir()
            test_set_path = suite_root / "test_set.jsonl"
            test_set_path.write_text(
                json.dumps({"type": "prompt", "seed": {"description": "test"}}) + "\n",
                encoding="utf-8",
            )
            scores_path = run_root / "scores.jsonl"
            scores_path.write_text(
                json.dumps({"test_case_id": "case-1"}) + "\n",
                encoding="utf-8",
            )
            judge_hash_path = run_root / ".judge_config_hash"
            judge_hash_path.write_text("hash", encoding="utf-8")
            metrics_path = run_root / "metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "stages": {
                            "inference": {"calls": 10, "total_tokens": 10_000},
                            "judge": {"calls": 5, "total_tokens": 2_000},
                        },
                        "totals": {"calls": 15, "total_tokens": 12_000},
                    }
                ),
                encoding="utf-8",
            )
            ctx = {
                "stages": [("inference", {})],
                "suite_root": str(suite_root),
                "run_root": str(run_root),
                "test_set_path": str(test_set_path),
            }
            real_unlink = Path.unlink

            def fail_judge_hash_unlink(path: Path, *args: object, **kwargs: object) -> None:
                if path.name == ".judge_config_hash":
                    raise PermissionError("simulated cleanup failure")
                real_unlink(path, *args, **kwargs)

            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch(
                    "assert_ai.runner.STAGES",
                    {
                        "inference": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=forced_inference,
                        )
                    },
                ),
                patch.object(Path, "unlink", autospec=True, side_effect=fail_judge_hash_unlink),
            ):
                rc = run_pipeline(
                    config="config.yaml",
                    force_stages=["inference"],
                )

            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            scores_exist = scores_path.exists()
            judge_hash_exists = judge_hash_path.exists()

        self.assertEqual(rc, 1)
        self.assertFalse(scores_exist)
        self.assertTrue(judge_hash_exists)
        self.assertNotIn("judge", metrics["stages"])
        self.assertEqual(metrics["totals"]["total_tokens"], 10_000)
        self.assertEqual(
            metrics["invocation"]["stage_merge_modes"]["judge"],
            "replace",
        )

    def test_stale_judge_cleanup_preserves_usage_when_scores_delete_fails(self) -> None:
        async def forced_inference(
            ctx: dict[str, object],
            raw_cfg: dict[str, object],
        ) -> None:
            await run_inference(
                test_set_path=str(ctx["test_set_path"]),
                save_dir=str(ctx["run_root"]),
                run_id="run",
                target=TargetConfig(model="azure/gpt-5.4"),
                forced=True,
                usage_merge_state=ctx,
            )

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            suite_root = root / "suite"
            run_root = root / "run"
            suite_root.mkdir()
            run_root.mkdir()
            test_set_path = suite_root / "test_set.jsonl"
            test_set_path.write_text(
                json.dumps({"type": "prompt", "seed": {"description": "test"}}) + "\n",
                encoding="utf-8",
            )
            scores_path = run_root / "scores.jsonl"
            scores_path.write_text(
                json.dumps({"test_case_id": "case-1"}) + "\n",
                encoding="utf-8",
            )
            judge_hash_path = run_root / ".judge_config_hash"
            judge_hash_path.write_text("hash", encoding="utf-8")
            metrics_path = run_root / "metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "stages": {
                            "inference": {"calls": 10, "total_tokens": 10_000},
                            "judge": {"calls": 5, "total_tokens": 2_000},
                        },
                        "totals": {"calls": 15, "total_tokens": 12_000},
                    }
                ),
                encoding="utf-8",
            )
            ctx = {
                "stages": [("inference", {})],
                "suite_root": str(suite_root),
                "run_root": str(run_root),
                "test_set_path": str(test_set_path),
            }
            real_unlink = Path.unlink

            def fail_scores_unlink(path: Path, *args: object, **kwargs: object) -> None:
                if path.name == "scores.jsonl":
                    raise PermissionError("simulated scores cleanup failure")
                real_unlink(path, *args, **kwargs)

            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch(
                    "assert_ai.runner.STAGES",
                    {
                        "inference": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=forced_inference,
                        )
                    },
                ),
                patch.object(Path, "unlink", autospec=True, side_effect=fail_scores_unlink),
            ):
                rc = run_pipeline(
                    config="config.yaml",
                    force_stages=["inference"],
                )

            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            scores_exist = scores_path.exists()
            judge_hash_exists = judge_hash_path.exists()

        self.assertEqual(rc, 1)
        self.assertTrue(scores_exist)
        self.assertTrue(judge_hash_exists)
        self.assertEqual(metrics["stages"]["judge"]["total_tokens"], 2_000)
        self.assertEqual(metrics["totals"]["total_tokens"], 12_000)
        self.assertNotIn(
            "judge",
            metrics["invocation"]["stage_merge_modes"],
        )

    def test_estimator_failure_does_not_block_pipeline(self) -> None:
        seen: list[str] = []
        stages = {
            "taxonomy": SimpleNamespace(
                SCOPE="suite",
                SUITE_OUTPUT=None,
                run=self._async_recorder("taxonomy", seen),
            )
        }
        with TemporaryDirectory() as tmp_dir:
            ctx = {
                "stages": [("taxonomy", {})],
                "suite_root": str(Path(tmp_dir) / "suite"),
                "run_root": None,
            }
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch("assert_ai.runner.STAGES", stages),
                patch(
                    "assert_ai.core.token_estimator.estimate_pipeline_tokens",
                    side_effect=RuntimeError("estimator failed"),
                ),
                self.assertLogs("assert_ai.runner", level="WARNING") as logs,
            ):
                rc = run_pipeline(config="config.yaml")

        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["taxonomy"])
        self.assertIn("Token estimate unavailable", "\n".join(logs.output))

    def test_partial_stage_marks_estimate_accuracy_unavailable(self) -> None:
        async def partial_stage(
            ctx: dict[str, object],
            raw_cfg: dict[str, object],
        ) -> dict[str, object]:
            _record_usage(
                UsageStats(
                    prompt_tokens=80,
                    completion_tokens=20,
                    total_tokens=100,
                ),
                model="test/model",
            )
            return {"_summary": {"errored_count": 1}}

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_root = root / "run"
            ctx = {
                "stages": [("inference", {})],
                "suite_root": str(root / "suite"),
                "run_root": str(run_root),
            }
            manifest = SimpleNamespace(
                started_at="",
                status="running",
                ended_at=None,
                stages={},
                stage_timings={},
                to_dict=lambda: {},
            )
            estimate = SimpleNamespace(
                to_dict=lambda: {"total_tokens": 110}
            )
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch("assert_ai.runner._build_manifest", return_value=manifest),
                patch("assert_ai.runner._write_manifest"),
                patch(
                    "assert_ai.runner.STAGES",
                    {
                        "inference": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=partial_stage,
                        )
                    },
                ),
                patch(
                    "assert_ai.core.token_estimator.estimate_pipeline_tokens",
                    return_value=estimate,
                ),
            ):
                rc = run_pipeline(config="config.yaml")

            metrics = json.loads(
                (run_root / "metrics.json").read_text(encoding="utf-8")
            )

        self.assertEqual(rc, 0)
        self.assertEqual(
            metrics["token_estimate_accuracy"]["reason"],
            "pipeline_partial",
        )

    def test_partial_inference_resume_accumulates_existing_usage(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_root = root / "run"
            run_root.mkdir()
            (run_root / "metrics.json").write_text(
                json.dumps(
                    {
                        "stages": {
                            "inference": {
                                "calls": 2,
                                "input_tokens": 800,
                                "output_tokens": 200,
                            },
                            "judge": {
                                "calls": 2,
                                "input_tokens": 400,
                                "output_tokens": 100,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            ctx = {
                "stages": [("inference", {})],
                "suite_root": str(root / "suite"),
                "run_root": str(run_root),
            }
            estimate = SimpleNamespace(
                to_dict=lambda: {
                    "total_tokens": 110,
                    "stages": {
                        "inference": {
                            "calls": 1,
                            "total_tokens": 110,
                        },
                    },
                }
            )
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch(
                    "assert_ai.runner.STAGES",
                    {
                        "inference": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=self._usage_stage(
                                prompt_tokens=80,
                                completion_tokens=20,
                                merge_mode="accumulate",
                                cached_count=2,
                            ),
                        ),
                    },
                ),
                patch(
                    "assert_ai.core.token_estimator.estimate_pipeline_tokens",
                    return_value=estimate,
                ),
            ):
                self.assertEqual(run_pipeline(config="config.yaml"), 0)

            metrics = json.loads(
                (run_root / "metrics.json").read_text(encoding="utf-8")
            )

        self.assertEqual(metrics["stages"]["inference"]["total_tokens"], 1_100)
        self.assertEqual(metrics["stages"]["judge"]["total_tokens"], 500)
        self.assertEqual(metrics["totals"]["total_tokens"], 1_600)
        self.assertEqual(
            metrics["token_estimate_accuracy"]["actual_total_tokens"],
            100,
        )

    def test_judge_only_pending_resume_preserves_inference_usage(self) -> None:
        async def cached_inference(
            ctx: dict[str, object],
            raw_cfg: dict[str, object],
        ) -> dict[str, object]:
            return {
                "_usage_merge": "accumulate",
                "_summary": {
                    "count": 3,
                    "new_count": 0,
                    "cached_count": 3,
                },
            }

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_root = root / "run"
            run_root.mkdir()
            (run_root / "metrics.json").write_text(
                json.dumps(
                    {
                        "stages": {
                            "inference": {"calls": 3, "total_tokens": 1_500},
                            "judge": {"calls": 2, "total_tokens": 400},
                        },
                    }
                ),
                encoding="utf-8",
            )
            ctx = {
                "stages": [("inference", {}), ("judge", {})],
                "suite_root": str(root / "suite"),
                "run_root": str(run_root),
            }
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch(
                    "assert_ai.runner.STAGES",
                    {
                        "inference": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=cached_inference,
                        ),
                        "judge": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=self._usage_stage(
                                prompt_tokens=160,
                                completion_tokens=40,
                                merge_mode="accumulate",
                                cached_count=2,
                            ),
                        ),
                    },
                ),
            ):
                self.assertEqual(run_pipeline(config="config.yaml"), 0)

            metrics = json.loads(
                (run_root / "metrics.json").read_text(encoding="utf-8")
            )

        self.assertEqual(metrics["stages"]["inference"]["total_tokens"], 1_500)
        self.assertEqual(metrics["stages"]["judge"]["total_tokens"], 600)
        self.assertEqual(metrics["invocation"]["totals"]["total_tokens"], 200)

    def test_force_judge_replaces_previous_stage_usage(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_root = root / "run"
            run_root.mkdir()
            (run_root / "metrics.json").write_text(
                json.dumps(
                    {
                        "stages": {
                            "inference": {"calls": 3, "total_tokens": 1_500},
                            "judge": {"calls": 3, "total_tokens": 900},
                        },
                    }
                ),
                encoding="utf-8",
            )
            ctx = {
                "stages": [("judge", {})],
                "suite_root": str(root / "suite"),
                "run_root": str(run_root),
            }
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch(
                    "assert_ai.runner.STAGES",
                    {
                        "judge": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=self._usage_stage(
                                prompt_tokens=240,
                                completion_tokens=60,
                                merge_mode="accumulate",
                            ),
                        ),
                    },
                ),
            ):
                self.assertEqual(
                    run_pipeline(
                        config="config.yaml",
                        force_stages=["judge"],
                    ),
                    0,
                )

            metrics = json.loads(
                (run_root / "metrics.json").read_text(encoding="utf-8")
            )

        self.assertEqual(metrics["stages"]["inference"]["total_tokens"], 1_500)
        self.assertEqual(metrics["stages"]["judge"]["total_tokens"], 300)
        self.assertEqual(
            metrics["invocation"]["stage_merge_modes"]["judge"],
            "replace",
        )


    def test_no_usage_writes_fresh_invocation_without_erasing_cumulative_metrics(self) -> None:
        note = "Target-internal usage for the callable target is not included."
        for existing in (False, True):
            with self.subTest(existing=existing), TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                run_root = root / "run"
                run_root.mkdir()
                metrics_path = run_root / "metrics.json"
                original = b'{"totals":{"calls":1,"total_tokens":123},"stages":{"judge":{"total_tokens":123}}}\n'
                if existing:
                    metrics_path.write_bytes(original)
                ctx = {
                    "stages": [("inference", {})],
                    "suite_root": str(root / "suite"),
                    "run_root": str(run_root),
                }
                manifest = SimpleNamespace(
                    started_at="", status="running", ended_at=None,
                    stages={}, stage_timings={}, to_dict=lambda: {},
                )
                estimate = {"total_tokens": 0, "calls": 0, "stages": {}, "notes": [note]}
                with (
                    patch("assert_ai.runner._load_context", return_value=ctx),
                    patch("assert_ai.runner._write_suite_metadata"),
                    patch("assert_ai.runner._build_manifest", return_value=manifest),
                    patch("assert_ai.runner._write_manifest"),
                    patch("assert_ai.runner.STAGES", {
                        "inference": SimpleNamespace(
                            SCOPE="run", SUITE_OUTPUT=None,
                            run=self._async_recorder("inference", []),
                        ),
                    }),
                    patch(
                        "assert_ai.core.token_estimator.estimate_pipeline_tokens",
                        return_value=SimpleNamespace(to_dict=lambda: estimate),
                    ),
                ):
                    self.assertEqual(run_pipeline(config="config.yaml"), 0)
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                if existing:
                    self.assertEqual(metrics["totals"]["total_tokens"], 123)
                    self.assertEqual(metrics["stages"]["judge"]["total_tokens"], 123)
                    self.assertEqual(metrics["invocation"]["totals"]["total_tokens"], 0)
                    self.assertEqual(metrics["invocation"]["stages"], {})
                else:
                    self.assertEqual(metrics["token_estimate"], estimate)

    def test_fully_cached_resume_refreshes_invocation_and_scopes_prior_estimate(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_root = root / "run"
            run_root.mkdir()
            metrics_path = run_root / "metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "stages": {
                            "inference": {
                                "requests": 1,
                                "calls": 1,
                                "total_tokens": 1_000,
                            }
                        },
                        "totals": {
                            "requests": 1,
                            "calls": 1,
                            "total_tokens": 1_000,
                        },
                        "invocation": {
                            "totals": {
                                "requests": 1,
                                "calls": 1,
                                "total_tokens": 1_000,
                            }
                        },
                        "token_estimate": {
                            "calls": 1,
                            "total_tokens": 900,
                        },
                        "token_estimate_scope": "current_invocation",
                        "token_estimate_accuracy": {
                            "status": "available",
                            "actual_total_tokens": 1_000,
                            "estimated_total_tokens": 900,
                            "difference_tokens": 100,
                            "difference_ratio": 100 / 900,
                            "absolute_percentage_error": 100 / 900,
                        },
                    }
                ),
                encoding="utf-8",
            )
            ctx = {
                "stages": [("inference", {})],
                "suite_root": str(root / "suite"),
                "run_root": str(run_root),
            }
            with (
                patch("assert_ai.runner._load_context", return_value=ctx),
                patch("assert_ai.runner._write_suite_metadata"),
                patch(
                    "assert_ai.runner._build_manifest",
                    return_value=self._manifest(),
                ),
                patch("assert_ai.runner._write_manifest"),
                patch(
                    "assert_ai.runner.STAGES",
                    {
                        "inference": SimpleNamespace(
                            SCOPE="run",
                            SUITE_OUTPUT=None,
                            run=self._async_recorder("inference", []),
                        )
                    },
                ),
                patch(
                    "assert_ai.core.token_estimator.estimate_pipeline_tokens",
                    side_effect=RuntimeError("simulated estimate failure"),
                ),
            ):
                self.assertEqual(run_pipeline(config="config.yaml"), 0)

            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

        self.assertEqual(metrics["totals"]["total_tokens"], 1_000)
        self.assertEqual(metrics["stages"]["inference"]["total_tokens"], 1_000)
        self.assertEqual(metrics["invocation"]["totals"]["total_tokens"], 0)
        self.assertEqual(metrics["invocation"]["stages"], {})
        self.assertEqual(metrics["token_estimate"]["total_tokens"], 900)
        self.assertEqual(metrics["token_estimate_scope"], "prior_invocation")
        self.assertEqual(
            metrics["token_estimate_accuracy"],
            {
                "status": "unavailable",
                "reason": "estimate_scope_mismatch",
                "scope": "current_invocation",
            },
        )


if __name__ == "__main__":
    unittest.main()
