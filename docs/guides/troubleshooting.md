# Troubleshooting Guide

Use this flow when a run fails or scores look wrong.

## 1. Identify where failure happened

Check status first:

```bash
assert-ai results status <suite> <run>
```

Inspect `manifest.json` to confirm the failing stage.

## 2. Debug low-quality or failing judgments

Start with `scores.jsonl` and look for:

- failing judge dimension
- cited evidence turns
- behavior category under test
- trace/tool references (if present)

Then inspect matching rows in `inference_set.jsonl` to see the full prompt and response or traces from the AI system inference stage with the generated test set.

## 3. Debug stage inputs

Often if the inputs are too vague or low-quality, then the resulting output can also lead to failures in the evaluation. Refer to the guidance on structuring high quality inputs in the [Best Practices and Limitations](../config/best-practices.md) documentation.

- `systematize` issues: inspect `taxonomy.json`
- `test_set` issues: inspect `test_set.jsonl` and stratification dimensions
- `inference` issues: inspect `inference_set.jsonl` events and outputs
- `judge` issues: inspect `scores.jsonl` plus judge dimensions/rubrics in `eval_config.yaml`

## 4. Re-run only what changed

If you changed inputs for a stage, force rerun from that stage:

```bash
assert-ai run --config <config-path> --force-stage <stage-name>
```

Common examples:

- changed behavior specification: `--force-stage systematize`
- changed dimensions/sample sizing: `--force-stage test_set`
- changed target: `--force-stage inference`
- changed judge rubrics/model: `--force-stage judge`

## 5. Common root causes for failures

- Missing model credentials in `.env` file
- Target callable import path typo
- Non-instrumented target when trace-level evidence is expected
- Overly vague judge dimensions and rubrics causing weak verdict evidence
- Stale artifacts reused without forcing the correct stage
- **macOS, `litellm` AttributeError after install** — some macOS security tooling can silently truncate the `litellm` wheel during extraction with `uv sync`, causing errors like `AttributeError: module 'litellm' has no attribute 'acompletion'`. The `pip install -e ".[otel,langgraph]"` path above uses copy-based installs and avoids this. If you must use `uv`, grant your terminal Full Disk Access and run `xattr -cr .venv` to clear quarantine attributes.
- **Windows, `UnicodeEncodeError` when running auto-trace demos** — set `$env:PYTHONUTF8 = "1"` before `python -m examples.phoenix_auto_trace.travel_openai`.
- **Docker-backed pipes fail with "docker daemon unavailable"** — `examples/pipes/health_assistant_sandbox.yaml` and `_external.yaml` need Docker Desktop running.

## 6.  Helpful comparisons

Compare runs to spot regressions:

```bash
assert-ai results compare <suite> <run-a> <run-b>
assert-ai results compare-suites <suite-a>/<run-a> <suite-b>/<run-b>
```

## 7. Environment-specific fixes

- macOS `litellm` install issue (`AttributeError: module 'litellm' has no attribute 'acompletion'`): some macOS security tooling can silently truncate wheels during package installation. The `pip install -e ".[otel,langgraph]"` path avoids this. If you hit it, grant your terminal Full Disk Access and run `xattr -cr .venv`.
- Windows `UnicodeEncodeError` when running auto-trace demos: set `$env:PYTHONUTF8 = "1"` before `python -m examples.phoenix_auto_trace.travel_openai`.
- Docker-backed Prompt Agent configs fail with `docker daemon unavailable`: ensure Docker Desktop is running for `examples/prompt_agents/health_assistant_sandbox.yaml` and `examples/prompt_agents/health_assistant_external.yaml`.
