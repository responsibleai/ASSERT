from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RUN_PREFIX_DIR = (
    SCRIPT_DIR / "templates-by-skill-v2" / "analysis" / "run-prefixes"
)
RUN_COMBINATION_PATTERN = re.compile(r"^runs-(\d+)-to-(\d+)$")
DIMENSION_RUN_PATTERN = re.compile(r"/run-(\d+)/")

BACKGROUND = "#F7F5EF"
PANEL_BACKGROUND = "#FFFDF8"
TEXT = "#1C252B"
MUTED = "#657078"
GRID = "#D9D4C8"
PLOT_COLORS = (
    "#1D6A78",
    "#B84A39",
    "#5B6F3A",
    "#B07824",
    "#63558A",
    "#2E6F9E",
    "#A34265",
)


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    source: str
    decimals: int
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = False


METRICS = (
    Metric(
        "mean_harm_level",
        "Mean harm level",
        "harm_results.<harm>.average_dimension_count",
        4,
        minimum=0,
    ),
    Metric(
        "llm_pair_diversity",
        "LLM Pair diversity",
        "harm_results.<harm>.union_llm_pairwise_diversity",
        4,
        minimum=0,
        maximum=1,
    ),
    Metric(
        "relevance_mean",
        "Relevance Mean",
        "harm_results.<harm>.relevance.mean",
        4,
        minimum=0,
        maximum=4,
    ),
    Metric(
        "coverage_score",
        "Coverage score",
        "running maximum of harm_results.<harm>.coverage.score",
        0,
        minimum=0,
        maximum=100,
        integer=True,
    ),
    Metric(
        "global_dimensions",
        "Total dimensions",
        "global_unique_metrics.<harm>.total_dimension_count",
        0,
        minimum=0,
        integer=True,
    ),
    Metric(
        "global_unique_dimensions",
        "Unique dimensions",
        "final-prefix global_unique_metrics.<harm>.unique_groups[].member_dimension_ids",
        0,
        minimum=0,
        integer=True,
    ),
    Metric(
        "relevant_unique_dimensions",
        "Relevant unique dimensions",
        "final-prefix unique_groups represented by prefix and relevant at the binary threshold",
        0,
        minimum=0,
        integer=True,
    ),
)

CSV_FIELDS = (
    "harm",
    "run_combination",
    "run_count",
    *(metric.key for metric in METRICS),
    "raw_coverage_score",
    "source_metrics",
    "unique_taxonomy_metrics",
)


def select_metrics(metrics: Sequence[Metric], skipped_keys: Sequence[str]) -> tuple[Metric, ...]:
    skip_set = {key for key in skipped_keys}
    available_keys = {metric.key for metric in metrics}
    invalid_keys = sorted(skip_set - available_keys)
    if invalid_keys:
        raise ValueError(
            f"Unknown metric key(s): {', '.join(invalid_keys)}. "
            f"Choose from: {', '.join(sorted(available_keys))}"
        )

    selected = tuple(metric for metric in metrics if metric.key not in skip_set)
    if not selected:
        raise ValueError("No metrics remain after filtering")
    return selected


def harm_label(harm: str) -> str:
    return harm.replace("_", " ").title()


def parse_run_combination(name: str) -> tuple[int, int, int]:
    match = RUN_COMBINATION_PATTERN.fullmatch(name)
    if not match:
        raise ValueError(f"Invalid run-combination directory name: {name}")
    first_run, last_run = (int(value) for value in match.groups())
    if first_run < 1 or last_run < first_run:
        raise ValueError(f"Invalid run range in directory name: {name}")
    return first_run, last_run, last_run - first_run + 1


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object at {location}")
    return value


