import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
RUNNER_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "runner.ts"
RUN_STATUS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "run-status.ts"
ARTIFACTS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "artifacts.ts"
CONFIG_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "config.ts"


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need ≥ 22.6)")
class ViewerRunnerTest(unittest.TestCase):
    def _copy_runner_harness(self, harness_dir: Path) -> Path:
        runner_path = harness_dir / "runner.ts"
        run_status_path = harness_dir / "run-status.ts"
        artifacts_path = harness_dir / "artifacts.ts"
        config_path = harness_dir / "config.ts"

        runner_source = RUNNER_SRC.read_text(encoding="utf-8").replace(
            "./run-status.js", "./run-status.ts"
        )
        run_status_source = (
            RUN_STATUS_SRC.read_text(encoding="utf-8")
            .replace("./artifacts.js", "./artifacts.ts")
            .replace("./config.js", "./config.ts")
        )
        artifacts_source = ARTIFACTS_SRC.read_text(encoding="utf-8").replace(
            "./config.js", "./config.ts"
        )

        runner_path.write_text(runner_source, encoding="utf-8")
        run_status_path.write_text(run_status_source, encoding="utf-8")
        artifacts_path.write_text(artifacts_source, encoding="utf-8")
        shutil.copyfile(CONFIG_SRC, config_path)
        return runner_path

    def _run_node(
        self,
        *,
        harness_dir: Path,
        script: str,
        env: dict[str, str],
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

    def test_load_persisted_run_state_reads_minimal_manifest(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            runner_path = self._copy_runner_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            run_dir = artifacts_root / "suite-trace" / "run-trace"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "started_at": 1774565712.0,
                        "stages": {
                            "inference": "failed",
                            "judge": "completed",
                        },
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(ROOT),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ loadPersistedRunState }} = await import({json.dumps(runner_path.as_uri())});
                const state = loadPersistedRunState('suite-trace', 'run-trace');
                console.log(JSON.stringify(state));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["exitCode"], 1)
            self.assertIsNone(payload["currentStage"])
            self.assertEqual(payload["stages"], {"inference": "error", "judge": "completed"})
    def test_get_active_runs_lists_only_running_manifests(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            runner_path = self._copy_runner_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            running_dir = artifacts_root / "suite-a" / "run-live"
            completed_dir = artifacts_root / "suite-b" / "run-done"
            running_dir.mkdir(parents=True, exist_ok=True)
            completed_dir.mkdir(parents=True, exist_ok=True)

            (running_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "started_at": "2026-03-31T00:00:00Z",
                        "stages": {"inference": "running", "judge": "pending"},
                    }
                ),
                encoding="utf-8",
            )
            (completed_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "started_at": "2026-03-30T23:00:00Z",
                        "stages": {"inference": "completed", "judge": "completed"},
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(ROOT),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ getActiveRuns }} = await import({json.dumps(runner_path.as_uri())});
                const runs = getActiveRuns();
                console.log(JSON.stringify(runs));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["suiteId"], "suite-a")
            self.assertEqual(payload[0]["runId"], "run-live")
            self.assertEqual(payload[0]["status"], "running")
            self.assertEqual(payload[0]["currentStage"], "inference")

    def test_load_run_status_payload_rejects_unsafe_ids(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            runner_path = self._copy_runner_harness(harness_dir)

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(tmp_root / "artifacts" / "results"),
                    "MEASUREMENTS_ROOT": str(ROOT),
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ loadRunStatusPayload }} = await import({json.dumps(runner_path.as_uri())});
                console.log(JSON.stringify({{ payload: loadRunStatusPayload('../suite-a', 'run-a') }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertIsNone(payload["payload"])

    def test_load_persisted_run_state_surfaces_invalid_manifest_json(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            runner_path = self._copy_runner_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            run_dir = artifacts_root / "suite-a" / "run-a"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text("{invalid json", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(ROOT),
                }
            )
            script = textwrap.dedent(
                f"""\
                try {{
                  const {{ loadPersistedRunState }} = await import({json.dumps(runner_path.as_uri())});
                  loadPersistedRunState('suite-a', 'run-a');
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
            self.assertIn("manifest.json", payload["message"])


if __name__ == "__main__":
    unittest.main()
