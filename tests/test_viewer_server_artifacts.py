# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assert_eval.viewer_read_model import ViewerReadModelBuildError, build_run_viewer_artifacts
from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
DATA_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "data.ts"
METRICS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "metrics.ts"
DIMENSIONS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "dimensions.ts"
ARTIFACTS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "artifacts.ts"
CONFIG_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "config.ts"
JUDGMENT_SRC = ROOT / "viewer" / "src" / "lib" / "judgment.ts"
RESULT_VIEW_SRC = ROOT / "viewer" / "src" / "lib" / "result-view.ts"
TYPES_SRC = ROOT / "viewer" / "src" / "lib" / "types.ts"


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need ≥ 22.6)")
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
            ["node", *node_ts_args(), "--input-type=module"],
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

            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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

    def test_build_viewer_read_model_uses_versioned_seed_artifact_from_manifest(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            suite_dir = tmp_root / "suite-a"
            run_dir = suite_dir / "run-a"
            test_set_path = suite_dir / "artifacts" / "test_set" / "v0001" / "test_set.jsonl"
            run_dir.mkdir(parents=True, exist_ok=True)
            test_set_path.parent.mkdir(parents=True, exist_ok=True)

            test_set_path.write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "versioned behavior"},
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
                        "stages": {"inference": "completed", "judge": "completed"},
                        "artifact_versions": {
                            "test_set": {
                                "version": "v0001",
                                "path": "artifacts/test_set/v0001/test_set.jsonl",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                "pipeline:\n  inference:\n    target:\n      model:\n        name: target-model\n",
                encoding="utf-8",
            )
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
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
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
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

            build_run_viewer_artifacts(run_dir)
            prompt_rows = json.loads(
                (run_dir / ".viewer" / "viewer_prompt_rows.json").read_text(encoding="utf-8")
            )

            self.assertEqual(prompt_rows[0]["dimensions"]["behavior"], "versioned behavior")

    def test_build_viewer_read_model_rejects_duplicate_transcript_keys(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            suite_dir = tmp_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
            inference_row = {
                "type": "prompt",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
                "permissible": False,
                "target": "target-model",
                "events": [],
                "llm_calls": [],
            }
            score_row = {
                "type": "prompt",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
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
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(inference_row) + "\n" + json.dumps(inference_row) + "\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(json.dumps(score_row) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ViewerReadModelBuildError, "Duplicate prompt:test-case-1 row"):
                build_run_viewer_artifacts(run_dir)

    def test_build_viewer_read_model_rejects_duplicate_score_keys(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            suite_dir = tmp_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
            inference_row = {
                "type": "prompt",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
                "permissible": False,
                "target": "target-model",
                "events": [],
                "llm_calls": [],
            }
            score_row = {
                "type": "prompt",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
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
            (run_dir / "inference_set.jsonl").write_text(json.dumps(inference_row) + "\n", encoding="utf-8")
            (run_dir / "scores.jsonl").write_text(
                json.dumps(score_row) + "\n" + json.dumps(score_row) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ViewerReadModelBuildError, "Duplicate prompt:test-case-1 row"):
                build_run_viewer_artifacts(run_dir)

    def test_load_judged_prompts_surfaces_invalid_scores_jsonl(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            data_path = self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)

            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
                  const {{ loadJudgedPrompts }} = await import({json.dumps(data_path.as_uri())});
                  loadJudgedPrompts('suite-a', 'run-a');
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

    def test_load_suite_snapshot_excludes_artifacts_cache_directory_from_runs(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            self._copy_data_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            run_dir = suite_dir / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            (suite_dir / "suite.json").write_text(
                json.dumps({"created_at": "2026-04-02T00:00:00Z"}), encoding="utf-8"
            )

            cache_dir = suite_dir / "artifacts" / "test_set" / "v0001"
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "test_set.jsonl").write_text("", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(tmp_root),
                }
            )
            script = textwrap.dedent(
                """\
                try {
                  const { loadSuiteSnapshot } = await import('./artifacts.ts');
                  const snapshot = loadSuiteSnapshot('suite-a');
                  console.log(JSON.stringify({ ok: true, runIds: snapshot?.runIds ?? [] }));
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
            self.assertTrue(payload["ok"], msg=payload)
            self.assertEqual(sorted(payload["runIds"]), ["run-a"])
            self.assertNotIn("artifacts", payload["runIds"])

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
                        "behavior_categories": [
                            {"name": "behavior", "definition": "def", "permissible": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "scenario",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
                "type": "scenario",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "dimensions": {"behavior": "behavior"},
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
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(valid_row) + "\n" + '{"type":"scenario"',
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
                  previewSeed: payload.inferencePreviewRows[0]?.test_case_id ?? null,
                  previewTurns: payload.inferencePreviewRows[0]?.turns_count ?? null,
                  previewDrawerTitle: payload.scenarioSeedMap?.['test-case-1']?.title ?? null,
                  previewDrawerMessages: payload.scenarioDrawerItems?.['test-case-1']?.messages.length ?? 0,
                  previewTotal: payload.inferencePreviewTotal,
                  auditScores: payload.auditScores.length
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["previewCount"], 1)
            self.assertEqual(payload["previewSeed"], "test-case-1")
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
                        "behavior_categories": [
                            {"name": "behavior", "definition": "def", "permissible": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "scenario",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
                "type": "scenario",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
                "permissible": False,
                "stop_reason": "max_turns",
                "events": [],
                "llm_calls": [],
            }
            (run_dir / "inference_set.jsonl").write_text(
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
            self.assertIn("inference_set.jsonl", payload["message"])
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
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "scenario",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
                "type": "scenario",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
                "permissible": False,
                "stop_reason": "max_turns",
                "events": [],
                "llm_calls": [],
            }
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(valid_row) + "\n" + '{"type":"scenario"',
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
            self.assertIn("inference_set.jsonl", payload["message"])

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
                        "behavior_categories": [
                            {"name": "behavior", "definition": "def", "permissible": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "scenario",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
                "type": "scenario",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "dimensions": {"behavior": "behavior"},
                "stop_reason": "max_turns",
                "events": [],
                "llm_calls": [],
            }
            score_row = {
                "type": "scenario",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "dimensions": {"behavior": "behavior"},
                "judge_model": "judge-model",
                "judge_status": "ok",
                "verdict": {
                    "dimensions": {"policy_violation": False, "overrefusal": False},
                    "justification": "clear",
                    "node_judgments": [],
                },
            }
            (run_dir / "inference_set.jsonl").write_text(
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
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
            inference_row = {
                "type": "prompt",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
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
                "type": "prompt",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
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
            (run_dir / "inference_set.jsonl").write_text(json.dumps(inference_row) + "\n", encoding="utf-8")
            (run_dir / "scores.jsonl").write_text(json.dumps(score_row) + "\n", encoding="utf-8")
            self._build_viewer_read_model(run_dir)
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(inference_row) + "\n" + json.dumps(inference_row) + "\n",
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
                        "behavior_categories": [
                            {
                                "name": "prompt-behavior",
                                "definition": "prompt definition",
                                "permissible": False,
                            },
                            {
                                "name": "scenario-behavior",
                                "definition": "scenario definition",
                                "permissible": False,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "test_set.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "prompt",
                                "test_case_id": "seed-prompt",
                                "behavior": "behavior",
                                "dimensions": {"behavior": "prompt-behavior"},
                                "seed": {"description": "Prompt seed"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "scenario",
                                "test_case_id": "seed-scenario",
                                "behavior": "behavior",
                                "dimensions": {"behavior": "scenario-behavior"},
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
                "type": "prompt",
                "test_case_id": "seed-prompt",
                "behavior": "behavior",
                "dimensions": {"behavior": "prompt-behavior"},
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
                "type": "scenario",
                "test_case_id": "seed-scenario",
                "behavior": "behavior",
                "dimensions": {"behavior": "scenario-behavior"},
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
                "type": "prompt",
                "test_case_id": "seed-prompt",
                "behavior": "behavior",
                "dimensions": {"behavior": "prompt-behavior"},
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
                "type": "scenario",
                "test_case_id": "seed-scenario",
                "behavior": "behavior",
                "dimensions": {"behavior": "scenario-behavior"},
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
            (run_dir / "inference_set.jsonl").write_text(
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
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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
                const promptItem = await loadPromptDrawerItem('suite-a', 'run-a', 'test-case-1');
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
            (suite_dir / "test_set.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
                        "seed": {"description": "Prompt seed"},
                    }
                        ),
                        json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-2",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
                "type": "prompt",
                "test_case_id": "test-case-1",
                "behavior": "behavior",
                "behavior": "behavior",
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
            (run_dir / "inference_set.jsonl").write_text(
                json.dumps(prompt_transcript) + "\n" + "{bad jsonl\n",
                encoding="utf-8",
            )
            (run_dir / "scores.jsonl").write_text(
                json.dumps(
                    {
                        "type": "prompt",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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
                  await loadPromptDrawerItem('suite-a', 'run-a', 'test-case-1');
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
            self.assertIn("inference_set.jsonl", payload["message"])

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
                        "behavior": {"name": "Risk", "definition": "Definition"},
                        "behavior_categories": [
                            {
                                "name": "behavior",
                                "definition": "def",
                                "examples": [],
                                "permissible": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (suite_dir / "test_set.jsonl").write_text(
                json.dumps(
                    {
                        "type": "scenario",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "dimensions": {"behavior": "behavior"},
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
                        "type": "scenario",
                        "test_case_id": "test-case-1",
                        "behavior": "behavior",
                        "behavior": "behavior",
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


    def test_run_metrics_policy_violation_by_permissibility(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            harness_dir = Path(tmp_dir) / "harness"
            harness_dir.mkdir()
            self._copy_data_harness(harness_dir)
            metrics_path = harness_dir / "metrics.ts"

            script = textwrap.dedent(
                f"""\
                const {{ computeRunMetrics }} = await import({json.dumps(metrics_path.as_uri())});
                const behaviors = [
                  {{ name: 'perm_a', definition: '', examples: [], permissible: true }},
                  {{ name: 'notperm_a', definition: '', examples: [], permissible: false }}
                ];
                const samples = [
                  {{
                    test_case_id: 'seed-1',
                    prompt: 'prompt',
                    response: 'response',
                    behavior: 'perm_a',
                    target: 'target-model',
                    judge_model: 'judge-model',
                    judge_status: 'ok',
                    verdict: {{
                      dimensions: {{ policy_violation: true, overrefusal: true }},
                      justification: '',
                      node_judgments: [
                        {{ node_index: 0, node_name: 'perm_a', relevant: true, violated: true, confidence: 'high', evidence_turns: [], reasoning: '' }},
                        {{ node_index: 1, node_name: 'notperm_a', relevant: false, violated: null, confidence: null, evidence_turns: [], reasoning: '' }}
                      ]
                    }}
                  }},
                  {{
                    test_case_id: 'seed-2',
                    prompt: 'prompt',
                    response: 'response',
                    behavior: 'notperm_a',
                    target: 'target-model',
                    judge_model: 'judge-model',
                    judge_status: 'ok',
                    verdict: {{
                      dimensions: {{ policy_violation: false, overrefusal: false }},
                      justification: '',
                      node_judgments: [
                        {{ node_index: 0, node_name: 'perm_a', relevant: true, violated: false, confidence: 'high', evidence_turns: [], reasoning: '' }},
                        null,
                        'malformed-node',
                        {{ node_index: 1, node_name: 'notperm_a', relevant: true, violated: true, confidence: 'high', evidence_turns: [], reasoning: '' }},
                        {{ node_index: 2, node_name: 'unknown_node', relevant: true, violated: true, confidence: 'high', evidence_turns: [], reasoning: '' }}
                      ]
                    }}
                  }}
                ];
                const withBehaviors = computeRunMetrics(samples, behaviors);
                const withoutBehaviors = computeRunMetrics(samples, []);
                console.log(JSON.stringify({{ withBehaviors, withoutBehaviors }}));
                """
            )
            result = self._run_node(
                harness_dir=harness_dir,
                script=script,
                env=os.environ.copy(),
            )
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)

            with_behaviors = payload["withBehaviors"]
            self.assertEqual(with_behaviors["scored_total"], 2)
            permissible = with_behaviors["policy_violation_on_permissible"]
            self.assertIsNotNone(permissible)
            self.assertEqual(permissible["count"], 2)
            self.assertEqual(permissible["flagged_count"], 1)
            self.assertEqual(permissible["clear_count"], 1)
            self.assertAlmostEqual(permissible["rate"], 0.5)

            not_permissible = with_behaviors["policy_violation_on_not_permissible"]
            self.assertIsNotNone(not_permissible)
            self.assertEqual(not_permissible["count"], 1)
            self.assertEqual(not_permissible["flagged_count"], 1)
            self.assertEqual(not_permissible["clear_count"], 0)
            self.assertAlmostEqual(not_permissible["rate"], 1.0)

            without_behaviors = payload["withoutBehaviors"]
            self.assertIsNone(without_behaviors["policy_violation_on_permissible"])
            self.assertIsNone(without_behaviors["policy_violation_on_not_permissible"])

    def test_audit_run_metrics_policy_violation_by_permissibility(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            harness_dir = Path(tmp_dir) / "harness"
            harness_dir.mkdir()
            self._copy_data_harness(harness_dir)
            metrics_path = harness_dir / "metrics.ts"

            script = textwrap.dedent(
                f"""\
                const {{ computeAuditRunMetrics }} = await import({json.dumps(metrics_path.as_uri())});
                const behaviors = [
                  {{ name: 'perm_a', definition: '', examples: [], permissible: true }},
                  {{ name: 'notperm_a', definition: '', examples: [], permissible: false }}
                ];
                const scores = [
                  {{
                    test_case_id: 'scenario-1',
                    target: 'target-model',
                    tester_model: 'tester-model',
                    judge_model: 'judge-model',
                    judge_status: 'ok',
                    verdict: {{
                      dimensions: {{ policy_violation: true, overrefusal: false }},
                      justification: '',
                      node_judgments: [
                        {{ node_index: 0, node_name: 'notperm_a', relevant: true, violated: true, confidence: 'high', evidence_turns: [], reasoning: '' }},
                        {{ node_index: 1, node_name: 'perm_a', relevant: true, violated: false, confidence: 'high', evidence_turns: [], reasoning: '' }}
                      ]
                    }}
                  }}
                ];
                const metrics = computeAuditRunMetrics(scores, behaviors);
                console.log(JSON.stringify(metrics));
                """
            )
            result = self._run_node(
                harness_dir=harness_dir,
                script=script,
                env=os.environ.copy(),
            )
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            metrics = json.loads(result.stdout)

            self.assertEqual(metrics["scored_total"], 1)
            permissible = metrics["policy_violation_on_permissible"]
            self.assertIsNotNone(permissible)
            self.assertEqual(permissible["count"], 1)
            self.assertEqual(permissible["flagged_count"], 0)
            self.assertEqual(permissible["clear_count"], 1)
            self.assertAlmostEqual(permissible["rate"], 0.0)

            not_permissible = metrics["policy_violation_on_not_permissible"]
            self.assertIsNotNone(not_permissible)
            self.assertEqual(not_permissible["count"], 1)
            self.assertEqual(not_permissible["flagged_count"], 1)
            self.assertEqual(not_permissible["clear_count"], 0)
            self.assertAlmostEqual(not_permissible["rate"], 1.0)

    def test_load_run_judge_taxonomy_prefers_run_config(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            harness_dir = Path(tmp_dir) / "harness"
            harness_dir.mkdir()
            self._copy_data_harness(harness_dir)
            artifacts_path = harness_dir / "artifacts.ts"

            suite_dir = Path(tmp_dir) / "demo-suite"
            run_dir = suite_dir / "demo-run"
            run_dir.mkdir(parents=True)
            judge_taxonomy = {
                "behavior": {"name": "demo", "definition": "demo"},
                "behavior_categories": [
                    {"name": "judge_only", "definition": "", "examples": [], "permissible": False}
                ],
            }
            taxonomy_path = suite_dir / "taxonomy.override.json"
            taxonomy_path.write_text(json.dumps(judge_taxonomy), encoding="utf-8")
            relative_taxonomy_path = Path("..") / "taxonomy.override.json"
            outside_taxonomy_path = Path(tmp_dir) / "outside-taxonomy.json"
            outside_taxonomy_path.write_text(
                json.dumps({"behavior": {"name": "outside", "definition": "outside"}, "behavior_categories": []}),
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text(
                f"pipeline:\n  judge:\n    taxonomy_path: {relative_taxonomy_path.as_posix()}\n",
                encoding="utf-8",
            )

            script = textwrap.dedent(
                f"""\
                const {{ loadRunJudgeTaxonomyForRun, loadRunJudgeTaxonomy, loadRunJudgeTaxonomyFromArtifacts }} = await import({json.dumps(artifacts_path.as_uri())});
                const fromRun = loadRunJudgeTaxonomyForRun('demo-suite', 'demo-run');
                const fromConfig = loadRunJudgeTaxonomyFromArtifacts(
                  {{ suite: 'demo-suite', pipeline: {{ judge: {{ taxonomy_path: {json.dumps(relative_taxonomy_path.as_posix())} }} }} }},
                  null,
                  {json.dumps(str(run_dir))}
                );
                const fromArtifact = loadRunJudgeTaxonomyFromArtifacts({{ suite: 'demo-suite' }}, {{ systematize: {{ path: 'taxonomy.override.json' }} }});
                const fromAbsolute = loadRunJudgeTaxonomyFromArtifacts(
                  {{ pipeline: {{ judge: {{ taxonomy_path: {json.dumps(str(outside_taxonomy_path))} }} }} }},
                  null,
                  {json.dumps(str(run_dir))}
                );
                const fromEscape = loadRunJudgeTaxonomyFromArtifacts(
                  {{ pipeline: {{ judge: {{ taxonomy_path: '../../../outside-taxonomy.json' }} }} }},
                  null,
                  {json.dumps(str(run_dir))}
                );
                const fromMissing = loadRunJudgeTaxonomy({{ pipeline: {{ judge: {{}} }} }});
                console.log(JSON.stringify({{ fromRun, fromConfig, fromArtifact, fromAbsolute, fromEscape, fromMissing }}));
                """
            )
            env = os.environ.copy()
            env["ARTIFACTS_ROOT"] = str(Path(tmp_dir))
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)

            self.assertEqual(payload["fromRun"], judge_taxonomy)
            self.assertEqual(payload["fromConfig"], judge_taxonomy)
            self.assertEqual(payload["fromArtifact"], judge_taxonomy)
            self.assertIsNone(payload["fromAbsolute"])
            self.assertIsNone(payload["fromEscape"])
            self.assertIsNone(payload["fromMissing"])



class ViewerReadModelHelpersTest(unittest.TestCase):
    """Tests for path-traversal defenses that don't depend on Node TS support."""

    def test_manifest_relative_path_rejects_parent_directory_segments(self) -> None:
        from assert_eval.viewer_read_model import _manifest_relative_path, _test_set_artifact_path

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True)

            self.assertIsNone(_manifest_relative_path(suite_dir, "../../etc/passwd"))
            self.assertIsNone(_manifest_relative_path(suite_dir, "artifacts/../../escape"))
            self.assertEqual(
                _manifest_relative_path(suite_dir, "artifacts/test_set/v0001/test_set.jsonl"),
                suite_dir / "artifacts" / "test_set" / "v0001" / "test_set.jsonl",
            )

            malicious_manifest = {
                "artifact_versions": {
                    "test_set": {"version": "v0001", "path": "../../etc/passwd"}
                }
            }
            self.assertEqual(
                _test_set_artifact_path(suite_dir, malicious_manifest),
                suite_dir / "test_set.jsonl",
            )

            safe_manifest = {
                "artifact_versions": {
                    "test_set": {"version": "v0001", "path": "artifacts/test_set/v0001/test_set.jsonl"}
                }
            }
            self.assertEqual(
                _test_set_artifact_path(suite_dir, safe_manifest),
                suite_dir / "artifacts" / "test_set" / "v0001" / "test_set.jsonl",
            )

    def test_test_set_artifact_path_rejects_absolute_paths(self) -> None:
        """Regression for Copilot review #003 (round 2).

        A tampered manifest that supplies an absolute test-set path must be
        ignored — otherwise the absolute branch silently bypasses the
        relative ``..`` defense and reads from anywhere on disk.
        """

        from assert_eval.viewer_read_model import _test_set_artifact_path

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True)

            absolute_target = Path(tmp_dir) / "outside.jsonl"
            absolute_target.write_text("{}\n", encoding="utf-8")

            malicious_manifest = {
                "artifact_versions": {
                    "test_set": {
                        "version": "v0001",
                        "path": str(absolute_target),
                    }
                }
            }
            self.assertEqual(
                _test_set_artifact_path(suite_dir, malicious_manifest),
                suite_dir / "test_set.jsonl",
            )

    def test_test_set_artifact_path_rejects_paths_that_normalize_to_directory(self) -> None:
        """Regression for Copilot review #002 (round 3).

        A manifest path that normalizes to no segments (``"."``, ``"./"``,
        ``"/."``) must not resolve to the suite directory itself, or the
        loader will try to read a directory as a JSONL file (raising
        IsADirectoryError / EISDIR).
        """

        from assert_eval.viewer_read_model import _manifest_relative_path, _test_set_artifact_path

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True)

            for raw_path in (".", "./", "/.", "./.", "././"):
                self.assertIsNone(
                    _manifest_relative_path(suite_dir, raw_path),
                    msg=f"expected None for {raw_path!r}",
                )
                manifest = {
                    "artifact_versions": {
                        "test_set": {"version": "v0001", "path": raw_path}
                    }
                }
                self.assertEqual(
                    _test_set_artifact_path(suite_dir, manifest),
                    suite_dir / "test_set.jsonl",
                    msg=f"expected fallback for {raw_path!r}",
                )




if __name__ == "__main__":
    unittest.main()
