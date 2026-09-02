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

from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
RUN_SPAWN_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "run-spawn.ts"
ARTIFACTS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "artifacts.ts"
CONFIG_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "config.ts"


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need >= 22.6)")
class ViewerRunSpawnTest(unittest.TestCase):
    def test_estimate_uses_temporary_config_without_reserving_run(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            root = Path(tmp_dir)
            harness = root / "harness"
            harness.mkdir()
            run_spawn_path = harness / "run-spawn.ts"
            run_spawn_path.write_text(
                RUN_SPAWN_SRC.read_text(encoding="utf-8")
                .replace("./artifacts.js", "./artifacts.ts")
                .replace("./config.js", "./config.ts"),
                encoding="utf-8",
            )
            (harness / "artifacts.ts").write_text(
                ARTIFACTS_SRC.read_text(encoding="utf-8").replace(
                    "./config.js", "./config.ts"
                ),
                encoding="utf-8",
            )
            shutil.copyfile(CONFIG_SRC, harness / "config.ts")

            args_path = root / "args.json"
            fake_cli = root / "fake-cli.mjs"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    import fs from 'node:fs';
                    fs.writeFileSync(process.env.ARGS_PATH, JSON.stringify(process.argv.slice(2)));
                    console.log(JSON.stringify({
                      schema_version: 1,
                      calls: 2,
                      input_tokens: 100,
                      output_tokens: 50,
                      total_tokens: 150,
                      lower_bound_tokens: 98,
                      upper_bound_tokens: 203,
                      stages: {},
                      notes: []
                    }));
                    """
                ),
                encoding="utf-8",
            )

            artifacts_root = root / "artifacts" / "results"
            env = os.environ.copy()
            env.update(
                {
                    "ARGS_PATH": str(args_path),
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(ROOT),
                    "ASSERT_AI_COMMAND": f"node {fake_cli}",
                }
            )
            script = textwrap.dedent(
                f"""\
                const {{ estimateAssertAiRun }} = await import({json.dumps(run_spawn_path.as_uri())});
                const estimate = await estimateAssertAiRun({{
                  suite: 'preview-suite',
                  run: 'preview-run',
                  behaviorName: 'answer_accuracy',
                  configObject: {{
                    suite: 'preview-suite',
                    run: 'preview-run',
                    behavior: {{ name: 'answer_accuracy', description: 'Answer accurately.' }},
                    context: 'A factual assistant.',
                    pipeline: {{}}
                  }},
                  warnings: [],
                  extraFiles: []
                }});
                console.log(JSON.stringify(estimate));
                """
            )
            result = subprocess.run(
                ["node", *node_ts_args(), "--input-type=module"],
                input=script,
                text=True,
                capture_output=True,
                cwd=harness,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            self.assertEqual(json.loads(result.stdout)["total_tokens"], 150)
            args = json.loads(args_path.read_text(encoding="utf-8"))
            self.assertEqual(args[0], "estimate")
            self.assertEqual(args[-2:], ["--output", "json"])
            config_path = Path(args[args.index("--config") + 1])
            self.assertFalse(config_path.exists())
            self.assertFalse((artifacts_root / "preview-suite").exists())

    def test_aborted_estimate_waits_for_child_close_before_cleanup(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            root = Path(tmp_dir)
            harness = root / "harness"
            harness.mkdir()
            run_spawn_path = harness / "run-spawn.ts"
            run_spawn_path.write_text(
                RUN_SPAWN_SRC.read_text(encoding="utf-8")
                .replace("./artifacts.js", "./artifacts.ts")
                .replace("./config.js", "./config.ts"),
                encoding="utf-8",
            )
            (harness / "artifacts.ts").write_text(
                ARTIFACTS_SRC.read_text(encoding="utf-8").replace(
                    "./config.js", "./config.ts"
                ),
                encoding="utf-8",
            )
            shutil.copyfile(CONFIG_SRC, harness / "config.ts")

            state_path = root / "state.json"
            fake_cli = root / "fake-cli.mjs"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    import fs from 'node:fs';
                    const args = process.argv.slice(2);
                    const configPath = args[args.indexOf('--config') + 1];
                    fs.writeFileSync(
                      process.env.STATE_PATH,
                      JSON.stringify({ pid: process.pid, configPath })
                    );
                    process.on('SIGTERM', () => {});
                    setInterval(() => {}, 1000);
                    """
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "STATE_PATH": str(state_path),
                    "ARTIFACTS_ROOT": str(root / "artifacts" / "results"),
                    "MEASUREMENTS_ROOT": str(ROOT),
                    "ASSERT_AI_COMMAND": f"node {fake_cli}",
                }
            )
            script = textwrap.dedent(
                f"""\
                import fs from 'node:fs';
                const {{ estimateAssertAiRun }} = await import({json.dumps(run_spawn_path.as_uri())});
                const controller = new AbortController();
                const pending = estimateAssertAiRun({{
                  suite: 'preview-suite',
                  run: 'preview-run',
                  behaviorName: 'answer_accuracy',
                  configObject: {{
                    suite: 'preview-suite',
                    run: 'preview-run',
                    behavior: {{ name: 'answer_accuracy', description: 'Answer accurately.' }},
                    context: 'A factual assistant.',
                    pipeline: {{}}
                  }},
                  warnings: [],
                  extraFiles: []
                }}, controller.signal);
                for (let attempt = 0; attempt < 200 && !fs.existsSync(process.env.STATE_PATH); attempt++) {{
                  await new Promise((resolve) => setTimeout(resolve, 10));
                }}
                if (!fs.existsSync(process.env.STATE_PATH)) {{
                  throw new Error('estimate child did not start');
                }}
                const state = JSON.parse(fs.readFileSync(process.env.STATE_PATH, 'utf-8'));
                const abortStartedAt = Date.now();
                controller.abort();
                let error = '';
                try {{
                  await pending;
                }} catch (err) {{
                  error = err.message;
                }}
                let childAlive = true;
                try {{
                  process.kill(state.pid, 0);
                }} catch {{
                  childAlive = false;
                }}
                console.log(JSON.stringify({{
                  error,
                  childAlive,
                  configExists: fs.existsSync(state.configPath),
                  abortElapsedMs: Date.now() - abortStartedAt
                }}));
                """
            )
            result = subprocess.run(
                ["node", *node_ts_args(), "--input-type=module"],
                input=script,
                text=True,
                capture_output=True,
                cwd=harness,
                env=env,
                check=False,
                timeout=15,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=f"{result.stdout}\n{result.stderr}",
            )
            payload = json.loads(result.stdout)
            self.assertIn("cancelled", payload["error"])
            self.assertFalse(payload["childAlive"])
            self.assertFalse(payload["configExists"])
            self.assertLess(payload["abortElapsedMs"], 5_000)


if __name__ == "__main__":
    unittest.main()
