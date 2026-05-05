import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
POLICY_ROUTE_SRC = ROOT / "viewer" / "src" / "routes" / "api" / "policy" / "+server.ts"


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need ≥ 22.6)")
class ViewerPolicyApiTest(unittest.TestCase):
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

    _ENV_MOCK = "export const env = { VIEWER_EDIT_MODE: '1' };\n"

    def _prepare_policy_harness(self, harness_dir: Path, artifacts_root: Path) -> None:
        source = POLICY_ROUTE_SRC.read_text(encoding="utf-8")
        source = source.replace("from '$lib/server/config.js';", "from './config.js';")
        source = source.replace("from '$lib/server/artifacts.js';", "from './artifacts.js';")
        source = source.replace("from '@sveltejs/kit';", "from './kit.js';")
        source = source.replace("from '$env/dynamic/private';", "from './env.js';")
        (harness_dir / "+server.ts").write_text(source, encoding="utf-8")
        (harness_dir / "$types.js").write_text("export {};\n", encoding="utf-8")
        (harness_dir / "config.js").write_text(
            "export const ARTIFACTS_ROOT = process.env.ARTIFACTS_ROOT;\n",
            encoding="utf-8",
        )
        (harness_dir / "artifacts.js").write_text(
            "export function isSafeArtifactId(value) { return typeof value === 'string' && /^[a-z0-9][a-z0-9._-]*$/i.test(value); }\n",
            encoding="utf-8",
        )
        (harness_dir / "kit.js").write_text(
            textwrap.dedent(
                """\
                export function json(body, init = {}) {
                  return { status: init.status ?? 200, body };
                }
                """
            ),
            encoding="utf-8",
        )
        (harness_dir / "env.js").write_text(self._ENV_MOCK, encoding="utf-8")

    def test_put_returns_400_when_existing_policy_is_malformed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()

            artifacts_root = tmp_root / "artifacts" / "results"
            self._prepare_policy_harness(harness_dir, artifacts_root)

            suite_dir = artifacts_root / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            (suite_dir / "policy.json").write_text("{bad json", encoding="utf-8")

            env = os.environ.copy()
            env["ARTIFACTS_ROOT"] = str(artifacts_root)

            script = textwrap.dedent(
                """\
                const mod = await import('./+server.ts');
                const response = await mod.PUT({
                  request: {
                    json: async () => ({
                      suite_id: 'suite-a',
                      policy: {
                        concept: { name: 'Risk', definition: 'Definition' },
                        behaviors: []
                      }
                    })
                  }
                });
                console.log(JSON.stringify(response));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], 400)
            self.assertEqual(payload["body"]["error"], "Existing policy is malformed")

    def test_put_returns_400_when_existing_policy_has_malformed_behavior_entry(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()

            artifacts_root = tmp_root / "artifacts" / "results"
            self._prepare_policy_harness(harness_dir, artifacts_root)

            suite_dir = artifacts_root / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            (suite_dir / "policy.json").write_text(
                json.dumps(
                    {
                        "concept": {"name": "Risk", "definition": "Definition"},
                        "behaviors": [None],
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["ARTIFACTS_ROOT"] = str(artifacts_root)

            script = textwrap.dedent(
                """\
                const mod = await import('./+server.ts');
                const response = await mod.PUT({
                  request: {
                    json: async () => ({
                      suite_id: 'suite-a',
                      policy: {
                        concept: { name: 'Risk', definition: 'Definition' },
                        behaviors: [{ name: 'Test behavior' }]
                      }
                    })
                  }
                });
                console.log(JSON.stringify(response));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], 400)
            self.assertEqual(payload["body"]["error"], "Existing policy is malformed")

    def test_put_returns_400_when_request_behavior_entry_is_malformed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()

            artifacts_root = tmp_root / "artifacts" / "results"
            self._prepare_policy_harness(harness_dir, artifacts_root)

            suite_dir = artifacts_root / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            (suite_dir / "policy.json").write_text(
                json.dumps(
                    {
                        "concept": {"name": "Risk", "definition": "Definition"},
                        "behaviors": [{"name": "Behavior"}],
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["ARTIFACTS_ROOT"] = str(artifacts_root)

            script = textwrap.dedent(
                """\
                const mod = await import('./+server.ts');
                const response = await mod.PUT({
                  request: {
                    json: async () => ({
                      suite_id: 'suite-a',
                      policy: {
                        concept: { name: 'Risk', definition: 'Definition' },
                        behaviors: [null]
                      }
                    })
                  }
                });
                console.log(JSON.stringify(response));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], 400)
            self.assertEqual(payload["body"]["error"], "policy must have concept and behaviors")


if __name__ == "__main__":
    unittest.main()
