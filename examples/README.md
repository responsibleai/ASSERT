# Examples

Runnable configs and sample agents for Adaptive Eval.

Start with the LangGraph travel planner. It is the customer-preview flagship because it exercises the real agent path on top of the universal `target.callable` integration: spec-driven test generation, inference outputs (conversations or agent actions), OTel-traced execution, and judge evidence. Phoenix/OpenInference auto-instrumentation captures the agent's OpenTelemetry spans so the judge cites tool calls, routing, and intermediate decisions in every verdict.

> **Any agent works.** `target.callable` accepts any agent or multi-agent system you can invoke from a Python function — frameworks (LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, DSPy, LlamaIndex, …), custom orchestration, REST clients, or thin wrappers around hosted models. The recommended integration adds two lines (`from phoenix.otel import register; register(auto_instrument=True)`) so the judge can score tool use and routing, not just the final response.

## First run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
# Edit .env with credentials for your provider. The shipped configs use `azure/...` models;
# any LiteLLM provider (OpenAI, Anthropic, Bedrock, Vertex, Ollama, …) works — see https://docs.litellm.ai/docs/providers.

p2m run --config examples\travel_planner_langgraph\eval_config.yaml
p2m results status travel-planner-langgraph-v1 demo-1
```

## Which example to start with

| Goal | Example | Notes |
|---|---|---|
| Evaluate any agent or multi-agent system (recommended) | `travel_planner_langgraph\eval_config.yaml` | Flagship. Uses `target.callable` with `target.trace.backend: phoenix` so the judge sees tool calls and routing. |
| Understand framework instrumentation breadth | `phoenix_auto_trace\README.md` | Same travel-planner idea across multiple framework auto-instrumentation paths. |
| Run a simple hosted-model eval | `pipes\health_assistant.yaml` | Good smoke test for a single LLM target with a system prompt. |
| Evaluate a Prompt Agent with planned tools but no backend | `pipes\health_assistant_simulated_tools.yaml` | Uses a fixed tool schema and simulated tool responses. |
| Evaluate a hosted target with Python tool functions | `pipes\health_assistant_sandbox.yaml` | Requires Docker. Use when you want actual tool execution around a hosted model. |
| Measure ACS policy on a banking agent | `bank_manager_agent_shield\eval_config_unguarded.yaml` / `eval_config_naive_prompt.yaml` / `eval_config_guarded.yaml` / `eval_config_guarded_gepa.yaml` | Port of the microsoft/AgentShield bank-manager demo. Four variants (no ACS, naïve DO-NOT prompt, full ACS, full ACS + GEPA-optimized prompt) measured across 4 RAI axes with a 9-dim judge; n=100. |

## Layout

```text
examples/
├── travel_planner_langgraph/   flagship callable-agent example with OTel trace capture
├── bank_manager_agent_shield/  ACS policy eval — unguarded vs. ACS-gated comparison
├── phoenix_auto_trace/         framework instrumentation gallery
├── pipes/                      simple hosted-model and Prompt Agent configs
├── behavior_specs/             reusable behavior spec references
└── agents/                     simple tool modules and tool schemas
```

See [`behavior_specs/README.md`](behavior_specs/README.md) for reusable behavior spec references.
