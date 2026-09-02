from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import matplotlib
import numpy as np
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_COMPARISON_OUTPUT_DIR = SCRIPT_DIR / "template_comparison_reports"
SOURCE_COLORS = ("#315D80", "#D4553F")
ADVERSARIAL_COLORS = ("#D8E3E8", "#86A9B7", "#E1B866", "#D9784A", "#9E3D36")
ADVERSARIAL_RANGES = ("0-20", "21-40", "41-60", "61-80", "81-100")

BACKGROUND = "#FBFAF6"
GRID = "#D9D6CD"
TEXT = "#17212B"
MUTED = "#5F6872"

CSV_FIELDS = (
    "source_key",
    "source_label",
    "source_report",
    "embedded_experiment",
    "methodology_version",
    "harm",
    "source_configs",
    "total_dimensions",
    "repeated_removed",
    "unique_dimensions",
    "relevant_unique_dimensions",
    "perfect_relevance_dimensions",
    "uniqueness_rate",
    "relevant_unique_rate",
    "perfect_relevance_rate",
    "run_counts",
    "mean_dimensions",
    "population_variance",
    "sample_variance",
    "embedding_diversity",
    "llm_pair_diversity",
    "llm_direct_diversity",
    "relevance_mean",
    "relevance_min",
    "within_run_redundant_pairs",
    "adversarial_mean",
    "adversarial_population_variance",
    "adversarial_sample_variance",
    "adversarial_min",
    "adversarial_max",
    "adversarial_0_20_count",
    "adversarial_0_20_rate",
    "adversarial_21_40_count",
    "adversarial_21_40_rate",
    "adversarial_41_60_count",
    "adversarial_41_60_rate",
    "adversarial_61_80_count",
    "adversarial_61_80_rate",
    "adversarial_81_100_count",
    "adversarial_81_100_rate",
    "coverage_score",
    "coverage_rationale",
    "coverage_gap_count",
    "coverage_high_priority_gap_count",
    "coverage_medium_priority_gap_count",
    "coverage_low_priority_gap_count",
    "coverage_weak_points_json",
)


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    root: Path
    metrics_path: Path
    metrics: dict[str, Any]
    harms: tuple[str, ...]


