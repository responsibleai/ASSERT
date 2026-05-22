---
title: CLI reference
description: The p2m command-line interface.
---

The `p2m` CLI is the primary surface. All commands are documented below.

## `p2m run`

Run a full pipeline from a config.

```bash
p2m run --config examples/travel_planner_langgraph/eval_config.yaml
```

Common flags:

- `--config <path>` — path to `eval_config.yaml` (required)
- `--force-stage <stage>` — bypass cache for a specific stage (`systematize`, `test_set`, `inference`, `judge`)

## `p2m results list`

List runs under `artifacts/results/`.

```bash
p2m results list
```

## `p2m results show`

Print a summary for a specific run.

```bash
p2m results show <suite> <run>
```

## `p2m results compare`

Compare two runs (within the same suite).

```bash
p2m results compare <suite> <run-a> <run-b>
```

## `p2m results compare-suites`

Compare runs across different suites.

```bash
p2m results compare-suites <suite-a>/<run-1> <suite-b>/<run-1>
```

## Notes

- The CLI and the viewer share artifact shapes but render results differently today. CLI / viewer alignment is tracked in [issue #58](https://github.com/microsoft/adaptive-eval/issues/58).
- The CLI uses Rich for table rendering. Set `NO_COLOR=1` to disable colors.
