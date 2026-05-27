# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
COMPARE_VIEW_SRC = ROOT / "viewer" / "src" / "lib" / "compare-view.ts"
JUDGMENT_SRC = ROOT / "viewer" / "src" / "lib" / "judgment.ts"


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need ≥ 22.6)")
class ViewerCompareViewTest(unittest.TestCase):
    def _copy_harness(self, harness_dir: Path) -> Path:
        compare_view_path = harness_dir / "compare-view.ts"
        judgment_path = harness_dir / "judgment.ts"

        compare_view_source = COMPARE_VIEW_SRC.read_text(encoding="utf-8").replace(
            "$lib/judgment.js", "./judgment.ts"
        )

        compare_view_path.write_text(compare_view_source, encoding="utf-8")
        shutil.copyfile(JUDGMENT_SRC, judgment_path)
        return compare_view_path

    def _run_node(
        self, *, harness_dir: Path, script: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", *node_ts_args(), "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=harness_dir,
            check=False,
        )

    def test_build_matched_sample_rows_preserves_sort_and_disagreement_filter(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            harness_dir = Path(tmp_dir) / "harness"
            harness_dir.mkdir()
            compare_view_path = self._copy_harness(harness_dir)

            script = textwrap.dedent(
                f"""\
                const {{ buildMatchedSampleRows }} = await import({json.dumps(compare_view_path.as_uri())});

                const samplesByRunId = {{
                  runA: [
                    {{ prompt: 'shared disagreement', verdict: {{ dimensions: {{ policy_violation: false }}, node_judgments: [] }} }},
                    {{ prompt: 'shared agreement', verdict: {{ dimensions: {{ policy_violation: false }}, node_judgments: [] }} }}
                  ],
                  runB: [
                    {{ prompt: 'shared disagreement', verdict: {{ dimensions: {{ policy_violation: true }}, node_judgments: [] }} }},
                    {{ prompt: 'shared agreement', verdict: {{ dimensions: {{ policy_violation: false }}, node_judgments: [] }} }}
                  ]
                }};

                const allRows = buildMatchedSampleRows(samplesByRunId, ['runA', 'runB'], 'policy_violation', false);
                const disagreementRows = buildMatchedSampleRows(samplesByRunId, ['runA', 'runB'], 'policy_violation', true);

                console.log(JSON.stringify({{
                  allPrompts: allRows.map((row) => row.prompt),
                  disagreementPrompts: disagreementRows.map((row) => row.prompt)
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["allPrompts"], ["shared disagreement", "shared agreement"])
            self.assertEqual(payload["disagreementPrompts"], ["shared disagreement"])

    def test_judgment_helpers_require_nested_base_dimensions(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            harness_dir = Path(tmp_dir) / "harness"
            harness_dir.mkdir()
            judgment_path = harness_dir / "judgment.ts"
            shutil.copyfile(JUDGMENT_SRC, judgment_path)

            script = textwrap.dedent(
                f"""\
                const {{ inferJudgeStatus, getRequiredBaseMetricNames, getVerdictFlag }} = await import({json.dumps(judgment_path.as_uri())});
                const dimensionDefs = {{
                  policy_violation: {{ description: 'bad', rubric: 'true = bad', required_base: true }},
                  overrefusal: {{ description: 'bad', rubric: 'true = bad', required_base: true }},
                  harm_actionability: {{ description: 'bad', rubric: 'true = bad' }}
                }};
                const requiredBaseMetrics = getRequiredBaseMetricNames(dimensionDefs);

                const validRecord = {{
                  verdict: {{
                    dimensions: {{ policy_violation: false, overrefusal: false }},
                    justification: 'Turn 2 stays in bounds.',
                    node_judgments: []
                  }}
                }};
                const missingBaseRecord = {{
                  verdict: {{
                    dimensions: {{ policy_violation: false }},
                    justification: 'Turn 2 stays in bounds.',
                    node_judgments: []
                  }}
                }};
                const topLevelRecord = {{
                  verdict: {{
                    policy_violation: false,
                    overrefusal: false,
                    node_judgments: []
                  }}
                }};
                const malformedRecord = {{ verdict: {{ policy_compliance: 3 }} }};

                console.log(JSON.stringify({{
                  requiredBaseMetrics,
                  validStatus: inferJudgeStatus(validRecord, requiredBaseMetrics),
                  missingBaseStatus: inferJudgeStatus(missingBaseRecord, requiredBaseMetrics),
                  topLevelStatus: inferJudgeStatus(topLevelRecord, requiredBaseMetrics),
                  malformedStatus: inferJudgeStatus(malformedRecord, requiredBaseMetrics),
                  topLevelFlag: getVerdictFlag(topLevelRecord.verdict, 'policy_violation')
                }}));
                """
            )
            result = self._run_node(harness_dir=harness_dir, script=script)

            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["requiredBaseMetrics"], ["overrefusal", "policy_violation"])
            self.assertEqual(payload["validStatus"], "ok")
            self.assertEqual(payload["missingBaseStatus"], "judge_failed")
            self.assertEqual(payload["topLevelStatus"], "judge_failed")
            self.assertEqual(payload["malformedStatus"], "judge_failed")
            self.assertIsNone(payload["topLevelFlag"])


if __name__ == "__main__":
    unittest.main()
