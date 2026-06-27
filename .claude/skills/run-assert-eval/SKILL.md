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

This skill has two entry modes:

- **Run mode** — no usable results exist yet: generate or reuse a config, run the
  pipeline (Steps 1-3), then report (Step 4).
- **Results Q&A mode** — judged artifacts already exist under
  `artifacts/results/<suite>/<run>/` and the user asks a *question* about them
  ("what are the highlights?", "top 3 examples of the worst failure mode?", "why
  did case X fail?"). Skip to Step 4 and answer THAT question from the artifacts —
  do not re-run, and do not fall back to the full canned report unless asked.

### Copilot vs. the local viewer

Copilot is for *answering questions* and *synthesis* — direct answers,
failure-mode clustering, cited examples, next actions — with no clicking. The
bundled local viewer is for *visual exploration* — forest plots, baseline compare,
facet grouping, and stepping through a transcript with the judge's citations
highlighted. Answer in chat when the user asks "what / why / which"; hand off to
the viewer (Step 5) when they want to *see*, *read a full transcript*, *compare
runs*, or *watch a live run*.

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

**Read only structured artifacts.** Aggregate from the pre-computed, schema'd files —
never trawl raw Phoenix/OpenTelemetry traces to reconstruct an answer (that bulk,
unguided trace-reading is exactly what the viewer's evidence drawer is for). Reading
the `inference_set.jsonl` row for a *specific case the judge already cited* is fine;
bulk trace trawling is not.

1. **Headline rates**: run `assert-ai results status <suite> <run>` for per-dimension
   flagged rates (split into prompt and scenario). Report `policy_violation` and
   `overrefusal` SEPARATELY — they are two different problems.

2. **Top failing cases**: read `scores.jsonl` from `artifacts/results/<suite>/<run>/`.
   For each dimension with failures, pull 3-5 representative cases with:
   - The test case description (what was tested)
   - `verdict.dimensions` — which dimensions failed
   - `verdict.dimension_justifications` — the judge's rationale with cited evidence
   - `verdict.node_judgments` — which behavior categories were violated, with reasoning

3. **Cost and timing**: read `metrics.json` for token usage and elapsed time per stage.
   This file contains cost metadata only, not score roll-ups.

For **Results Q&A mode**, answer the user's specific question from these same artifacts
(e.g. rank dimensions by flagged rate for "top failure mode", then quote
`dimension_justifications` for the cited examples). Don't emit the full template unless asked.

### 5. Hand off to the local viewer

After reporting, point the user to the bundled viewer for anything visual or
self-directed — it went through extensive design iteration and owns the exploration
surface Copilot should not replicate:

```
cd viewer && npm install && npm run dev   # then open http://localhost:5174
```

Select the suite and run for forest plots, per-dimension breakdowns, facet grouping,
the permissible vs. not-permissible policy-violation split (a viewer-only breakdown),
and a transcript drawer with the judge's `[N]` citations highlighted on the cited turns.
Suggest it specifically when the user wants to:

- **read a full transcript** or **see the trace** for a case → viewer evidence drawer
- **compare against a baseline** → viewer compare view (or `assert-ai results compare <suite> <runA> <runB>`)
- **watch a run in progress** → viewer live run monitor (`manifest.json`-driven)

See `docs/guides/use-local-viewer.md` for the full layout.

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
- **Don't trawl raw traces to answer questions** — answer from `results status`, `scores.jsonl`, and `metrics.json`; hand off to the viewer for visual trace/transcript exploration.
- **Hand off, don't reimplement the viewer** — for visual drill-down, baseline compare, or live monitoring, point to the local viewer rather than reproducing it in chat.
- **Don't read, print, or commit** `.env`, credential values, `artifacts/`, traces, `.venv`, or logs.
- **If the spec is vague**, ask one clarifying question FIRST.
- **Reference env variable NAMES only** (AZURE_API_KEY, AZURE_API_BASE, azure_ad_token) — never values.
- **Don't commit artifacts** to the repository.
