#!/usr/bin/env python3
"""Generate an HTML report from tau2↔p2m correlation results.

Usage:
    python generate_report.py                   # uses results/ dir
    python generate_report.py --out report.html # custom output path
    python generate_report.py --no-open         # skip auto-open
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = SCRIPT_DIR / "results"
SIM_DIR = SCRIPT_DIR / "data" / "simulations"


def slug(model: str) -> str:
    return model.split("/")[-1] if "/" in model else model


def load_results(results_dir: Path) -> dict:
    path = results_dir / "correlation_results.json"
    if not path.exists():
        print(f"ERROR: {path} not found. Run the pipeline first:", file=sys.stderr)
        print("  python run_comparison.py --preset quick", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text())


def fig_to_base64(fig) -> str:
    """Render a matplotlib figure to a base64-encoded PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def build_report(results_dir: Path, sim_dir: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import pandas as pd
    import seaborn as sns
    from scipy.stats import spearmanr  # noqa: F401

    sns.set_theme(style="whitegrid", font_scale=1.0)

    results = load_results(results_dir)
    tau2_rewards = results["tau2_rewards"]
    tau2_samples = results.get("tau2_sample_sizes", {})
    p2m_scores = results["p2m_scores"]
    correlations = results["correlations"]
    models = results.get("models_compared", sorted(set(tau2_rewards) & set(p2m_scores)))

    charts: list[tuple[str, str]] = []  # (title, base64_png)

    # ── Chart 1: Model comparison bars ────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(models))
    labels = [slug(m) for m in models]
    tau2_vals = [tau2_rewards.get(m, 0) for m in models]
    p2m_vals = [p2m_scores.get(m, {}).get("_overall", 0) for m in models]
    width = 0.35
    bars1 = ax.bar([i - width / 2 for i in x], tau2_vals, width,
                   label="τ² Mean Reward", color="#4C72B0")
    bars2 = ax.bar([i + width / 2 for i in x], p2m_vals, width,
                   label="p2m Overall Score", color="#55A868")
    ax.set_ylabel("Score")
    ax.set_title("Model Performance: τ²-bench vs p2m")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.legend()
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    for bar in list(bars1) + list(bars2):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{bar.get_height():.2f}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    charts.append(("Model Comparison", fig_to_base64(fig)))
    plt.close(fig)

    # ── Chart 2: Correlation heatmap ──────────────────────────────
    dims = sorted(d for d in correlations if d != "_overall")
    if dims:
        rho_vals = [correlations[d]["rho"] for d in dims]
        pval_vals = [correlations[d]["pval"] for d in dims]
        fig, axes = plt.subplots(1, 2, figsize=(14, max(3, len(dims) * 0.7)))
        rho_df = pd.DataFrame({"Spearman ρ": rho_vals}, index=dims)
        sns.heatmap(rho_df, annot=True, fmt=".3f", cmap="RdBu_r", center=0,
                    vmin=-1, vmax=1, ax=axes[0], cbar_kws={"shrink": 0.8})
        axes[0].set_title("Spearman ρ by Dimension")
        pval_df = pd.DataFrame({"p-value": pval_vals}, index=dims)
        sns.heatmap(pval_df, annot=True, fmt=".3f", cmap="YlOrRd_r",
                    vmin=0, vmax=1, ax=axes[1], cbar_kws={"shrink": 0.8})
        axes[1].set_title("p-values (< 0.05 = significant)")
        plt.tight_layout()
        charts.append(("Correlation Heatmap", fig_to_base64(fig)))
        plt.close(fig)

    # ── Chart 3: p2m dimension bars ───────────────────────────────
    all_dims = sorted({d for scores in p2m_scores.values() for d in scores if d != "_overall"})
    if all_dims:
        fig, ax = plt.subplots(figsize=(12, 5))
        x = range(len(all_dims))
        n_models = len(models)
        total_width = 0.8
        bar_width = total_width / n_models
        colors = sns.color_palette("Set2", n_models)
        for i, m in enumerate(models):
            vals = [p2m_scores.get(m, {}).get(d, 0) for d in all_dims]
            offset = (i - n_models / 2 + 0.5) * bar_width
            ax.bar([xi + offset for xi in x], vals, bar_width, label=slug(m), color=colors[i])
        ax.set_ylabel("Violation Rate")
        ax.set_title("p2m Violation Rates by Dimension")
        ax.set_xticks(list(x))
        ax.set_xticklabels([d.replace("_", " ").title() for d in all_dims], rotation=30, ha="right")
        ax.legend(title="Model")
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
        plt.tight_layout()
        charts.append(("p2m Dimension Breakdown", fig_to_base64(fig)))
        plt.close(fig)

    # ── Chart 4: Scatter plots ────────────────────────────────────
    dims_to_plot = ["_overall"] + sorted(d for d in correlations if d != "_overall")
    ncols = min(3, len(dims_to_plot))
    nrows = (len(dims_to_plot) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4.5 * nrows))
    axes_flat = [axes] if len(dims_to_plot) == 1 else list(axes.flat) if hasattr(axes, "flat") else [axes]
    for idx, dim in enumerate(dims_to_plot):
        ax = axes_flat[idx]
        tau2_v, p2m_v, point_labels = [], [], []
        for m in models:
            if dim in p2m_scores.get(m, {}):
                t = tau2_rewards[m]
                p = p2m_scores[m][dim]
                if dim != "_overall":
                    p = 1.0 - p
                tau2_v.append(t)
                p2m_v.append(p)
                point_labels.append(slug(m))
        ax.scatter(tau2_v, p2m_v, s=100, zorder=5)
        for xi, yi, label in zip(tau2_v, p2m_v, point_labels):
            ax.annotate(label, (xi, yi), textcoords="offset points", xytext=(5, 5), fontsize=8)
        c = correlations.get(dim, {})
        rho = c.get("rho", float("nan"))
        pval = c.get("pval", float("nan"))
        title = dim.replace("_", " ").title()
        sig = " *" if pval < 0.05 else ""
        ax.set_title(f"{title}\nρ={rho:.3f}, p={pval:.3f}{sig}")
        ax.set_xlabel("τ² Mean Reward")
        ax.set_ylabel("p2m Overall" if dim == "_overall" else f"p2m {title} (success rate)")
    for idx in range(len(dims_to_plot), len(axes_flat)):
        axes_flat[idx].set_visible(False)
    fig.suptitle("τ² Reward vs p2m Score — Scatter by Dimension", fontsize=14, y=1.02)
    plt.tight_layout()
    charts.append(("Scatter Plots", fig_to_base64(fig)))
    plt.close(fig)

    # ── Chart 5: Reward distribution ──────────────────────────────
    sim_data: dict[str, list] = {}
    for m in models:
        path = sim_dir / f"telecom_{slug(m)}.json"
        if path.exists():
            sim_data[m] = json.loads(path.read_text())["simulations"]

    if sim_data:
        n = len(sim_data)
        fig, axes = plt.subplots(1, n, figsize=(5 * n, 4), sharey=True)
        if n == 1:
            axes = [axes]
        for ax, (m, sims) in zip(axes, sim_data.items()):
            rewards = [s["reward_info"]["reward"] for s in sims]
            ax.hist(rewards, bins=20, color="#4C72B0", edgecolor="white", alpha=0.8)
            mean_r = sum(rewards) / len(rewards)
            ax.axvline(mean_r, color="red", linestyle="--", label=f"mean={mean_r:.3f}")
            ax.set_title(slug(m))
            ax.set_xlabel("Reward")
            ax.legend(fontsize=9)
        axes[0].set_ylabel("Count")
        fig.suptitle("τ²-bench Reward Distribution", fontsize=14)
        plt.tight_layout()
        charts.append(("Reward Distribution", fig_to_base64(fig)))
        plt.close(fig)

    # ── Build HTML ────────────────────────────────────────────────
    overall_rho = correlations.get("_overall", {}).get("rho", float("nan"))
    overall_pval = correlations.get("_overall", {}).get("pval", float("nan"))
    tau2_rank = sorted(models, key=lambda m: tau2_rewards.get(m, 0), reverse=True)
    p2m_rank = sorted(models, key=lambda m: p2m_scores.get(m, {}).get("_overall", 0), reverse=True)

    # Overview table
    overview_rows = ""
    for m in models:
        n = tau2_samples.get(m, 0)
        t_r = tau2_rewards.get(m, 0)
        p_o = p2m_scores.get(m, {}).get("_overall", 0)
        overview_rows += (
            f"<tr><td>{slug(m)}</td><td>{t_r:.4f}</td>"
            f"<td>{n}</td><td>{p_o:.4f}</td></tr>\n"
        )

    # Correlation table
    corr_rows_html = ""
    for dim in sorted(correlations):
        c = correlations[dim]
        sig_mark = "✓" if c["pval"] < 0.05 else ""
        cls = ' class="significant"' if c["pval"] < 0.05 else ""
        corr_rows_html += (
            f"<tr{cls}><td>{dim}</td><td>{c['rho']:.4f}</td>"
            f"<td>{c['pval']:.4f}</td><td>{c['n']}</td><td>{sig_mark}</td></tr>\n"
        )

    # Simulation detail table
    sim_rows_html = ""
    if sim_data:
        for m, sims in sim_data.items():
            rewards = [s["reward_info"]["reward"] for s in sims]
            costs = [s.get("agent_cost", 0) + s.get("user_cost", 0) for s in sims]
            mean_r = sum(rewards) / len(rewards)
            succ = sum(1 for r in rewards if r == 1.0) / len(rewards)
            total_c = sum(costs)
            sim_rows_html += (
                f"<tr><td>{slug(m)}</td><td>{len(sims)}</td>"
                f"<td>{mean_r:.4f}</td><td>{succ:.1%}</td>"
                f"<td>${total_c:.4f}</td></tr>\n"
            )

    # Quality warnings
    warnings = []
    n_models = len(models)
    if n_models < 5:
        warnings.append(
            f"Only {n_models} models compared. Spearman correlation needs "
            f"≥5 models for meaningful significance testing."
        )
    low_sample = [m for m in models if tau2_samples.get(m, 0) < 50]
    if low_sample:
        names = ", ".join(slug(m) for m in low_sample)
        warnings.append(
            f"Models with low τ² sample count (&lt;50): {names}. "
            f"Re-run with <code>--stages tau2 --models ...</code> to improve."
        )
    sig_dims = [d for d, c in correlations.items() if c["pval"] < 0.05]

    warnings_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        warnings_html = f'<div class="warning"><h3>⚠ Caveats</h3><ul>{items}</ul></div>'

    chart_sections = ""
    for title, b64 in charts:
        chart_sections += (
            f'<div class="chart-section"><h3>{title}</h3>'
            f'<img src="data:image/png;base64,{b64}" /></div>\n'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>τ²-bench ↔ p2m Correlation Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 1200px; margin: 0 auto; padding: 20px; color: #333; }}
  h1 {{ border-bottom: 2px solid #4C72B0; padding-bottom: 8px; }}
  h2 {{ color: #4C72B0; margin-top: 40px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: right; }}
  th {{ background: #4C72B0; color: white; }}
  td:first-child, th:first-child {{ text-align: left; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  tr.significant {{ background: #d4edda; }}
  .summary-box {{ background: #f0f7ff; border-left: 4px solid #4C72B0;
                   padding: 16px; margin: 20px 0; }}
  .warning {{ background: #fff3cd; border-left: 4px solid #ffc107;
              padding: 16px; margin: 20px 0; }}
  .chart-section {{ margin: 24px 0; }}
  .chart-section img {{ max-width: 100%; }}
  .metric {{ display: inline-block; padding: 8px 16px; margin: 4px;
             background: #e8f0fe; border-radius: 8px; }}
  .metric strong {{ display: block; font-size: 1.4em; color: #4C72B0; }}
</style>
</head>
<body>
<h1>τ²-bench ↔ p2m Correlation Report</h1>

<div class="summary-box">
  <div class="metric"><strong>{n_models}</strong> Models</div>
  <div class="metric"><strong>{len(correlations)}</strong> Dimensions</div>
  <div class="metric"><strong>ρ = {overall_rho:.4f}</strong> Overall Spearman</div>
  <div class="metric"><strong>p = {overall_pval:.4f}</strong> p-value</div>
  <div class="metric"><strong>{len(sig_dims)}</strong> Significant dims</div>
</div>

{warnings_html}

<h2>1. Model Rankings</h2>
<table>
<tr><th>Rank</th><th>τ²-bench (reward)</th><th>p2m (overall)</th></tr>
{"".join(f"<tr><td>{i+1}</td><td>{slug(tau2_rank[i])}</td><td>{slug(p2m_rank[i])}</td></tr>"
         for i in range(len(models)))}
</table>

<h2>2. Model Scores Overview</h2>
<table>
<tr><th>Model</th><th>τ² Mean Reward</th><th>τ² Simulations</th><th>p2m Overall</th></tr>
{overview_rows}
</table>

<h2>3. Spearman Rank Correlations</h2>
<p>Spearman ρ measures monotonic rank agreement. For violation-rate dimensions,
p2m scores are inverted (1 − rate) so higher = better, matching τ² convention.</p>
<table>
<tr><th>Dimension</th><th>Spearman ρ</th><th>p-value</th><th>n</th><th>Sig.</th></tr>
{corr_rows_html}
</table>

{"<h2>4. Simulation Details</h2>" + '''
<table>
<tr><th>Model</th><th>Simulations</th><th>Mean Reward</th><th>Success Rate</th><th>Total Cost</th></tr>
''' + sim_rows_html + "</table>" if sim_rows_html else ""}

<h2>5. Charts</h2>
{chart_sections}

<hr />
<p style="color: #888; font-size: 0.9em;">
  Generated from <code>results/correlation_results.json</code>.
  Re-run <code>python generate_report.py</code> after updating results.
</p>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(description="Generate τ²↔p2m correlation HTML report.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR,
                        help="Directory containing correlation_results.json")
    parser.add_argument("--sim-dir", type=Path, default=SIM_DIR,
                        help="Directory containing raw simulation JSONs")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output HTML path (default: results/report.html)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open the report in browser")
    args = parser.parse_args()

    out_path = args.out or (args.results_dir / "report.html")

    print(f"Loading results from {args.results_dir}/")
    html = build_report(args.results_dir, args.sim_dir)

    out_path.write_text(html)
    print(f"Report saved to {out_path}")

    if not args.no_open:
        import webbrowser
        webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
