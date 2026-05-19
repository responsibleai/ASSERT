# Telecom τ²-bench Correlation Study

Evaluates whether p2m's LLM-judged scores correlate with τ²-bench's
deterministic scores on the **telecom customer service** domain.

## Motivation

τ²-bench (arXiv:2506.07982) uses curated task scenarios with
database-level ground truth to score agents deterministically. p2m uses
LLM-generated test cases with LLM-judged scoring. If the two rankings
agree across a range of models (weak → strong), p2m can be trusted as a
lightweight proxy for domains where deterministic evaluation harnesses
don't exist.

## What's in this directory

| File | Purpose |
|---|---|
| `concept.md` | Telecom agent behavior spec (derived from τ²-bench's `main_policy.md`) |
| `telecom_tools.yaml` | 14 agent tool schemas in p2m YAML format |
| `eval_config.yaml` | p2m pipeline config (policy → seeds → rollout → judge) |
| `run_correlation.py` | Orchestration script — runs tau2, p2m, and correlation analysis |

## Prerequisites

p2m is installed as part of adaptive-eval (`pip install -e ".[otel]"`).

τ²-bench is a **separate package** that must be installed independently:

```bash
# Install tau2 from the public repo
pip install "tau2 @ git+https://github.com/SEACrowd/tau3-bench.git"
```

Both `p2m` and `tau2` CLIs must be on your PATH (or in your active venv).

You also need Azure OpenAI credentials:

```bash
export AZURE_API_KEY="..."
export AZURE_API_BASE="https://..."
```

## Running the p2m evaluation

```bash
# Single model (edit rollout.target.model in eval_config.yaml)
p2m run --config examples/telecom_tau2_correlation/eval_config.yaml

# Override model and run name from CLI
p2m run --config examples/telecom_tau2_correlation/eval_config.yaml \
  --force-stage rollout --force-stage judge
```

## Running the full correlation study

The `run_correlation.py` script automates the end-to-end workflow:

```bash
# All three stages (tau2 → p2m → correlate) with 7 default models
python examples/telecom_tau2_correlation/run_correlation.py

# Selective stages — reuse existing tau2 results, only run p2m + correlate
python examples/telecom_tau2_correlation/run_correlation.py --stages p2m,correlate

# Custom model set
python examples/telecom_tau2_correlation/run_correlation.py \
  --models azure/gpt-4o-mini azure/gpt-4o azure/gpt-5.4

# Dry-run to see what commands would execute
python examples/telecom_tau2_correlation/run_correlation.py --dry-run

# Verbose logging
python examples/telecom_tau2_correlation/run_correlation.py -v
```

### Stages

| Stage | What it does |
|---|---|
| `tau2` | Run τ²-bench on the telecom domain for each model |
| `p2m` | Run p2m evaluation with the eval_config for each model |
| `correlate` | Compute Spearman rank correlation and print summary |

Stages can be run independently. The `correlate` stage loads prior results
from `results/correlation_results.json` for any stages not included.

## Correlation study workflow

1. **Run τ²-bench** on the telecom domain for each model → collect
   per-model `reward` (binary accuracy).
2. **Run p2m** with this config for each model → collect per-model
   aggregate scores from `metrics.json`.
3. **Compute Spearman rank correlation** between τ²-bench rewards and
   p2m dimension scores across the model set.

### Suggested model set

| Model | Expected quality |
|---|---|
| `azure/gpt-4o-mini` | Weak |
| `azure/gpt-4o` | Medium |
| `azure/gpt-5.4-nano` | Medium |
| `azure/gpt-5.4-mini` | Medium-strong |
| `azure/gpt-5.4` | Strong |
| `azure/claude-sonnet-4` | Strong |
| `azure/claude-opus-4` | Very strong |

A wider quality spread produces a more informative correlation.

## Design decisions

- **Simulated tools** (not real tau2 tools): p2m generates its own test
  scenarios via the seeds pipeline, so it cannot reuse tau2's pre-configured
  database states. The tool simulator responds realistically based on tool
  descriptions.
- **15 behavior categories** (`behavior_count: 15`): covers the 7 operational
  areas plus edge cases, procedure violations, and safety scenarios.
- **4 judge dimensions**: `workflow_violation`, `policy_adherence`,
  `communication_quality`, `escalation_judgment` — designed to capture the
  same failure modes that tau2's deterministic scoring catches.
- **50 prompts + 20 scenarios**: provides enough signal for reliable
  per-model aggregate scores while keeping cost manageable.
