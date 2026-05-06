# Quickstart: LangGraph travel planner

This walkthrough runs the flagship customer-preview example: a LangGraph travel planner evaluated through a Python callable. Optional Phoenix/OpenInference auto-instrumentation captures OpenTelemetry spans so the judge can also see tool calls, routing, and intermediate decisions.

> **Works for any agent.** `target.callable` accepts any agent or multi-agent system you can call from Python — frameworks (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, …), custom orchestration, or thin wrappers around hosted models. You do not need OpenTelemetry to start; add Phoenix later when you want richer trace-grounded scoring.

## What you will run

```text
examples/travel_planner_langgraph/
├── eval_config.yaml          pipeline config
├── travel_planner_eval.md    eval spec
├── agent.py                  LangGraph travel planner
└── auto_trace.py             Phoenix auto-instrumentation wrapper
```

The config points rollout at:

```yaml
target:
  callable: examples.travel_planner_langgraph.auto_trace:chat_sync
  trace:
    backend: phoenix
    group_by: session.id
```

`auto_trace.py` registers Phoenix instrumentation, then imports the agent entrypoint. The pipeline calls `chat_sync(message)`, records the conversation, and links the trace data into the run artifacts.

## Prerequisites

- Python 3.11+
- `pip` (uv also works for contributors; see the README install path)
- Azure OpenAI credentials in `.env`
- Optional: Phoenix running locally if you want to browse traces during the run

## Run it

> **Setup is the same `pip install -e ".[otel,langgraph]"` flow shown in the [README](../README.md#quickstart-langgraph-travel-planner-any-agent-works-the-same-way).** This page focuses on what you do after setup.

```powershell
# (one-time setup, see README for full details)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
# Edit .env with AZURE_API_KEY, AZURE_API_BASE, and any deployment settings.

# Run the pipeline
p2m run --config examples\travel_planner_langgraph\eval_config.yaml
p2m results status travel-planner-langgraph-v1 demo-1
```

> **Optional — browse traces in Phoenix.** In a separate terminal, before running the eval:
>
> ```powershell
> phoenix serve   # opens http://localhost:6006
> ```
>
> If you skip Phoenix, the pipeline still runs the target and judges results — you just won't get the live trace-browsing UI.

## Read the config

The developer-authored pieces are:

1. **Eval spec**: `concept.name: travel_planner_eval` loads `travel_planner_eval.md`.
2. **About the target**: `context` tells the generator this is a multi-agent travel planner with flight, hotel, weather, advisory, and budget tools.
3. **Variations**: `factors` ask the generator to vary traveler type and trip type.
4. **Judge dimensions**: `judge.dimensions` defines what the judge should score, with descriptions and rubrics.

The pipeline fills in the rest:

1. `policy` creates a behavior taxonomy.
2. `seeds` generates single-turn prompts and multi-turn scenarios.
3. `rollout` executes those tests against the agent.
4. `judge` scores each transcript and writes metrics.

## Inspect results

Artifacts land under:

```text
artifacts/results/travel-planner-langgraph-v1/demo-1/
```

Read:

- `config.yaml` for the exact config snapshot.
- `transcripts.jsonl` for conversations and trace references.
- `scores.jsonl` for judge verdicts and evidence.
- `metrics.json` for aggregate rates.

For the agent graph itself, see [`docs/travel-planner-agent-flow.md`](travel-planner-agent-flow.md) or inspect [`examples/travel_planner_langgraph/agent.py`](../examples/travel_planner_langgraph/agent.py).
