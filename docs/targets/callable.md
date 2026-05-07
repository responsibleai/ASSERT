# Callable Target

Use the callable target for any agent or multi-agent system with a Python entry function. This is the universal integration boundary — frameworks (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, …), custom orchestration, REST clients, and thin model wrappers all qualify.

The callable target has two integration paths:

- **Recommended (happy path):** OTel-traced agent — two-line auto-instrumentation across 33 supported frameworks. The judge cites tool calls, routing decisions, model calls, and latency as evidence.
- **Customization:** for unsupported frameworks (emit your own OTel spans) or for cases where instrumentation is impossible or unnecessary (plain callable / HTTP endpoint, no traces).

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
  rollout:
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

If your framework is not in the [auto-instrument list](https://github.com/Arize-ai/openinference#instrumentations) — or you have custom orchestration — emit OTel spans yourself with the OpenTelemetry SDK. Adaptive Eval's `target.trace` block reads the same span data either way.

```yaml
pipeline:
  rollout:
    target:
      callable: examples.travel_planner_neurosan.agent:plan_trip_sync
      trace:
        backend: otel        # generic OTel exporter (vs. backend: phoenix for auto-instrument)
        group_by: session.id
```

`examples/travel_planner_neurosan/agent.py` shows ~20 lines that wrap a multi-agent flow in `tracer.start_as_current_span(...)` calls following OpenInference semantic conventions. Same trace visibility as auto-instrumentation; the judge cannot tell the difference.

### Customization without traces

Omit `target.trace` only when:

- your target is a black-box API you cannot instrument (e.g. a third-party endpoint with no execution surface to trace)
- you are running a quick smoke against a thin model wrapper with no real internals
- you are validating the eval pipeline itself, not the agent

**This path is not recommended for evaluating real agents.** Without traces the judge sees only the final response (and, for litellm-style returns, the final tool calls) — it misses intermediate routing, sub-agent decisions, and the per-step tool argument flow. Add OTel instrumentation as soon as the agent has internals worth scoring.

#### Plain Python callable (`target.callable`)

The callable can be sync or async. The signature determines what the runtime passes:

```python
# Single-turn — receives only the current user message.
def chat_sync(message: str) -> str:
    return "assistant response"

# Multi-turn — also receives prior user/assistant turns.
def chat_sync(message: str, history: list[dict[str, str]]) -> str:
    ...
```

`history` is OpenAI / LiteLLM message format with one Adaptive-Eval-specific convention: the **current** user turn is split off into `message`, and `history` contains only **prior** turns:

```python
history == [
    {"role": "user", "content": "..."},        # earlier user turn
    {"role": "assistant", "content": "..."},   # prior assistant reply
    # ...
]
```

To plug `message + history` straight into a LiteLLM call inside your callable:

```python
import litellm

def chat(message: str, history: list[dict[str, str]]) -> str:
    response = litellm.completion(
        model="azure/gpt-5.4-mini",
        messages=history + [{"role": "user", "content": message}],
    )
    return response.choices[0].message.content
```

System prompts are **not** included in `history` — your callable owns the system prompt. (`target.system_prompt` is only consumed by the model+tools target, not by `target.callable`.)

Return types and what the judge sees:

| Return type | Judge sees |
|---|---|
| `str` | final response text only |
| `dict` with `text` or `content` field | final response text only |
| LiteLLM / OpenAI-style response object (has `choices`) | final response text **plus** final tool calls, token usage, and model name extracted from the response |
| `p2m.core.model_client.ModelResponse` | same as the litellm/OpenAI shape above |

Returning the raw litellm/OpenAI response is a useful middle ground for thin model wrappers — the judge gets some tool-call visibility without OTel setup. It is still narrower than OTel: only the **final** tool calls on the response are visible, not intermediate routing or sub-agent decisions.

#### HTTP endpoint (`target.endpoint`)

When your agent runs as a service you cannot import as Python, point at its URL:

```yaml
pipeline:
  rollout:
    target:
      endpoint: https://my-agent.internal/chat
```

The runtime POSTs `{"message": "...", "history": [...]}` (same shape as the plain-callable history above) and expects `{"response": "..."}` back. Same black-box visibility as a plain string-returning callable. Requires `aiohttp` (`pip install aiohttp`).
