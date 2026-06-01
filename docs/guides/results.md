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
- `metrics.json`: pipeline token-usage telemetry (API calls, token counts, cache stats, timing)

## Fast inspection order

1. `scores.jsonl`
2. `inference_set.jsonl`
3. `config.yaml`
4. `metrics.json`

## Useful CLI commands

```bash
assert-ai results list
assert-ai results status <suite>
assert-ai results status <suite> <run>
assert-ai results compare <suite> <run-a> <run-b>
assert-ai results compare-suites <suite-a>/<run-a> <suite-b>/<run-b>
```

See `docs/cli/commands.md` for full options.
