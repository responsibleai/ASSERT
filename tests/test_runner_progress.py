import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from p2m.core.io import write_json
from p2m.runner import run_pipeline


class RunnerProgressTest(unittest.TestCase):
    def test_run_pipeline_writes_minimal_manifest(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = root / "config.yaml"
            suite_root = root / "results" / "suite-a"
            suite_root.mkdir(parents=True)
            inference_set_path = suite_root / "inference_set.jsonl"
            inference_set_path.write_text('{"type":"prompt","test_case_id":"test-case-1"}\n', encoding="utf-8")
            (suite_root / "taxonomy.json").write_text("{}", encoding="utf-8")
            cfg_path.write_text(
                "\n".join(
                    [
                        "suite: suite-a",
                        "run: run-a",
                        f"results_dir: {root / 'results'}",
                        "behavior:",
                        "  name: harmful_medical_advice",
                        "pipeline:",
                        "  judge:",
                        "    model:",
                        "      name: azure/gpt-5.4",
                        f"    inference_set_path: {inference_set_path}",
                    ]
                ),
                encoding="utf-8",
            )
            async def fake_run_judge(**_: object) -> dict[str, str]:
                run_root = root / "results" / "suite-a" / "run-a"
                run_root.mkdir(parents=True, exist_ok=True)
                scores = run_root / "scores.jsonl"
                scores.write_text("", encoding="utf-8")
                return {
                    "scores_path": str(scores),
                }

            with patch("p2m.stages.judge.run_judge", new=fake_run_judge):
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 0)
            manifest = json.loads((root / "results" / "suite-a" / "run-a" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["stages"]["judge"], "completed")
            self.assertGreaterEqual(
                set(manifest.keys()),
                {"ended_at", "stages", "started_at", "status"},
            )

    def test_run_pipeline_rejects_existing_run_directory(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_root = root / "results" / "suite-a" / "run-a"
            run_root.mkdir(parents=True)
            cfg_path = root / "config.yaml"
            cfg_path.write_text(
                "\n".join(
                    [
                        "suite: suite-a",
                        "run: run-a",
                        f"results_dir: {root / 'results'}",
                        "behavior:",
                        "  name: harmful_medical_advice",
                        "pipeline:",
                        "  judge:",
                        "    model:",
                        "      name: azure/gpt-5.4",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(run_pipeline(config=str(cfg_path)), 1)

    def test_run_pipeline_reports_old_config_keys_as_config_errors(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = root / "config.yaml"
            cfg_path.write_text(
                "\n".join(
                    [
                        "suite_id: suite-a",
                        "run_id: run-a",
                        "pipeline:",
                        "  inference:",
                        "    target:",
                        "      model:",
                        "        name: azure/gpt-5.4",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertLogs("p2m.runner", level="ERROR") as cm:
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 1)
            log_output = "\n".join(cm.output)
            self.assertIn("[config error]", log_output)
            self.assertIn("run_id, suite_id", log_output)

    @unittest.skip("resume parameter removed from run_pipeline in merge")
    def test_run_pipeline_allows_existing_run_directory_with_resume(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = self._make_judge_config(root)
            run_root = root / "results" / "suite-a" / "run-a"
            run_root.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(cfg_path, run_root / "config.yaml")

            fake_judge = self._make_fake_run_judge(root)
            with patch("p2m.stages.judge.run_judge", new=fake_judge):
                rc = run_pipeline(config=str(cfg_path), resume=True)
            self.assertEqual(rc, 0)

    @unittest.skip("resume parameter removed from run_pipeline in merge")
    def test_run_pipeline_rejects_resume_with_changed_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = self._make_judge_config(root)
            run_root = root / "results" / "suite-a" / "run-a"
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "config.yaml").write_text("different: config\n", encoding="utf-8")

            rc = run_pipeline(config=str(cfg_path), resume=True)
            self.assertEqual(rc, 1)

    def _make_judge_config(self, root: Path) -> Path:
        """Return path to a minimal config with one judge stage."""
        cfg_path = root / "config.yaml"
        suite_root = root / "results" / "suite-a"
        suite_root.mkdir(parents=True)
        inference_set_path = suite_root / "inference_set.jsonl"
        inference_set_path.write_text(
            '{"type":"prompt","test_case_id":"test-case-1"}\n', encoding="utf-8"
        )
        (suite_root / "taxonomy.json").write_text("{}", encoding="utf-8")
        cfg_path.write_text(
            "\n".join(
                [
                    "suite: suite-a",
                    "run: run-a",
                    f"results_dir: {root / 'results'}",
                    "behavior:",
                    "  name: harmful_medical_advice",
                    "pipeline:",
                    "  judge:",
                    "    model:",
                    "      name: azure/gpt-5.4",
                    f"    inference_set_path: {inference_set_path}",
                ]
            ),
            encoding="utf-8",
        )
        return cfg_path

    def _make_fake_run_judge(self, root: Path):
        """Return an async fake that creates the expected judge outputs."""
        async def fake_run_judge(**_: object) -> dict[str, str]:
            run_root = root / "results" / "suite-a" / "run-a"
            run_root.mkdir(parents=True, exist_ok=True)
            scores = run_root / "scores.jsonl"
            scores.write_text("", encoding="utf-8")
            return {
                "scores_path": str(scores),
            }
        return fake_run_judge

    # ── new tests ─────────────────────────────────────────────

    def test_run_pipeline_prints_traceback_on_stage_failure(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = self._make_judge_config(root)

            async def boom(**_: object) -> None:
                raise RuntimeError("boom")

            with (
                patch("p2m.stages.judge.run_judge", new=boom),
                self.assertLogs("p2m.runner", level="ERROR") as cm,
            ):
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 1)
            log_output = "\n".join(cm.output)
            self.assertIn("boom", log_output)
            self.assertIn("failed", log_output)

    def test_run_pipeline_prints_timing_output(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = self._make_judge_config(root)

            with (
                patch("p2m.stages.judge.run_judge", new=self._make_fake_run_judge(root)),
                self.assertLogs("p2m.runner", level="INFO") as cm,
            ):
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 0)
            log_output = "\n".join(cm.output)
            self.assertIn("judge", log_output)
            self.assertIn("Pipeline completed", log_output)
            self.assertRegex(log_output, r"\(\d+\.\d+s\)")

    def test_run_pipeline_writes_manifest_mid_run(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = self._make_judge_config(root)
            manifest_path = root / "results" / "suite-a" / "run-a" / "manifest.json"

            captured: dict[str, Any] = {}

            async def spy_run_judge(**_: object) -> dict[str, str]:
                self.assertTrue(manifest_path.exists(), "manifest should exist mid-run")
                mid = json.loads(manifest_path.read_text(encoding="utf-8"))
                captured["mid"] = mid

                run_root = manifest_path.parent
                scores = run_root / "scores.jsonl"
                scores.write_text("", encoding="utf-8")
                return {
                    "scores_path": str(scores),
                }

            with patch("p2m.stages.judge.run_judge", new=spy_run_judge):
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 0)
            self.assertEqual(captured["mid"]["stages"]["judge"], "running")

            final = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(final["stages"]["judge"], "completed")

    def test_run_pipeline_writes_manifest_via_atomic_json_helper(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = self._make_judge_config(root)

            with (
                patch("p2m.stages.judge.run_judge", new=self._make_fake_run_judge(root)),
                patch("p2m.runner.write_json", wraps=write_json) as write_json_spy,
            ):
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 0)
            manifest_writes = [
                call.args[0]
                for call in write_json_spy.call_args_list
                if isinstance(call.args[0], Path) and call.args[0].name == "manifest.json"
            ]
            self.assertGreaterEqual(len(manifest_writes), 2)


if __name__ == "__main__":
    unittest.main()
