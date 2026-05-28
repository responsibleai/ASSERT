# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import unittest

from assert_eval.analysis.test_set_metrics import _prompt_test_case_text


class TestSetMetricsAnalysisTest(unittest.TestCase):
    def test_prompt_test_case_text_ignores_scenario_rows(self) -> None:
        self.assertEqual(
            _prompt_test_case_text({"type": "prompt", "seed": {"description": "seed"}}),
            "seed",
        )
        self.assertIsNone(
            _prompt_test_case_text({"type": "scenario", "seed": {"description": "hidden brief"}})
        )


if __name__ == "__main__":
    unittest.main()
