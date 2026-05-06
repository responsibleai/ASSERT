# Examples

Runnable configs and sample agents for Adaptive Eval.

Start with the LangGraph travel planner. It is the customer-preview flagship because it exercises the real agent path on top of the universal `target.callable` integration: spec-driven test generation, transcripts, and judge evidence. Optional Phoenix/OpenInference auto-instrumentation captures OpenTelemetry spans for richer trace-grounded scoring.

> **Any agent works.** `target.callable` accepts any agent or multi-agent system you can invoke from a Python function — frameworks (LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, DSPy, LlamaIndex, …), custom orchestration, REST clients, or thin wrappers around hosted models. You do not need OpenTelemetry to start; add it later when you want the judge to see tool calls, routing, and intermediate decisions.

## First run

```powershell
uv venv
uv sync
Copy-Item .env.example .env
# Edit .env with your Azure OpenAI settings.

uv run p2m run --config examples\travel_planner_langgraph\eval_config.yaml
uv run p2m results status travel-planner-langgraph-v1 demo-1
```

## Which example to start with

| Goal | Example | Notes |
|---|---|---|
| Evaluate any agent or multi-agent system (recommended) | `travel_planner_langgraph\eval_config.yaml` | Flagship. Uses `target.callable`. Optional Phoenix trace capture via `target.trace.backend: phoenix`. |
| Understand framework instrumentation breadth | `phoenix_auto_trace\README.md` | Same travel-planner idea across multiple framework auto-instrumentation paths. |
| Run a simple hosted-model eval | `pipes\health_assistant.yaml` | Good smoke test for a single LLM target with a system prompt. |
| Evaluate a prompt agent with planned tools but no backend | `pipes\health_assistant_simulated_tools.yaml` | Uses a fixed tool schema and simulated tool responses. |
| Evaluate a hosted target with Python tool functions | `pipes\health_assistant_sandbox.yaml` | Requires Docker. Use when you want actual tool execution around a hosted model. |

## Layout

```text
examples/
├── travel_planner_langgraph/   flagship callable-agent example (with optional OTel trace capture)
├── phoenix_auto_trace/         framework instrumentation gallery
├── pipes/                      simple hosted-model and model+tools configs
├── specs/                   reusable eval spec definitions
└── agents/                     simple tool modules and tool schemas
```

See [`specs\README.md`](specs/README.md) for reusable spec definitions.
