# Examples

Runnable configs and sample agents for ASSERT.

Start with the LangGraph travel planner. It is the customer-preview flagship because it exercises the real agent path on top of the universal `target.callable` integration: spec-driven test generation, inference outputs (conversations or agent actions), OTel-traced execution, and judge evidence. Phoenix/OpenInference auto-instrumentation captures the agent's OpenTelemetry spans so the judge cites tool calls, routing, and intermediate decisions in every verdict.

> **Any agent works.** `target.callable` accepts any agent or multi-agent system you can invoke from a Python function — frameworks (LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, DSPy, LlamaIndex, …), custom orchestration, REST clients, or thin wrappers around hosted models. The recommended integration adds two lines (`from phoenix.otel import register; register(auto_instrument=True)`) so the judge can score tool use and routing, not just the final response.

## First run

```powershell
python -m venv .venv
./.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
# Edit .env with credentials for your provider. The shipped configs use `azure/...` models;
# any LiteLLM provider (OpenAI, Anthropic, Bedrock, Vertex, Ollama, …) works — see https://docs.litellm.ai/docs/providers.

assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
assert-ai results status travel-planner-langgraph-v1 demo-1
```

## Create your own config

Use `assert-ai init` to design an eval config interactively instead of writing YAML by hand.
Pass `--model` with any [LiteLLM model string](https://docs.litellm.ai/docs/providers) and make sure the matching API key is in your `.env`:

```powershell
assert-ai init --model azure/gpt-4o-mini
# or seed from an existing example:
assert-ai init --model azure/gpt-4o-mini --from examples/travel_planner_langgraph/eval_config.yaml
```

See the [CLI reference](../docs/cli/commands.md#design-a-config-interactively) for all options.

## Which example to start with

| Goal | Example | Notes |
|---|---|---|
| Evaluate any agent or multi-agent system (recommended) | `travel_planner_langgraph/eval_config.yaml` | Flagship. Uses `target.callable` with `target.trace.backend: phoenix` so the judge sees tool calls and routing. |
| Evaluate the same agent across multiple frameworks | `travel_planner_neurosan/eval_config.yaml` | Alternative travel-planner using the Neuro-SAN framework. Useful as a side-by-side with the LangGraph flagship. |
| **See runtime + eval close the loop on a real workflow** | `incident_triage_agent/eval_config_baseline.yaml` + `eval_config_naive_prompt.yaml` + `eval_config_guarded.yaml` + `eval_config_guarded_gepa.yaml` | Joint AgentShield + ASSERT example. SRE incident-triage agent run across a 4-variant matrix (baseline weak prompt → naïve DO-NOT prompt → ACS gates → ACS + GEPA-optimized prompt) over a 4-axis failure-mode taxonomy to prove the runtime+eval loop and surface the security/overrefusal trade-off. See [`incident_triage_agent/README.md`](incident_triage_agent/README.md). |
| Evaluate a change-control governance agent | `change_control_agent/eval_config.yaml` | Generic enterprise change-management pattern with deterministic tool simulation. Covers tool-misuse, doc-fabrication, and sequence-violation failure modes. |
| Evaluate a multi-agent RAG over Azure docs | `azure_doc_qa/eval_config.yaml` | LangGraph multi-agent system with retrieval. Walks through eval-driven iteration in `IMPROVEMENT_JOURNEY.md`. |
| Understand framework instrumentation breadth | `phoenix_auto_trace/README.md` | Same travel-planner idea across multiple framework auto-instrumentation paths. |
| Run a simple hosted-model eval | `prompt_agents/health_assistant.yaml` | Good smoke test for a single LLM target with a system prompt. |
| Evaluate a Prompt Agent with planned tools but no backend | `prompt_agents/health_assistant_simulated_tools.yaml` | Uses a fixed tool schema and simulated tool responses. |
| Evaluate a hosted target with Python tool functions | `prompt_agents/health_assistant_sandbox.yaml` | Requires Docker. Use when you want actual tool execution around a hosted model. |
| Evaluate a science research agent with real retrieval tools | `science_research_agent/eval_config.yaml` | Callable-agent example with `web_search`, `fetch_url`, and `file_search`. Run `python -m pip install -e ".[examples]"`, set `TAVILY_API_KEY` for web search, then `assert-ai run --config examples/science_research_agent/eval_config.yaml`. |
| Run a benchmark-style comparison | `benchmark/eval_config.yaml` | Minimal config wired for benchmark-style evaluation across multiple models. |

## Layout

```text
examples/
├── travel_planner_langgraph/   flagship callable-agent example with OTel trace capture
├── travel_planner_neurosan/    same shape via the Neuro-SAN framework
├── incident_triage_agent/      joint AgentShield + ASSERT 4-variant example
├── change_control_agent/       enterprise change-management governance agent
├── azure_doc_qa/               multi-agent RAG over Azure documentation
├── science_research_agent/     callable science research agent with real retrieval tools
├── phoenix_auto_trace/         framework instrumentation gallery
├── prompt_agents/              simple hosted-model and Prompt Agent configs
├── benchmark/                  benchmark-style comparison config
├── behavior_specs/             reusable behavior spec references
└── agents/                     simple tool modules and tool schemas
```

See [`behavior_specs/README.md`](behavior_specs/README.md) for reusable behavior spec references.
