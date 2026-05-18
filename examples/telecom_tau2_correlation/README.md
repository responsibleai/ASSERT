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

## Running the p2m evaluation

```bash
# Single model (edit rollout.target.model in eval_config.yaml)
p2m run --config examples/telecom_tau2_correlation/eval_config.yaml

# Override model and run name from CLI
p2m run --config examples/telecom_tau2_correlation/eval_config.yaml \
  --set rollout.target.model.name=azure/gpt-4o \
  --set run=gpt-4o-eval
```

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
