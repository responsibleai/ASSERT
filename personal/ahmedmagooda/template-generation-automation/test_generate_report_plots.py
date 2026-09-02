from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


MODULE_PATH = Path(__file__).with_name("generate_report_plots.py")
SPEC = importlib.util.spec_from_file_location("generate_report_plots", MODULE_PATH)
assert SPEC and SPEC.loader
plots = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = plots
SPEC.loader.exec_module(plots)


class SourceDiscoveryTests(unittest.TestCase):
    def test_single_source_reports_all_harms(self) -> None:
        sources = plots.load_sources(["templates-by-skill-v1"])

        harms = plots.select_report_harms(sources)

        self.assertEqual(harms, tuple(sorted(sources[0].harms)))
        rows = plots.build_rows(sources, harms)
        self.assertEqual(
            set(rows),
            {(sources[0].key, harm) for harm in harms},
        )
        self.assertEqual(
            rows[(sources[0].key, "violent_content")]["run_counts"],
            {"run-1": 4, "run-2": 3, "run-3": 4},
        )

    def test_two_sources_report_only_common_harms(self) -> None:
        sources = plots.load_sources(
            ["templates-by-skill-v1", "templates-by-skill-v2"]
        )

        harms = plots.select_report_harms(sources)

        self.assertEqual(harms, tuple(sorted(set(sources[0].harms) & set(sources[1].harms))))
        self.assertIn("violent_content", harms)
        self.assertNotIn("child_safety", harms)
        rows = plots.build_rows(sources, harms)
        self.assertEqual(len(rows), len(harms) * 2)

    def test_csv_preserves_dynamic_runs_and_weak_points(self) -> None:
        sources = plots.load_sources(["templates-by-skill-v1"])
        harms = plots.select_report_harms(sources)
        rows = plots.build_rows(sources, harms)
        key = (sources[0].key, "violent_content")
        rows[key]["run_counts"] = {"baseline": 2, "release-candidate": 3}
        rows[key]["coverage_weak_points"] = [
            {
                "gap_id": "manifestation_type",
                "priority": "high",
                "suggested_dimension": {
                    "name": "manifestation_type",
                    "description": "The manifestation under test.",
                    "levels": [
                        {"name": "first", "definition": "First type."},
                        {"name": "second", "definition": "Second type."},
                    ],
                },
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "data.csv"
            plots.write_comparison_data(rows, output_path)
            with output_path.open(encoding="utf-8") as handle:
                saved_rows = list(csv.DictReader(handle))

        violent = next(row for row in saved_rows if row["harm"] == "violent_content")
        self.assertEqual(violent["run_counts"], "baseline=2/release-candidate=3")
        weak_points = json.loads(violent["coverage_weak_points_json"])
        self.assertEqual(weak_points[0]["suggested_dimension"]["name"], "manifestation_type")


class MergedConfigTests(unittest.TestCase):
    def test_creates_representative_union_and_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "experiment"
            harm_dir = root / "example_harm"
            run_dimensions = {
                "baseline": [
                    {"name": "severity", "description": "Severity axis."},
                    {"name": "audience", "description": "Audience axis."},
                ],
                "retry": [
                    {"name": "harm_intensity", "description": "Duplicate severity."},
                    {"name": "context", "description": "Context axis."},
                ],
            }
            dimension_records = []
            for run_number, (run, dimensions) in enumerate(run_dimensions.items(), 1):
                run_dir = harm_dir / run
                run_dir.mkdir(parents=True)
                config = {
                    "suite": f"example-{run}",
                    "run": run,
                    "behavior": {"preset": "example_harm"},
                    "pipeline": {
                        "test_set": {"stratify": {"dimensions": dimensions}}
                    },
                }
                (run_dir / "eval_config.yaml").write_text(
                    yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
                )
                for index, dimension in enumerate(dimensions, 1):
                    dimension_records.append(
                        {
                            "dimension_id": f"example_harm/{run}/{index}",
                            "harm": "example_harm",
                            "run_number": run_number,
                            "index": index,
                            "raw_name": dimension["name"],
                            "source_config": f"example_harm/{run}/eval_config.yaml",
                        }
                    )

            groups = [
                {
                    "representative_dimension_id": "example_harm/baseline/1",
                    "representative_name": "severity",
                },
                {
                    "representative_dimension_id": "example_harm/baseline/2",
                    "representative_name": "audience",
                },
                {
                    "representative_dimension_id": "example_harm/retry/2",
                    "representative_name": "context",
                },
            ]
            metrics = {
                "global_unique_metrics": {
                    "example_harm": {"unique_groups": groups}
                },
                "dimensions": dimension_records,
            }
            source = plots.Source(
                key="source-1",
                label="experiment",
                root=root,
                metrics_path=root / "analysis" / "metrics.json",
                metrics=metrics,
                harms=("example_harm",),
            )

            first = plots.generate_merged_config(source, "example_harm")
            merged_path = harm_dir / "merged" / "eval_config.yaml"
            first_text = merged_path.read_text(encoding="utf-8")
            merged = yaml.safe_load(first_text)
            second = plots.generate_merged_config(source, "example_harm")
            second_text = merged_path.read_text(encoding="utf-8")

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first_text, second_text)
        self.assertEqual(merged["run"], "merged")
        self.assertEqual(
            [
                dimension["name"]
                for dimension in merged["pipeline"]["test_set"]["stratify"]["dimensions"]
            ],
            ["severity", "audience", "context"],
        )


class ReportTests(unittest.TestCase):
    def test_single_source_report_contains_metrics_and_merged_configs(self) -> None:
        sources = plots.load_sources(["templates-by-skill-v1"])
        harms = plots.select_report_harms(sources)
        rows = plots.build_rows(sources, harms)
        merged_results = [
            {
                "source_label": sources[0].label,
                "harm": harm,
                "dimension_count": rows[(sources[0].key, harm)]["unique_dimensions"],
                "created": False,
                "path": sources[0].root / harm / "merged" / "eval_config.yaml",
            }
            for harm in harms
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "report.md"
            plots.write_report(rows, merged_results, output_path)
            report = output_path.read_text(encoding="utf-8")

        self.assertIn("# Harm-template experiment report", report)
        self.assertIn("## Expected adversarial pressure", report)
        self.assertIn("## Canonical harm scenario-space coverage", report)
        self.assertIn("## Merged configurations", report)
        for harm in harms:
            self.assertIn(plots.harm_label(harm), report)


if __name__ == "__main__":
    unittest.main()