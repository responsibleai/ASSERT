# Troubleshooting

Use this flow when a run fails or scores look wrong.

## 1) Identify where failure happened

Check status first:

```bash
assert-eval results status <suite> <run>
```

Inspect `manifest.json` to confirm the failing stage.

## 2) Debug low-quality or failing judgments

Start with `scores.jsonl` and look for:

- failing judge dimension
- cited evidence turns
- behavior category under test
- trace/tool references (if present)

Then inspect matching rows in `inference_set.jsonl`.

## 3) Debug stage inputs

- `systematize` issues: inspect `taxonomy.json`
- `test_set` issues: inspect `test_set.jsonl` and stratification choices
- `inference` issues: inspect `inference_set.jsonl` events and outputs
- `judge` issues: inspect `scores.jsonl` plus rubrics in `config.yaml`

## 4) Re-run only what changed

If you changed inputs for a stage, force rerun from that stage:

```bash
assert-eval run --config <config-path> --force-stage <stage-name>
```

Common examples:

- changed behavior spec: `--force-stage systematize`
- changed dimensions/sample sizing: `--force-stage test_set`
- changed target: `--force-stage inference`
- changed judge rubrics/model: `--force-stage judge`

## 5) Common root causes

- Missing model credentials in `.env`
- Target callable import path typo
- Non-instrumented target when trace-level evidence is expected
- Overly vague judge rubrics causing weak verdict evidence
- stale artifacts reused without forcing the correct stage

## 6) Helpful comparisons

Compare runs to spot regressions:

```bash
assert-eval results compare <suite> <run-a> <run-b>
assert-eval results compare-suites <suite-a>/<run-a> <suite-b>/<run-b>
```

## 7) Environment-specific fixes

- macOS `litellm` install issue (`AttributeError: module 'litellm' has no attribute 'acompletion'`): some macOS security tooling can silently truncate wheels during `uv sync`. The `pip install -e ".[otel,langgraph]"` path avoids this. If you must use `uv`, grant your terminal Full Disk Access and run `xattr -cr .venv`.
- Windows `UnicodeEncodeError` when running auto-trace demos: set `$env:PYTHONUTF8 = "1"` before `python -m examples.phoenix_auto_trace.travel_openai`.
- Docker-backed Prompt Agent configs fail with `docker daemon unavailable`: ensure Docker Desktop is running for `examples/prompt_agents/health_assistant_sandbox.yaml` and `examples/prompt_agents/health_assistant_external.yaml`.