def _number(value: Any, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Expected a number at {location}, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Expected a finite number at {location}, got {value!r}")
    return result


def _integer(value: Any, location: str) -> int:
    number = _number(value, location)
    if not number.is_integer():
        raise ValueError(f"Expected an integer at {location}, got {value!r}")
    return int(number)


def extract_row(
    metrics: dict[str, Any],
    harm: str,
    combination: str,
    run_count: int,
    source_metrics: str,
) -> dict[str, object]:
    harm_results = _mapping(metrics.get("harm_results"), "harm_results")
    harm_result = _mapping(
        harm_results.get(harm), f"harm_results.{harm}"
    )
    global_results = _mapping(
        metrics.get("global_unique_metrics"), "global_unique_metrics"
    )
    global_result = _mapping(
        global_results.get(harm), f"global_unique_metrics.{harm}"
    )
    relevance = _mapping(
        harm_result.get("relevance"), f"harm_results.{harm}.relevance"
    )
    coverage = _mapping(
        harm_result.get("coverage"), f"harm_results.{harm}.coverage"
    )

    embedded_prefix = _mapping(
        _mapping(metrics.get("methodology"), "methodology").get("run_prefix"),
        "methodology.run_prefix",
    )
    embedded_harm = embedded_prefix.get("harm")
    embedded_run_count = _integer(
        embedded_prefix.get("run_count"), "methodology.run_prefix.run_count"
    )
    if embedded_harm != harm or embedded_run_count != run_count:
        raise ValueError(
            f"Directory/metadata mismatch in {source_metrics}: expected {harm}/"
            f"{run_count} runs, found {embedded_harm}/{embedded_run_count}"
        )

    run_counts = harm_result.get("run_dimension_counts")
    if not isinstance(run_counts, list) or len(run_counts) != run_count:
        raise ValueError(
            f"Expected {run_count} run dimension counts in {source_metrics}, "
            f"found {run_counts!r}"
        )

    row: dict[str, object] = {
        "harm": harm,
        "run_combination": combination,
        "run_count": run_count,
        "mean_harm_level": _number(
            harm_result.get("average_dimension_count"),
            f"harm_results.{harm}.average_dimension_count",
        ),
        "llm_pair_diversity": _number(
            harm_result.get("union_llm_pairwise_diversity"),
            f"harm_results.{harm}.union_llm_pairwise_diversity",
        ),
        "relevance_mean": _number(
            relevance.get("mean"), f"harm_results.{harm}.relevance.mean"
        ),
        "coverage_score": _integer(
            coverage.get("score"), f"harm_results.{harm}.coverage.score"
        ),
        "global_dimensions": _integer(
            global_result.get("total_dimension_count"),
            f"global_unique_metrics.{harm}.total_dimension_count",
        ),
        "global_unique_dimensions": _integer(
            global_result.get("unique_dimension_count"),
            f"global_unique_metrics.{harm}.unique_dimension_count",
        ),
        "relevant_unique_dimensions": _integer(
            global_result.get("relevant_unique_dimension_count"),
            f"global_unique_metrics.{harm}.relevant_unique_dimension_count",
        ),
        "source_metrics": source_metrics,
    }

    if not 0 <= float(row["llm_pair_diversity"]) <= 1:
        raise ValueError(f"LLM Pair diversity is outside 0..1 in {source_metrics}")
    if not 0 <= float(row["relevance_mean"]) <= 4:
        raise ValueError(f"Relevance Mean is outside 0..4 in {source_metrics}")
    if not 0 <= int(row["coverage_score"]) <= 100:
        raise ValueError(f"Coverage score is outside 0..100 in {source_metrics}")
    if int(row["global_unique_dimensions"]) > int(row["global_dimensions"]):
        raise ValueError(f"Unique dimensions exceed total dimensions in {source_metrics}")
    if int(row["relevant_unique_dimensions"]) > int(
        row["global_unique_dimensions"]
    ):
        raise ValueError(
            f"Relevant unique dimensions exceed unique dimensions in {source_metrics}"
        )
    return row


def _dimension_run_number(dimension_id: object, location: str) -> int:
    if not isinstance(dimension_id, str):
        raise ValueError(f"Expected a dimension ID string at {location}")
    match = DIMENSION_RUN_PATTERN.search(dimension_id)
    if not match:
        raise ValueError(f"Cannot read a run number from {dimension_id!r} at {location}")
    return int(match.group(1))


def apply_final_taxonomy_counts(
    rows: list[dict[str, object]],
    final_metrics: dict[str, Any],
    harm: str,
    taxonomy_source: str,
) -> None:
    global_results = _mapping(
        final_metrics.get("global_unique_metrics"), "global_unique_metrics"
    )
    global_result = _mapping(
        global_results.get(harm), f"global_unique_metrics.{harm}"
    )
    raw_groups = global_result.get("unique_groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise ValueError(f"Expected final-prefix unique groups for {harm}")

    groups: list[tuple[set[int], bool]] = []
    seen_members: set[str] = set()
    for index, raw_group in enumerate(raw_groups):
        location = f"global_unique_metrics.{harm}.unique_groups[{index}]"
        group = _mapping(raw_group, location)
        member_ids = group.get("member_dimension_ids")
        if not isinstance(member_ids, list) or not member_ids:
            raise ValueError(f"Expected member dimension IDs at {location}")
        relevant = group.get("is_relevant_at_binary_threshold")
        if not isinstance(relevant, bool):
            raise ValueError(f"Expected a relevance boolean at {location}")
        run_numbers: set[int] = set()
        for member_index, member_id in enumerate(member_ids):
            member_location = f"{location}.member_dimension_ids[{member_index}]"
            run_numbers.add(_dimension_run_number(member_id, member_location))
            if member_id in seen_members:
                raise ValueError(f"Dimension {member_id!r} occurs in multiple final groups")
            seen_members.add(member_id)
        groups.append((run_numbers, relevant))

    expected_unique = _integer(
        global_result.get("unique_dimension_count"),
        f"global_unique_metrics.{harm}.unique_dimension_count",
    )
    expected_relevant = _integer(
        global_result.get("relevant_unique_dimension_count"),
        f"global_unique_metrics.{harm}.relevant_unique_dimension_count",
    )
    if len(groups) != expected_unique:
        raise ValueError(
            f"Final taxonomy group count mismatch for {harm}: "
            f"expected {expected_unique}, found {len(groups)}"
        )
    if sum(relevant for _, relevant in groups) != expected_relevant:
        raise ValueError(f"Final taxonomy relevant-group count mismatch for {harm}")

    rows.sort(key=lambda row: int(row["run_count"]))
    for row in rows:
        run_count = int(row["run_count"])
        represented_groups = [
            (run_numbers, relevant)
            for run_numbers, relevant in groups
            if any(run_number <= run_count for run_number in run_numbers)
        ]
        row["global_unique_dimensions"] = len(represented_groups)
        row["relevant_unique_dimensions"] = sum(
            relevant for _, relevant in represented_groups
        )
        row["unique_taxonomy_metrics"] = taxonomy_source

    for key in ("global_unique_dimensions", "relevant_unique_dimensions"):
        values = [int(row[key]) for row in rows]
        if any(current < previous for previous, current in zip(values, values[1:])):
            raise ValueError(f"Fixed-taxonomy metric {key} decreased for {harm}: {values}")


def apply_cumulative_coverage(rows: list[dict[str, object]], harm: str) -> None:
    rows.sort(key=lambda row: int(row["run_count"]))
    cumulative_score = 0
    for row in rows:
        raw_score = int(row["coverage_score"])
        row["raw_coverage_score"] = raw_score
        cumulative_score = max(cumulative_score, raw_score)
        row["coverage_score"] = cumulative_score

    values = [int(row["coverage_score"]) for row in rows]
    if any(current < previous for previous, current in zip(values, values[1:])):
        raise ValueError(f"Cumulative coverage decreased for {harm}: {values}")


def load_rows(run_prefix_dir: Path) -> list[dict[str, object]]:
    if not run_prefix_dir.is_dir():
        raise FileNotFoundError(f"Run-prefix directory does not exist: {run_prefix_dir}")

    rows: list[dict[str, object]] = []
    for harm_dir in sorted(path for path in run_prefix_dir.iterdir() if path.is_dir()):
        if harm_dir.name == "summary":
            continue
        combination_dirs = sorted(
            path
            for path in harm_dir.iterdir()
            if path.is_dir() and RUN_COMBINATION_PATTERN.fullmatch(path.name)
        )
        harm_rows: list[dict[str, object]] = []
        metrics_by_run_count: dict[int, tuple[dict[str, Any], str]] = {}
        for combination_dir in combination_dirs:
            _, _, run_count = parse_run_combination(combination_dir.name)
            metrics_path = combination_dir / "metrics.json"
            if not metrics_path.is_file():
                raise FileNotFoundError(f"Missing run-prefix metrics: {metrics_path}")
            metrics = _mapping(
                json.loads(metrics_path.read_text(encoding="utf-8")),
                str(metrics_path),
            )
            relative_metrics_path = str(metrics_path.relative_to(run_prefix_dir))
            if run_count in metrics_by_run_count:
                raise ValueError(f"Duplicate {run_count}-run prefix for {harm_dir.name}")
            metrics_by_run_count[run_count] = (metrics, relative_metrics_path)
            harm_rows.append(
                extract_row(
                    metrics,
                    harm_dir.name,
                    combination_dir.name,
                    run_count,
                    relative_metrics_path,
                )
            )
        if not harm_rows:
            raise ValueError(f"No run-prefix metric combinations found for {harm_dir.name}")
        final_run_count = max(metrics_by_run_count)
        final_metrics, taxonomy_source = metrics_by_run_count[final_run_count]
        apply_final_taxonomy_counts(
            harm_rows, final_metrics, harm_dir.name, taxonomy_source
        )
        apply_cumulative_coverage(harm_rows, harm_dir.name)
        rows.extend(harm_rows)

    if not rows:
        raise ValueError(f"No run-prefix metric combinations found in {run_prefix_dir}")
    rows.sort(key=lambda row: (str(row["harm"]), int(row["run_count"])))
    return rows


def group_rows(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["harm"])].append(row)
    for harm_rows in grouped.values():
        harm_rows.sort(key=lambda row: int(row["run_count"]))
    return dict(sorted(grouped.items()))


def write_csv(
    rows: Sequence[dict[str, object]],
    output_path: Path,
    metrics: Sequence[Metric],
) -> None:
    fieldnames = [
        "harm",
        "run_combination",
        "run_count",
        *(metric.key for metric in metrics),
        "raw_coverage_score",
        "source_metrics",
        "unique_taxonomy_metrics",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            filtered_row = {
                "harm": row["harm"],
                "run_combination": row["run_combination"],
                "run_count": row["run_count"],
                **{metric.key: row[metric.key] for metric in metrics},
                "raw_coverage_score": row.get("raw_coverage_score"),
                "source_metrics": row.get("source_metrics"),
                "unique_taxonomy_metrics": row.get("unique_taxonomy_metrics"),
            }
            writer.writerow(filtered_row)


def _format_value(metric: Metric, value: object) -> str:
    if metric.integer:
        return str(int(value))
    return f"{float(value):.{metric.decimals}f}"


def _format_delta(metric: Metric, first: object, last: object) -> str:
    difference = float(last) - float(first)
    if metric.integer:
        return f"{int(round(difference)):+d}"
    return f"{difference:+.{metric.decimals}f}"


def _set_plot_limits(axis: Any, metric: Metric, values: Sequence[float]) -> None:
    lower = min(values)
    upper = max(values)
    spread = upper - lower
    scale = max(abs(lower), abs(upper), 1.0)
    padding = max(spread * 0.25, scale * 0.035)
    if math.isclose(lower, upper):
        padding = max(scale * 0.08, 0.08)
    lower -= padding
    upper += padding
    if metric.minimum is not None:
        lower = max(metric.minimum, lower)
    if metric.maximum is not None:
        upper = min(metric.maximum, upper)
    if math.isclose(lower, upper):
        lower = max(metric.minimum or 0, lower - padding)
        upper = min(metric.maximum or upper + padding, upper + padding)
    axis.set_ylim(lower, upper)


def write_harm_plots(
    grouped_rows: dict[str, list[dict[str, object]]],
    output_dir: Path,
    metrics: Sequence[Metric],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_paths: dict[str, Path] = {}
    for harm, rows in grouped_rows.items():
        run_counts = [int(row["run_count"]) for row in rows]
        with plt.rc_context(
            {
                "font.family": "DejaVu Sans",
                "axes.titleweight": "bold",
                "axes.labelcolor": MUTED,
                "text.color": TEXT,
                "xtick.color": MUTED,
                "ytick.color": MUTED,
            }
        ):
            if len(metrics) == 1:
                figure, axis = plt.subplots(
                    1,
                    1,
                    figsize=(12, 8),
                    constrained_layout=True,
                    facecolor=BACKGROUND,
                )
                axes_list = [axis]
            else:
                rows_needed = 2
                columns_needed = max(1, math.ceil(len(metrics) / rows_needed))
                figure, axes = plt.subplots(
                    rows_needed,
                    columns_needed,
                    figsize=(2.4 * columns_needed, 2.5 * rows_needed),
                    constrained_layout=True,
                    facecolor=BACKGROUND,
                )
                axes_list = list(axes.flat)
            for index, (metric, axis) in enumerate(zip(metrics, axes_list, strict=False)):
                values = [float(row[metric.key]) for row in rows]
                color = PLOT_COLORS[index]
                axis.set_facecolor(PANEL_BACKGROUND)
                axis.plot(
                    run_counts,
                    values,
                    color=color,
                    linewidth=2.4,
                    marker="o",
                    markersize=7,
                    markeredgecolor=PANEL_BACKGROUND,
                    markeredgewidth=1.5,
                )
                _set_plot_limits(axis, metric, values)
                for run_count, value in zip(run_counts, values, strict=True):
                    axis.annotate(
                        _format_value(metric, value),
                        (run_count, value),
                        xytext=(0, 6),
                        textcoords="offset points",
                        ha="center",
                        fontsize=6,
                        color=TEXT,
                    )
                axis.set_title(metric.label, loc="left", fontsize=7, pad=8)
                axis.set_xlabel("Included runs", fontsize=5)
                axis.set_ylabel(metric.label, fontsize=5)
                axis.tick_params(axis="x", labelsize=5)
                axis.tick_params(axis="y", labelsize=5)
                axis.set_xticks(run_counts)
                axis.xaxis.set_major_locator(MaxNLocator(integer=True))
                if metric.integer:
                    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
                axis.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
                axis.spines[["top", "right"]].set_visible(False)
                axis.spines[["left", "bottom"]].set_color(GRID)

            figure.suptitle(
                f"{harm_label(harm)}: cumulative metrics",
                fontsize=12,
                fontweight="bold",
            )
            output_path = output_dir / f"{harm}.png"
            figure.savefig(output_path, dpi=170, facecolor=figure.get_facecolor())
            plt.close(figure)
            plot_paths[harm] = output_path
    return plot_paths


def write_dashboard(
    rows: Sequence[dict[str, object]],
    output_path: Path,
    metrics: Sequence[Metric],
) -> None:
    harms = sorted({str(row["harm"]) for row in rows})
    dashboard_rows = [
        {
            key: row[key]
            for key in ("harm", "run_combination", "run_count", *(m.key for m in metrics))
        }
        for row in rows
    ]
    metric_data = [
        {
            "key": metric.key,
            "label": metric.label,
            "decimals": metric.decimals,
        }
        for metric in metrics
    ]
    harm_options = "\n".join(
        f'<option value="{html.escape(harm)}">{html.escape(harm_label(harm))}</option>'
        for harm in harms
    )
    chart_elements = "\n".join(
        f'<div class="chart" id="chart-{index}"></div>'
        for index in range(len(metrics))
    )
    output_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Run-prefix metric dashboard</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{ --background: {BACKGROUND}; --panel: {PANEL_BACKGROUND}; --text: {TEXT}; --muted: {MUTED}; --grid: {GRID}; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--background); color: var(--text); font-family: "Avenir Next", "Segoe UI", sans-serif; }}
    header {{ padding: 28px clamp(18px, 4vw, 54px) 18px; border-bottom: 1px solid var(--grid); }}
    h1 {{ margin: 0 0 14px; font-family: Georgia, serif; font-size: clamp(28px, 4vw, 48px); font-weight: 500; letter-spacing: 0; }}
    .controls {{ display: flex; align-items: center; gap: 12px; color: var(--muted); }}
    select {{ min-width: 260px; padding: 9px 34px 9px 11px; border: 1px solid var(--grid); border-radius: 4px; background: var(--panel); color: var(--text); font: inherit; }}
    main {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 22px; padding: 22px clamp(10px, 3vw, 42px) 36px; }}
    .chart {{ min-width: 0; height: 390px; border-bottom: 1px solid var(--grid); }}
    @media (max-width: 850px) {{ main {{ grid-template-columns: 1fr; }} .chart {{ height: 350px; }} .controls {{ align-items: flex-start; flex-direction: column; }} select {{ width: 100%; min-width: 0; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Run-prefix metric dashboard</h1>
    <div class="controls">
      <label for="harm-select">Harm</label>
      <select id="harm-select">{harm_options}</select>
    </div>
  </header>
  <main>{chart_elements}</main>
  <script>
    const rows = {json.dumps(dashboard_rows, separators=(",", ":"))};
    const metrics = {json.dumps(metric_data, separators=(",", ":"))};
    const colors = {json.dumps(PLOT_COLORS)};
    const config = {{responsive: true, displaylogo: false}};

    function render(harm) {{
      const harmRows = rows.filter(row => row.harm === harm).sort((a, b) => a.run_count - b.run_count);
      metrics.forEach((metric, index) => {{
        const values = harmRows.map(row => row[metric.key]);
        Plotly.react(`chart-${{index}}`, [{{
          x: harmRows.map(row => row.run_count),
          y: values,
          customdata: harmRows.map(row => row.run_combination),
          text: values.map(value => Number(value).toFixed(metric.decimals)),
          textposition: "top center",
          mode: "lines+markers+text",
          line: {{color: colors[index], width: 3}},
          marker: {{color: colors[index], size: 9, line: {{color: "{PANEL_BACKGROUND}", width: 2}}}},
          hovertemplate: "%{{customdata}}<br>Included runs: %{{x}}<br>Value: %{{y}}<extra></extra>"
        }}], {{
          title: {{text: metric.label, x: 0.02, xanchor: "left", font: {{size: 18, color: "{TEXT}"}}}},
          paper_bgcolor: "{BACKGROUND}",
          plot_bgcolor: "{PANEL_BACKGROUND}",
          margin: {{l: 58, r: 20, t: 58, b: 52}},
          showlegend: false,
          xaxis: {{title: "Included runs", tickmode: "array", tickvals: harmRows.map(row => row.run_count), gridcolor: "{GRID}", zeroline: false}},
          yaxis: {{gridcolor: "{GRID}", zeroline: false, automargin: true}},
          font: {{family: "Avenir Next, Segoe UI, sans-serif", color: "{MUTED}"}}
        }}, config);
      }});
    }}

    const select = document.getElementById("harm-select");
    select.addEventListener("change", event => render(event.target.value));
    render(select.value);
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_report(
    grouped_rows: dict[str, list[dict[str, object]]],
    plot_paths: dict[str, Path],
    output_path: Path,
    metrics: Sequence[Metric],
) -> None:
    lines = [
        "# Harm run-prefix metric summary",
        "",
        "This report extracts one row from each cumulative run-prefix `metrics.json`. ",
        "The plots use the number of included runs as the x-axis. Coverage score is ",
        "the running maximum of the independently judged prefix scores, preserving ",
        "the set-based invariant that adding dimensions cannot remove scenario-space ",
        "coverage. Raw LLM coverage judgments remain visible in each harm table and ",
        "in the CSV. Global unique and ",
        "relevant unique dimensions use each harm's final-prefix groups as a fixed ",
        "taxonomy, so adding runs cannot revise historical group assignments and the ",
        "two cumulative uniqueness series are nondecreasing.",
        "",
        "- [Extracted metric data](run_prefix_metrics.csv)",
        "- [Interactive plots](run_prefix_dashboard.html)",
        "",
        "## Metric definitions",
        "",
        "`Mean harm level` is the existing harm-level mean dimension count: ",
        "`harm_results.<harm>.average_dimension_count`. It is the mean number of ",
        "dimensions per run in the cumulative prefix.",
        "",
        "| Report metric | Source field | Scale |",
        "| --- | --- | --- |",
    ]
    scales = {
        "mean_harm_level": "dimension count",
        "llm_pair_diversity": "0-1",
        "relevance_mean": "0-4",
        "coverage_score": "0-100",
        "global_dimensions": "count",
        "global_unique_dimensions": "count",
        "relevant_unique_dimensions": "count",
    }
    for metric in metrics:
        lines.append(f"| {metric.label} | `{metric.source}` | {scales[metric.key]} |")

    lines.extend(
        [
            "",
            "## First-to-last change",
            "",
            "| Harm | Run counts | "
            + " | ".join(f"{metric.label} Δ" for metric in metrics)
            + " |",
            "| --- | --- | " + " | ".join("---:" for _ in metrics) + " |",
        ]
    )
    for harm, rows in grouped_rows.items():
        first, last = rows[0], rows[-1]
        deltas = " | ".join(
            _format_delta(metric, first[metric.key], last[metric.key])
            for metric in metrics
        )
        lines.append(
            f"| {harm_label(harm)} | {first['run_count']}→{last['run_count']} | "
            f"{deltas} |"
        )

    for harm, rows in grouped_rows.items():
        lines.extend(
            [
                "",
                f"## {harm_label(harm)}",
                "",
                "| Run combination | Runs | "
                + " | ".join(metric.label for metric in metrics)
                + " | Raw coverage judgment |",
                "| --- | ---: | " + " | ".join("---:" for _ in metrics) + " | ---: |",
            ]
        )
        for row in rows:
            values = [
                _format_value(metric, row[metric.key]) for metric in metrics
            ]
            values.append(str(int(row["raw_coverage_score"])))
            lines.append(
                f"| `{row['run_combination']}` | {row['run_count']} | "
                f"{' | '.join(values)} |"
            )
        relative_plot = plot_paths[harm].relative_to(output_path.parent)
        lines.extend(
            [
                "",
                f"![{harm_label(harm)} run-prefix metrics]({relative_plot.as_posix()})",
            ]
        )

    lines.extend(
        [
            "",
            "## Extraction details",
            "",
            f"- Harm count: {len(grouped_rows)}",
            f"- Run-combination count: {sum(len(rows) for rows in grouped_rows.values())}",
            "- Grouping: one source artifact per harm and cumulative run combination; no cross-harm averaging.",
            "- Coverage: the reported score is the running maximum of raw prefix judgments; raw scores are retained separately.",
            "- Uniqueness: final-prefix groups are fixed, then counted when their first member appears in a prefix.",
            "- Relevant uniqueness: the same projection, filtered by each final group's binary relevance judgment.",
            "- Ordering: harms alphabetically, then run combinations by included run count.",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract cumulative run-prefix metrics and generate a Markdown report, "
            "CSV, static harm plots, and an interactive dashboard."
        )
    )
    parser.add_argument(
        "run_prefix_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_RUN_PREFIX_DIR,
        help=f"Run-prefix analysis directory (default: {DEFAULT_RUN_PREFIX_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: <run-prefix-dir>/summary)",
    )
    parser.add_argument(
        "--skip-metric",
        action="append",
        default=["mean_harm_level"],
        help=(
            "Omit a metric from plots, dashboard, report, and CSV output. "
            "Repeat the flag to skip multiple metrics."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run_prefix_dir = args.run_prefix_dir.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else run_prefix_dir / "summary"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(run_prefix_dir)
    grouped_rows = group_rows(rows)
    selected_metrics = select_metrics(METRICS, args.skip_metric)
    csv_path = output_dir / "run_prefix_metrics.csv"
    report_path = output_dir / "report.md"
    dashboard_path = output_dir / "run_prefix_dashboard.html"
    plot_paths = write_harm_plots(grouped_rows, output_dir / "metric_plots", selected_metrics)
    write_csv(rows, csv_path, selected_metrics)
    write_dashboard(rows, dashboard_path, selected_metrics)
    write_report(grouped_rows, plot_paths, report_path, selected_metrics)

    print(f"validated rows: {len(rows)} across {len(grouped_rows)} harms")
    print(f"aggregation: one row per harm/run-prefix metrics.json")
    print(f"report: {report_path}")
    print(f"data: {csv_path}")
    print(f"interactive plots: {dashboard_path}")
    print(f"static plots: {len(plot_paths)} files in {output_dir / 'metric_plots'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())