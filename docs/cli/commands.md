# CLI Commands

This page lists command signatures and key options.

## Base command

```bash
assert-ai [GLOBAL_OPTIONS] COMMAND [ARGS] [OPTIONS]
```

### Global options

- `-v`, `--verbose`
- `-q`, `--quiet`
- `--log-file <path>`
- `--output text|json`

## Command groups

- `init`: interactive config generation assistant
- `run`: execute pipeline stages
- `results`: list/status/compare suites and runs
- `analysis`: post-hoc metrics commands
- `judge-traces`: score pre-collected OTel traces
- `acs`: generate, validate, and regression-check ACS policies
- `library`: browse built-in behavior/judge presets

## `init`

Design an eval config with an LLM assistant.

```bash
assert-ai init [OPTIONS]
```

Options:

- `-o, --output <path>` optional, default `eval_config.yaml`
- `--describe <text>` optional
- `--describe-file <path>` optional, mutually exclusive with `--describe`; use for
  generated or multi-line text so shell quoting cannot mangle it
- `--from <path>` optional
- `--behavior <name>` optional
- `--judge-preset <name>` optional
- `--dimensions <csv>` optional
- `--model <litellm-model>` optional, default `azure/gpt-4o-mini`
- `--web-search / --no-web-search` optional, default `--web-search`. Lets the design agent do live web research via the OpenAI/Azure Responses API `web_search_preview` tool. Automatically falls back to a knowledge-only conversation when the design-agent model or region does not support it.
- `--env-file <path>` optional, default `.env`
- `--non-interactive` optional flag
- `--max-turns <int>` optional, default `20`
- `--force` optional flag
- `--dry-run` optional flag
- `--no-color` optional flag

## `run`

Run the evaluation pipeline from evaluation config YAML file.

```bash
assert-ai run --config <path> [OPTIONS]
```

Required:

- `--config <path>`

Optional:

- `--force-stage <stage>` repeatable (`systematize`, `test_set`, `inference`, `judge`)
- `--strict`
- `--override <key=value>` repeatable
- `-v`, `--verbose`
- `-q`, `--quiet`
- `--log-file <path>`
- `--output text|json`

## `results list`

List suites or list runs for one suite.

```bash
assert-ai results list [OPTIONS]
```

Options:

- `--results-dir <path>` optional
- `--suite <suite-id>` optional
- `--json` optional flag
- `--no-color` optional flag

## `results status`

Show suite summary or run details.

```bash
assert-ai results status <suite> [run] [OPTIONS]
```

Args:

- `suite` required
- `run` optional

Options:

- `--results-dir <path>` optional
- `--json` optional flag
- `--no-color` optional flag

## `results compare`

Compare runs in the same suite or across suites.

```bash
assert-ai results compare <suite> <run1> <run2> [run3 ...] [OPTIONS]
assert-ai results compare <suite1>/<run1> <suite2>/<run2> [suite3/run3 ...] [OPTIONS]
```

Options:

- `--results-dir <path>` optional
- `--metric <dimension>` optional; defaults to `policy_violation_not_permissible` when every compared run has permissibility-split data, otherwise `policy_violation`
- `--limit <int>` optional, default `8`
- `--json` optional flag
- `--no-color` optional flag

## `results compare-suites`

Compare named runs across different suites.

```bash
assert-ai results compare-suites <suite1>/<run1> <suite2>/<run2> [OPTIONS]
```

Options:

- `--results-dir <path>` optional
- `--metric <dimension>` optional; defaults to `policy_violation_not_permissible` when every compared run has permissibility-split data, otherwise `policy_violation`
- `--json` optional flag
- `--no-color` optional flag

## `analysis test-set-metrics`

Compute test-set coverage/diversity metrics.

```bash
assert-ai analysis test-set-metrics --taxonomy <path> --test_set <path> [OPTIONS]
```

Required:

- `--taxonomy <path>`
- `--test_set <path>`

Optional:

- `--embed-model <name>` default `text-embedding-3-large`
- `--embed-backend openai|hf` default `openai`
- `--k <int>` repeatable
- `--example-distance-thresh <float>` default `0.2`
- `--presence-coverage` flag
- `--out-json <path>` default `artifacts/analysis/test_set_metrics.json`
- `--out-md <path>`

## `judge-traces`

Judge pre-collected OTLP JSON traces without invoking the target, generating test cases,
or regenerating the taxonomy. This command calls the configured judge model and incurs
its normal cost. Use `--parse-only` for conversion without model calls.

```bash
assert-ai judge-traces --traces <path> --config <path> [OPTIONS]
```

