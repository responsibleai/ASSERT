# Phoenix Auto-Trace Demo — Same Scenario, 20+ Frameworks

This demo proves the spec's core claim (§4.4.3 Approach A): **2 lines of
Phoenix instrumentation gives full OTel tracing across 20+ frameworks — zero
per-framework maintenance for P2M.**

All examples implement the same travel planner scenario:
> "Book me a week in Tokyo under $3000"

Each file shows only what's different per framework. The instrumentation is
always the same 2 lines at the top.

---

## Supported Frameworks (OpenInference auto-instrumentation)

### LLM Providers
| Package | Framework |
|---------|-----------|
| `openinference-instrumentation-openai` | OpenAI |
| `openinference-instrumentation-anthropic` | Anthropic |
| `openinference-instrumentation-litellm` | LiteLLM |
| `openinference-instrumentation-bedrock` | AWS Bedrock |
| `openinference-instrumentation-mistralai` | MistralAI |
| `openinference-instrumentation-groq` | Groq |
| `openinference-instrumentation-google-genai` | Google GenAI |
| `openinference-instrumentation-google-adk` | Google ADK |
| `openinference-instrumentation-portkey` | Portkey |

### Agent Frameworks
| Package | Framework |
|---------|-----------|
| `openinference-instrumentation-langchain` | LangChain / LangGraph |
| `openinference-instrumentation-llama-index` | LlamaIndex |
| `openinference-instrumentation-crewai` | CrewAI |
| `openinference-instrumentation-dspy` | DSPy |
| `openinference-instrumentation-haystack` | Haystack |
| `openinference-instrumentation-guardrails` | Guardrails AI |
| `openinference-instrumentation-instructor` | Instructor |
| `openinference-instrumentation-mcp` | MCP |
| `openinference-instrumentation-agno` | Agno Agents |
| `openinference-instrumentation-beeai` | BeeAI |

### Java / TypeScript
| Package | Framework |
|---------|-----------|
| `openinference-instrumentation-langchain4j` | LangChain4j (Java) |
| `openinference-instrumentation-springAI` | Spring AI (Java) |
| Vercel AI SDK | Vercel AI (TypeScript) |

**Total: 22 auto-instrumented frameworks + manual `@tracer` for anything else.**

---

## Demo Files

Each file is a self-contained travel planner using a different framework.
All share the same 2-line instrumentation preamble:

```python
from phoenix.otel import register
register(auto_instrument=True)
```

| File | Framework | What it demonstrates |
|------|-----------|---------------------|
| `travel_openai.py` | OpenAI (direct) | Tool calling with `gpt-4o` |
| `travel_langchain.py` | LangChain/LangGraph | Multi-node graph (mirrors `travel_planner/agent.py`) |
| `travel_litellm.py` | LiteLLM | Provider-agnostic model calls |
| `travel_anthropic.py` | Anthropic | Claude tool use |
| `travel_crewai.py` | CrewAI | Multi-agent crew |
| `travel_llamaindex.py` | LlamaIndex | RAG + agent pipeline |
| `travel_dspy.py` | DSPy | Declarative signatures |

## Running

```bash
# Install Phoenix + the instrumentors you need
pip install arize-phoenix-otel openinference-instrumentation-openai openinference-instrumentation-langchain

# Run any example — traces appear in Phoenix
python examples/phoenix_auto_trace/travel_openai.py

# View traces
phoenix serve  # http://localhost:6006
```

## The P2M integration

All of these can be evaluated by P2M with the same config:

```yaml
rollout:
  target:
    callable: examples.phoenix_auto_trace.travel_openai:chat
    trace:
      backend: phoenix
      group_by: session.id
```

Swap the callable to any framework — the eval pipeline, judge, and artifacts
stay identical. That's the point.