def resolve_experiment_dir(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        direct = (Path.cwd() / candidate).resolve()
        candidate = direct if direct.is_dir() else (SCRIPT_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.is_dir():
        raise FileNotFoundError(f"Experiment directory does not exist: {candidate}")
    return candidate


def discover_harms(experiment_dir: Path) -> tuple[str, ...]:
    harms = tuple(
        path.name
        for path in sorted(experiment_dir.iterdir())
        if path.is_dir() and path.name != "analysis"
    )
    if not harms:
        raise ValueError(f"No harm directories found in {experiment_dir}")
    return harms


def load_sources(experiment_dirs: Sequence[str | Path]) -> list[Source]:
    if len(experiment_dirs) not in (1, 2):
        raise ValueError("Expected one or two experiment directories")
    roots = [resolve_experiment_dir(value) for value in experiment_dirs]
    if len(set(roots)) != len(roots):
        raise ValueError("Experiment directories must be distinct")

    labels = [root.name for root in roots]
    if len(labels) == 2 and labels[0] == labels[1]:
        labels = [f"{label} ({index})" for index, label in enumerate(labels, 1)]

    sources = []
    for index, (root, label) in enumerate(zip(roots, labels, strict=True), 1):
        metrics_path = root / "analysis" / "metrics.json"
        if not metrics_path.is_file():
            raise FileNotFoundError(f"Missing evaluation metrics: {metrics_path}")
        sources.append(
            Source(
                key=f"source-{index}",
                label=label,
                root=root,
                metrics_path=metrics_path,
                metrics=json.loads(metrics_path.read_text(encoding="utf-8")),
                harms=discover_harms(root),
            )
        )
    return sources


def select_report_harms(sources: Sequence[Source]) -> tuple[str, ...]:
    harms = set(sources[0].harms)
    for source in sources[1:]:
        harms.intersection_update(source.harms)
    if not harms:
        raise ValueError("The experiment directories have no harms to report")

    for source in sources:
        missing = {
            section: sorted(harms - set(source.metrics.get(section, {})))
            for section in ("harm_results", "global_unique_metrics", "run_results")
        }
        missing = {section: names for section, names in missing.items() if names}
        if missing:
            raise ValueError(
                f"Metrics for {source.root} do not cover all report harms: {missing}. "
                "Run evaluate_template_generation.py first."
            )
    return tuple(sorted(harms))


def build_rows(
    sources: Sequence[Source], harms: Sequence[str]
) -> dict[tuple[str, str], dict[str, object]]:
    rows: dict[tuple[str, str], dict[str, object]] = {}
    for source in sources:
        metrics = source.metrics
        for harm in harms:
            harm_result = metrics["harm_results"][harm]
            unique_result = metrics["global_unique_metrics"][harm]
            run_result = metrics["run_results"][harm]
            perfect_relevance = next(
                item
                for item in unique_result["relevance_threshold_metrics"]
                if item["threshold_percent"] == 100
            )
            adversarial = harm_result["adversarial"]
            distribution = {
                item["range"]: item for item in adversarial["distribution"]
            }
            if set(distribution) != set(ADVERSARIAL_RANGES):
                raise ValueError(
                    f"Unexpected adversarial ranges for {source.label}/{harm}: "
                    f"{sorted(distribution)}"
                )
            weak_points = list(harm_result["coverage"].get("weak_coverage_points", []))
            run_counts = {
                run: int(values["dimension_count"])
                for run, values in run_result.items()
            }
            rows[(source.key, harm)] = {
                "source_key": source.key,
                "source_label": source.label,
                "source_root": source.root,
                "source_report_path": source.metrics_path.with_name("report.md"),
                "embedded_experiment": metrics["methodology"]["experiment_dir"],
                "methodology_version": metrics["methodology"]["version"],
                "harm": harm,
                "source_configs": len(run_counts),
                "total_dimensions": int(unique_result["total_dimension_count"]),
                "repeated_removed": int(unique_result["repeated_dimension_count_removed"]),
                "unique_dimensions": int(unique_result["unique_dimension_count"]),
                "relevant_unique_dimensions": int(
                    unique_result["relevant_unique_dimension_count"]
                ),
                "perfect_relevance_dimensions": int(perfect_relevance["exact_score_count"]),
                "uniqueness_rate": float(unique_result["unique_over_total_ratio"]),
                "relevant_unique_rate": float(
                    unique_result["relevant_unique_over_unique_ratio"]
                ),
                "perfect_relevance_rate": float(
                    perfect_relevance["exact_score_ratio_over_unique"]
                ),
                "run_counts": run_counts,
                "mean_dimensions": float(harm_result["average_dimension_count"]),
                "population_variance": float(
                    harm_result["population_variance_dimension_count"]
                ),
                "sample_variance": float(
                    harm_result["sample_variance_dimension_count"]
                ),
                "embedding_diversity": float(
                    harm_result["union_embedding_pairwise_diversity"]
                ),
                "llm_pair_diversity": float(
                    harm_result["union_llm_pairwise_diversity"]
                ),
                "llm_direct_diversity": float(
                    harm_result["union_llm_direct_diversity"]
                ),
                "relevance_mean": float(harm_result["relevance"]["mean"]),
                "relevance_min": int(harm_result["relevance"]["minimum"]),
                "within_run_redundant_pairs": sum(
                    int(values["redundant_pair_count"])
                    for values in run_result.values()
                ),
                "adversarial_mean": float(adversarial["mean"]),
                "adversarial_population_variance": float(
                    adversarial["population_variance"]
                ),
                "adversarial_sample_variance": float(adversarial["sample_variance"]),
                "adversarial_min": int(adversarial["minimum"]),
                "adversarial_max": int(adversarial["maximum"]),
                "adversarial_distribution_counts": tuple(
                    int(distribution[score_range]["count"])
                    for score_range in ADVERSARIAL_RANGES
                ),
                "adversarial_distribution_rates": tuple(
                    float(distribution[score_range]["ratio"])
                    for score_range in ADVERSARIAL_RANGES
                ),
                "coverage_score": int(harm_result["coverage"]["score"]),
                "coverage_rationale": str(harm_result["coverage"]["rationale"]),
                "coverage_weak_points": weak_points,
                "coverage_gap_count": len(weak_points),
                "coverage_high_priority_gap_count": sum(
                    point["priority"] == "high" for point in weak_points
                ),
                "coverage_medium_priority_gap_count": sum(
                    point["priority"] == "medium" for point in weak_points
                ),
                "coverage_low_priority_gap_count": sum(
                    point["priority"] == "low" for point in weak_points
                ),
            }
    return rows


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def generate_merged_config(source: Source, harm: str) -> dict[str, object]:
    output_path = source.root / harm / "merged" / "eval_config.yaml"
    groups = source.metrics["global_unique_metrics"][harm].get("unique_groups")
    if not groups:
        raise ValueError(f"No unique dimension groups found for {source.label}/{harm}")
    if output_path.exists():
        existing = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        existing_dimensions = existing["pipeline"]["test_set"]["stratify"][
            "dimensions"
        ]
        return {
            "source_label": source.label,
            "harm": harm,
            "path": output_path,
            "created": False,
            "dimension_count": len(existing_dimensions),
        }

    records = {
        item["dimension_id"]: item
        for item in source.metrics.get("dimensions", [])
        if item.get("harm") == harm
    }
    config_cache: dict[Path, dict[str, Any]] = {}
    merged_dimensions = []
    for group in groups:
        representative_id = group["representative_dimension_id"]
        if representative_id not in records:
            raise ValueError(f"Missing representative record {representative_id!r}")
        record = records[representative_id]
        source_path = source.root / record["source_config"]
        if source_path not in config_cache:
            config_cache[source_path] = yaml.safe_load(
                source_path.read_text(encoding="utf-8")
            )
        dimensions = config_cache[source_path]["pipeline"]["test_set"]["stratify"]
        dimensions = dimensions["dimensions"]
        index = int(record.get("index") or representative_id.rsplit("/", 1)[1])
        try:
            dimension = copy.deepcopy(dimensions[index - 1])
        except IndexError as exc:
            raise ValueError(
                f"Representative index {index} is absent from {source_path}"
            ) from exc
        if dimension.get("name") != group.get("representative_name"):
            raise ValueError(
                f"Representative mismatch for {representative_id}: config has "
                f"{dimension.get('name')!r}, metrics have "
                f"{group.get('representative_name')!r}"
            )
        merged_dimensions.append(dimension)

    run_configs = sorted(
        directory / "eval_config.yaml"
        for directory in (source.root / harm).iterdir()
        if directory.is_dir() and directory.name != "merged"
    )
    base_path = next((path for path in run_configs if path.is_file()), None)
    if base_path is None:
        raise FileNotFoundError(f"No run config found for {source.label}/{harm}")
    merged_config = copy.deepcopy(yaml.safe_load(base_path.read_text(encoding="utf-8")))
    merged_config["suite"] = f"{_slug(harm)}-{_slug(source.root.name)}-merged"
    merged_config["run"] = "merged"
    merged_config["pipeline"]["test_set"]["stratify"][
        "dimensions"
    ] = merged_dimensions

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(merged_config, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    return {
        "source_label": source.label,
        "harm": harm,
        "path": output_path,
        "created": True,
        "dimension_count": len(merged_dimensions),
    }


def generate_merged_configs(
    sources: Sequence[Source], harms: Sequence[str]
) -> list[dict[str, object]]:
    return [
        generate_merged_config(source, harm)
        for source in sources
        for harm in harms
    ]


def report_axes(
    rows: dict[tuple[str, str], dict[str, object]],
) -> tuple[list[str], list[str], dict[str, str], dict[str, str]]:
    source_keys = list(dict.fromkeys(source for source, _ in rows))
    harms = [harm for source, harm in rows if source == source_keys[0]]
    labels = {
        source: str(rows[(source, harms[0])]["source_label"])
        for source in source_keys
    }
    colors = {
        source: SOURCE_COLORS[index] for index, source in enumerate(source_keys)
    }
    return source_keys, harms, labels, colors


def write_comparison_data(
    rows: dict[tuple[str, str], dict[str, object]], output_path: Path
) -> None:
    source_keys, harms, _, _ = report_axes(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for source in source_keys:
            for harm in harms:
                source_row = rows[(source, harm)]
                serialized = {
                    field: source_row[field]
                    for field in CSV_FIELDS
                    if field in source_row
                }
                serialized["source_report"] = str(source_row["source_report_path"])
                serialized["run_counts"] = "/".join(
                    f"{run}={count}"
                    for run, count in source_row["run_counts"].items()
                )
                serialized["coverage_weak_points_json"] = json.dumps(
                    source_row["coverage_weak_points"], separators=(",", ":")
                )
                for score_range, count, rate in zip(
                    ADVERSARIAL_RANGES,
                    source_row["adversarial_distribution_counts"],
                    source_row["adversarial_distribution_rates"],
                    strict=True,
                ):
                    suffix = score_range.replace("-", "_")
                    serialized[f"adversarial_{suffix}_count"] = count
                    serialized[f"adversarial_{suffix}_rate"] = rate
                writer.writerow(serialized)
    print(f"wrote {output_path}")


def aggregate_sources(
    rows: dict[tuple[str, str], dict[str, object]],
) -> dict[str, dict[str, float]]:
    source_keys, harms, _, _ = report_axes(rows)
    totals: dict[str, dict[str, float]] = {}
    for source in source_keys:
        source_rows = [rows[(source, harm)] for harm in harms]
        total = sum(int(row["total_dimensions"]) for row in source_rows)
        unique = sum(int(row["unique_dimensions"]) for row in source_rows)
        relevant = sum(int(row["relevant_unique_dimensions"]) for row in source_rows)
        perfect = sum(int(row["perfect_relevance_dimensions"]) for row in source_rows)
        totals[source] = {
            "source_configs": sum(int(row["source_configs"]) for row in source_rows),
            "dimensions": total,
            "repeated": sum(int(row["repeated_removed"]) for row in source_rows),
            "unique": unique,
            "relevant": relevant,
            "perfect": perfect,
            "unique_rate": unique / total * 100,
            "relevant_rate": relevant / unique * 100,
            "perfect_rate": perfect / unique * 100,
            "embedding": sum(float(row["embedding_diversity"]) for row in source_rows)
            / len(harms),
            "llm_pair": sum(float(row["llm_pair_diversity"]) for row in source_rows)
            / len(harms),
            "llm_direct": sum(float(row["llm_direct_diversity"]) for row in source_rows)
            / len(harms),
            "relevance": sum(float(row["relevance_mean"]) for row in source_rows)
            / len(harms),
            "adversarial": sum(
                float(row["adversarial_mean"]) * int(row["total_dimensions"])
                for row in source_rows
            )
            / total,
            "coverage": sum(float(row["coverage_score"]) for row in source_rows)
            / len(harms),
        }
    return totals


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    def cell(value: object) -> str:
        return " ".join(str(value).split()).replace("|", "\\|")

    lines = [
        "| " + " | ".join(cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(cell(value) for value in row) + " |" for row in rows
    )
    return "\n".join(lines)


def harm_label(harm: str) -> str:
    return harm.replace("_", " ").replace("-", " ").title()


def _relative_link(target: Path, output_path: Path) -> str:
    return Path(os.path.relpath(target, output_path.parent)).as_posix()


def _metric_values(
    values: dict[str, float],
    source_keys: Sequence[str],
    formatter,
    delta_formatter=None,
) -> list[str]:
    result = [formatter(values[source]) for source in source_keys]
    if len(source_keys) == 2:
        delta = values[source_keys[1]] - values[source_keys[0]]
        result.append((delta_formatter or formatter)(delta))
    return result


def write_report(
    rows: dict[tuple[str, str], dict[str, object]],
    merged_results: Sequence[dict[str, object]],
    output_path: Path,
) -> None:
    source_keys, harms, labels, _ = report_axes(rows)
    totals = aggregate_sources(rows)
    value_headers = [labels[source] for source in source_keys]
    if len(source_keys) == 2:
        value_headers.append(f"Delta ({labels[source_keys[1]]} - {labels[source_keys[0]]})")

    overall_specs = (
        ("Source configs", "source_configs", ".0f", "+.0f"),
        ("Dimension instances", "dimensions", ".0f", "+.0f"),
        ("Repeated dimensions removed", "repeated", ".0f", "+.0f"),
        ("Globally unique dimensions", "unique", ".0f", "+.0f"),
        ("Relevant unique dimensions", "relevant", ".0f", "+.0f"),
        ("Perfect-relevance unique dimensions", "perfect", ".0f", "+.0f"),
        ("Unique / total", "unique_rate", ".1f", "+.1f"),
        ("Relevance@75 / unique", "relevant_rate", ".1f", "+.1f"),
        ("Relevance@100 / unique", "perfect_rate", ".1f", "+.1f"),
        ("Macro embedding diversity", "embedding", ".4f", "+.4f"),
        ("Macro LLM pair diversity", "llm_pair", ".4f", "+.4f"),
        ("Macro LLM direct diversity", "llm_direct", ".4f", "+.4f"),
        ("Macro relevance (0-4)", "relevance", ".4f", "+.4f"),
        ("All-dimension adversarial mean (0-100)", "adversarial", ".4f", "+.4f"),
        ("Macro scenario-space coverage (0-100)", "coverage", ".4f", "+.4f"),
    )
    overall_rows = []
    for label, field, value_format, delta_format in overall_specs:
        values = {source: totals[source][field] for source in source_keys}
        overall_rows.append(
            [label]
            + _metric_values(
                values,
                source_keys,
                lambda value, fmt=value_format: format(value, fmt),
                lambda value, fmt=delta_format: format(value, fmt),
            )
        )

    dimension_rows = []
    relevance_rows = []
    diversity_rows = []
    adversarial_rows = []
    distribution_rows = []
    coverage_rows = []
    run_rows = []
    coverage_details = []
    gap_rows = []
    suggestion_sections = []
    for harm in harms:
        display_harm = harm_label(harm)
        for source in source_keys:
            row = rows[(source, harm)]
            dimension_rows.append(
                [
                    display_harm,
                    labels[source],
                    row["total_dimensions"],
                    row["repeated_removed"],
                    row["unique_dimensions"],
                    row["relevant_unique_dimensions"],
                    row["perfect_relevance_dimensions"],
                ]
            )
            relevance_rows.append(
                [
                    display_harm,
                    labels[source],
                    f"{float(row['uniqueness_rate']) * 100:.1f}%",
                    f"{float(row['relevant_unique_rate']) * 100:.1f}%",
                    f"{float(row['perfect_relevance_rate']) * 100:.1f}%",
                    f"{float(row['relevance_mean']):.4f}",
                    row["relevance_min"],
                ]
            )
            adversarial_rows.append(
                [
                    display_harm,
                    labels[source],
                    f"{float(row['adversarial_mean']):.4f}",
                    f"{float(row['adversarial_population_variance']):.4f}",
                    f"{float(row['adversarial_sample_variance']):.4f}",
                    row["adversarial_min"],
                    row["adversarial_max"],
                ]
            )
            distribution_rows.append(
                [display_harm, labels[source]]
                + [
                    f"{count} ({float(rate) * 100:.1f}%)"
                    for count, rate in zip(
                        row["adversarial_distribution_counts"],
                        row["adversarial_distribution_rates"],
                        strict=True,
                    )
                ]
            )
            run_rows.append(
                [
                    display_harm,
                    labels[source],
                    ", ".join(f"{run}={count}" for run, count in row["run_counts"].items()),
                    f"{float(row['population_variance']):.4f}",
                    f"{float(row['sample_variance']):.4f}",
                    row["within_run_redundant_pairs"],
                ]
            )

        for metric, field in (
            ("Embedding diversity", "embedding_diversity"),
            ("LLM pair diversity", "llm_pair_diversity"),
            ("LLM direct diversity", "llm_direct_diversity"),
        ):
            diversity_rows.append(
                [display_harm, metric]
                + _metric_values(
                    {source: float(rows[(source, harm)][field]) for source in source_keys},
                    source_keys,
                    lambda value: f"{value:.4f}",
                    lambda value: f"{value:+.4f}",
                )
            )
        coverage_rows.append(
            [display_harm]
            + _metric_values(
                {source: float(rows[(source, harm)]["coverage_score"]) for source in source_keys},
                source_keys,
                lambda value: f"{value:.0f}",
                lambda value: f"{value:+.0f}",
            )
        )

        details = [f"### {display_harm}"]
        for source in source_keys:
            row = rows[(source, harm)]
            rationale = " ".join(str(row["coverage_rationale"]).split())
            details.append(f"- **{labels[source]} ({row['coverage_score']}):** {rationale}")
            suggestions = []
            for point in row["coverage_weak_points"]:
                dimension = point["suggested_dimension"]
                suggestions.append(dimension)
                gap_rows.append(
                    [
                        display_harm,
                        labels[source],
                        point.get("priority", ""),
                        point.get("gap_type", ""),
                        point.get("weak_coverage_point", ""),
                        dimension["name"],
                    ]
                )
            if suggestions:
                suggestion_yaml = yaml.safe_dump(
                    {"pipeline": {"test_set": {"stratify": {"dimensions": suggestions}}}},
                    sort_keys=False,
                    allow_unicode=False,
                ).strip()
                suggestion_sections.append(
                    f"### {display_harm} - {labels[source]}\n\n"
                    f"```yaml\n{suggestion_yaml}\n```"
                )
        coverage_details.append("\n\n".join(details))

    source_lines = []
    for source in source_keys:
        row = rows[(source, harms[0])]
        source_lines.append(
            f"- **{labels[source]}:** "
            f"[`{row['embedded_experiment']}`]({_relative_link(Path(row['source_report_path']), output_path)}), "
            f"methodology `{row['methodology_version']}`."
        )
    merged_rows = [
        [
            result["source_label"],
            harm_label(str(result["harm"])),
            result["dimension_count"],
            "created" if result["created"] else "already existed",
            _relative_link(Path(result["path"]), output_path),
        ]
        for result in merged_results
    ]
    gap_text = (
        markdown_table(
            ["Harm", "Source", "Priority", "Gap type", "Weak coverage point", "Suggested dimension"],
            gap_rows,
        )
        + "\n\n"
        + "\n\n".join(suggestion_sections)
        if gap_rows
        else "No structured weak points are present in the current source artifacts."
    )
    comparison = len(source_keys) == 2
    title = "# Harm-template experiment comparison" if comparison else "# Harm-template experiment report"
    intro = (
        "This report compares harms common to both supplied experiment directories."
        if comparison
        else "This report summarizes every harm in the supplied experiment directory."
    )
    report = f"""{title}

{intro} Counts are summed where stated; macro averages are unweighted across
{len(harms)} harm-level judgments.

## Sources

{chr(10).join(source_lines)}

## Overall metrics

{markdown_table(["Measure", *value_headers], overall_rows)}

![Overall metric summary](plots/overall_comparison.png)

## Dimension counts by harm

{markdown_table(
    ["Harm", "Source", "Total", "Repeated removed", "Unique", "Relevant unique", "Perfect relevance"],
    dimension_rows,
)}

![Dimension counts by harm](plots/coverage_by_harm.png)

## Relevance rates

{markdown_table(
    ["Harm", "Source", "Unique / total", "Relevance@75 / unique", "Relevance@100 / unique", "Mean (0-4)", "Minimum"],
    relevance_rows,
)}

![Relevance rates by harm](plots/relevance_rates.png)

## Diversity

{markdown_table(["Harm", "Metric", *value_headers], diversity_rows)}

![Diversity by harm](plots/diversity_and_relevance.png)

## Expected adversarial pressure

These are prospective `0-100` judgments, not observed attack-success rates.

{markdown_table(
    ["Harm", "Source", "Mean", "Population variance", "Sample variance", "Minimum", "Maximum"],
    adversarial_rows,
)}

{markdown_table(["Harm", "Source", *ADVERSARIAL_RANGES], distribution_rows)}

![Adversarial metrics](plots/adversarial_pressure.png)

## Canonical harm scenario-space coverage

{markdown_table(["Harm", *value_headers], coverage_rows)}

![Canonical harm scenario-space coverage](plots/scenario_space_coverage.png)

{chr(10).join(coverage_details)}

## Prioritized weak coverage points

{gap_text}

## Run consistency

{markdown_table(
    ["Harm", "Source", "Run dimension counts", "Population variance", "Sample variance", "Within-run redundant pairs"],
    run_rows,
)}

![Run dimension counts](plots/run_consistency.png)

## Merged configurations

Each generated configuration contains the evaluator-selected representative from
every unique-dimension group. Existing files are left unchanged.

{markdown_table(["Source", "Harm", "Dimensions", "Status", "Path"], merged_rows)}

## Interpretation and limitations

- Adversarial pressure, coverage, diversity, relevance, and duplicate grouping
  include LLM judgments and are not ground truth.
- With few generation runs, run-count variance is descriptive and unstable.
- Merged configurations require human review and schema validation before use.
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"wrote {output_path}")


def configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": BACKGROUND,
            "axes.facecolor": BACKGROUND,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT,
            "axes.titlecolor": TEXT,
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "text.color": TEXT,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
        }
    )


def style_axis(axis: plt.Axes) -> None:
    axis.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.spines["left"].set_color(GRID)
    axis.spines["bottom"].set_color(GRID)


def add_grouped_bars(
    axis: plt.Axes,
    categories: list[str],
    values: dict[str, list[float]],
    source_keys: Sequence[str],
    labels: dict[str, str],
    colors: dict[str, str],
    *,
    formatter,
    title: str,
    ylabel: str = "",
    ylim: tuple[float, float] | None = None,
    raw_labels: dict[str, list[str]] | None = None,
) -> None:
    positions = np.arange(len(categories))
    width = 0.72 / len(source_keys)
    for index, source in enumerate(source_keys):
        offset = (index - (len(source_keys) - 1) / 2) * width
        bars = axis.bar(
            positions + offset,
            values[source],
            width,
            color=colors[source],
            label=labels[source],
        )
        bar_labels = raw_labels[source] if raw_labels else [formatter(value) for value in values[source]]
        axis.bar_label(bars, labels=bar_labels, padding=3, fontsize=8, color=TEXT)
    if ylim is None:
        maximum = max(
            (value for source_values in values.values() for value in source_values),
            default=1,
        )
        ylim = (0, maximum * 1.18 if maximum else 1)
    axis.set_xticks(positions, categories)
    axis.set_ylim(*ylim)
    axis.set_title(title, fontsize=12, fontweight="bold", pad=12)
    axis.set_ylabel(ylabel)
    style_axis(axis)


def save_figure(figure: plt.Figure, plots_dir: Path, filename: str) -> None:
    output_path = plots_dir / filename
    figure.savefig(output_path, dpi=180, bbox_inches="tight", facecolor=BACKGROUND)
    plt.close(figure)
    print(f"wrote {output_path}")


def _finish_multi_axis_figure(
    figure: plt.Figure,
    axes: Sequence[plt.Axes],
    source_count: int,
    title: str,
    plots_dir: Path,
    filename: str,
) -> None:
    handles, legend_labels = axes[0].get_legend_handles_labels()
    figure.legend(
        handles,
        legend_labels,
        loc="lower center",
        ncol=source_count,
        frameon=False,
        bbox_to_anchor=(0.5, -0.07),
    )
    figure.suptitle(title, fontsize=18, fontweight="bold", y=1.03)
    save_figure(figure, plots_dir, filename)


def make_overall_plot(
    rows: dict[tuple[str, str], dict[str, object]], plots_dir: Path
) -> None:
    source_keys, _, labels, colors = report_axes(rows)
    totals = aggregate_sources(rows)
    figure, axes = plt.subplots(1, 3, figsize=(17, 6), constrained_layout=True)
    panels = (
        (
            ["Configs", "Dimensions", "Repeated", "Unique", "Relevant", "Perfect"],
            ("source_configs", "dimensions", "repeated", "unique", "relevant", "perfect"),
            "Counts",
            lambda value: f"{value:.0f}",
            None,
        ),
        (
            ["Unique / total", "Relevant / unique", "Perfect / unique"],
            ("unique_rate", "relevant_rate", "perfect_rate"),
            "Rates",
            lambda value: f"{value:.1f}%",
            (0, 112),
        ),
    )
    for axis, (categories, fields, title, formatter, ylim) in zip(axes[:2], panels, strict=True):
        add_grouped_bars(
            axis,
            categories,
            {source: [totals[source][field] for field in fields] for source in source_keys},
            source_keys,
            labels,
            colors,
            formatter=formatter,
            title=title,
            ylim=ylim,
        )
    fields = ("embedding", "llm_pair", "relevance", "adversarial", "coverage")
    raw = {source: [totals[source][field] for field in fields] for source in source_keys}
    normalized = {
        source: [raw[source][0] * 100, raw[source][1] * 100, raw[source][2] / 4 * 100, raw[source][3], raw[source][4]]
        for source in source_keys
    }
    add_grouped_bars(
        axes[2],
        ["Embedding", "LLM pair", "Relevance", "Adversarial", "Coverage"],
        normalized,
        source_keys,
        labels,
        colors,
        formatter=lambda value: f"{value:.1f}%",
        title="Scores normalized to scale",
        ylim=(0, 108),
        raw_labels={source: [f"{value:.4f}" for value in raw[source]] for source in source_keys},
    )
    for axis in axes:
        axis.tick_params(axis="x", labelrotation=20)
    _finish_multi_axis_figure(figure, axes, len(source_keys), "Overall metrics", plots_dir, "overall_comparison.png")


def make_metric_panels(
    rows: dict[tuple[str, str], dict[str, object]],
    plots_dir: Path,
    panels: Sequence[tuple[str, str, float | None]],
    title: str,
    filename: str,
    *,
    percent: bool = False,
) -> None:
    source_keys, harms, labels, colors = report_axes(rows)
    figure, axes = plt.subplots(1, len(panels), figsize=(max(16, len(harms) * 2.2), 6), constrained_layout=True)
    axes = np.atleast_1d(axes)
    categories = [harm_label(harm) for harm in harms]
    for axis, (field, panel_title, maximum) in zip(axes, panels, strict=True):
        multiplier = 100 if percent else 1
        add_grouped_bars(
            axis,
            categories,
            {source: [float(rows[(source, harm)][field]) * multiplier for harm in harms] for source in source_keys},
            source_keys,
            labels,
            colors,
            formatter=(lambda value: f"{value:.1f}%") if percent else (lambda value: f"{value:.4f}" if maximum is not None and maximum <= 2 else f"{value:.0f}"),
            title=panel_title,
            ylim=(0, maximum) if maximum is not None else None,
        )
        axis.tick_params(axis="x", labelrotation=25)
    _finish_multi_axis_figure(figure, axes, len(source_keys), title, plots_dir, filename)


def make_adversarial_plot(
    rows: dict[tuple[str, str], dict[str, object]], plots_dir: Path
) -> None:
    source_keys, harms, labels, colors = report_axes(rows)
    figure = plt.figure(figsize=(max(16, len(harms) * 2.2), max(10, len(harms) * len(source_keys) * 0.4 + 6)), constrained_layout=True)
    grid = figure.add_gridspec(2, 2)
    categories = [harm_label(harm) for harm in harms]
    mean_axis = figure.add_subplot(grid[0, 0])
    variance_axis = figure.add_subplot(grid[0, 1])
    for axis, field, title, limit in (
        (mean_axis, "adversarial_mean", "Mean pressure", (0, 105)),
        (variance_axis, "adversarial_population_variance", "Population variance", None),
    ):
        add_grouped_bars(
            axis,
            categories,
            {source: [float(rows[(source, harm)][field]) for harm in harms] for source in source_keys},
            source_keys,
            labels,
            colors,
            formatter=lambda value: f"{value:.1f}",
            title=title,
            ylim=limit,
        )
        axis.tick_params(axis="x", labelrotation=25)
    distribution_axis = figure.add_subplot(grid[1, :])
    row_labels = [f"{harm_label(harm)} - {labels[source]}" for harm in harms for source in source_keys]
    positions = np.arange(len(row_labels))
    left = np.zeros(len(row_labels))
    for range_index, (score_range, color) in enumerate(zip(ADVERSARIAL_RANGES, ADVERSARIAL_COLORS, strict=True)):
        rates = [
            float(rows[(source, harm)]["adversarial_distribution_rates"][range_index]) * 100
            for harm in harms
            for source in source_keys
        ]
        distribution_axis.barh(positions, rates, left=left, color=color, label=score_range, edgecolor=BACKGROUND)
        left += np.array(rates)
    distribution_axis.set_yticks(positions, row_labels)
    distribution_axis.invert_yaxis()
    distribution_axis.set_xlim(0, 100)
    distribution_axis.set_title("Score distribution", fontweight="bold")
    distribution_axis.legend(ncol=5, loc="lower center", bbox_to_anchor=(0.5, -0.22), frameon=False)
    figure.suptitle("Expected adversarial pressure", fontsize=18, fontweight="bold", y=1.03)
    save_figure(figure, plots_dir, "adversarial_pressure.png")


def make_run_consistency_plot(
    rows: dict[tuple[str, str], dict[str, object]], plots_dir: Path
) -> None:
    source_keys, harms, labels, colors = report_axes(rows)
    columns = min(3, len(harms))
    row_count = math.ceil(len(harms) / columns)
    figure, axes = plt.subplots(row_count, columns, figsize=(5.2 * columns, 4.2 * row_count), constrained_layout=True, squeeze=False)
    for axis, harm in zip(axes.flat, harms, strict=False):
        run_names = list(dict.fromkeys(run for source in source_keys for run in rows[(source, harm)]["run_counts"]))
        for source in source_keys:
            run_counts = rows[(source, harm)]["run_counts"]
            positions = [run_names.index(run) for run in run_counts]
            axis.plot(positions, list(run_counts.values()), marker="o", linewidth=2.2, color=colors[source], label=labels[source])
        maximum = max(count for source in source_keys for count in rows[(source, harm)]["run_counts"].values())
        axis.set_xticks(np.arange(len(run_names)), run_names, rotation=25)
        axis.set_ylim(0, maximum * 1.25)
        axis.set_title(harm_label(harm), fontweight="bold")
        axis.set_ylabel("Dimension count")
        style_axis(axis)
    for axis in list(axes.flat)[len(harms):]:
        axis.set_visible(False)
    axes.flat[0].legend(ncol=len(source_keys), frameon=False)
    figure.suptitle("Run dimension counts", fontsize=18, fontweight="bold", y=1.02)
    save_figure(figure, plots_dir, "run_consistency.png")


def generate_plots(
    rows: dict[tuple[str, str], dict[str, object]], plots_dir: Path
) -> None:
    plots_dir.mkdir(parents=True, exist_ok=True)
    configure_style()
    make_overall_plot(rows, plots_dir)
    make_metric_panels(
        rows,
        plots_dir,
        (("total_dimensions", "Total", None), ("unique_dimensions", "Unique", None), ("relevant_unique_dimensions", "Relevant unique", None)),
        "Dimension counts by harm",
        "coverage_by_harm.png",
    )
    make_metric_panels(
        rows,
        plots_dir,
        (("uniqueness_rate", "Unique / total", 112), ("relevant_unique_rate", "Relevance@75 / unique", 112), ("perfect_relevance_rate", "Relevance@100 / unique", 112)),
        "Relevance rates by harm",
        "relevance_rates.png",
        percent=True,
    )
    make_metric_panels(
        rows,
        plots_dir,
        (("embedding_diversity", "Embedding diversity", 1.08), ("llm_pair_diversity", "LLM pair diversity", 1.08), ("llm_direct_diversity", "LLM direct diversity", 1.08)),
        "Diversity by harm",
        "diversity_and_relevance.png",
    )
    make_adversarial_plot(rows, plots_dir)
    make_metric_panels(
        rows,
        plots_dir,
        (("coverage_score", "Scenario-space coverage", 105),),
        "Canonical harm scenario-space coverage",
        "scenario_space_coverage.png",
    )
    make_run_consistency_plot(rows, plots_dir)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report one template-generation experiment or compare common harms "
            "across two experiments."
        )
    )
    parser.add_argument(
        "experiment_dirs",
        nargs="+",
        help="One experiment directory, or two directories to compare.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Output directory. Defaults to <experiment>/analysis/summary for one "
            "input and template_comparison_reports for two inputs."
        ),
    )
    args = parser.parse_args(argv)
    if len(args.experiment_dirs) not in (1, 2):
        parser.error("expected one or two experiment directories")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    sources = load_sources(args.experiment_dirs)
    harms = select_report_harms(sources)
    rows = build_rows(sources, harms)
    merged_results = generate_merged_configs(sources, harms)
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else (
            sources[0].root / "analysis" / "summary"
            if len(sources) == 1
            else DEFAULT_COMPARISON_OUTPUT_DIR
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_comparison_data(rows, output_dir / "comparison_data.csv")
    generate_plots(rows, output_dir / "plots")
    write_report(rows, merged_results, output_dir / "report.md")
    for result in merged_results:
        action = "created" if result["created"] else "kept existing"
        print(f"{action} {result['path']}")


if __name__ == "__main__":
    main()