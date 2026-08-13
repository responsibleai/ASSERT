# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assert_ai.runner import _log_run_headline


def _score_row() -> dict:
    return {
        "type": "prompt",
        "test_case_id": "prompt-1",
        "dimensions": {"behavior": "allowed"},
        "target": "target-model",
        "judge_model": "judge-model",
        "judge_status": "ok",
        "verdict": {
            "dimensions": {
                "policy_violation": True,
                "overrefusal": True,
            },
            "node_judgments": [
                {
                    "node_index": 0,
                    "node_name": "allowed",
                    "relevant": True,
                    "violated": True,
                },
                {
                    "node_index": 1,
                    "node_name": "blocked",
                    "relevant": True,
                    "violated": False,
                },
            ],
        },
    }


def _write_run(root: Path, *, with_taxonomy: bool, stale_judgments: bool = False) -> Path:
    suite_dir = root / "suite"
    run_dir = suite_dir / "run-1"
    run_dir.mkdir(parents=True)
    score_row = _score_row()
    if stale_judgments:
        for judgment in score_row["verdict"]["node_judgments"]:
            judgment["node_index"] = 100 + int(judgment["node_index"])
            judgment["node_name"] = f"stale-{judgment['node_name']}"
    (run_dir / "scores.jsonl").write_text(json.dumps(score_row) + "\n", encoding="utf-8")
    if with_taxonomy:
        (suite_dir / "taxonomy.json").write_text(
            json.dumps(
                {
                    "behavior_categories": [
                        {"name": "allowed", "permissible": True},
                        {"name": "blocked", "permissible": False},
                    ]
                }
            ),
            encoding="utf-8",
        )
    return run_dir


class RunHeadlineTest(unittest.TestCase):
    def test_taxonomy_run_logs_permissibility_split(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = _write_run(Path(tmp_dir), with_taxonomy=True)
            with self.assertLogs("assert_ai.runner", level="INFO") as captured:
                _log_run_headline(run_dir)

        output = "\n".join(captured.output)
        self.assertIn("Impermissible behavior violated rate: prompt 0.0%", output)
        self.assertIn("Permissible behavior violated rate: prompt 100.0%", output)
        self.assertNotIn("Policy violation rate", output)
        self.assertNotIn("Overrefusal rate", output)

    def test_run_without_taxonomy_keeps_legacy_headline(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = _write_run(Path(tmp_dir), with_taxonomy=False)
            with self.assertLogs("assert_ai.runner", level="INFO") as captured:
                _log_run_headline(run_dir)

        output = "\n".join(captured.output)
        self.assertIn("Policy violation rate: prompt 100.0%", output)
        self.assertIn("Overrefusal rate: prompt 100.0%", output)
        self.assertNotIn("Impermissible behavior violated rate", output)
        self.assertNotIn("Permissible behavior violated rate", output)

    def test_run_with_stale_judgments_keeps_legacy_headline(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = _write_run(Path(tmp_dir), with_taxonomy=True, stale_judgments=True)
            with self.assertLogs("assert_ai.runner", level="INFO") as captured:
                _log_run_headline(run_dir)

        output = "\n".join(captured.output)
        self.assertIn("Policy violation rate: prompt 100.0%", output)
        self.assertIn("Overrefusal rate: prompt 100.0%", output)
        self.assertNotIn("Impermissible behavior violated rate", output)
        self.assertNotIn("Permissible behavior violated rate", output)


if __name__ == "__main__":
    unittest.main()
