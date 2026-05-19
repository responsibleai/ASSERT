# p2m/analysis

Post-hoc analysis of pipeline results. Each module computes metrics from the JSONL artifacts that the pipeline stages produce. All functions accept plain dicts and return plain dicts.

## Modules

**stats.py** — Confidence intervals and rate aggregation. Wilson score intervals for single-run proportions, cluster bootstrap when observations are grouped (e.g. multiple inferences per seed). Also provides macro-averaging across behavior groups with configurable minimum support.

**inference_metrics.py** — Inference-stage health metrics from `transcripts.jsonl`. Stop-reason distribution, turn counts (mean/median/p95), completion rate, invalid-tester-turn rate, per-behavior breakdowns.

**stability.py** — Outcome stability across runs. Two analyses that must not be conflated: *repeatability* (same tester, same test_set, different inferences — measures inference stochasticity) and *cross-tester variation* (different testers, same test_set — measures how much tester choice affects results).

**suite_analysis.py** — Orchestrator that loads a suite directory, computes inference, judge, and stability metrics for all runs, and formats a human-readable terminal summary. The summary is structured: comparison table → cross-tester variation → repeatability → non-main runs → per-run behavior detail.

## Usage

```python
from p2m.analysis.suite_analysis import analyze_suite, format_suite_summary

results = analyze_suite("artifacts/results/relationship-entanglement-v1")
print(format_suite_summary(results))
```

The `results` dict contains structured data for programmatic use. The `format_suite_summary` function produces a terminal-readable report.

## What the output tells you

The summary answers four questions about a suite of evaluation runs:

1. **How often does the target violate taxonomy?** Per-run violation rates with 90% Wilson CIs. Only behavior_categories with n≥5 are shown individually; sparse behavior_categories are collapsed.

2. **How much does tester choice matter?** Cross-tester variation shows the spread in violation rates across different tester models on the same test_set (main runs only).

3. **How stable are results across reruns?** Repeatability shows what fraction of shared test_set produce the same outcome across runs of the same configuration. Agreement is computed on shared test_set only.

4. **Is the run data trustworthy?** Run quality flags (high/medium/low) surface invalid-tester-turn rates, target errors, and judge failures.

## Dependencies

`stats.py` requires numpy (installed via `uv sync --extra analysis`). The other modules use only the standard library.
