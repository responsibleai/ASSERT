# CLI Overview

The canonical command is:

```bash
assert-ai
```

Use CLI flows to create, run, inspect, and compare evaluations.

## Typical workflow

1. Design config:

```bash
assert-ai init --model azure/gpt-4o
```

1. Run pipeline:

```bash
assert-ai run --config <path-to-eval_config.yaml>
```

1. Inspect results:

```bash
assert-ai results status <suite> <run>
```

1. Compare runs:

```bash
assert-ai results compare <suite> <run-a> <run-b>
```

## Command groups

- `init`: interactive config generation assistant
- `run`: execute pipeline stages
- `results`: list/status/compare suites and runs
- `analysis`: post-hoc metrics commands
- `judge-traces`: score pre-collected OTel traces
- `library`: browse built-in behavior/judge presets

## Global options

Top-level options:

- `-v`, `--verbose`
- `-q`, `--quiet`
- `--log-file <path>`
- `--output text|json`

For full syntax and required/optional flags per command, see `docs/cli/commands.md`.
