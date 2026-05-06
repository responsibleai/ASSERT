import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_ROUTE_SRC = (
    ROOT / "viewer" / "src" / "routes" / "api" / "download" / "[...path]" / "+server.ts"
)
ARTIFACTS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "artifacts.ts"
CONFIG_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "config.ts"


class ViewerDownloadApiTest(unittest.TestCase):
    def _copy_harness(self, harness_dir: Path) -> Path:
        route_path = harness_dir / "server.ts"
        artifacts_path = harness_dir / "artifacts.ts"
        config_path = harness_dir / "config.ts"
        kit_path = harness_dir / "kit.js"

        route_source = (
            DOWNLOAD_ROUTE_SRC.read_text(encoding="utf-8")
            .replace("from '@sveltejs/kit';", "from './kit.js';")
            .replace("$lib/server/artifacts.js", "./artifacts.ts")
        )
        artifacts_source = ARTIFACTS_SRC.read_text(encoding="utf-8").replace(
            "./config.js", "./config.ts"
        )

        route_path.write_text(route_source, encoding="utf-8")
        artifacts_path.write_text(artifacts_source, encoding="utf-8")
        shutil.copyfile(CONFIG_SRC, config_path)
        kit_path.write_text(
            "export function error(status, message) { const err = new Error(message); err.status = status; throw err; }\n",
            encoding="utf-8",
        )
        return route_path

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

    def test_get_rejects_sibling_prefix_traversal(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            route_path = self._copy_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            sibling_root = tmp_root / "artifacts" / "results-evil" / "suite-a"
            sibling_root.mkdir(parents=True, exist_ok=True)
            (sibling_root / "taxonomy.json").write_text('{"secret": true}', encoding="utf-8")

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
                  const mod = await import({json.dumps(route_path.as_uri())});
                  await mod.GET({{ params: {{ path: '../results-evil/suite-a/taxonomy.json' }} }});
                  console.log(JSON.stringify({{ ok: true }}));
                }} catch (error) {{
                  console.log(JSON.stringify({{
                    ok: false,
                    status: error.status,
                    message: error.message
                  }}));
                }}
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], 403)

    def test_get_streams_allowed_artifact(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            tmp_root = Path(tmp_dir)
            harness_dir = tmp_root / "harness"
            harness_dir.mkdir()
            route_path = self._copy_harness(harness_dir)

            artifacts_root = tmp_root / "artifacts" / "results"
            suite_dir = artifacts_root / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            expected_body = '{"ok": true}\n'
            (suite_dir / "taxonomy.json").write_text(expected_body, encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(ROOT),
                }
            )
            script = textwrap.dedent(
                f"""\
                const mod = await import({json.dumps(route_path.as_uri())});
                const response = await mod.GET({{ params: {{ path: 'suite-a/taxonomy.json' }} }});
                console.log(JSON.stringify({{
                  status: response.status,
                  body: await response.text(),
                  disposition: response.headers.get('content-disposition'),
                  contentLength: response.headers.get('content-length')
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script, env=env)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], 200)
            self.assertEqual(payload["body"].replace("\r\n", "\n"), expected_body)
            self.assertEqual(payload["disposition"], 'attachment; filename="taxonomy.json"')
            actual_body_bytes = payload["body"].encode("utf-8")
            self.assertEqual(payload["contentLength"], str(len(actual_body_bytes)))


if __name__ == "__main__":
    unittest.main()
