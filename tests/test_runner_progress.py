import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from p2m.core.io import write_json
from p2m.runner import run_pipeline


class RunnerProgressTest(unittest.TestCase):
    def _write_concept_markdown(self, root: Path) -> None:
        (root / "concept.md").write_text("Help with harmful medical advice.", encoding="utf-8")

    def test_run_pipeline_writes_minimal_manifest(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = root / "config.yaml"
            suite_root = root / "results" / "suite-a"
            suite_root.mkdir(parents=True)
            transcripts_path = suite_root / "transcripts.jsonl"
            transcripts_path.write_text('{"kind":"prompt","seed_id":"seed-1"}\n', encoding="utf-8")
            (suite_root / "policy.json").write_text("{}", encoding="utf-8")
            cfg_path.write_text(
                "\n".join(
                    [
                        "suite: suite-a",
                        "run: run-a",
                        f"results_dir: {root / 'results'}",
                        "concept:",
                        "  name: harmful_medical_advice",
                        "pipeline:",
                        "  judge:",
                        "    model:",
                        "      name: azure/gpt-5.4",
                        f"    transcripts_path: {transcripts_path}",
                    ]
                ),
                encoding="utf-8",
            )
            self._write_concept_markdown(root)

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
                        "concept:",
                        "  name: harmful_medical_advice",
                        "pipeline:",
                        "  judge:",
                        "    model:",
                        "      name: azure/gpt-5.4",
                    ]
                ),
                encoding="utf-8",
            )
            self._write_concept_markdown(root)

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
                        "  rollout:",
                        "    target:",
                        "      model:",
                        "        name: azure/gpt-5.4",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("sys.stderr", new_callable=io.StringIO) as fake_err:
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 1)
            self.assertIn("[config error]", fake_err.getvalue())
            self.assertIn("run_id, suite_id", fake_err.getvalue())

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
        transcripts_path = suite_root / "transcripts.jsonl"
        transcripts_path.write_text(
            '{"kind":"prompt","seed_id":"seed-1"}\n', encoding="utf-8"
        )
        (suite_root / "policy.json").write_text("{}", encoding="utf-8")
        cfg_path.write_text(
            "\n".join(
                [
                    "suite: suite-a",
                    "run: run-a",
                    f"results_dir: {root / 'results'}",
                    "concept:",
                    "  name: harmful_medical_advice",
                    "pipeline:",
                    "  judge:",
                    "    model:",
                    "      name: azure/gpt-5.4",
                    f"    transcripts_path: {transcripts_path}",
                ]
            ),
            encoding="utf-8",
        )
        self._write_concept_markdown(root)
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
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 1)
            err = fake_err.getvalue()
            self.assertIn("boom", err)
            self.assertIn("failed", err)

    def test_run_pipeline_prints_timing_output(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cfg_path = self._make_judge_config(root)

            with (
                patch("p2m.stages.judge.run_judge", new=self._make_fake_run_judge(root)),
                patch("sys.stderr", new_callable=io.StringIO) as fake_err,
            ):
                rc = run_pipeline(config=str(cfg_path))

            self.assertEqual(rc, 0)
            err = fake_err.getvalue()
            self.assertIn("judge", err)
            self.assertIn("pipeline completed", err)
            self.assertRegex(err, r"\(\d+\.\d+s\)")

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
