# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
PAGE_SERVER_SRC = ROOT / "viewer" / "src" / "routes" / "suite" / "[suite_id]" / "[run_id]" / "+page.server.ts"


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need ≥ 22.6)")
class ViewerRunPageServerTest(unittest.TestCase):
    def _run_node(self, *, harness_dir: Path, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", *node_ts_args(), "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=harness_dir,
            env=os.environ.copy(),
            check=False,
        )

    def test_page_server_load_passes_audit_tab_to_data_loader(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            harness_dir = Path(tmp_dir)
            source = PAGE_SERVER_SRC.read_text(encoding="utf-8")
            source = source.replace("from '$lib/server/artifacts.js';", "from './artifacts.js';")
            source = source.replace("from '$lib/server/data.js';", "from './data.js';")
            source = source.replace("from '@sveltejs/kit';", "from './kit.js';")
            (harness_dir / "page.server.ts").write_text(source, encoding="utf-8")
            (harness_dir / "$types.js").write_text("export {};\n", encoding="utf-8")
            (harness_dir / "kit.js").write_text(
                "export function error(status, message) { const err = new Error(message); err.status = status; throw err; }\n",
                encoding="utf-8",
            )
            (harness_dir / "artifacts.js").write_text(
                "export function isSafeArtifactId(value) { return typeof value === 'string' && /^[a-z0-9][a-z0-9._-]*$/i.test(value); }\n"
                "export class ViewerReadModelError extends Error {}\n",
                encoding="utf-8",
            )
            (harness_dir / "data.js").write_text(
                textwrap.dedent(
                    """\
                    export function loadRunPageData(suiteId, runId, activeTab) {
                      return { suite_id: suiteId, run_id: runId, activeTab };
                    }
                    """
                ),
                encoding="utf-8",
            )

            script = textwrap.dedent(
                """\
                const mod = await import('./page.server.ts');
                const payload = await mod.load({
                  params: { suite_id: 'suite-a', run_id: 'run-a' },
                  url: new URL('http://example.test/suite/suite-a/run-a?tab=audit')
                });
                console.log(JSON.stringify(payload));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["suite_id"], "suite-a")
            self.assertEqual(payload["run_id"], "run-a")
            self.assertEqual(payload["activeTab"], "audit")

    def test_page_server_load_returns_404_when_data_loader_returns_null(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            harness_dir = Path(tmp_dir)
            source = PAGE_SERVER_SRC.read_text(encoding="utf-8")
            source = source.replace("from '$lib/server/artifacts.js';", "from './artifacts.js';")
            source = source.replace("from '$lib/server/data.js';", "from './data.js';")
            source = source.replace("from '@sveltejs/kit';", "from './kit.js';")
            (harness_dir / "page.server.ts").write_text(source, encoding="utf-8")
            (harness_dir / "$types.js").write_text("export {};\n", encoding="utf-8")
            (harness_dir / "kit.js").write_text(
                "export function error(status, message) { const err = new Error(message); err.status = status; throw err; }\n",
                encoding="utf-8",
            )
            (harness_dir / "artifacts.js").write_text(
                "export function isSafeArtifactId(value) { return typeof value === 'string' && /^[a-z0-9][a-z0-9._-]*$/i.test(value); }\n",
                encoding="utf-8",
            )
            (harness_dir / "data.js").write_text(
                textwrap.dedent(
                    """\
                    export function loadRunPageData() {
                      return null;
                    }
                    """
                ),
                encoding="utf-8",
            )

            script = textwrap.dedent(
                """\
                try {
                  const mod = await import('./page.server.ts');
                  await mod.load({
                    params: { suite_id: 'suite-a', run_id: 'run-a' },
                    url: new URL('http://example.test/suite/suite-a/run-a')
                  });
                  console.log(JSON.stringify({ ok: true }));
                } catch (error) {
                  console.log(JSON.stringify({ ok: false, status: error.status, message: error.message }));
                }
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], 404)


if __name__ == "__main__":
    unittest.main()
