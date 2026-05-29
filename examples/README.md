# Examples

Runnable configs and sample agents for Adaptive Eval.

Start with the LangGraph travel planner. It is the customer-preview flagship because it exercises the real agent path on top of the universal `target.callable` integration: spec-driven test generation, inference outputs (conversations or agent actions), OTel-traced execution, and judge evidence. Phoenix/OpenInference auto-instrumentation captures the agent's OpenTelemetry spans so the judge cites tool calls, routing, and intermediate decisions in every verdict.

> **Any agent works.** `target.callable` accepts any agent or multi-agent system you can invoke from a Python function — frameworks (LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, DSPy, LlamaIndex, …), custom orchestration, REST clients, or thin wrappers around hosted models. The recommended integration adds two lines (`from phoenix.otel import register; register(auto_instrument=True)`) so the judge can score tool use and routing, not just the final response.

## First run

Published example configs default to 10 total test cases (`prompt.sample_size: 5` + `scenario.sample_size: 5`, or a single scenario rail at 10) so a first run should complete in under 5 minutes on a typical Azure OpenAI deployment. Use `--override test_set.sample_size=<N>` or a per-rail sample-size override when you want a larger run for stronger statistics; each example README calls out the recommended full-run size and wall-clock expectation.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
# Edit .env with credentials for your provider. The shipped configs use `azure/...` models;
# any LiteLLM provider (OpenAI, Anthropic, Bedrock, Vertex, Ollama, …) works — see https://docs.litellm.ai/docs/providers.

assert-eval run --config examples\travel_planner_langgraph\eval_config.yaml
assert-eval results status travel-planner-langgraph-v1 demo-1

# Larger smoke run: 20 total test cases, about 6-10 minutes.
assert-eval run --config examples\travel_planner_langgraph\eval_config.yaml --override test_set.sample_size=20
```

## Create your own config

Use `assert-eval init` to design an eval config interactively instead of writing YAML by hand.
Pass `--model` with any [LiteLLM model string](https://docs.litellm.ai/docs/providers) and make sure the matching API key is in your `.env`:

```powershell
assert-eval init --model azure/gpt-5.4-mini
# or seed from an existing example:
assert-eval init --model azure/gpt-5.4-mini --from examples\travel_planner_langgraph\eval_config.yaml
```

See the [CLI reference](../docs/reference/cli.md#design-a-config-interactively) for all options.

## Which example to start with

| Goal | Example | Default / full size | Notes |
|---|---|---|---|
| Evaluate any agent or multi-agent system (recommended) | `travel_planner_langgraph\eval_config.yaml` | Default n=10 (<5 min); larger n=20 (~6-10 min) | Flagship. Uses `target.callable` with `target.trace.backend: phoenix` so the judge sees tool calls and routing. |
| **See runtime + eval close the loop on a real workflow** | `incident_triage_agent\eval_config_baseline.yaml` + `eval_config_naive_prompt.yaml` + `eval_config_guarded.yaml` + `eval_config_guarded_gepa.yaml` | Default n=10 per variant (<5 min each); published reproduction n=400 per variant (~65-80 min first variant, ~35-65 min cached variants) | Joint AgentShield + ASSERT demo. SRE incident-triage agent run across a 4-variant matrix (baseline weak prompt → naïve DO-NOT prompt → ACS gates → ACS + GEPA-optimized prompt) over a 4-axis failure-mode taxonomy to prove the runtime+eval loop and surface the security/overrefusal trade-off. See [`incident_triage_agent\README.md`](incident_triage_agent/README.md) and [`docs\case-study-incident-triage-joint.md`](../docs/case-study-incident-triage-joint.md). |
| Understand framework instrumentation breadth | `phoenix_auto_trace\README.md` | Default n=10 (<5 min); larger n=20 (~6-10 min) | Same travel-planner idea across multiple framework auto-instrumentation paths. |
| Run a simple hosted-model eval | `pipes\health_assistant.yaml` | Default n=10 (<5 min); larger n=20 (~5-10 min) | Good smoke test for a single LLM target with a system prompt. |
| Evaluate a Prompt Agent with planned tools but no backend | `pipes\health_assistant_simulated_tools.yaml` | Default n=10 (<5 min); larger n=20 (~5-10 min) | Uses a fixed tool schema and simulated tool responses. |
| Evaluate a hosted target with Python tool functions | `pipes\health_assistant_sandbox.yaml` | Default n=10 (<5 min, plus any Docker image pull); larger n=20 (~5-10 min after Docker warm-up) | Requires Docker. Use when you want actual tool execution around a hosted model. |
| Benchmark quality throughput | `benchmark\eval_config.yaml` | Default n=10 scenario cases (<5 min); larger n=50 via `--override test_set.scenario.sample_size=50` (~10-20 min) | Scenario-only travel-planner quality benchmark for throughput experiments. |

## Layout

```text
examples/
├── travel_planner_langgraph/   flagship callable-agent example with OTel trace capture
├── phoenix_auto_trace/         framework instrumentation gallery
├── pipes/                      simple hosted-model and Prompt Agent configs
├── behavior_specs/             reusable behavior spec references
└── agents/                     simple tool modules and tool schemas
```

See [`behavior_specs/README.md`](behavior_specs/README.md) for reusable behavior spec references.
