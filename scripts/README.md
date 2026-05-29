# Scripts

Run scripts in this directory with `uv run python ...` from the repo root so they see the project package and pinned dependencies.

## Seed sampling

Test-set stratification and generation run through `assert-eval run` now. Start from
`examples/prompt_agents/health_assistant.yaml`, keep `pipeline.test_set.stratify` and
`pipeline.test_set`, then run:

```bash
source .env
uv run assert-eval run --config examples/prompt_agents/health_assistant.yaml
```

Use `uv run assert-eval --help` for CLI options.

## `benchmark.py`

Drives the full pipeline at a chosen `(test_set, concurrency)` point so you
can probe how throughput, latency, and rate-limit pressure scale. Each
invocation materializes a per-run working directory under
`artifacts/benchmark/<run_id>/`, applies overrides on top of
`examples/benchmark/eval_config.yaml`, calls `run_pipeline()` directly,
and appends a single summary row (timestamp, test_set, concurrency,
behavior_category_count, exit_code, wall_time, rate-limit cooldowns, scenarios
scored, judge dimension rates) to `artifacts/benchmark/results.csv`.

The base config is scenario-only by design — the inference/judge
concurrency knob is what this benchmark exists to exercise, and
multi-turn scenarios are the heavier shape that makes that knob bite.
`pipeline.systematize.behavior_category_count` auto-scales with `--test_set` (~10 test_set
per behavior, clamped to `[6, 50]`); pass `--behavior_categories` to override.

```bash
source .env
uv run python scripts/benchmark.py --test_set 100  --concurrency 10
uv run python scripts/benchmark.py --test_set 500  --concurrency 25
uv run python scripts/benchmark.py --test_set 1000 --concurrency 50
uv run python scripts/benchmark.py --test_set 5000 --concurrency 100
```

Successive runs accumulate in `artifacts/benchmark/results.csv` so you
can compare them at a glance. Pass `--no-csv` to skip the row when
spot-checking. Pass `--run-id <name>` to use a fixed run id (useful for
re-running with the same parameters after a code change).

## `turn_checkpoint_judge.py`

This script re-judges conversation prefixes from one completed run at fixed inference-turn checkpoints. It reads `<run>/inference_set.jsonl`, `<run>/config.yaml`, and the suite `taxonomy.json`, then writes checkpoint artifacts under `<run>/checkpoint_judge/` by default: `checkpoint_scores.jsonl`, `checkpoint_metrics.json`, and `<dimension>_by_turn.svg`.

```bash
uv run python scripts/turn_checkpoint_judge.py \
  --run-dir artifacts/results/<suite>/<run> \
  --checkpoint-step 5 \
  --dimension policy_violation
```

## `export_suite_results.py`

This script consolidates all runs in one suite into flat review tables under `artifacts/results/<suite>/exports/`. It reads the suite artifacts plus each run's `manifest.json`, `inference_set.jsonl`, and `scores.jsonl`, then derives the relevant-node summary directly from `scores.jsonl` and the suite `taxonomy.json`. `--format csv` writes `runs.csv`, `test_set.csv`, `conversations.csv`, `scores.csv`, and `relevant_nodes.csv`. `--format excel` writes `suite_results.xlsx`. `--format html` writes `suite_results.html`. If you omit `--format`, the script writes CSV only. Excel export requires `uv sync --extra analysis`.

```bash
uv run python scripts/export_suite_results.py \
  --suite relationship-entanglement-v1 \
  --format csv \
  --format excel \
  --format html
```

## `tester_pairwise_eval.py`

This script compares two tester inference runs on the same suite by matching scenario `test_case_id`s and judging two separate axes for each matched pair. The primary axis is persona realism: which conversation more convincingly impersonates the seeded person on persona embodiment, style/register fidelity, persona consistency, turn-to-turn coherence, escalation plausibility, and meta-leakage risk. The second axis is tester effectiveness: which conversation uses its turns more effectively to surface taxonomy-relevant evidence through stronger elicitation, adaptivity, evidence yield, pressure calibration, and turn efficiency. Both judge prompts are blinded to run IDs and inference metadata, and each axis is judged twice per pair: once as A/B and once as B/A. Only pairs whose winner and per-dimension winners stay consistent after swapping are counted in the aggregate results for that axis. The script writes `pairwise_scores.jsonl`, `pairwise_metrics.json`, `pairwise_summary.md`, and a judge-written `pairwise_eval.md` under `artifacts/tmp/<suite>/pairwise/<run-a>_vs_<run-b>/` by default. In `pairwise_scores.jsonl`, realism stays at the top level and the second axis is stored under `effectiveness_*` fields. In `pairwise_metrics.json`, the aggregate effectiveness block lives under the top-level `effectiveness` key.

```bash
uv run python scripts/tester_pairwise_eval.py \
  --run-a artifacts/results/<suite>/<run-a> \
  --run-b artifacts/results/<suite>/<run-b> \
  --judge-model azure/gpt-5.4
```

## `scenario_failure_prediction.py`

This script predicts taxonomy violations from scenario metadata before running conversations. It runs four stages: (0) per-test-case failure-rate distribution, (1) baselines (global rate, behavior rate, embedding nearest neighbor, logistic regression on embeddings), (2) zero-shot LLM forecaster with field ablations, (3) retrieval-augmented LLM forecaster. It also runs two robustness checks: within-behavior discrimination and tester transfer. The script auto-detects the primary tester (most common across runs) and uses the remaining runs for the transfer check. Intermediate results (embeddings, predictions) are cached under the output directory, so re-runs with `--skip-api` reuse them.

```bash
uv run python scripts/scenario_failure_prediction.py \
  --suite relationship-entanglement-v1 \
  --model gpt-5.4-mini
```

## `run_pairwise_expansion.sh`

Shell script that runs `tester_pairwise_eval.py` for new tester comparisons (GPT-5.4-mini and GPT-5.4-nano) against GPT-5.4 and GPT-5-railfree, across all 9 judge models. Skips comparisons whose output directory already exists. Requires all four tester runs to be complete (checks for `metrics.json`).

```bash
bash scripts/run_pairwise_expansion.sh
```

## `aggregate_pairwise_results.py`

Scans all `pairwise_metrics.json` files under `artifacts/tmp/relationship-entanglement-v1/pairwise/`, deduplicates by (comparison, judge), excludes GPT-5-nano, and writes a CSV table to stdout. The output has one row per (comparison, judge) with realism and effectiveness consistency rates and win counts.

```bash
uv run python scripts/aggregate_pairwise_results.py > pairwise_table.csv
```
