# Callable Target

Use the callable target for any agent or multi-agent system with a Python entry function. This is the universal integration boundary — frameworks (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, …), custom orchestration, REST clients, and thin model wrappers all qualify.

The callable target has two integration paths:

- **Recommended (happy path):** OTel-traced agent — two-line auto-instrumentation across 33 supported frameworks. The judge cites tool calls, routing decisions, model calls, and latency as evidence.
- **Customization:** for unsupported frameworks (emit your own OTel spans) or for cases where instrumentation is impossible or unnecessary (plain callable / HTTP endpoint, no traces).

## What the judge sees, by integration path

Pick the path that exposes enough internals for the judge to score what matters. OTel is recommended because every other path is strictly narrower.

| Observability for the judge | Plain `str` return | LiteLLM-style response | OTel traces (recommended) |
|---|:---:|:---:|:---:|
| Final response text | ✅ | ✅ | ✅ |
| Final tool calls (names + arguments) | — | ✅ | ✅ |
| Token usage | — | ✅ | ✅ |
| Model name | — | ✅ | ✅ |
| Intermediate tool calls (per step) | — | — | ✅ |
| Routing / sub-agent decisions | — | — | ✅ |
| Intermediate model calls | — | — | ✅ |
| Per-span latency | — | — | ✅ |
| **Total** | **1 / 8** | **4 / 8** | **8 / 8** |

## Recommended: OTel-traced agent (33 frameworks)

When your agent emits OpenTelemetry spans, the judge can cite tool arguments, routing decisions, model calls, and latency as evidence — not just the final response. This is the integration shape every flagship example uses.

For 33 supported frameworks (OpenAI Agents SDK, LangChain/LangGraph, CrewAI, DSPy, LlamaIndex, AutoGen, MAF, Pydantic AI, Smolagents, Instructor, Haystack, …), instrumentation is **two lines** at the top of your callable module:

```python
# e.g. examples/travel_planner_langgraph/auto_trace.py
from phoenix.otel import register

register(auto_instrument=True)  # picks up any installed openinference-instrumentation-* package

def chat_sync(message: str, history: list[dict[str, str]] | None = None) -> str:
    return run_my_agent(message, history)
```

Wire the target up in your config:

```yaml
pipeline:
  inference:
    target:
      callable: examples.travel_planner_langgraph.auto_trace:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
```

See `examples/phoenix_auto_trace/` for one runnable file per framework.

### Why traces matter to the judge

The judge can only score what it sees. With final text only:

- it cannot tell if the agent used the right tool with the right arguments
- it cannot tell which sub-agent or branch made a decision
- "the answer was right but for the wrong reason" looks like a pass

With trace capture, the judge cites specific spans as evidence and catches process failures even when the surface answer looks fine.

## Customization

The customization paths exist as fallbacks. The judge sees less, so use them only when (a) you cannot instrument the target or (b) you are validating the eval pipeline itself, not the agent.

### Customization with OTel traces (frameworks not on the auto-instrument list)

If your framework is not in the [auto-instrument list](https://github.com/Arize-ai/openinference#instrumentations) — or you have custom orchestration — emit OTel spans yourself with the OpenTelemetry SDK. ASSERT's `target.trace` block reads the same span data either way.

```yaml
pipeline:
  inference:
    target:
      callable: examples.travel_planner_neurosan.agent:plan_trip_sync
      trace:
        backend: phoenix
        group_by: session.id
```

`examples/travel_planner_neurosan/agent.py` shows ~20 lines that wrap a multi-agent flow in `tracer.start_as_current_span(...)` calls following OpenInference semantic conventions. Same trace visibility as auto-instrumentation; the judge cannot tell the difference.

### Customization without traces

Omit `target.trace` only when:

- your target is a black-box API you cannot instrument
- you are smoke-testing a thin wrapper around a hosted model
- you are validating the eval pipeline itself, not the agent

For real agents this is **not recommended** — the visibility table above shows what the judge loses. To recover tool-call visibility without OTel, return the response object from [LiteLLM](https://github.com/BerriAI/litellm) (a unified Python interface supporting 100+ model providers — Azure OpenAI, Anthropic, Bedrock, Vertex, Ollama, …) directly:

```python
import litellm

def chat(message: str, history: list[dict[str, str]]) -> "litellm.ModelResponse":
    return litellm.completion(model="azure/gpt-4o-mini", messages=history)
```

The judge then sees final tool calls, token usage, and model name — still narrower than OTel (no intermediate routing or sub-agent decisions).

#### Plain Python callable (`target.callable`)

Sync or async function with one of two signatures:

```python
def chat(message: str) -> str: ...                              # single-turn
def chat(message: str, history: list[dict[str, str]]) -> str:   # multi-turn
    ...
```

`history` follows the [OpenAI / LiteLLM chat-messages format](https://platform.openai.com/docs/api-reference/chat/create#chat-create-messages), filtered to `user` / `assistant` roles only. The current user turn is at `history[-1]`; `message` is a convenience for callables that ignore history. System prompts are owned by your callable (`target.system_prompt` is consumed only by the Prompt Agent target).

To round-trip directly into LiteLLM, pass `history` as `messages` — do **not** re-append `message` (it is already at `history[-1]`):

```python
import litellm

def chat(message: str, history: list[dict[str, str]]) -> str:
    response = litellm.completion(model="azure/gpt-4o-mini", messages=history)
    return response.choices[0].message.content
```

Return types and what the judge sees:

| Return type | Judge sees |
|---|---|
| `str`, or `dict` with `text` / `content` | final response text only |
| Any object with a `.choices` attribute — [`litellm.ModelResponse`](https://github.com/BerriAI/litellm), OpenAI's [`ChatCompletion`](https://platform.openai.com/docs/api-reference/chat/object), etc. — or a [`assert_ai.core.model_client.ModelResponse`](../../assert_ai/core/model_client.py) returned directly | final response text **plus** final tool calls, token usage, and model name (the `.choices` form is normalized to `assert_ai.core.model_client.ModelResponse` internally) |

#### HTTP endpoint (`target.endpoint`)

When your agent runs as a service you cannot import as Python, point at its URL:

```yaml
pipeline:
  inference:
    target:
      endpoint: https://my-agent.internal/chat
```

The runtime POSTs `{"message": "...", "history": [...]}` (same `history` shape as above) and expects `{"response": "..."}` back. Same black-box visibility as a plain string-returning callable. Requires [`aiohttp`](https://github.com/aio-libs/aiohttp) (`pip install aiohttp`).
