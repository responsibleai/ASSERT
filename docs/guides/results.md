# Results Guide

ASSERT writes local artifacts under:

```text
artifacts/results/<suite>/
```

Run-level outputs are under:

```text
artifacts/results/<suite>/<run>/
```

## Artifact layout

```text
artifacts/results/<suite>/
├── suite.json
├── taxonomy.json
├── test_set.jsonl
└── <run>/
    ├── manifest.json
    ├── config.yaml
    ├── inference_set.jsonl
    ├── scores.jsonl
    └── metrics.json
```

## What each file means

- `suite.json`: suite metadata
- `taxonomy.json`: behavior categories generated from your spec
- `test_set.jsonl`: generated prompt and scenario test cases
- `manifest.json`: stage-by-stage run status and timestamps
- `config.yaml`: frozen config snapshot used for this run
- `inference_set.jsonl`: target outputs plus trace references/events
- `scores.jsonl`: per-case judge verdicts, dimensions, and evidence
- `metrics.json`: aggregate rates by dimension and category

## Fast inspection order

1. `metrics.json`
2. `scores.jsonl`
3. `inference_set.jsonl`
4. `config.yaml`

## Useful CLI commands

```bash
assert-eval results list
assert-eval results status <suite>
assert-eval results status <suite> <run>
assert-eval results compare <suite> <run-a> <run-b>
assert-eval results compare-suites <suite-a>/<run-a> <suite-b>/<run-b>
```

See `docs/cli/commands.md` for full options.
