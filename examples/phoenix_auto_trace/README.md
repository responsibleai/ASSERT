# Phoenix Auto-Trace Gallery — Same Scenario, 33 Frameworks

This is a **multi-framework instrumentation gallery**, not a single-agent example. It shows how one Phoenix/OpenInference setup (typically 2 lines: install the instrumentor, call `register(auto_instrument=True)`) gives `target.callable` + `target.trace` OpenTelemetry visibility across supported frameworks — without per-framework integration work in ASSERT itself.

All runnable demos implement the **same travel planner** with 5 mock tools:

- `search_flights` — find flights to a destination
- `search_hotels` — find hotels in a city
- `check_weather` — get forecast and advisories
- `check_travel_advisories` — visa, safety, health precautions
- `validate_budget` — verify the plan fits the user's budget

> Test query: "Plan a week in Tokyo for under $3000"

Each `travel_<framework>.py` file shows only what's different per framework; mock tool responses come from `_tools.py`. Swap the callable path or YAML, and the eval pipeline stays the same.

---

## Supported Frameworks (OpenInference auto-instrumentation)

Demo paths are relative to `examples/phoenix_auto_trace/`; rows without a Demo entry are supported instrumentors without a local demo script.

### LLM Providers

| Package | Framework | Demo |
|---------|-----------|------|
| `openinference-instrumentation-openai` | OpenAI | `travel_openai.py` |
| `openinference-instrumentation-anthropic` | Anthropic | `travel_anthropic.py` |
| `openinference-instrumentation-litellm` | LiteLLM | `travel_litellm.py` |
| `openinference-instrumentation-bedrock` | AWS Bedrock | `travel_bedrock.py` |
| `openinference-instrumentation-mistralai` | MistralAI | `travel_mistralai.py` |
| `openinference-instrumentation-groq` | Groq | `travel_groq.py` |
| `openinference-instrumentation-google-genai` | Google GenAI | `travel_google_genai.py` |
| `openinference-instrumentation-google-adk` | Google ADK | `travel_google_adk.py` |
| `openinference-instrumentation-portkey` | Portkey | `travel_portkey.py` |

### Agent Frameworks

| Package | Framework | Demo |
|---------|-----------|------|
| `openinference-instrumentation-langchain` | LangChain / LangGraph | `travel_langchain.py` |
| `openinference-instrumentation-llama-index` | LlamaIndex | `travel_llamaindex.py` |
| `openinference-instrumentation-crewai` | CrewAI | `travel_crewai.py` |
| `openinference-instrumentation-dspy` | DSPy | `travel_dspy.py` |
| `openinference-instrumentation-openai-agents` | OpenAI Agents SDK | `travel_openai_agents.py` |
| `openinference-instrumentation-instructor` | Instructor | `travel_instructor.py` |
| `openinference-instrumentation-pydantic-ai` | PydanticAI | `travel_pydantic_ai.py` |
| `openinference-instrumentation-autogen-agentchat` | AutoGen AgentChat | `travel_autogen.py` |
| `openinference-instrumentation-smolagents` | smolagents | `travel_smolagents.py` |
| `openinference-instrumentation-haystack` | Haystack | `travel_haystack.py` |

Additional runnable variants: `travel_langgraph.py` (LangGraph-specific target using the LangChain/LangGraph instrumentor) and `travel_openai_router.py` (OpenAI-compatible router target requiring router-specific credentials and endpoint access).

### Additional (no demo file, same 2-line pattern)

| Package | Framework |
|---------|-----------|
| `openinference-instrumentation-claude-agent-sdk` | Claude Agent SDK |
| `openinference-instrumentation-guardrails` | Guardrails AI |
| `openinference-instrumentation-mcp` | MCP |
| `openinference-instrumentation-agno` | Agno Agents |
| `openinference-instrumentation-beeai` | BeeAI |
| `openinference-instrumentation-pipecat` | Pipecat |
| `openinference-instrumentation-strands-agents` | Strands Agents |
| `openinference-instrumentation-agentspec` | AgentSpec |
| `openinference-instrumentation-vertexai` | VertexAI |

**Total: 33 auto-instrumented frameworks** in OpenInference. This gallery includes 19 per-framework demos plus the runnable variants above; the remaining instrumentors follow the same install + `register(auto_instrument=True)` pattern. For anything not in OpenInference, you can still emit spans via the OpenTelemetry SDK with `@tracer.start_as_current_span`.

---

## Architecture

```
_tools.py              -> shared mock tool data + simulate_tool() + schemas
travel_openai.py       -> OpenAI SDK + 2-line instrumentation + tool loop
travel_langchain.py    -> LangGraph + 2-line instrumentation + graph routing
travel_crewai.py       -> CrewAI + 2-line instrumentation + multi-agent crew
...                    -> same pattern for each framework
```

## Quick Start (OpenAI)

```bash
# From the repo root
python -m pip install -e ".[otel,examples]"
python -m pip install openai openinference-instrumentation-openai

# Optional: start Phoenix in a second terminal to browse traces
phoenix serve  # http://localhost:6006

# Run one framework demo directly
python -m examples.phoenix_auto_trace.travel_openai

# Run the matching eval
assert-ai run --config examples/phoenix_auto_trace/eval_openai.yaml
```

Set provider credentials for the SDK you choose; for the OpenAI/Azure-compatible starter path, use `AZURE_API_BASE`, `AZURE_API_KEY`, and optionally `ASSERT_AZURE_DEPLOYMENT` (or the OpenAI SDK's standard variables).

## Phoenix and external-service prerequisites

| Area | Requirement |
|---|---|
| Phoenix collector | Local process only: `phoenix serve` opens `http://localhost:6006`; no Docker is required for the gallery itself. |
| Provider SDK demos | Need network access and credentials for the selected provider. |
| Agent framework demos | Need the matching framework package plus its `openinference-instrumentation-*` package. |
| Router variant | `travel_openai_router.py` needs router-specific credentials and endpoint access. |

## The ASSERT integration

All of these can be evaluated by ASSERT with the same config shape:

```yaml
pipeline:
  inference:
    target:
      callable: examples.phoenix_auto_trace.travel_openai:chat
      trace:
        backend: phoenix
        group_by: session.id
```

Swap the callable to any framework — the eval pipeline, judge, and artifacts stay identical. That's the point.

Starter YAMLs: `eval_openai.yaml`, `eval_litellm.yaml`, `eval_langchain.yaml`, `eval_crewai.yaml`, `eval_dspy.yaml`; `eval_config.yaml` is the OpenAI default, and `eval_framework_template.yaml` is the copy/paste starting point for another callable.

## What the judge sees from traces

Final-text-only judging can see the itinerary but not whether the agent skipped budget validation, ignored an advisory, or used the wrong tool. With `target.trace`, Phoenix spans expose tool names, arguments, model calls, routing, and per-step latency. `assert-ai` can use that evidence when scoring generated `behavior_categories`.

## Behavior violation rate results

Not yet measured for this gallery README. After running an eval, check `artifacts/results/framework-eval-mini/<run>/metrics.json` for aggregate behavior-violation and overrefusal rates. Do not compare frameworks until they use the same generated test set and judge config.
