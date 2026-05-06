import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from p2m.viewer_read_model import ViewerReadModelBuildError, build_run_viewer_artifacts


ROOT = Path(__file__).resolve().parents[1]
DATA_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "data.ts"
METRICS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "metrics.ts"
DIMENSIONS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "dimensions.ts"
ARTIFACTS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "artifacts.ts"
CONFIG_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "config.ts"
JUDGMENT_SRC = ROOT / "viewer" / "src" / "lib" / "judgment.ts"
RESULT_VIEW_SRC = ROOT / "viewer" / "src" / "lib" / "result-view.ts"
TYPES_SRC = ROOT / "viewer" / "src" / "lib" / "types.ts"


class ViewerServerArtifactsTest(unittest.TestCase):
    def _copy_data_harness(self, harness_dir: Path) -> Path:
        data_path = harness_dir / "data.ts"
        metrics_path = harness_dir / "metrics.ts"
        dimensions_path = harness_dir / "dimensions.ts"
        artifacts_path = harness_dir / "artifacts.ts"
        config_path = harness_dir / "config.ts"
        judgment_path = harness_dir / "judgment.ts"
        result_view_path = harness_dir / "result-view.ts"
        types_path = harness_dir / "types.ts"

        data_source = (
            DATA_SRC.read_text(encoding="utf-8")
            .replace("./config.js", "./config.ts")
            .replace("./dimensions.js", "./dimensions.ts")
            .replace("./artifacts.js", "./artifacts.ts")
            .replace("./metrics.js", "./metrics.ts")
            .replace("$lib/judgment.js", "./judgment.ts")
            .replace("$lib/result-view.js", "./result-view.ts")
        )
        metrics_source = (
            METRICS_SRC.read_text(encoding="utf-8")
            .replace("$lib/judgment.js", "./judgment.ts")
            .replace("./dimensions.js", "./dimensions.ts")
            .replace("$lib/types.js", "./types.ts")
        )
        dimensions_source = (
            DIMENSIONS_SRC.read_text(encoding="utf-8")
            .replace("./config.js", "./config.ts")
            .replace("./artifacts.js", "./artifacts.ts")
        )
        artifacts_source = (
            ARTIFACTS_SRC.read_text(encoding="utf-8")
            .replace("./config.js", "./config.ts")
            .replace("$lib/types.js", "./types.ts")
        )
        judgment_source = JUDGMENT_SRC.read_text(encoding="utf-8").replace(
            "./types.js", "./types.ts"
        )
        result_view_source = RESULT_VIEW_SRC.read_text(encoding="utf-8").replace(
            "$lib/types.js", "./types.ts"
        )

        data_path.write_text(data_source, encoding="utf-8")
        metrics_path.write_text(metrics_source, encoding="utf-8")
        dimensions_path.write_text(dimensions_source, encoding="utf-8")
        artifacts_path.write_text(artifacts_source, encoding="utf-8")
        shutil.copyfile(CONFIG_SRC, config_path)
        judgment_path.write_text(judgment_source, encoding="utf-8")
        result_view_path.write_text(result_view_source, encoding="utf-8")
        shutil.copyfile(TYPES_SRC, types_path)
        return data_path

    def _run_node(
        self, *, harness_dir: Path, script: str, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=harness_dir,
            env=env,
            check=False,
        )

    def _build_viewer_read_model(self, run_dir: Path) -> None:
        build_run_viewer_artifacts(run_dir)

    def test_build_viewer_read_model_writes_cache_under_hidden_subdir(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            suite_dir = tmp_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps({"status": "completed", "stages": {"inference": "completed", "judge": "completed"}}),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "target": "target-model",
                        "events": [],
                        "llm_calls": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "judge_model": "judge-model",
                        "target": "target-model",
                        "judge_status": "ok",
                        "verdict": {
                            "dimensions": {"policy_violation": False},
                            "justification": "clear",
                            "node_judgments": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = build_run_viewer_artifacts(run_dir)
            prompt_rows = json.loads(
                (run_dir / ".viewer" / "viewer_prompt_rows.json").read_text(
                    encoding="utf-8"
                )
            )
            audit_rows = json.loads(
                (run_dir / ".viewer" / "viewer_audit_rows.json").read_text(
                    encoding="utf-8"
                )
            )

            normalized_files = [f.replace("\\", "/") for f in result["built_files"]]
            self.assertIn(".viewer/viewer_run_manifest.json", normalized_files)
            self.assertTrue((run_dir / ".viewer" / "viewer_run_manifest.json").exists())
            self.assertTrue((run_dir / ".viewer" / "viewer_prompt_rows.json").exists())
            self.assertTrue((run_dir / ".viewer" / "viewer_score_index.json").exists())
            self.assertFalse((run_dir / "viewer_run_manifest.json").exists())
            self.assertFalse((run_dir / "viewer_prompt_rows.json").exists())
            self.assertFalse((run_dir / "viewer_score_index.json").exists())
            self.assertNotIn("permissible", prompt_rows[0])
            self.assertEqual(audit_rows, [])

    def test_build_viewer_read_model_rejects_duplicate_transcript_keys(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            suite_dir = tmp_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps({"status": "completed", "stages": {"inference": "completed", "judge": "completed"}}),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            transcript_row = {
                "kind": "prompt",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "target": "target-model",
                "events": [],
                "llm_calls": [],
            }
            score_row = {
                "kind": "prompt",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "judge_model": "judge-model",
                "target": "target-model",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False},
                    "justification": "clear",
                    "node_judgments": [],
                },
            }
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(transcript_row) + "\n" + json.dumps(transcript_row) + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(json.dumps(score_row) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ViewerReadModelBuildError, "Duplicate prompt:seed-1 row"):
                build_run_viewer_artifacts(run_dir)

    def test_build_viewer_read_model_rejects_duplicate_score_keys(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            suite_dir = tmp_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps({"status": "completed", "stages": {"inference": "completed", "judge": "completed"}}),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            transcript_row = {
                "kind": "prompt",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "target": "target-model",
                "events": [],
                "llm_calls": [],
            }
            score_row = {
                "kind": "prompt",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "judge_model": "judge-model",
                "target": "target-model",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False},
                    "justification": "clear",
                    "node_judgments": [],
                },
            }
            (run_dir / "transcripts.jsonl").write_text(json.dumps(transcript_row) + "\n", encoding="utf-8")
            (run_dir / "scores.jsonl").write_text(
                json.dumps(score_row) + "\n" + json.dumps(score_row) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ViewerReadModelBuildError, "Duplicate prompt:seed-1 row"):
                build_run_viewer_artifacts(run_dir)

    def test_load_judged_samples_surfaces_invalid_scores_jsonl(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "prompt"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text("{bad jsonl\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                try {{
                  const {{ loadJudgedSamples }} = await import({json.dumps(data_path.as_uri())});
                  loadJudgedSamples('suite-a', 'run-a');
                  console.log(JSON.stringify({{ ok: true }}));
                }} catch (error) {{
                  console.log(JSON.stringify({{
                    ok: false,
                    name: error.name,
                    message: error.message
                  }}));
                }}
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["name"], "ArtifactParseError")
            self.assertIn("scores.jsonl", payload["message"])

    def test_load_dimensions_returns_built_in_dimensions(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            self._copy_data_harness(harness_dir)

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(tmp_root / "artifacts" / "results"),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                """\
                try {
                  const { loadDimensions } = await import('./dimensions.ts');
                  const dims = loadDimensions();
                  console.log(JSON.stringify({ ok: true, keys: Object.keys(dims) }));
                } catch (error) {
                  console.log(JSON.stringify({
                    ok: false,
                    name: error.name,
                    message: error.message
                  }));
                }
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertIn("policy_violation", payload["keys"])
            self.assertIn("overrefusal", payload["keys"])

    def test_load_run_page_data_reads_live_transcripts_during_inference(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "taxonomy.json").write_text(
                json.dumps(
                    {
                        "failure_modes": [
                            {"name": "failure_mode", "definition": "def", "permissible": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "scenario",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {
                            "title": "Scenario title",
                            "description": "Scenario description",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps({"status": "running", "stages": {"inference": "running"}}),
                encoding="utf-8",
            )
            valid_row = {
                "kind": "scenario",
                "seed_id": "seed-1",
                "spec": "spec",
                "factors": {"failure_mode": "failure_mode"},
                "stop_reason": "max_turns",
                "target": "target-model",
                "tester_model": "tester-model",
                "events": [
                    {
                        "view": ["target"],
                        "actor": "tester",
                        "edit": {
                            "type": "set_system_message",
                            "message": {"role": "system", "content": "System prompt"},
                        },
                    },
                    {
                        "view": ["target"],
                        "actor": "tester",
                        "edit": {
                            "type": "add_message",
                            "message": {"role": "user", "content": "Need advice"},
                        },
                    },
                    {
                        "view": ["target"],
                        "actor": "target",
                        "edit": {
                            "type": "add_message",
                            "message": {"role": "assistant", "content": "Response"},
                        },
                    },
                ],
                "llm_calls": [],
            }
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(valid_row) + "\n" + '{"kind":"scenario"',
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                const payload = loadRunPageData('suite-a', 'run-a');
                console.log(JSON.stringify({{
                  previewCount: payload.inferencePreviewRows.length,
                  previewSeed: payload.inferencePreviewRows[0]?.seed_id ?? null,
                  previewTurns: payload.inferencePreviewRows[0]?.turns_count ?? null,
                  previewDrawerTitle: payload.scenarioSeedMap?.['seed-1']?.title ?? null,
                  previewDrawerMessages: payload.scenarioDrawerItems?.['seed-1']?.messages.length ?? 0,
                  previewTotal: payload.inferencePreviewTotal,
                  auditScores: payload.auditScores.length
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["previewCount"], 1)
            self.assertEqual(payload["previewSeed"], "seed-1")
            self.assertEqual(payload["previewTurns"], 2)
            self.assertEqual(payload["previewDrawerTitle"], "Scenario title")
            self.assertEqual(payload["previewDrawerMessages"], 0)
            self.assertEqual(payload["previewTotal"], 1)
            self.assertEqual(payload["auditScores"], 0)

    def test_load_run_page_data_rejects_malformed_interior_live_transcript_line(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "taxonomy.json").write_text(
                json.dumps(
                    {
                        "failure_modes": [
                            {"name": "failure_mode", "definition": "def", "permissible": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "scenario",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"title": "Scenario title", "description": "Scenario description"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps({"status": "running", "stages": {"inference": "running"}}),
                encoding="utf-8",
            )
            valid_row = {
                "kind": "scenario",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "stop_reason": "max_turns",
                "events": [],
                "llm_calls": [],
            }
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(valid_row) + "\n{bad jsonl\n" + json.dumps(valid_row) + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                try {{
                  const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                  loadRunPageData('suite-a', 'run-a', 'audit');
                  console.log(JSON.stringify({{ ok: true }}));
                }} catch (error) {{
                  console.log(JSON.stringify({{
                    ok: false,
                    name: error.name,
                    message: error.message
                  }}));
                }}
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["name"], "ArtifactParseError")
            self.assertIn("transcripts.jsonl", payload["message"])
            self.assertIn("line 2", payload["message"])

    def test_load_run_page_data_rejects_truncated_trailing_line_after_inference(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "scenario",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"title": "Scenario title", "description": "Scenario description"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {"status": "running", "stages": {"inference": "completed", "judge": "running"}}
                ),
                encoding="utf-8",
            )
            valid_row = {
                "kind": "scenario",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "stop_reason": "max_turns",
                "events": [],
                "llm_calls": [],
            }
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(valid_row) + "\n" + '{"kind":"scenario"',
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                try {{
                  const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                  loadRunPageData('suite-a', 'run-a', 'audit');
                  console.log(JSON.stringify({{ ok: true }}));
                }} catch (error) {{
                  console.log(JSON.stringify({{
                    ok: false,
                    name: error.name,
                    message: error.message
                  }}));
                }}
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["name"], "ArtifactParseError")
            self.assertIn("transcripts.jsonl", payload["message"])

    def test_load_run_page_data_skips_preview_once_scores_exist(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "taxonomy.json").write_text(
                json.dumps(
                    {
                        "failure_modes": [
                            {"name": "failure_mode", "definition": "def", "permissible": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "scenario",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"title": "Scenario title", "description": "Scenario description"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {"status": "completed", "stages": {"inference": "completed", "judge": "completed"}}
                ),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            valid_row = {
                "kind": "scenario",
                "seed_id": "seed-1",
                "spec": "spec",
                "factors": {"failure_mode": "failure_mode"},
                "stop_reason": "max_turns",
                "events": [],
                "llm_calls": [],
            }
            score_row = {
                "kind": "scenario",
                "seed_id": "seed-1",
                "spec": "spec",
                "factors": {"failure_mode": "failure_mode"},
                "judge_model": "judge-model",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False, "overrefusal": False},
                    "justification": "clear",
                    "node_judgments": [],
                },
            }
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(valid_row) + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(score_row) + "\n",
                encoding="utf-8",
            )
            self._build_viewer_read_model(run_dir)

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                const payload = loadRunPageData('suite-a', 'run-a');
                console.log(JSON.stringify({{
                  previewCount: payload.inferencePreviewRows.length,
                  auditScoreCount: payload.auditScores.length,
                  turnsCount: payload.auditScores[0]?.metadata?.turns_count ?? null
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["previewCount"], 0)
            self.assertEqual(payload["auditScoreCount"], 1)
            self.assertEqual(payload["turnsCount"], 0)

    def test_completed_run_page_data_preserves_pipeline_manifest_fields(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "started_at": "2026-04-02T00:00:00Z",
                        "ended_at": "2026-04-02T00:10:00Z",
                        "stages": {"inference": "completed", "judge": "completed"},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "target": "target-model",
                        "events": [
                            {
                                "view": ["target"],
                                "actor": "target",
                                "edit": {"type": "add_message", "message": {"role": "user", "content": "Need advice"}},
                            }
                        ],
                        "llm_calls": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "judge_model": "judge-model",
                        "target": "target-model",
                        "judge_status": "ok",
                        "verdict": {
                            "dimensions": {"policy_violation": False, "overrefusal": True},
                            "justification": "clear",
                            "node_judgments": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._build_viewer_read_model(run_dir)

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                const payload = loadRunPageData('suite-a', 'run-a');
                console.log(JSON.stringify({{
                  status: payload.manifest?.status ?? null,
                  startedAt: payload.manifest?.started_at ?? null,
                  endedAt: payload.manifest?.ended_at ?? null,
                  inferenceStage: payload.manifest?.stages?.inference ?? null,
                  judgeStage: payload.manifest?.stages?.judge ?? null
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["startedAt"], "2026-04-02T00:00:00Z")
            self.assertEqual(payload["endedAt"], "2026-04-02T00:10:00Z")
            self.assertEqual(payload["inferenceStage"], "completed")
            self.assertEqual(payload["judgeStage"], "completed")

    def test_load_run_page_data_falls_back_to_raw_files_when_viewer_read_model_is_stale(self) -> None:
        """When transcripts change after the read model was built, data.ts falls back to raw files."""
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {"status": "completed", "stages": {"inference": "completed", "judge": "completed"}}
                ),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            transcript_row = {
                "kind": "prompt",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "target": "target-model",
                "events": [
                    {
                        "view": ["target"],
                        "actor": "target",
                        "edit": {"type": "add_message", "message": {"role": "user", "content": "Need advice"}},
                    },
                    {
                        "view": ["target"],
                        "actor": "target",
                        "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Refuse"}},
                    },
                ],
                "llm_calls": [],
            }
            score_row = {
                "kind": "prompt",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "judge_model": "judge-model",
                "target": "target-model",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False, "overrefusal": True},
                    "justification": "clear",
                    "node_judgments": [],
                },
            }
            (run_dir / "transcripts.jsonl").write_text(json.dumps(transcript_row) + "\n", encoding="utf-8")
            (run_dir / "scores.jsonl").write_text(json.dumps(score_row) + "\n", encoding="utf-8")
            self._build_viewer_read_model(run_dir)
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(transcript_row) + "\n" + json.dumps(transcript_row) + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                try {{
                  const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                  const result = loadRunPageData('suite-a', 'run-a');
                  console.log(JSON.stringify({{ ok: result != null }}));
                }} catch (error) {{
                  console.log(JSON.stringify({{
                    ok: false,
                    name: error.name,
                    message: error.message
                  }}));
                }}
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"], "stale read model should fall back to raw files")

    def test_prompt_and_scenario_drawers_read_indexed_detail_for_completed_runs(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "taxonomy.json").write_text(
                json.dumps(
                    {
                        "failure_modes": [
                            {
                                "name": "prompt-failure_mode",
                                "definition": "prompt definition",
                                "permissible": False,
                            },
                            {
                                "name": "scenario-failure_mode",
                                "definition": "scenario definition",
                                "permissible": False,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "kind": "prompt",
                                "seed_id": "seed-prompt",
                                "spec": "spec",
                                "factors": {"failure_mode": "prompt-failure_mode"},
                                "seed": {"description": "Prompt seed"},
                            }
                        ),
                        json.dumps(
                            {
                                "kind": "scenario",
                                "seed_id": "seed-scenario",
                                "spec": "spec",
                                "factors": {"failure_mode": "scenario-failure_mode"},
                                "seed": {
                                    "title": "Scenario title",
                                    "description": "Scenario description",
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {"status": "completed", "stages": {"inference": "completed", "judge": "completed"}}
                ),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            prompt_transcript = {
                "kind": "prompt",
                "seed_id": "seed-prompt",
                "spec": "spec",
                "factors": {"failure_mode": "prompt-failure_mode"},
                "target": "target-model",
                "events": [
                    {
                        "view": ["target"],
                        "actor": "tester",
                        "edit": {
                            "type": "set_system_message",
                            "message": {"role": "system", "content": "System prompt"},
                        },
                    },
                    {
                        "view": ["target"],
                        "actor": "target",
                        "edit": {"type": "add_message", "message": {"role": "user", "content": "Need advice now"}},
                    },
                    {
                        "view": ["target"],
                        "actor": "target",
                        "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Prompt answer"}},
                    },
                ],
                "llm_calls": [
                    {
                        "call_id": "llm:0",
                        "source": "target",
                        "api_mode": "chat_completion",
                        "request": {"model": "target-model"},
                        "response": {"id": "resp_prompt"},
                        "message_ids": ["event:2"],
                    }
                ],
            }
            scenario_transcript = {
                "kind": "scenario",
                "seed_id": "seed-scenario",
                "spec": "spec",
                "factors": {"failure_mode": "scenario-failure_mode"},
                "target": "target-model",
                "tester_model": "tester-model",
                "stop_reason": "max_turns",
                "events": [
                    {
                        "view": ["target"],
                        "actor": "tester",
                        "edit": {
                            "type": "set_system_message",
                            "message": {"role": "system", "content": "Scenario system"},
                        },
                    },
                    {
                        "view": ["target"],
                        "actor": "tester",
                        "edit": {"type": "add_message", "message": {"role": "user", "content": "Start"}},
                    },
                    {
                        "view": ["target"],
                        "actor": "target",
                        "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Reply"}},
                    },
                ],
                "llm_calls": [],
            }
            prompt_score = {
                "kind": "prompt",
                "seed_id": "seed-prompt",
                "spec": "spec",
                "factors": {"failure_mode": "prompt-failure_mode"},
                "judge_model": "judge-model",
                "target": "target-model",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False, "overrefusal": True},
                    "justification": "clear",
                    "node_judgments": [],
                },
            }
            scenario_score = {
                "kind": "scenario",
                "seed_id": "seed-scenario",
                "spec": "spec",
                "factors": {"failure_mode": "scenario-failure_mode"},
                "judge_model": "judge-model",
                "target": "target-model",
                "tester_model": "tester-model",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": True, "overrefusal": False},
                    "justification": "bad",
                    "node_judgments": [],
                },
            }
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(prompt_transcript) + "\n" + json.dumps(scenario_transcript) + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(prompt_score) + "\n" + json.dumps(scenario_score) + "\n",
                encoding="utf-8",
            )
            self._build_viewer_read_model(run_dir)

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ loadRunPageData, loadPromptDrawerItem, loadScenarioDrawerItem }} = await import({json.dumps(data_path.as_uri())});
                const page = loadRunPageData('suite-a', 'run-a');
                const promptItem = await loadPromptDrawerItem('suite-a', 'run-a', 'seed-prompt');
                const scenarioItem = await loadScenarioDrawerItem('suite-a', 'run-a', 'seed-scenario');
                console.log(JSON.stringify({{
                  promptCount: page.samples.length,
                  promptSummary: page.samples[0]?.prompt ?? null,
                  promptDrawerTitle: promptItem?.row_title ?? null,
                  promptDrawerMessages: promptItem?.messages.length ?? 0,
                  scenarioDrawerTitle: scenarioItem?.row_title ?? null,
                  scenarioDrawerTurns: scenarioItem?.context.turns_count ?? null,
                  scenarioDrawerStopReason: scenarioItem?.context.stop_reason ?? null,
                  scenarioDrawerMessages: scenarioItem?.messages.length ?? 0
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["promptCount"], 1)
            self.assertEqual(payload["promptSummary"], "Need advice now")
            self.assertEqual(payload["promptDrawerTitle"], "Need advice now")
            self.assertEqual(payload["promptDrawerMessages"], 3)
            self.assertEqual(payload["scenarioDrawerTitle"], "Scenario title")
            self.assertEqual(payload["scenarioDrawerTurns"], 2)
            self.assertEqual(payload["scenarioDrawerStopReason"], "max_turns")
            self.assertEqual(payload["scenarioDrawerMessages"], 3)

    def test_prompt_drawer_reads_canonical_detail_before_judge_completion(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed", "system_prompt": "System prompt"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {"status": "running", "stages": {"inference": "completed", "judge": "running"}}
                ),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "target": "target-model",
                        "events": [
                            {
                                "view": ["target"],
                                "actor": "tester",
                                "edit": {
                                    "type": "set_system_message",
                                    "message": {"role": "system", "content": "System prompt"},
                                },
                            },
                            {
                                "view": ["target"],
                                "actor": "target",
                                "edit": {"type": "add_message", "message": {"role": "user", "content": "Need advice"}},
                            },
                            {
                                "view": ["target"],
                                "actor": "target",
                                "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Refuse"}},
                            },
                        ],
                        "llm_calls": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "judge_model": "judge-model",
                        "target": "target-model",
                        "judge_status": "ok",
                        "verdict": {
                            "dimensions": {"policy_violation": False, "overrefusal": True},
                            "justification": "clear",
                            "node_judgments": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ loadRunPageData, loadPromptDrawerItem }} = await import({json.dumps(data_path.as_uri())});
                const page = loadRunPageData('suite-a', 'run-a');
                const promptItem = await loadPromptDrawerItem('suite-a', 'run-a', 'seed-1');
                console.log(JSON.stringify({{
                  promptCount: page.samples.length,
                  drawerTitle: promptItem?.row_title ?? null,
                  drawerMessages: promptItem?.messages.length ?? 0
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["promptCount"], 1)
            self.assertEqual(payload["drawerTitle"], "Need advice")
            self.assertEqual(payload["drawerMessages"], 3)

    def test_prompt_drawer_rejects_unrelated_malformed_transcript_line_before_judge_completion(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed"},
                    }
                        ),
                        json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-2",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"description": "Prompt seed 2"},
                    }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {"status": "running", "stages": {"inference": "completed", "judge": "running"}}
                ),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            prompt_transcript = {
                "kind": "prompt",
                "seed_id": "seed-1",
                "spec": "spec",
                "failure_mode": "failure_mode",
                "permissible": False,
                "target": "target-model",
                "events": [
                    {
                        "view": ["target"],
                        "actor": "target",
                        "edit": {"type": "add_message", "message": {"role": "user", "content": "Need advice"}},
                    }
                ],
                "llm_calls": [],
            }
            (run_dir / "transcripts.jsonl").write_text(
                json.dumps(prompt_transcript) + "\n" + "{bad jsonl\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "prompt",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "judge_model": "judge-model",
                        "target": "target-model",
                        "judge_status": "ok",
                        "verdict": {
                            "dimensions": {"policy_violation": False, "overrefusal": True},
                            "justification": "clear",
                            "node_judgments": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                try {{
                  const {{ loadPromptDrawerItem }} = await import({json.dumps(data_path.as_uri())});
                  await loadPromptDrawerItem('suite-a', 'run-a', 'seed-1');
                  console.log(JSON.stringify({{ ok: true }}));
                }} catch (error) {{
                  console.log(JSON.stringify({{
                    ok: false,
                    name: error.name,
                    message: error.message
                  }}));
                }}
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["name"], "ArtifactParseError")
            self.assertIn("transcripts.jsonl", payload["message"])

    def test_list_suites_marks_scenario_only_scored_suite_as_has_results(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}),
                encoding="utf-8",
            )
            (suite_dir / "taxonomy.json").write_text(
                json.dumps(
                    {
                        "spec": {"name": "Risk", "definition": "Definition"},
                        "failure_modes": [
                            {
                                "name": "failure_mode",
                                "definition": "def",
                                "examples": [],
                                "permissible": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "seeds.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "scenario",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "factors": {"failure_mode": "failure_mode"},
                        "seed": {"title": "Scenario title", "description": "Scenario description"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {"status": "completed", "stages": {"inference": "completed", "judge": "completed"}}
                ),
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "scenario",
                        "seed_id": "seed-1",
                        "spec": "spec",
                        "failure_mode": "failure_mode",
                        "permissible": False,
                        "judge_model": "judge-model",
                        "judge_status": "ok",
                        "verdict": {
                            "dimensions": {
                                "policy_violation": False,
                                "overrefusal": False,
                            },
                            "justification": "clear",
                            "node_judgments": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ listSuites }} = await import({json.dumps(data_path.as_uri())});
                console.log(JSON.stringify(listSuites()));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["suite_id"], "suite-a")
            self.assertEqual(payload[0]["status"], "has_results")


if __name__ == "__main__":
    unittest.main()
