# Phoenix Auto-Trace Demo — Same Scenario, 33 Frameworks

This demo shows how a single Phoenix/OpenInference instrumentation snippet (typically 2 lines: install the instrumentor, call `register(auto_instrument=True)`) gives you OpenTelemetry tracing across 33 supported frameworks — without per-framework integration work in Adaptive Eval itself.

All examples implement the **same travel planner** with 5 mock tools:
- `search_flights` — find flights to a destination
- `search_hotels` — find hotels in a city
- `check_weather` — get forecast and advisories
- `check_travel_advisories` — visa, safety, health precautions
- `validate_budget` — verify the plan fits the user's budget

> Test query: "Plan a week in Tokyo for under $3000"

Each file shows only what's different per framework. The instrumentation is
always the same 2 lines. Mock tool responses come from `_tools.py`.

---

## Supported Frameworks (OpenInference auto-instrumentation)

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

**Total: 33 auto-instrumented frameworks** (this README enumerates 28 of them with example demos; the remaining handful follow the same install + `register(auto_instrument=True)` pattern). For anything not in OpenInference, you can still emit spans via the OpenTelemetry SDK with `@tracer.start_as_current_span`.

---

## Architecture

```
_tools.py              -> shared mock tool data + simulate_tool() + schemas
travel_openai.py       -> OpenAI SDK + 2-line instrumentation + tool loop
travel_langchain.py    -> LangGraph + 2-line instrumentation + graph routing
travel_crewai.py       -> CrewAI + 2-line instrumentation + multi-agent crew
...                    -> same pattern for each framework
```

## Running

Run a standalone framework demo:

```powershell
# Install Phoenix + the instrumentor for your framework
pip install arize-phoenix-otel openinference-instrumentation-openai

# (Windows only — avoids UnicodeEncodeError on console output with arrows)
$env:PYTHONUTF8 = "1"

# Run any example — traces appear in Phoenix
python -m examples.phoenix_auto_trace.travel_openai

# View traces (in a separate terminal)
phoenix serve   # http://localhost:6006
```

Run the Adaptive Eval config:

```powershell
assert-eval run --config examples\phoenix_auto_trace\eval_config.yaml
```

All shipped eval configs in this directory default to 10 total test cases (`prompt.sample_size: 5` + `scenario.sample_size: 5`) so a first eval run should finish in under 5 minutes. For a larger framework smoke run, use `--override test_set.sample_size=20`; expect about 6-10 minutes depending on provider latency and the framework package import cost.

## The Adaptive Eval integration

All of these can be evaluated by Adaptive Eval with the same config:

```yaml
pipeline:
  inference:
    target:
      callable: examples.phoenix_auto_trace.travel_openai:chat
      trace:
        backend: phoenix
        group_by: session.id
```

Swap the callable to any framework — the eval pipeline, judge, and artifacts
stay identical. That's the point.
