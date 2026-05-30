# Config Schema

This page summarizes the `eval_config.yaml` schema and points to the complete reference.

For full field-by-field constraints and defaults, use `CONFIG_REFERENCE.md`.

## Top-level keys

- `suite`: optional string
- `run`: optional string
- `behavior`: object
- `context`: optional string
- `default_model`: optional model object
- `pipeline`: object

## `behavior`

- `name`: required when behavior is used
- `description`: required when `systematize` is enabled

## `pipeline.systematize`

- `behavior_category_count`: optional positive integer
- `web_search`: optional boolean
- `model`: required unless `default_model` is provided

Outputs include `systematization.json` and `taxonomy.json`.

## `pipeline.test_set`

- `prompt.sample_size`: optional integer
- `scenario.sample_size`: optional integer
- `stratify.dimensions`: optional dimension list
- `stratify.level_count`: optional integer
- `model`: optional stage fallback model
- `tool_source`: optional (`runtime` or `per_test_case`)

At least one of `prompt` or `scenario` must be configured.

Output: `test_set.jsonl`.

## `pipeline.inference`

`target` is required when inference is enabled, and must include exactly one of:

- `target.model`
- `target.callable`
- `target.endpoint`

Optional target fields:

- `target.system_prompt`
- `target.trace` (`backend`, `group_by`)
- `target.tools` (`module`, `toolset`, `simulator`) when using `target.model`

Other inference keys:

- `tester.model`
- `max_turns`
- `concurrency`
- `max_tool_calls`

Output: `inference_set.jsonl`.

## `pipeline.judge`

- `model`: required unless `default_model` is provided
- `n`: optional number of judge samples
- `dimensions`: map of dimension definitions

Each custom dimension contains:

- `description`
- `rubric`

Outputs: `scores.jsonl` and `metrics.json`.

## Model object shape

Most stage model blocks share this shape:

- `name` (required)
- `temperature` (optional)
- `max_tokens` (optional)
- `reasoning_effort` (optional)
