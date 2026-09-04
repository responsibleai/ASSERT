# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import subprocess
import textwrap
import unittest
from pathlib import Path

from tests.node_runner import node_supports_ts, node_ts_args


ROOT = Path(__file__).resolve().parents[1]
TOKEN_USAGE_SRC = ROOT / "viewer" / "src" / "lib" / "token-usage.ts"
RUN_PAGE_SRC = (
    ROOT
    / "viewer"
    / "src"
    / "routes"
    / "suite"
    / "[suite_id]"
    / "[run_id]"
    / "+page.svelte"
)
EXPORT_PAGE_SRC = ROOT / "viewer" / "src" / "lib" / "export" / "ExportPage.svelte"
NEW_PAGE_SRC = ROOT / "viewer" / "src" / "routes" / "new" / "+page.svelte"
TOKEN_SUMMARY_SRC = (
    ROOT / "viewer" / "src" / "lib" / "components" / "TokenUsageSummary.svelte"
)
ESTIMATE_ROUTE_SRC = (
    ROOT
    / "viewer"
    / "src"
    / "routes"
    / "api"
    / "runs"
    / "estimate"
    / "+server.ts"
)


class ViewerTokenUsageWiringTest(unittest.TestCase):
    def test_summary_is_wired_into_run_and_export_views(self) -> None:
        for path in (RUN_PAGE_SRC, EXPORT_PAGE_SRC):
            source = path.read_text(encoding="utf-8")
            self.assertIn("TokenUsageSummary", source)
            self.assertIn("tokenUsage={data.tokenUsage}", source)

    def test_wizard_shows_estimate_before_submit(self) -> None:
        page_source = NEW_PAGE_SRC.read_text(encoding="utf-8")
        route_source = ESTIMATE_ROUTE_SRC.read_text(encoding="utf-8")

        self.assertIn("Estimated token usage", page_source)
        self.assertIn("fetch('/api/runs/estimate'", page_source)
        self.assertIn("estimateAssertAiRun", route_source)
        self.assertIn("request.signal", route_source)
        self.assertIn("No provider calls are", route_source)

    def test_completed_token_summary_is_compact(self) -> None:
        source = TOKEN_SUMMARY_SRC.read_text(encoding="utf-8")

        self.assertIn("<details", source)
        self.assertIn("In range", source)
        self.assertIn("actual.missingUsageCalls > 0", source)
        self.assertIn("'Reported' : 'Actual'", source)
        self.assertGreaterEqual(
            source.count("tokenAccuracyUnavailableMessage"),
            3,
        )
        self.assertNotIn("md:grid-cols-3", source)
        self.assertNotIn("text-2xl", source)


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need >= 22.6)")
class ViewerTokenUsageFormattingTest(unittest.TestCase):
    def test_formats_comparison_and_range_states(self) -> None:
        script = textwrap.dedent(
            f"""\
            const helpers = await import({json.dumps(TOKEN_USAGE_SRC.as_uri())});
            console.log(JSON.stringify({{
              small: helpers.formatTokenCount(999),
              thousands: helpers.formatTokenCount(3089),
              millions: helpers.formatTokenCount(1250000),
              lower: helpers.formatActualVsEstimate(-0.179),
              higher: helpers.formatActualVsEstimate(0.072),
              matched: helpers.formatActualVsEstimate(0),
              matchedSentence: helpers.actualVsEstimateSentence(0),
              within: helpers.actualIsWithinEstimate(2536, {{
                lowerBoundTokens: 2162,
                upperBoundTokens: 4016
              }}),
              outside: helpers.actualIsWithinEstimate(4500, {{
                lowerBoundTokens: 2162,
                upperBoundTokens: 4016
              }}),
              incomplete: helpers.tokenAccuracyUnavailableMessage(
                'provider_usage_incomplete',
                0.5
              ),
              stage: helpers.tokenStageLabel('test_set')
            }}));
            """
        )
        result = subprocess.run(
            ["node", *node_ts_args(), "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=ROOT / "viewer",
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["small"], "999")
        self.assertEqual(payload["thousands"], "3.1K")
        self.assertEqual(payload["millions"], "1.3M")
        self.assertEqual(payload["lower"], "17.9% lower")
        self.assertEqual(payload["higher"], "7.2% higher")
        self.assertEqual(payload["matched"], "Matched estimate")
        self.assertEqual(
            payload["matchedSentence"],
            "Actual usage matched the pre-run estimate.",
        )
        self.assertTrue(payload["within"])
        self.assertFalse(payload["outside"])
        self.assertEqual(
            payload["incomplete"],
            "Complete usage was reported for 50.0% of calls.",
        )
        self.assertEqual(payload["stage"], "Test set")


if __name__ == "__main__":
    unittest.main()
