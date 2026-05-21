# Telecom tau2-bench / p2m Correlation Study

Evaluates whether p2m's LLM-judged scores correlate with tau2-bench's
deterministic scores on the **telecom customer service** domain.

## Motivation

tau2-bench (arXiv:2506.07982) uses curated task scenarios with
database-level ground truth to score agents deterministically. p2m uses
LLM-generated test cases with LLM-judged scoring. This study checks
whether the two produce **similar relative model rankings** despite
using fundamentally different test methodologies.

**Important caveat:** tau2 evaluates agents against pre-configured
database states with deterministic tool backends. p2m generates its
own test scenarios and uses simulated tool responses based on tool
descriptions. This study measures whether the two approaches produce
similar relative model rankings despite different methodologies.

## What's in this directory

| File | Purpose |
|---|---|
| `eval_config.yaml` | p2m pipeline config; the `behavior:` block contains the telecom agent spec (derived from tau2-bench's main_policy.md) |
| `telecom_tools.yaml` | 14 agent tool schemas in p2m YAML format |
| `models.yaml` | Model inventory, endpoint mapping, and presets |
| `run_comparison.py` | Orchestration script -- runs tau2, p2m, and correlation analysis |
| `analysis_report.ipynb` | Jupyter notebook with tables, charts, and statistical interpretation |
| `generate_report.py` | Standalone HTML report generator (no notebook server needed) |
| `smoke_test.py` | Quick connectivity check for each configured endpoint |

## Prerequisites

p2m is installed as part of adaptive-eval (`pip install -e ".[otel]"`).

tau2-bench is a **separate package** that must be installed independently:

```bash
pip install "tau2 @ git+https://github.com/SEACrowd/tau3-bench.git"
```

Both `p2m` and `tau2` CLIs must be on your PATH (or in your active venv).

### tau2-bench domain data

tau2-bench's pip package does **not** include its domain data files.
You need the data from the tau3-bench git repository:

```bash
# Option A: Clone the repo and symlink the data directory (recommended)
git clone --depth 1 https://github.com/SEACrowd/tau3-bench.git /tmp/tau3-bench
ln -s /tmp/tau3-bench/data examples/telecom_tau2_correlation/data

# Option B: Point to an existing clone
export TAU2_DATA_DIR=/path/to/your/tau3-bench/data
```

The script looks for data at `examples/telecom_tau2_correlation/data/`
by default, or at the path specified by `TAU2_DATA_DIR`. It will print
setup instructions and exit if the telecom domain files are missing.

### Azure OpenAI credentials

Models are deployed across multiple Azure OpenAI endpoints, each with
its own API key. Set the env vars referenced in `models.yaml` (or add
them to `.env` at the repo root):

```bash
# westus2 endpoint — DeepSeek, Grok, gpt-oss models
export AZURE_API_KEY_WESTUS2="..."
export AZURE_API_BASE_WESTUS2="https://..."

# australiaeast endpoint — gpt-5.4-mini, gpt-5.4
export AZURE_API_KEY_AUSTRALIAEAST="..."
export AZURE_API_BASE_AUSTRALIAEAST="https://..."

# default fallback (used when a model has no explicit endpoint)
export AZURE_API_KEY="..."
export AZURE_API_BASE="https://..."
```

Verify endpoints before running the full study:

```bash
python examples/telecom_tau2_correlation/smoke_test.py          # test all
python examples/telecom_tau2_correlation/smoke_test.py westus2  # test one
python examples/telecom_tau2_correlation/smoke_test.py --list   # show config
```

## Quick start

```bash
# Quick validation -- 4 models, 10 test cases, 2 tau2 trials
python examples/telecom_tau2_correlation/run_comparison.py --preset quick

# Medium run -- 3 models, 70 test cases, 4 tau2 trials
python examples/telecom_tau2_correlation/run_comparison.py --preset mini

# Full study -- all models, 70 test cases, 4 tau2 trials
python examples/telecom_tau2_correlation/run_comparison.py --preset full

# Dry-run to preview commands
python examples/telecom_tau2_correlation/run_comparison.py --preset quick --dry-run
```

## CLI reference

```
python run_comparison.py [options]

Options:
  --preset {quick,mini,full}  Model set + override bundle from models.yaml
  --stages STAGES         Comma-separated: tau2,p2m,correlate (default: all)
  --models MODEL [...]    Override preset model selection
  --trials N              tau2 trials per task (default: 4)
  --concurrency N         Max concurrent tau2 tasks (default: 10)
  --tau2-retries N        Max tau2 retry attempts per model (default: 10)
  --test-cases N          Override p2m test_set prompt sample_size
  --user-model MODEL      LLM for tau2 user simulator
  --log-file PATH         Write log output to file (default: logs/run_YYYYMMDD_HHMMSS.log)
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
| `mini` | 3 (one per tier) | 70 | 4 | gpt-5.4 |
| `full` | all deployed | 70 | 4 | gpt-5.4 |

CLI flags override preset values when both are provided.

## Stages

| Stage | What it does |
|---|---|
| `tau2` | Run tau2-bench on the telecom domain for each model (with retry) |
| `p2m` | Run p2m evaluation with eval_config for each model |
| `correlate` | Compute Spearman rank correlation with p-values and sample sizes |

Stages skip models that already have results on disk (use `--force` to
re-run). Intermediate results are saved after each model completes.

## Results

Results are written to `results/` within this directory:

- `data/simulations/telecom_{slug}.json` -- tau2-bench simulation outputs
- `results/correlation_results.json` -- final correlation data
- `results/tau2_rewards.json` -- tau2 mean rewards per model
- `results/p2m_scores.json` -- p2m judged scores per model
- `artifacts/results/telecom-tau2-correlation/` -- p2m run artifacts

## Report generation

The pipeline includes a `report` stage that automatically generates HTML reports
after correlate. Reports are also generated standalone:

```bash
# Automatic — included in the default pipeline stages (tau2,p2m,correlate,report)
python run_comparison.py --preset quick

# Jupyter notebook — interactive, with tables and charts
jupyter notebook examples/telecom_tau2_correlation/analysis_report.ipynb

# HTML reports — standalone, no notebook server needed
python examples/telecom_tau2_correlation/generate_report.py

# Custom output path and sim threshold
python examples/telecom_tau2_correlation/generate_report.py --out my_report.html --min-sims 100

# Auto-open in browser
python examples/telecom_tau2_correlation/generate_report.py --open
```

A single `results/report.html` is generated containing:

- **Data status** — all tau2/p2m models, coverage gaps, simulation counts
- **Full analysis** — correlations across all overlapping models
- **Filtered analysis** — only models with ≥50 tau2 simulations
  (threshold configurable via `--min-sims`)
- **Reward distributions** — histograms from raw simulation data

Each analysis section includes:

- **Model overview table** with tau2 rewards, p2m scores, and sample sizes
- **Bar charts** comparing tau2 vs p2m performance across models
- **Correlation heatmap** showing Spearman ρ and p-values per dimension
- **p2m dimension breakdown** with per-model violation rates
- **Scatter plots** for each dimension (tau2 reward vs p2m score)
- **Reward distribution histograms** per model (from raw simulation data)
- **Per-task analysis** highlighting tasks where models disagree most
- **Data quality checks** flagging low sample sizes and power concerns

Both formats read from `results/correlation_results.json` and
`data/simulations/` — re-run after each pipeline execution to refresh.

## Design decisions

- **Simulated tools** (not real tau2 tools): see the caveat in
  Motivation above. p2m generates its own test scenarios via the
  test_set pipeline and uses simulated tool responses, so it does not
  reuse tau2's pre-configured database states.
- **4 judge dimensions**: `workflow_violation`, `policy_adherence`,
  `communication_quality`, `escalation_judgment` -- designed to capture
  the same failure modes that tau2's deterministic scoring catches.
- **Multi-endpoint support**: Models are deployed across Azure regions.
  `models.yaml` maps each model to its endpoint env var.
- **tau2 retry loop**: tau2-bench can crash mid-run due to malformed LLM
  responses. The script retries up to `--tau2-retries` times per model,
  leveraging tau2's built-in resume (skips completed tasks on re-run).
  A completion table is printed after the tau2 stage.
- **Enhanced correlation report**: The correlate stage reports Spearman
  rho, p-values, significance markers, and per-model tau2 sample sizes.
  Models with low sample completion (< 50%) are flagged with a
  suggested re-run command.

## Inspecting results

After a run completes (or partially completes), use these commands to
check data completeness and review analysis output.

### Tau2 simulation completeness

Each model's sim file lives at `data/simulations/telecom_{slug}.json`.
Check how many simulations completed vs expected (`114 tasks × num_trials`):

```bash
# Summary for all sim files
for f in data/simulations/telecom_*.json; do
  slug=$(basename "$f" .json | sed 's/telecom_//')
  trials=$(python3 -c "import json; print(json.load(open('$f'))['info']['num_trials'])")
  count=$(python3 -c "import json; print(len(json.load(open('$f'))['simulations']))")
  expected=$((114 * trials))
  pct=$((count * 100 / expected))
  echo "$slug: $count / $expected sims (${pct}%, ${trials} trials)"
done
```

Example output:
```
gpt-5.4: 456 / 456 sims (100%, 4 trials)
gpt-oss-120b: 9 / 456 sims (1%, 4 trials)
grok-4: 57 / 456 sims (12%, 4 trials)
```

Models below ~80% completion produce unreliable tau2 reward estimates.
Re-run with more retries to push them toward completion:

```bash
python run_comparison.py --preset mini --stages tau2 --tau2-retries 10 --yes
```

Tau2 has built-in resume -- it reads the existing sim file and only
runs missing task/trial combinations.

### Correlation analysis output

Results are written to `results/` as JSON files:

```bash
# Tau2 deterministic rewards per model
python3 -m json.tool results/tau2_rewards.json

# P2m LLM-judged scores per model (per dimension + overall)
python3 -m json.tool results/p2m_scores.json

# Spearman correlations, sample sizes, and per-dimension breakdowns
python3 -m json.tool results/correlation_results.json
```

Key fields in `correlation_results.json`:

| Field | Description |
|---|---|
| `tau2_rewards` | Mean reward per model (0-1 scale) |
| `tau2_sample_sizes` | Number of completed sims per model |
| `p2m_scores` | Per-dimension + `_overall` scores per model |
| `correlations` | Spearman `rho`, `pval`, and `n` per dimension |

Interpreting the correlations:

- **rho** close to ±1 = strong monotonic agreement between tau2 and p2m
- **pval < 0.05** = statistically significant at 95% confidence
- **n** = number of models compared (need n ≥ 10 for reliable inference,
  n ≥ 20 recommended)

### P2m evaluation artifacts

P2m writes detailed artifacts per model under the adaptive-eval
artifacts directory:

```bash
ls ../../artifacts/results/telecom-tau2-correlation/
```

Each model subfolder contains `metrics.json`, `scores.jsonl`,
`inference_set.jsonl`, and the generated `test_set.jsonl`.

### Run logs

When file logging is enabled (default), logs are written to `logs/`:

```bash
ls -lt logs/   # most recent log first
tail -100 logs/run_*.log   # check the latest run
```

## Troubleshooting

### Phoenix/OTEL connection errors

If you see `Failed to export traces to localhost:4317` errors, Phoenix
isn't running. These are harmless -- trace export is optional and the
script suppresses them automatically. To enable tracing:

```bash
uv run phoenix serve   # start Phoenix in a separate terminal
```
