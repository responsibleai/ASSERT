---
name: run-assert-eval
description: >
  Run an ASSERT evaluation from a plain-language behavior requirement.
  Use when the user wants to evaluate, test, or check an AI agent, LLM app,
  or model against requirements/policies (e.g. "evaluate my agent for budget
  violations", "test that the support bot never gives legal advice"). Generates
  or reuses an eval_config.yaml, runs the pipeline, and reports pass/violation
  rates with trace-cited failure examples.
---

# Run an ASSERT evaluation

## When to use

The user describes a behavior they want their agent or model to follow or avoid,
and wants evidence of how it actually behaves. Not for fixing the agent — this
skill finds and reports failures.

## Preconditions (check, don't assume)

1. **ASSERT installed**: `assert-ai --help` succeeds. If not, guide install:
   ```
   python -m pip install -e ".[otel,langgraph]"
   ```

2. **Provider creds exist** in `.env`. NEVER read or print `.env`. If a run fails
   with an auth error, tell the user which variable NAMES are required
   (AZURE_API_KEY, AZURE_API_BASE, OPENAI_API_KEY, etc.) — never their values.

## Steps

### 1. Get or make a config

- **If the user has an existing config**, use `--from <path>` to extend it:
  ```
  assert-ai init --from <path> --model <litellm-model> --non-interactive -o eval_config.yaml
  ```

- **If the user provides a plain-language requirement**, generate from scratch:
  ```
  assert-ai init --model <litellm-model> --describe "<user requirement + target context>" --non-interactive -o eval_config.yaml
  ```

- **If the spec is vague**, ask ONE clarifying question first — vague specs produce vague test sets.

- After generation, show the user the generated `behavior.description`, `context`,
  and `pipeline.judge` dimensions. Confirm before running.

### 2. Identify the target shape

Help the user set the right target in the config:

- **Framework agent** (LangGraph, CrewAI, etc.) with a Python entry function:
  use `target.callable` WITH `target.trace` so the judge can cite tool calls and routing.
- **Hosted model** with a system prompt and optional tools:
  use `target.model` and `target.tools`.
- **Pre-collected traces** (no live inference needed):
  use `assert-ai judge-traces --traces <path> --config <path>`.

### 3. Run the pipeline

```
assert-ai run --config eval_config.yaml --output json
```

This is long-running (systematize -> test_set -> inference -> judge). Stream status
to the user as each stage completes.

- To re-run from a specific stage: `--force-stage <stage>`
- Note the `suite` and `run` names from the config for Step 4.

### 4. Report results — never collapse to one number

1. **Headline rates**: run `assert-ai results status <suite> <run>` for per-dimension
   pass/violation rates. Report `policy_violation` and `overrefusal` SEPARATELY —
   they are two different problems.

2. **Top failing cases**: read `scores.jsonl` from `artifacts/results/<suite>/<run>/`.
   For each dimension with failures, pull 3-5 representative cases with:
   - The test case description (what was tested)
   - `verdict.dimensions` — which dimensions failed
   - `verdict.dimension_justifications` — the judge's rationale with cited evidence
   - `verdict.node_judgments` — which behavior categories were violated, with reasoning

3. **Cost and timing**: read `metrics.json` for token usage and elapsed time per stage.
   This file contains cost metadata only, not score roll-ups.

4. **Offer deeper inspection**: suggest `assert-ai results compare <suite> <runA> <runB>`
   to compare against a baseline, or open the local viewer.

## Output format

Present a short summary with this structure:

**Headline metrics** (per dimension):
- Policy violation rate: X% (N/M cases)
- Overrefusal rate: X% (N/M cases)
- [any custom dimensions]: X%

**Top failing cases** (3-5 per dimension):
For each failure:
- Requirement cited: [behavior category from taxonomy]
- Action cited: [specific turn or tool call from judge rationale]
- Judge rationale: [verbatim from dimension_justifications]

**Suggested next step**: one concrete action (e.g. "tighten the system prompt
around X behavior", "add a dimension for Y", "apply an ACS guardrail at the
failing checkpoint").

## Guardrails

- **Don't invent metrics** — only report what's in the artifacts.
- **Don't read, print, or commit** `.env`, credential values, `artifacts/`, traces, `.venv`, or logs.
- **If the spec is vague**, ask one clarifying question FIRST.
- **Reference env variable NAMES only** (AZURE_API_KEY, AZURE_API_BASE, azure_ad_token) — never values.
- **Don't commit artifacts** to the repository.
