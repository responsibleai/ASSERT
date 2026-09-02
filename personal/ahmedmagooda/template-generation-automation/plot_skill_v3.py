"""Generate the comparison plots embedded in report-skill-v3.md."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt


REPORT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = REPORT_DIR / "figures" / "report-skill-v3"

METRICS = (
    "Prompt / Impermissible",
    "Prompt / Permissible",
    "Scenario / Impermissible",
    "Scenario / Permissible",
)

RESULTS = {
    # "imminent_crisis_management": {
    #     "with_template": (71.1, 23.3, 91.0, 24.0),
    #     "without_template": (87.6, 73.9, 97.4, 63.3),
    # },
    "violent_content": {
        "with_template": (25, 20, 11, 4),
        "without_template": (7, 2, 10, 2),
    },
    "relationship_entanglement": {
        "with_template": (25, 128, 113, 104),
        "without_template": (21, 49, 95, 66),
    },
}

GLOBAL_QUALITY_RESULTS = {
    # "imminent_crisis_management": {
    #     "llm_pair_diversity": 0.9209,
    #     "relevance_mean": 3.8571,
    # },
    "violent_content": {
        "llm_pair_diversity": 0.9424,
        "relevance_mean": 3.8519,
    },
    "relationship_entanglement": {
        "llm_pair_diversity": 0.9488,
        "relevance_mean": 3.5152,
    },
}

COLORS = {
    "with_template": "#087E8B",
    "without_template": "#C95D3A",
}

HARM_COLORS = {
    "imminent_crisis_management": "#087E8B",
    "violent_content": "#C95D3A",
    "relationship_entanglement": "#657A3E",
}


def validate_results() -> None:
    expected_series = {"with_template", "without_template"}
    for harm, series_by_condition in RESULTS.items():
        if set(series_by_condition) != expected_series:
            raise ValueError(f"{harm} must define exactly {sorted(expected_series)}")
        for condition, values in series_by_condition.items():
            if len(values) != len(METRICS):
                raise ValueError(
                    f"{harm}/{condition} has {len(values)} values; "
                    f"expected {len(METRICS)}"
                )
            # if any(value < 0 or value > 100 for value in values):
            #     raise ValueError(f"{harm}/{condition} contains a value outside 0-100")

    if not set(GLOBAL_QUALITY_RESULTS).issubset(RESULTS):
        raise ValueError("Global quality harms must have violation results")
    for harm, metrics in GLOBAL_QUALITY_RESULTS.items():
        if set(metrics) != {"llm_pair_diversity", "relevance_mean"}:
            raise ValueError(f"{harm} has unexpected global quality metrics")
        if not 0 <= metrics["llm_pair_diversity"] <= 1:
            raise ValueError(f"{harm} LLM pair diversity must be within 0-1")
        if not 0 <= metrics["relevance_mean"] <= 4:
            raise ValueError(f"{harm} relevance mean must be within 0-4")


def _display_name(harm: str) -> str:
    return harm.replace("_", " ").title()


def write_static_plots() -> list[Path]:
    output_paths: list[Path] = []
    positions = list(range(len(METRICS)))
    width = 0.36

    for harm, series_by_condition in RESULTS.items():
        figure, axis = plt.subplots(figsize=(10, 5.8))
        with_positions = [position - width / 2 for position in positions]
        without_positions = [position + width / 2 for position in positions]

        with_bars = axis.bar(
            with_positions,
            series_by_condition["with_template"],
            width,
            color=COLORS["with_template"],
            label="With Template",
        )
        without_bars = axis.bar(
            without_positions,
            series_by_condition["without_template"],
            width,
            color=COLORS["without_template"],
            label="Without Template",
        )

        axis.bar_label(with_bars, padding=3, fontsize=9)
        axis.bar_label(without_bars, padding=3, fontsize=9)
        axis.set_title(f"{_display_name(harm)}: Violation Rates", fontsize=16, pad=18)
        axis.set_ylabel("Violation cases")
        axis.set_xticks(
            positions,
            [metric.replace(" / ", "\n") for metric in METRICS],
        )
        axis.set_ylim(0, 150)
        axis.set_yticks(range(0, 150, 20), [f"{value}" for value in range(0, 150, 20)])
        axis.grid(axis="y", color="#D9DEE3", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, ncols=2, loc="upper left")
        figure.tight_layout()

        output_path = OUTPUT_DIR / f"{harm}.svg"
        figure.savefig(output_path, format="svg", bbox_inches="tight")
        plt.close(figure)
        output_paths.append(output_path)

    return output_paths


def _write_quality_plot(
    *,
    metric_key: str,
    title: str,
    ylabel: str,
    maximum: float,
    output_name: str,
) -> Path:
    harms = list(GLOBAL_QUALITY_RESULTS)
    values = [GLOBAL_QUALITY_RESULTS[harm][metric_key] for harm in harms]
    labels = [_display_name(harm) for harm in harms]

    figure, axis = plt.subplots(figsize=(8.5, 5.5))
    bars = axis.bar(
        labels,
        values,
        width=0.58,
        color=[HARM_COLORS[harm] for harm in harms],
    )
    axis.bar_label(
        bars,
        labels=[f"{value:.4f}" for value in values],
        label_type="edge",
        padding=-20,
        color="white",
        fontsize=11,
        fontweight="bold",
    )
    axis.set_title(title, fontsize=16, pad=18)
    axis.set_ylabel(ylabel)
    axis.set_ylim(0, maximum)
    axis.grid(axis="y", color="#D9DEE3", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()

    output_path = OUTPUT_DIR / output_name
    figure.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(figure)
    return output_path


def write_quality_plots() -> list[Path]:
    return [
        _write_quality_plot(
            metric_key="llm_pair_diversity",
            title="LLM Pair Diversity",
            ylabel="Mean semantic distance (0-1)",
            maximum=1,
            output_name="global_llm_pair_diversity.svg",
        ),
        _write_quality_plot(
            metric_key="relevance_mean",
            title="Relevance Mean",
            ylabel="Relevance score (0-4)",
            maximum=4,
            output_name="global_relevance_mean.svg",
        ),
    ]


def write_interactive_plot() -> Path:
    interactive_metrics = [metric.replace(" / ", "<br>") for metric in METRICS]
    chart_data = [
        {
            "harm": harm,
            "title": _display_name(harm),
            **series_by_condition,
        }
        for harm, series_by_condition in RESULTS.items()
    ]
    output_path = OUTPUT_DIR / "template_violation_comparison.html"
    output_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Template Violation Comparison</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; background: #f7f8fa; color: #17202a; font-family: Georgia, serif; }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 36px auto; }}
    h1 {{ margin-bottom: 6px; font-size: 30px; }}
    .chart {{ min-height: 520px; border-top: 1px solid #d9dee3; margin-top: 28px; padding-top: 20px; }}
  </style>
</head>
<body>
  <main>
    <h1>Template Violation Comparison</h1>
    <div id="charts"></div>
  </main>
  <script>
        const metrics = {json.dumps(interactive_metrics)};
    const charts = {json.dumps(chart_data)};
    const root = document.getElementById("charts");

    charts.forEach((chart) => {{
      const element = document.createElement("div");
      element.className = "chart";
      root.appendChild(element);
      Plotly.newPlot(element, [
        {{
          type: "bar",
          name: "With Template",
          x: metrics,
          y: chart.with_template,
          marker: {{ color: "{COLORS['with_template']}" }},
          text: chart.with_template.map((value) => `${{value.toFixed(1)}}%`),
          textposition: "outside",
          cliponaxis: false
        }},
        {{
          type: "bar",
          name: "Without Template",
          x: metrics,
          y: chart.without_template,
          marker: {{ color: "{COLORS['without_template']}" }},
          text: chart.without_template.map((value) => `${{value.toFixed(1)}}%`),
          textposition: "outside",
          cliponaxis: false
        }}
      ], {{
        title: {{ text: `${{chart.title}}: Violation Rates`, x: 0.02, xanchor: "left" }},
        barmode: "group",
        template: "plotly_white",
        height: 520,
        margin: {{ l: 58, r: 12, t: 82, b: 100 }},
        legend: {{ orientation: "h", x: 0, y: 1.12 }},
        xaxis: {{ automargin: true, tickangle: 0, tickfont: {{ size: 9 }} }},
        yaxis: {{ title: "Violation rate (%)", range: [0, 108], ticksuffix: "%", dtick: 20 }}
      }}, {{ responsive: true, displaylogo: false }});
    }});
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    validate_results()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_paths = [
        *write_static_plots(),
        *write_quality_plots(),
        write_interactive_plot(),
    ]
    violation_value_count = len(RESULTS) * 2 * len(METRICS)
    quality_value_count = len(GLOBAL_QUALITY_RESULTS) * 2
    print(
        f"Validated {len(RESULTS)} harms and "
        f"{violation_value_count + quality_value_count} plotted values."
    )
    print("Grouping: harm x template condition; no aggregation applied.")
    for output_path in output_paths:
        print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()