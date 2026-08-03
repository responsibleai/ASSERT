from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from assert_ai import runner
from scripts import regression_test as rt


class FrozenUpstreamConfigTest(unittest.TestCase):
    def test_treatment_disables_upstream_stages(self) -> None:
        source = rt.REPO_ROOT / "tests" / "regression" / "config_safety.yaml"
        with tempfile.TemporaryDirectory() as tmp:
            rendered = rt._render_config(
                source,
                suite_name="science-treatment",
                run_label="treatment",
                test_set_size=20,
                judge_model="azure/gpt-5.4",
                upstream_model="azure/gpt-5.4",
                target_dir=Path(tmp),
                freeze_upstream=True,
                target_callable_override=rt.REGRESSION_ASYNC_TARGET,
            )
            cfg = yaml.safe_load(rendered.read_text(encoding="utf-8"))
            ctx = runner._load_context(config=str(rendered))

        pipeline = cfg["pipeline"]
        self.assertFalse(pipeline["systematize"]["enabled"])
        self.assertFalse(pipeline["test_set"]["enabled"])
        self.assertTrue(pipeline["inference"].get("enabled", True))
        self.assertTrue(pipeline["judge"].get("enabled", True))
        self.assertEqual(
            pipeline["inference"]["target"]["callable"],
            rt.REGRESSION_ASYNC_TARGET,
        )
        self.assertEqual(pipeline["inference"]["tool_timeout_s"], 180.0)
        enabled_stages = [
            name for name, raw_cfg in ctx["stages"] if raw_cfg.get("enabled", True)
        ]
        self.assertEqual(enabled_stages, ["inference", "judge"])


class SharedArtifactCopyTest(unittest.TestCase):
    def test_copies_required_and_optional_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            for name in rt.SHARED_UPSTREAM_FILES:
                (source / name).write_text(name, encoding="utf-8")

            rt._copy_shared_upstream_artifacts(source, destination)

            self.assertEqual(
                {path.name for path in destination.iterdir()},
                set(rt.SHARED_UPSTREAM_FILES),
            )

    def test_rejects_incomplete_shared_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "taxonomy.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "test_set.jsonl"):
                rt._copy_shared_upstream_artifacts(source, root / "destination")

    def test_rejects_nonidentical_paired_test_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            treatment = root / "treatment"
            baseline.mkdir()
            treatment.mkdir()
            for directory in (baseline, treatment):
                (directory / "taxonomy.json").write_text("{}", encoding="utf-8")
            (baseline / "test_set.jsonl").write_text('{"id":"a"}\n', encoding="utf-8")
            (treatment / "test_set.jsonl").write_text('{"id":"b"}\n', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "identical test_set.jsonl"):
                rt._assert_shared_upstream_identical(baseline, treatment)


class PairCountTest(unittest.TestCase):
    def test_counts_only_common_test_case_ids(self) -> None:
        baseline = [{"test_case_id": "a"}, {"test_case_id": "b"}]
        treatment = [{"test_case_id": "b"}, {"test_case_id": "c"}]
        self.assertEqual(rt._paired_test_case_count(baseline, treatment), 1)


class CombinedReportTest(unittest.TestCase):
    def test_uses_highest_config_decision_and_namespaces_results(self) -> None:
        per_config = {
            "config_safety.yaml": {
                "report": {
                    "decision": {"decision": "PASS", "reasons": ["no regression"]},
                    "results": [
                        {
                            "metric_name": "permissible_policy_violation_rate",
                        }
                    ],
                    "baseline_metrics": {
                        "permissible_policy_violation_rate": {"value": 0.0}
                    },
                    "treatment_metrics": {
                        "permissible_policy_violation_rate": {"value": 0.0}
                    },
                }
            },
            "config_quality.yaml": {
                "report": {
                    "decision": {
                        "decision": "WARN",
                        "reasons": ["not-permissible violation rate rose"],
                    },
                    "results": [
                        {
                            "metric_name": "not_permissible_policy_violation_rate",
                        }
                    ],
                    "baseline_metrics": {
                        "not_permissible_policy_violation_rate": {"value": 0.0}
                    },
                    "treatment_metrics": {
                        "not_permissible_policy_violation_rate": {"value": 0.1}
                    },
                }
            },
        }

        report = rt._combine_config_reports(
            per_config,
            alpha=0.01,
            test_set_size=100,
        )

        self.assertEqual(report["decision"]["decision"], "WARN")
        self.assertEqual(
            report["decision"]["reasons"],
            ["config_quality.yaml: not-permissible violation rate rose"],
        )
        self.assertEqual(
            [result["config"] for result in report["results"]],
            ["config_safety.yaml", "config_quality.yaml"],
        )


if __name__ == "__main__":
    unittest.main()
