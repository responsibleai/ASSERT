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

assert-eval run --config examples/travel_planner_langgraph/eval_config.yaml
assert-eval results status travel-planner-langgraph-v1 demo-1
```

## Create your own config

Use `assert-eval init` to design an eval config interactively instead of writing YAML by hand.
Pass `--model` with any [LiteLLM model string](https://docs.litellm.ai/docs/providers) and make sure the matching API key is in your `.env`:

```powershell
assert-eval init --model azure/gpt-5.4-mini
# or seed from an existing example:
assert-eval init --model azure/gpt-5.4-mini --from examples/travel_planner_langgraph/eval_config.yaml
```

See the [CLI reference](../docs/reference/cli.md#design-a-config-interactively) for all options.

## Which example to start with

| Goal | Example | Notes |
|---|---|---|
| Evaluate any agent or multi-agent system (recommended) | `travel_planner_langgraph/eval_config.yaml` | Flagship. Uses `target.callable` with `target.trace.backend: phoenix` so the judge sees tool calls and routing. |
| **See runtime + eval close the loop on a real workflow** | `incident_triage_agent/eval_config_baseline.yaml` + `eval_config_naive_prompt.yaml` + `eval_config_guarded.yaml` + `eval_config_guarded_gepa.yaml` | Joint AgentShield + ASSERT demo. SRE incident-triage agent run across a 4-variant matrix (baseline weak prompt → naïve DO-NOT prompt → ACS gates → ACS + GEPA-optimized prompt) over a 4-axis failure-mode taxonomy to prove the runtime+eval loop and surface the security/overrefusal trade-off. See [`incident_triage_agent/README.md`](incident_triage_agent/README.md) and [`docs/case-study-incident-triage-joint.md`](../docs/case-study-incident-triage-joint.md). |
| **Compare an unguarded agent against an ACS-guarded variant** | `bank_manager_agent_shield/eval_unguarded_v2.yaml` + `eval_guarded_acs.yaml` | Self-contained ASSERT example that compares an unguarded LangGraph bank-manager agent against the same agent guarded by [Agent Control Specification (ACS)](https://github.com/responsibleai/AgentControlSpecification) on a 100-case eval suite. Ships frozen results so the viewer renders the comparison without re-running the suite. Headline: ACS cuts safety failures from 39% → 9% with no helpfulness cost. See [`bank_manager_agent_shield/README.md`](bank_manager_agent_shield/README.md). |
| Understand framework instrumentation breadth | `phoenix_auto_trace/README.md` | Same travel-planner idea across multiple framework auto-instrumentation paths. |
| Run a simple hosted-model eval | `pipes/health_assistant.yaml` | Good smoke test for a single LLM target with a system prompt. |
| Evaluate a Prompt Agent with planned tools but no backend | `pipes/health_assistant_simulated_tools.yaml` | Uses a fixed tool schema and simulated tool responses. |
| Evaluate a hosted target with Python tool functions | `pipes/health_assistant_sandbox.yaml` | Requires Docker. Use when you want actual tool execution around a hosted model. |
| Evaluate a science research agent with real retrieval tools | `science_research_agent/eval_config.yaml` | Callable-agent example ported from Omni. Uses `web_search`, `fetch_url`, and `file_search`. Run `python -m pip install -e ".[examples]"`, set `TAVILY_API_KEY` for web search, then `assert-eval run --config examples/science_research_agent/eval_config.yaml`. |

## Layout

```text
examples/
├── travel_planner_langgraph/   flagship callable-agent example with OTel trace capture
├── bank_manager_agent_shield/  ACS-vs-unguarded comparison demo with frozen n=100 results
├── science_research_agent/     callable science research agent with real retrieval tools
├── phoenix_auto_trace/         framework instrumentation gallery
├── pipes/                      simple hosted-model and Prompt Agent configs
├── behavior_specs/             reusable behavior spec references
└── agents/                     simple tool modules and tool schemas
```

See [`behavior_specs/README.md`](behavior_specs/README.md) for reusable behavior spec references.
