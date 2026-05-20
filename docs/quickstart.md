# Quickstart: LangGraph travel planner

This walkthrough runs the flagship customer-preview example: a LangGraph travel planner evaluated through a Python callable with Phoenix/OpenInference auto-instrumentation. The judge sees the agent's OpenTelemetry spans — tool calls, routing, and intermediate decisions — and cites them as evidence in each verdict.

> **Works for any agent.** `target.callable` accepts any agent or multi-agent system you can call from Python — frameworks (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, …), custom orchestration, or thin wrappers around hosted models. The `auto_trace.py` pattern below is the recommended integration shape; it is two lines of Phoenix instrumentation around your existing entry function.

## What you will run

```text
examples/travel_planner_langgraph/
├── eval_config.yaml          pipeline config with inline behavior spec
├── agent.py                  LangGraph travel planner
└── auto_trace.py             Phoenix auto-instrumentation wrapper
```

The config points inference at:

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
- Credentials for whichever model provider the example calls. The shipped `examples/travel_planner_langgraph/eval_config.yaml` uses `azure/gpt-5.4-mini`, so by default you set `AZURE_API_KEY` and `AZURE_API_BASE` in `.env`. To use any other [LiteLLM-supported provider](https://docs.litellm.ai/docs/providers) (OpenAI, Anthropic, Bedrock, Vertex, Ollama, …), edit `model.name` in the YAML and set the matching env vars (`.env.example` lists the common ones).
- Optional: Phoenix UI running locally if you want to browse traces interactively (the pipeline still captures OTel spans without it)

## Run it

> **Setup is the same `pip install -e ".[otel,langgraph]"` flow shown in the [README](../README.md#quickstart-langgraph-travel-planner-any-agent-works-the-same-way).** This page focuses on what you do after setup.

```powershell
# (one-time setup, see README for full details)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
# Edit .env with credentials for your provider. Defaults match the example's `azure/...` model;
# any LiteLLM provider (OpenAI, Anthropic, Bedrock, Vertex, Ollama, …) works — see https://docs.litellm.ai/docs/providers.

# Run the pipeline
p2m run --config examples\travel_planner_langgraph\eval_config.yaml
p2m results status travel-planner-langgraph-v1 demo-1
```

> **Optional — browse traces in the Phoenix UI.** Span capture happens inside `auto_trace.py` regardless; running `phoenix serve` only adds an interactive UI for browsing them. In a separate terminal, before running the eval:
>
> ```powershell
> phoenix serve   # opens http://localhost:6006
> ```
>
> If you skip this, the pipeline still captures spans, judges results, and writes them into the run artifacts — you just won't get the live trace-browsing UI.

## Read the config

The developer-authored pieces are:

1. **Eval spec**: `behavior.name` names the eval and `behavior.description` contains the behavior spec.
2. **About the target**: `context` tells the generator this is a multi-agent travel planner with flight, hotel, weather, advisory, and budget tools.
3. **Variations**: `pipeline.test_set.stratify.dimensions` ask the generator to vary traveler type and trip type.
4. **Judge dimensions**: `pipeline.judge.dimensions` defines what the judge should score, with descriptions and rubrics.

The pipeline fills in the rest:

1. `systematize` creates behavior categories.
2. `test_set` generates prompt and scenario test cases.
3. `inference` executes those test cases against the agent.
4. `judge` scores each transcript and writes metrics.

## Inspect results

Artifacts land under:

```text
artifacts/results/travel-planner-langgraph-v1/demo-1/
```

Read:

- `config.yaml` for the exact config snapshot.
- `inference_set.jsonl` for inference outputs (conversations or agent actions) and trace references.
- `scores.jsonl` for judge verdicts and evidence.
- `metrics.json` for aggregate rates.

For the agent graph itself, see [`docs/travel-planner-agent-flow.md`](travel-planner-agent-flow.md) or inspect [`examples/travel_planner_langgraph/agent.py`](../examples/travel_planner_langgraph/agent.py).
