---
title: Quickstart
description: Run the LangGraph travel planner example end-to-end.
---

This walkthrough runs the flagship example: a LangGraph travel planner evaluated through a Python callable with Phoenix / OpenInference auto-instrumentation. The judge sees the agent's OpenTelemetry spans — tool calls, routing, and intermediate decisions — and cites them in each verdict.

:::tip[Works for any agent.]
`target.callable` accepts any agent or multi-agent system you can call from Python — LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, or custom orchestration. The OTel auto-instrumentation pattern below is the recommended integration shape.
:::

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
cp .env.example .env
# Edit .env with your provider credentials.
```

## Run

```bash
p2m run --config examples/travel_planner_langgraph/eval_config.yaml
```

The pipeline runs the four stages: failure-mode taxonomy → test cases → execute → judge.

## Inspect results

Artifacts land under `artifacts/results/travel-planner-langgraph-v1/<run>/`:

- `taxonomy.json` — failure-mode categories derived from the spec
- `test_set.jsonl` — prompts and multi-turn scenarios the pipeline generated
- `inference_set.jsonl` — inference outputs (conversations or agent actions) + captured OTel spans
- `scores.jsonl` — judge verdicts + cited evidence per turn
- `metrics.json` — aggregate behavior categories, pass rates, judge-failure counts

Open the viewer:

```bash
cd viewer && npm install && npm run dev
# http://localhost:5174
```

## Next

- [Concepts](/adaptive-eval/get-started/concepts/) — what each authored field does
- [How it works](/adaptive-eval/learn/how-it-works/) — pipeline internals
- [Examples](/adaptive-eval/examples/) — three end-to-end walkthroughs