Required:

- `--traces <path>`
- `--config <path>` with an enabled `pipeline.judge`, a judge model (or `default_model`),
  and an existing taxonomy. Relative `taxonomy_path` values resolve from this config.

Optional:

- `--group-by <attribute>` default `session.id`
- `--output <results>/<suite>/<run>` overrides the run directory. Otherwise the command
  uses the config's normal `results_dir`, `suite`, and `run`.
- `--parse-only` preserves the earlier conversion-only behavior. Its output directory
  contains parsed rows, not a scored run.

Minimal judge-only config:

```yaml
suite: imported-traces
run: baseline-1
pipeline:
  judge:
    model:
      name: azure/my-judge-deployment
    taxonomy_path: ./taxonomy.json
```

Provide the judge's usual provider credentials through the environment. Neither agent
credentials nor a target configuration are needed. Upstream stages in a supplied config
are ignored. The taxonomy must contain non-empty `behavior_categories`; use reviewed
categories and permissibility labels for the behavior being evaluated.

The command writes standard `inference_set.jsonl`, `scores.jsonl`, a judge-only
`config.yaml`, `manifest.json`, and viewer artifacts. It also writes `trace_import.json`
with the source hash and grouping choice. Scores carry an `inference_row_sha256` binding
them to their exact imported row. Tool arguments/results and source trace/span IDs stay
in the local artifacts; review access and retention before importing sensitive traces.

Use a dedicated trace suite. Existing run directories, differing suite taxonomies, and
suites with generated artifact versions are rejected rather than overwritten. Select a
new run for another cohort and a new suite when the taxonomy changes.

The exit status reports evaluation completeness, not whether the agent was safe. It is
nonzero for missing evidence, skipped/failed judgments, or provider errors, with counts
printed separately. Successful judgments that find violations do not fail the command.
Request text is retained where the export provides it, but a response-only export cannot
establish request context or prove tool enforcement. A recorded tool request without an
execution receipt is an attempted action, not proof of a side effect.

Imports retain final outputs recorded on root chain/agent spans and structured tool
requests in message history. Matching parent/child outputs are deduplicated only when
the span lineage identifies the child; tool history is correlated by call ID, name,
and arguments. Conflicting recorded results or contradictory causal relationships
at tied timestamps fail the import instead of choosing silently.
Evidence eligibility is checked against the reconstructed transcript,
so a source field that the parser cannot represent does not make an empty row scoreable.
Repeated history retains its established action bindings across intervening wrappers.
It cannot transfer a receipt to an earlier-captured or incomplete action; replacing
a captured binding requires a later capture that records the matching receipt.

## `acs generate`

Requires the `acs` extra: `python -m pip install -e ".[acs]"` (editable install of the ASSERT
repo itself — this is not yet published as an installable extra on PyPI's `assert-ai` package).

Generate a deployable ACS policy from an ASSERT run.

```bash
assert-ai acs generate [OPTIONS]
```

Required:

- `--run-dir <path>` or `--suite <suite> --run <run>`

Optional:

- `--out <path>`
- `--min-rate <float>` default `0.0`
- `--min-count <int>` default `1`
- `--model <name>`
- `--lm-kind assert|openai-compatible` default `assert`
- `--strict/--no-strict` default `--no-strict`
- `--validate/--no-validate` default `--validate`
- `--fail-on-allow` optional flag
- `--require-block` optional flag

## `acs validate`

Validate an ACS manifest against an ASSERT run.

```bash
assert-ai acs validate --manifest <path> [OPTIONS]
```

Required:

- `--manifest <path>`
- `--run-dir <path>` or `--suite <suite> --run <run>`

Optional:

- `--min-rate <float>` default `0.0`
- `--min-count <int>` default `1`
- `--max-cases <int>`
- `--fail-on-allow` optional flag
- `--require-block` optional flag

## `acs eval-config`

Generate an ASSERT eval config from an existing ACS manifest.

```bash
assert-ai acs eval-config --manifest <path> --target-callable <module:function> --out <path> [OPTIONS]
```

Required:

- `--manifest <path>`
- `--target-callable <module:function>`
- `--out <path>`

Optional:

- `--model <name>`

## `library list`

List available built-in presets.

```bash
assert-ai library list [OPTIONS]
```

Options:

- `-k, --kind behavior|judge_preset|scenario`
- `--json`
- `--no-color`

## `library show`

Show one preset.

```bash
assert-ai library show <name> [OPTIONS]
```

Options:

- `-k, --kind behavior|judge_preset|scenario`
- `--json`
