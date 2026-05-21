#!/usr/bin/env python3
"""Generate a combined HTML report from tau2↔p2m correlation results.

Produces a single report.html with:
  - Data status overview (all tau2/p2m models, coverage gaps)
  - Full analysis (all overlapping models)
  - Filtered analysis (only models with ≥ min_sims tau2 simulations)

Usage:
    python generate_report.py                         # generate report (no browser)
    python generate_report.py --open                  # auto-open in browser
    python generate_report.py --min-sims 100          # custom sim threshold for filter
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


DEFAULT_MIN_SIMS = 50


def _recompute_correlations(
    tau2_rewards: dict[str, float],
    p2m_scores: dict[str, dict[str, float]],
) -> dict[str, dict]:
    """Compute Spearman ρ for the given model subset."""
    from scipy.stats import spearmanr

    common = sorted(set(tau2_rewards) & {m for m in p2m_scores if p2m_scores[m]})
    if len(common) < 3:
        return {}

    all_dims: set[str] = set()
    for scores in p2m_scores.values():
        all_dims.update(scores.keys())

    correlations: dict[str, dict] = {}
    for dim in sorted(all_dims):
        tau2_vals, p2m_vals = [], []
        for m in common:
            if dim in p2m_scores[m]:
                val = p2m_scores[m][dim]
                if dim != "_overall":
                    val = 1.0 - val
                p2m_vals.append(val)
                tau2_vals.append(tau2_rewards[m])
        if len(p2m_vals) < 3:
            continue
        rho, pval = spearmanr(tau2_vals, p2m_vals)
        correlations[dim] = {"rho": rho, "pval": pval, "n": len(p2m_vals)}

    return correlations


def build_report(
    results_dir: Path,
    sim_dir: Path,
    *,
    min_sims: int = DEFAULT_MIN_SIMS,
) -> str:
    """Build a single combined HTML report with data status, full and filtered analysis."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import pandas as pd
    import seaborn as sns
    from scipy.stats import spearmanr

    sns.set_theme(style="whitegrid", font_scale=1.0)

    results = load_results(results_dir)
    tau2_rewards = results["tau2_rewards"]
    tau2_samples = results.get("tau2_sample_sizes", {})
    p2m_scores = results["p2m_scores"]

    # All models with both tau2 + p2m
    all_models = sorted(set(tau2_rewards) & set(p2m_scores))
    if len(all_models) < 2:
        return ""

    # Filtered models (high sim count)
    filtered_models = [m for m in all_models if tau2_samples.get(m, 0) >= min_sims]
    excluded_models = [m for m in all_models if tau2_samples.get(m, 0) < min_sims]

    # ── Data Status section ───────────────────────────────────────
    tau2_only = sorted(set(tau2_rewards) - set(p2m_scores))
    p2m_only = sorted(set(p2m_scores) - set(tau2_rewards))

    status_rows = ""
    for m in sorted(tau2_rewards):
        has_p2m = m in p2m_scores
        sims = tau2_samples.get(m, 0)
        reward = tau2_rewards[m]
        p2m_overall = p2m_scores.get(m, {}).get("_overall", None)
        p2m_cell = f"{p2m_overall:.4f}" if p2m_overall is not None else '<span style="color:#c00">missing</span>'
        overlap = "✓" if has_p2m else "✗"
        sim_style = ' style="color:#c00"' if sims < min_sims else ""
        status_rows += (
            f"<tr><td>{slug(m)}</td><td>{reward:.4f}</td>"
            f"<td{sim_style}>{sims}</td><td>{p2m_cell}</td><td>{overlap}</td></tr>\n"
        )
    for m in p2m_only:
        p2m_overall = p2m_scores[m].get("_overall", 0)
        status_rows += (
            f'<tr><td>{slug(m)}</td><td><span style="color:#c00">missing</span></td>'
            f'<td>—</td><td>{p2m_overall:.4f}</td><td>✗</td></tr>\n'
        )

    data_status_html = f"""
<h2>1. Data Status</h2>
<p>{len(tau2_rewards)} models with τ² data, {len(p2m_scores)} with p2m data,
   <strong>{len(all_models)}</strong> overlap ({len(filtered_models)} with ≥{min_sims} sims).</p>
<table>
<tr><th>Model</th><th>τ² Reward</th><th>τ² Sims</th><th>p2m Overall</th><th>Both?</th></tr>
{status_rows}
</table>
"""
    if tau2_only:
        data_status_html += (
            f'<p style="color:#666">τ² only (no p2m yet): '
            f'{", ".join(slug(m) for m in tau2_only)}</p>\n'
        )
    if p2m_only:
        data_status_html += (
            f'<p style="color:#666">p2m only (no τ² yet): '
            f'{", ".join(slug(m) for m in p2m_only)}</p>\n'
        )

    # ── Helper to build analysis sections ─────────────────────────
    def _build_analysis_section(
        models: list[str],
        section_num: int,
        section_title: str,
    ) -> tuple[str, list[tuple[str, str]]]:
        """Returns (html_str, charts_list) for a set of models."""
        if len(models) < 2:
            return f"<h2>{section_num}. {section_title}</h2><p>Not enough models (need ≥2).</p>", []

        tau2_sub = {m: tau2_rewards[m] for m in models}
        p2m_sub = {m: p2m_scores[m] for m in models}
        correlations = _recompute_correlations(tau2_sub, p2m_sub)

        charts: list[tuple[str, str]] = []

        # Chart: Model comparison bars
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
        ax.set_title(f"Model Performance: τ²-bench vs p2m — {section_title}")
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

        # Chart: Correlation heatmap
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

        # Chart: p2m dimension bars
        all_dims = sorted({d for m in models for d in p2m_scores.get(m, {}) if d != "_overall"})
        if all_dims:
            fig, ax = plt.subplots(figsize=(12, 5))
            x = range(len(all_dims))
            nm = len(models)
            total_width = 0.8
            bar_width = total_width / nm
            colors = sns.color_palette("Set2", nm)
            for i, m in enumerate(models):
                vals = [p2m_scores.get(m, {}).get(d, 0) for d in all_dims]
                offset = (i - nm / 2 + 0.5) * bar_width
                ax.bar([xi + offset for xi in x], vals, bar_width, label=slug(m), color=colors[i])
            ax.set_ylabel("Violation Rate")
            ax.set_title(f"p2m Violation Rates by Dimension — {section_title}")
            ax.set_xticks(list(x))
            ax.set_xticklabels([d.replace("_", " ").title() for d in all_dims], rotation=30, ha="right")
            ax.legend(title="Model")
            ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
            plt.tight_layout()
            charts.append(("p2m Dimension Breakdown", fig_to_base64(fig)))
            plt.close(fig)

        # Chart: Scatter plots
        dims_to_plot = ["_overall"] + sorted(d for d in correlations if d != "_overall")
        ncols = min(3, len(dims_to_plot))
        nrows = (len(dims_to_plot) + ncols - 1) // ncols
        fig, axes_arr = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4.5 * nrows))
        axes_flat = [axes_arr] if len(dims_to_plot) == 1 else list(axes_arr.flat) if hasattr(axes_arr, "flat") else [axes_arr]
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
        fig.suptitle(f"τ² Reward vs p2m Score — {section_title}", fontsize=14, y=1.02)
        plt.tight_layout()
        charts.append(("Scatter Plots", fig_to_base64(fig)))
        plt.close(fig)

        # Rankings & tables
        overall_rho = correlations.get("_overall", {}).get("rho", float("nan"))
        overall_pval = correlations.get("_overall", {}).get("pval", float("nan"))
        tau2_rank = sorted(models, key=lambda m: tau2_rewards.get(m, 0), reverse=True)
        p2m_rank = sorted(models, key=lambda m: p2m_scores.get(m, {}).get("_overall", 0), reverse=True)
        sig_dims = [d for d, c in correlations.items() if c["pval"] < 0.05]

        overview_rows = ""
        for m in models:
            n = tau2_samples.get(m, 0)
            t_r = tau2_rewards.get(m, 0)
            p_o = p2m_scores.get(m, {}).get("_overall", 0)
            overview_rows += (
                f"<tr><td>{slug(m)}</td><td>{t_r:.4f}</td>"
                f"<td>{n}</td><td>{p_o:.4f}</td></tr>\n"
            )

        corr_rows = ""
        for dim in sorted(correlations):
            c = correlations[dim]
            sig_mark = "✓" if c["pval"] < 0.05 else ""
            cls = ' class="significant"' if c["pval"] < 0.05 else ""
            corr_rows += (
                f"<tr{cls}><td>{dim}</td><td>{c['rho']:.4f}</td>"
                f"<td>{c['pval']:.4f}</td><td>{c['n']}</td><td>{sig_mark}</td></tr>\n"
            )

        # Warnings
        warnings = []
        if len(models) < 5:
            warnings.append(
                f"Only {len(models)} models. Spearman correlation needs ≥5 for meaningful significance."
            )
        low_sample = [m for m in models if tau2_samples.get(m, 0) < 50]
        if low_sample:
            names = ", ".join(slug(m) for m in low_sample)
            warnings.append(f"Low τ² sample count (&lt;50): {names}")
        warnings_html = ""
        if warnings:
            items = "".join(f"<li>{w}</li>" for w in warnings)
            warnings_html = f'<div class="warning"><h3>⚠ Caveats</h3><ul>{items}</ul></div>'

        chart_html = ""
        for title, b64 in charts:
            chart_html += (
                f'<div class="chart-section"><h3>{title}</h3>'
                f'<img src="data:image/png;base64,{b64}" /></div>\n'
            )

        html = f"""
<h2>{section_num}. {section_title}</h2>
<div class="summary-box">
  <div class="metric"><strong>{len(models)}</strong> Models</div>
  <div class="metric"><strong>{len(correlations)}</strong> Dimensions</div>
  <div class="metric"><strong>ρ = {overall_rho:.4f}</strong> Overall Spearman</div>
  <div class="metric"><strong>p = {overall_pval:.4f}</strong> p-value</div>
  <div class="metric"><strong>{len(sig_dims)}</strong> Significant dims</div>
</div>

{warnings_html}

<h3>Rankings</h3>
<table>
<tr><th>Rank</th><th>τ²-bench (reward)</th><th>p2m (overall)</th></tr>
{"".join(f"<tr><td>{i+1}</td><td>{slug(tau2_rank[i])}</td><td>{slug(p2m_rank[i])}</td></tr>"
         for i in range(len(models)))}
</table>

<h3>Model Scores</h3>
<table>
<tr><th>Model</th><th>τ² Mean Reward</th><th>τ² Simulations</th><th>p2m Overall</th></tr>
{overview_rows}
</table>

<h3>Spearman Rank Correlations</h3>
<p>Spearman ρ measures monotonic rank agreement. Violation-rate dimensions are
inverted (1 − rate) so higher = better, matching τ² convention.</p>
<table>
<tr><th>Dimension</th><th>Spearman ρ</th><th>p-value</th><th>n</th><th>Sig.</th></tr>
{corr_rows}
</table>

{chart_html}
"""
        return html, charts

    # ── Chart: Reward distributions (shared, uses all models) ─────
    sim_data: dict[str, list] = {}
    for m in all_models:
        path = sim_dir / f"telecom_{slug(m)}.json"
        if path.exists():
            sim_data[m] = json.loads(path.read_text())["simulations"]

    reward_dist_html = ""
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
        reward_dist_html = (
            '<div class="chart-section"><h3>Reward Distribution</h3>'
            f'<img src="data:image/png;base64,{fig_to_base64(fig)}" /></div>\n'
        )
        plt.close(fig)

        # Simulation detail table
        sim_rows = ""
        for m, sims in sim_data.items():
            rewards = [s["reward_info"]["reward"] for s in sims]
            costs = [s.get("agent_cost", 0) + s.get("user_cost", 0) for s in sims]
            mean_r = sum(rewards) / len(rewards)
            succ = sum(1 for r in rewards if r == 1.0) / len(rewards)
            total_c = sum(costs)
            sim_rows += (
                f"<tr><td>{slug(m)}</td><td>{len(sims)}</td>"
                f"<td>{mean_r:.4f}</td><td>{succ:.1%}</td>"
                f"<td>${total_c:.4f}</td></tr>\n"
            )
        reward_dist_html += f"""
<h3>Simulation Details</h3>
<table>
<tr><th>Model</th><th>Simulations</th><th>Mean Reward</th><th>Success Rate</th><th>Total Cost</th></tr>
{sim_rows}
</table>
"""

    # ── Build analysis sections ───────────────────────────────────
    full_html, _ = _build_analysis_section(all_models, 2, f"Full Analysis (all {len(all_models)} models)")

    filtered_html = ""
    if excluded_models:
        excluded_names = ", ".join(slug(m) for m in excluded_models)
        f_html, _ = _build_analysis_section(
            filtered_models, 3,
            f"Filtered Analysis (≥{min_sims} sims, excluding {excluded_names})",
        )
        filtered_html = f_html
    else:
        filtered_html = (
            f"<h2>3. Filtered Analysis</h2>"
            f"<p>All {len(all_models)} models have ≥{min_sims} simulations — "
            f"no filtering needed. Same as full analysis above.</p>"
        )

    # ── Assemble final HTML ───────────────────────────────────────
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

{data_status_html}

{full_html}

{filtered_html}

<h2>4. Reward Distributions</h2>
{reward_dist_html}

<hr />
<p style="color: #888; font-size: 0.9em;">
  Generated from <code>results/correlation_results.json</code>.
  Re-run <code>python generate_report.py</code> or <code>python run_comparison.py</code> to refresh.
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
    parser.add_argument("--min-sims", type=int, default=DEFAULT_MIN_SIMS,
                        help=f"Min tau2 simulations for filtered section (default: {DEFAULT_MIN_SIMS})")
    parser.add_argument("--open", action="store_true",
                        help="Auto-open report in browser after generation")
    args = parser.parse_args()

    out_path = args.out or (args.results_dir / "report.html")

    print(f"Loading results from {args.results_dir}/")
    html = build_report(args.results_dir, args.sim_dir, min_sims=args.min_sims)
    if html:
        out_path.write_text(html)
        print(f"Report saved to {out_path}")
        if args.open:
            import webbrowser
            webbrowser.open(out_path.as_uri())
    else:
        print("Not enough data for report (need ≥2 models with both tau2 + p2m).")


if __name__ == "__main__":
    main()
