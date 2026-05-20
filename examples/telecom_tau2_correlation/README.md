# Telecom tau2-bench / p2m Correlation Study

Evaluates whether p2m's LLM-judged scores correlate with tau2-bench's
deterministic scores on the **telecom customer service** domain.

## Motivation

tau2-bench (arXiv:2506.07982) uses curated task scenarios with
database-level ground truth to score agents deterministically. p2m uses
LLM-generated test cases with LLM-judged scoring. If the two rankings
agree across a range of models (weak to strong), p2m can be trusted as a
lightweight proxy for domains where deterministic evaluation harnesses
don't exist.

## What's in this directory

| File | Purpose |
|---|---|
| `concept.md` | Telecom agent behavior spec (derived from tau2-bench's main_policy.md) |
| `telecom_tools.yaml` | 14 agent tool schemas in p2m YAML format |
| `eval_config.yaml` | p2m pipeline config (systematize / test_set / inference / judge) |
| `models.yaml` | Model inventory, endpoint mapping, and presets |
| `run_comparison.py` | Orchestration script -- runs tau2, p2m, and correlation analysis |

## Prerequisites

p2m is installed as part of adaptive-eval (`pip install -e ".[otel]"`).

tau2-bench is a **separate package** that must be installed independently:

```bash
pip install "tau2 @ git+https://github.com/SEACrowd/tau3-bench.git"
```

Both `p2m` and `tau2` CLIs must be on your PATH (or in your active venv).

### Azure OpenAI credentials

Models are deployed across multiple Azure OpenAI endpoints. Set the
env vars referenced in `models.yaml`:

```bash
export AZURE_API_KEY="..."
export AZURE_API_BASE_WESTUS2="https://..."      # DeepSeek, Grok, gpt-oss
export AZURE_API_BASE_AUSTRALIAEAST="https://..." # gpt-5.4-mini, gpt-5.4
```

A `default` endpoint (`AZURE_API_BASE`) is used as fallback when no
model-specific endpoint is configured.

## Quick start

```bash
# Quick validation -- 4 models, 10 test cases, 2 tau2 trials
python examples/telecom_tau2_correlation/run_comparison.py --preset quick

# Full study -- 8 models, 70 test cases, 4 tau2 trials
python examples/telecom_tau2_correlation/run_comparison.py --preset full

# Dry-run to preview commands
python examples/telecom_tau2_correlation/run_comparison.py --preset quick --dry-run
```

## CLI reference

```
python run_comparison.py [options]

Options:
  --preset {quick,full}   Model set + override bundle from models.yaml
  --stages STAGES         Comma-separated: tau2,p2m,correlate (default: all)
  --models MODEL [...]    Override preset model selection
  --trials N              tau2 trials per task (default: 4)
  --concurrency N         Max concurrent tau2 tasks (default: 10)
  --test-cases N          Override p2m test_set prompt sample_size
  --user-model MODEL      LLM for tau2 user simulator
  --force                 Re-run models even if results exist
  --dry-run               Print commands without executing
  -y, --yes               Skip confirmation prompts
  -v, --verbose           Debug-level logging
```

### Selective stages

```bash
# Reuse existing tau2 results, only run p2m + correlate
python run_comparison.py --preset quick --stages p2m,correlate

# Just recompute correlation from existing results
python run_comparison.py --stages correlate
```

### Custom model set

```bash
python run_comparison.py --models azure/gpt-5.4-mini azure/gpt-5.4
```

## Presets

Defined in `models.yaml`. Each preset selects a subset of models and
sets default values for `trials`, `concurrency`, `test_cases`,
`max_turns`, and `judge_model`:

| Preset | Models | test_cases | trials | judge_model |
|---|---|---|---|---|
| `quick` | 4 (across tiers) | 10 | 2 | gpt-5.4-mini |
| `full` | 8 (all deployed) | 70 | 4 | gpt-5.4 |

CLI flags override preset values when both are provided.

## Stages

| Stage | What it does |
|---|---|
| `tau2` | Run tau2-bench on the telecom domain for each model |
| `p2m` | Run p2m evaluation with eval_config for each model |
| `correlate` | Compute Spearman rank correlation and print summary |

Stages skip models that already have results on disk (use `--force` to
re-run). Intermediate results are saved after each model completes.

## Results

Results are written to `results/` within this directory:

- `results/tau2/{model_slug}/` -- tau2-bench simulation outputs
- `results/correlation_results.json` -- final correlation data
- `artifacts/results/telecom-tau2-correlation-v1/` -- p2m run artifacts

## Design decisions

- **Simulated tools** (not real tau2 tools): p2m generates its own test
  scenarios via the test_set pipeline, so it cannot reuse tau2's
  pre-configured database states. The tool simulator responds
  realistically based on tool descriptions.
- **4 judge dimensions**: `workflow_violation`, `policy_adherence`,
  `communication_quality`, `escalation_judgment` -- designed to capture
  the same failure modes that tau2's deterministic scoring catches.
- **Multi-endpoint support**: Models are deployed across Azure regions.
  `models.yaml` maps each model to its endpoint env var.
