# Examples

Runnable configs and sample agents for Adaptive Eval.

Start with the OTel-traced LangGraph travel planner. It is the customer-preview flagship because it exercises the real agent path: spec-driven test generation, `target.callable`, Phoenix/OpenInference spans, transcripts, and judge evidence.

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
| Evaluate a real framework agent with OTel traces | `travel_planner_langgraph\eval_config.yaml` | Recommended starting point. Uses `target.callable` and `target.trace.backend: phoenix`. |
| Understand framework instrumentation breadth | `phoenix_auto_trace\README.md` | Shows the same travel-planner idea across multiple framework integration paths. |
| Run a simple hosted-model eval | `pipes\health_assistant.yaml` | Good smoke test for a single LLM target with a system prompt. |
| Evaluate a prompt agent with planned tools but no backend | `pipes\health_assistant_simulated_tools.yaml` | Uses a fixed tool schema and simulated tool responses. |
| Evaluate a hosted target with Python tool functions | `pipes\health_assistant_sandbox.yaml` | Requires Docker. Use when you want actual tool execution around a hosted model. |

## Layout

```text
examples/
├── travel_planner_langgraph/   flagship OTel/callable agent example
├── phoenix_auto_trace/         framework instrumentation gallery
├── pipes/                      simple hosted-model and model+tools configs
├── concepts/                   reusable eval spec definitions
└── agents/                     simple tool modules and tool schemas
```

See [`concepts\README.md`](concepts/README.md) for reusable concept definitions.
