# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
METRICS_SRC = ROOT / "viewer" / "src" / "lib" / "server" / "metrics.ts"


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need ≥ 22.6)")
class ViewerMetricsTest(unittest.TestCase):
    def _run_node(self, *, harness_dir: Path, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", *node_ts_args(), "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=harness_dir,
            check=False,
        )

    def test_audit_metrics_fall_back_to_tester_model_for_target_label(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            harness_dir = Path(tmp_dir)
            source = METRICS_SRC.read_text(encoding="utf-8")
            source = source.replace("from '$lib/judgment.js';", "from './judgment.js';")
            source = source.replace("from './dimensions.js';", "from './dimensions.js';")
            (harness_dir / "metrics.ts").write_text(source, encoding="utf-8")
            (harness_dir / "judgment.js").write_text(
                textwrap.dedent(
                    """\
                    export function getRequiredBaseMetricNames() { return ['policy_violation', 'overrefusal']; }
                    export function isBooleanFlag(value) { return typeof value === 'boolean'; }
                    export function getRecordFlag(record, name) {
                      const value = record?.verdict?.dimensions?.[name];
                      return typeof value === 'boolean' ? value : null;
                    }
                    export function isNotApplicableRecordDimension(record, name) {
                      return record?.verdict?.dimensions?.[name] === null && record?.verdict?.dimension_applicability?.[name] === false;
                    }
                    export function isSuccessfulJudgment(record) { return record?.judge_status !== 'error'; }
                    """
                ),
                encoding="utf-8",
            )
            (harness_dir / "dimensions.js").write_text(
                "export function loadDimensions() { return []; }\n",
                encoding="utf-8",
            )

            script = textwrap.dedent(
                """\
                const { computeAuditRunMetrics } = await import('./metrics.ts');
                const metrics = computeAuditRunMetrics([{
                  test_case_id: 'scenario-1',
                  behavior: 'medical advice',
                  tester_model: 'azure/gpt-5.4-1',
                  judge_model: 'azure/gpt-5.4-1',
                  judge_status: 'ok',
                  verdict: { dimensions: { policy_violation: false, overrefusal: false } },
                  metadata: { turns_count: 2, stop_reason: 'max_turns' }
                }]);
                console.log(JSON.stringify(metrics));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["target"], "azure/gpt-5.4-1")
            self.assertEqual(payload["tester_model"], "azure/gpt-5.4-1")
            self.assertEqual(payload["judge_model"], "azure/gpt-5.4-1")


if __name__ == "__main__":
    unittest.main()
